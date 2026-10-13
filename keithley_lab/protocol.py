"""Legacy KXCI user-mode transport; no retry and one outstanding command."""
import math
import os
import re
import select
import socket
import threading
import time

EXPECTED_ID = 'KI4200 V8.2-SP4'
STATUSES = {'N': 'normal', 'L': 'interval_too_short', 'V': 'overflow',
            'X': 'oscillation', 'C': 'compliance', 'T': 'other_channel_compliance'}


class ProtocolError(RuntimeError):
    pass


class AuditError(IOError):
    pass


class ResponseError(ProtocolError):
    """One complete frame received, but its command-specific payload is invalid."""


def response_kind(command):
    if command == 'ID':return 'identity'
    if command == 'SP':return 'status'
    if re.fullmatch(r'TI[1-3]',command):return 'reading'
    if command == 'US' or re.fullmatch(r'IT[1-3]',command) or re.fullmatch(r'DV[1-3](?:,0,[+-]?[0-9.eE+-]+,[0-9.eE+-]+)?',command):return 'completion'
    raise ProtocolError('Unsupported command in reviewed legacy transport: '+repr(command))


def decode_response(frame, kind):
    """NUL is the boundary; a single trailing CRLF is optional, never inferred."""
    payload=frame[:-2] if frame.endswith(b'\r\n') else frame
    if any(b<32 or b>126 for b in payload):
        raise ResponseError('Non-ASCII/control or multiline response payload')
    reply=payload.decode('ascii').strip(' ')
    if kind=='completion':
        if payload==b'':return '', 'empty_completion'
        if reply=='ACK':return reply, 'literal_ACK'
        raise ResponseError('Expected empty completion or literal ACK, received '+repr(reply))
    if not reply:raise ResponseError('Empty payload is not a valid '+kind+' response')
    if kind=='identity' and not re.fullmatch(r'KI4200 [A-Za-z0-9.\-]+',reply):
        raise ResponseError('Malformed identity response: '+repr(reply))
    if kind=='status':
        try:parse_sp(reply)
        except ProtocolError as exc:raise ResponseError(str(exc))
    # Reading grammar/status/channel checks remain in parse_reading so the raw
    # malformed value and explicit status are retained in the partial point.
    return reply, kind


def parse_reading(raw, channel):
    result = {'raw': raw, 'status_code': '', 'status': 'malformed',
              'value_A': None, 'channel': channel, 'valid': False}
    match = re.fullmatch(r'\s*([A-Z])([A-H])(I)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*[Ee][+-]?\d+)\s*', raw)
    if not match:
        return result
    code, letter, _, number = match.groups()
    result.update(status_code=code, status=STATUSES.get(code, 'unknown_status'))
    if ord(letter) - ord('A') + 1 != channel:
        result['status'] = 'wrong_channel'
        return result
    value = float(re.sub(r'\s+', '', number))
    if not math.isfinite(value) or abs(value) >= 9e37:
        result['status'] = 'invalid_sentinel'
        return result
    result['value_A'] = value
    result['valid'] = code == 'N'
    return result


def parse_sp(raw):
    if not re.fullmatch(r'\d{1,3}', raw):
        raise ProtocolError('Malformed SP response: ' + repr(raw))
    value = int(raw)
    if not 0 <= value <= 255:
        raise ProtocolError('SP outside byte range')
    return {'raw': value, 'data_ready': bool(value & 1), 'syntax_error': bool(value & 2),
            'busy': bool(value & 16), 'request_service': bool(value & 64),
            'unknown_bits': value & ~83}


