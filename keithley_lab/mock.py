"""Deterministic loopback-only KXCI simulator. Never creates outbound sockets."""
import socket
import threading
import time

from .protocol import EXPECTED_ID


class MockKXCI:
    def __init__(self, fragment=True, fault=None, fault_at=1, mapping=None, framing='mixed'):
        self.fragment, self.fault, self.fault_at = fragment, fault, fault_at
        self.mapping = mapping or {'drain': 1, 'source': 2, 'gate': 3}
        self.framing=framing
        self.commands = []
        self.bias = {1: 0.0, 2: 0.0, 3: 0.0}
        self.compliance = {1: 1e-4, 2: 1e-4, 3: 1e-4}
        self.enabled = {1: False, 2: False, 3: False}
        self.stop_event = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.sock.settimeout(.1)
        self.port = self.sock.getsockname()[1]
        self.clients = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        for client in self.clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        self.sock.close()
        self.thread.join(1)

    def _serve(self):
        while not self.stop_event.is_set():
            try:
                client, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.clients.append(client)
            client.settimeout(.1)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            buffer = b''
            reads = 0
            try:
                while not self.stop_event.is_set():
                    try:
                        data = client.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    buffer += data
                    while b'\0' in buffer:
                        packet, buffer = buffer.split(b'\0', 1)
                        command = packet.decode('ascii')
                        self.commands.append(command)
                        is_read = command.startswith('TI')
                        if is_read:
                            reads += 1
                        if self.fault == 'disconnect_bias' and command.startswith('DV') and ',0,0.05,' in command:
                            client.close()
                            return
                        reply = self._reply(command)
                        active = is_read and reads == self.fault_at
                        if active:
                            if self.fault == 'timeout':
                                self.stop_event.wait(1.0)
                            elif self.fault == 'disconnect':
                                client.close()
                                return
                            elif self.fault == 'malformed':
                                reply = 'nonsense'
                            elif self.fault == 'compliance':
                                reply = 'C' + reply[1:]
                            elif self.fault == 'overflow':
                                reply = 'V' + reply[1:]
                            elif self.fault == 'unknown':
                                reply = 'Q' + reply[1:]
                            elif self.fault == 'sentinel':
                                reply = 'NAI 9.000000E+37'
                        if self.framing=='crlf':frame=(reply+'\r\n').encode('ascii')+b'\0'
                        elif self.framing=='nul':frame=reply.encode('ascii')+b'\0'
                        else:
                            # Mixed fixture: ID has CRLF, setup completion is empty,
                            # numeric SP/TI are NUL-only. This is not a wire capture.
                            frame=(reply+'\r\n').encode('ascii')+b'\0' if command=='ID' else (b'\0' if reply=='ACK' else reply.encode('ascii')+b'\0')
                        if active and self.fault == 'multiple':
                            frame += b'ACK\r\n\0'
                        if active and self.fault == 'partial':
                            client.sendall(frame[:-1])
                            self.stop_event.wait(1.0)
                            continue
                        if self.fragment and not (active and self.fault == 'multiple'):
                            edges=sorted(set([0,min(1,len(frame)),min(3,len(frame)),max(0,len(frame)-1),len(frame)]))
                            for piece in (frame[a:b] for a,b in zip(edges,edges[1:])):
                                if piece:
                                    client.sendall(piece)
                                    time.sleep(.001)
                        else:
                            client.sendall(frame)
            except (OSError, ValueError):
                pass
            finally:
                client.close()

    def _reply(self, command):
        if command == 'ID':
            return EXPECTED_ID
        if command == 'SP':
            return '2' if self.fault == 'syntax' else '1'
        if command == 'US' or command in ('IT1', 'IT2', 'IT3'):
            return 'ACK'
        if command.startswith('DV'):
            parts = command[2:].split(',')
            channel = int(parts[0])
            if len(parts) == 1:
                self.enabled[channel] = False
            elif len(parts) == 4 and parts[1] == '0':
                self.bias[channel] = float(parts[2])
                self.compliance[channel] = float(parts[3])
                self.enabled[channel] = True
            else:
                return 'ERROR'
            return 'ACK'
        if command.startswith('TI'):
            channel = int(command[2:])
            vd = self.bias[self.mapping['drain']] - self.bias[self.mapping['source']]
            vg = self.bias[self.mapping['gate']] - self.bias[self.mapping['source']]
            drain = vd * (6e-8 + 2e-8 * vg)
            gate = vg * 1e-11
            current = {self.mapping['drain']: drain, self.mapping['source']: -drain-gate,
                       self.mapping['gate']: gate}[channel]
            code = 'N'
            if abs(current) >= self.compliance[channel]:
                code = 'C'
                current = max(-self.compliance[channel], min(self.compliance[channel], current))
            return '%s%sI %.6E' % (code, chr(64+channel), current)
        return 'ERROR'