class KXCI:
    def __init__(self, host, port, timeout=3.0, simulation=False, audit=None):
        if simulation and host != '127.0.0.1':
            raise ValueError('Simulation transport must use literal loopback')
        if not simulation and os.environ.get('KXCI_SIM_ONLY') == '1':
            raise RuntimeError('QA lock: real instrument access is disabled')
        if not 0.1 <= timeout <= 10:
            raise ValueError('Command timeout must be 0.1..10 seconds')
        self.host, self.port, self.timeout = host, int(port), timeout
        self.audit = audit or (lambda *args: None)
        self.sock = None
        self.broken = False
        self.lock = threading.Lock()
        self.last_exchange = None

    def connect(self):
        if self.sock is not None or self.broken:
            raise ProtocolError('Session cannot reconnect; create a new reviewed run')
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def exchange(self, command):
        if not self.lock.acquire(False):
            raise ProtocolError('Concurrent instrument command rejected')
        data=bytearray();record={'command':command,'tx_hex':'','tx_state':'not_sent',
                                'rx_hex':'','chunks_hex':[],'complete_frame':False}
        self.last_exchange=record
        wire_logged=False
        try:
            if self.broken or self.sock is None:
                raise ProtocolError('Disconnected or desynchronized session')
            if not command or any(c in command for c in '\0\r\n;'):
                raise ProtocolError('Only one ASCII command per frame is allowed')
            kind=response_kind(command);record['expected_kind']=kind
            # An unsolicited reply must never be consumed as the next command ACK.
            if select.select([self.sock], [], [], 0)[0]:
                record['stale_peek_hex']=self.sock.recv(4096,socket.MSG_PEEK).hex()
                raise ProtocolError('Unsolicited/stale bytes before command')
            try:
                self.audit('send', command)
            except Exception as exc:
                raise AuditError('Command journal write failed before send: '+str(exc))
            deadline = time.monotonic() + self.timeout
            self.sock.settimeout(self.timeout)
            tx=command.encode('ascii')+b'\0';record['tx_hex']=tx.hex()
            record['tx_state']='send_attempted_possibly_partial'
            self.sock.sendall(tx);record['tx_state']='sent'
            while b'\0' not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Full response deadline exceeded')
                self.sock.settimeout(remaining)
                packet = self.sock.recv(4096)
                if not packet:
                    raise ConnectionError('Instrument disconnected before complete frame')
                data.extend(packet)
                record['chunks_hex'].append(packet.hex())
                record['rx_hex']=bytes(data).hex()
                if len(data) > 16384:
                    raise ProtocolError('Response exceeds frame size limit')
            frame, extra = bytes(data).split(b'\0', 1)
            record.update(complete_frame=True,frame_hex=(frame+b'\0').hex(),extra_hex=extra.hex(),
                          trailing_crlf=frame.endswith(b'\r\n'))
            if extra or select.select([self.sock], [], [], 0)[0]:
                if not extra:record['extra_peek_hex']=self.sock.recv(4096,socket.MSG_PEEK).hex()
                raise ProtocolError('Multiple or unsolicited response frames')
            try:
                self.audit('receive_bytes',dict(record));wire_logged=True
            except Exception as exc:
                raise AuditError('Raw response journal write failed after complete frame: '+str(exc))
            reply,reply_kind=decode_response(frame,kind)
            record.update(reply=reply,reply_kind=reply_kind)
            try:
                self.audit('receive', reply)
            except Exception as exc:
                raise AuditError('Command journal write failed after complete reply: '+str(exc))
            return reply
        except (AuditError,ResponseError) as exc:
            # No outstanding reply: preserve synchronized transport for emergency cleanup.
            record['error']=type(exc).__name__+': '+str(exc)
            raise
        except Exception as exc:
            record['error']=type(exc).__name__+': '+str(exc)
            self.broken = True
            self.close()
            raise
        finally:
            if record.get('error'):
                try:
                    if not wire_logged:self.audit('receive_bytes',dict(record))
                    self.audit('exchange_error',dict(record))
                except Exception:pass  # Memory evidence survives even a failed disk.
            self.lock.release()

    def ack(self, command):
        if response_kind(command)!='completion':
            raise ProtocolError('Data query cannot be treated as an ACK command')
        reply = self.exchange(command)
        # Empty completion is NOT literal ACK or physical-state proof. The normal
        # acquisition must SP-check it before proceeding; cleanup checks SP last.
        return self.last_exchange['reply_kind']

    def status(self):
        status = parse_sp(self.exchange('SP'))
        if status['syntax_error'] or status['busy'] or status['unknown_bits']:
            raise ProtocolError('Instrument SP fault/busy: %s' % status)
        return status
