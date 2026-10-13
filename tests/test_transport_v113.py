import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from tests import test_core as core_fixtures
from keithley_lab.protocol import KXCI,ProtocolError,ResponseError,decode_response,parse_reading
from keithley_lab.engine import Controller,RunStore
from keithley_lab.mock import MockKXCI


class WireServer:
    """Scripted loopback transcript, never an instrument endpoint."""
    def __init__(self,steps):
        self.steps=steps;self.commands=[];self.error=None;self.done=threading.Event()
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen(1);self.port=self.sock.getsockname()[1]
        self.client=None;self.thread=threading.Thread(target=self.serve,daemon=True);self.thread.start()
    def serve(self):
        try:
            self.client,_=self.sock.accept();self.client.settimeout(2);pending=b''
            for expected,chunks in self.steps:
                while b'\0' not in pending:
                    packet=self.client.recv(4096)
                    if not packet:return
                    pending+=packet
                cmd,pending=pending.split(b'\0',1);cmd=cmd.decode('ascii');self.commands.append(cmd)
                if cmd!=expected:raise AssertionError((expected,cmd))
                for delay,payload in chunks:
                    if self.done.wait(delay):return
                    if payload is None:self.client.close();return
                    self.client.sendall(payload)
            self.done.wait(2)
        except (ConnectionError,OSError):pass
        except Exception as exc:self.error=exc
    def close(self):
        self.done.set()
        if self.client:
            try:self.client.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            self.client.close()
        self.sock.close();self.thread.join(3)


class ResponseTests(unittest.TestCase):
    def test_completion_nul_and_literal_are_distinct(self):
        for frame in [b'',b'\r\n']:self.assertEqual(decode_response(frame,'completion'),('','empty_completion'))
        for frame in [b'ACK',b'ACK\r\n']:self.assertEqual(decode_response(frame,'completion'),('ACK','literal_ACK'))
        for frame in [b'0',b'ERROR',b'\x06',b' ',b'ACK\n',b'ACK\r',b'ACK\r\nBAD',b'\xff']:
            with self.subTest(frame=frame),self.assertRaises(ResponseError):decode_response(frame,'completion')

    def test_queries_require_payload_and_shape(self):
        for kind in ['identity','status','reading']:
            with self.assertRaises(ResponseError):decode_response(b'',kind)
        for frame in [b'ACK',b'-1',b'256',b'1junk',b'NaN']:
            with self.assertRaises(ResponseError):decode_response(frame,'status')
        self.assertEqual(decode_response(b'1','status')[0],'1')
        self.assertEqual(decode_response(b'KI4200 V8.2-SP4\r\n','identity')[0],'KI4200 V8.2-SP4')
        for frame in [b'NAI 2.994974E-09',b'NAI 2.994974E-09\r\n']:
            self.assertTrue(parse_reading(decode_response(frame,'reading')[0],1)['valid'])

    def test_fragmented_mixed_transcript_and_data_ready(self):
        server=WireServer([('ID',[(0,b'KI4200 V8.'),(.005,b'2-SP4\r'),(.005,b'\n'),(.005,b'\0')]),
                           ('US',[(0,b'\0')]),('SP',[(0,b'1\0')]),('DV1',[(0,b'A'),(.005,b'CK\0')]),('TI1',[(0,b'NAI 2.994974E-09'),(.005,b'\0')])])
        journal=[];c=KXCI('127.0.0.1',server.port,.2,True,lambda k,t:journal.append((k,t)));c.connect()
        try:
            self.assertEqual(c.exchange('ID'),'KI4200 V8.2-SP4');self.assertEqual(c.ack('US'),'empty_completion')
            self.assertEqual(c.status()['raw'],1);self.assertEqual(c.ack('DV1'),'literal_ACK')
            self.assertTrue(parse_reading(c.exchange('TI1'),1)['valid'])
            self.assertTrue(any(k=='receive_bytes' and t['command']=='US' and t['rx_hex']=='00' for k,t in journal))
        finally:c.close();server.close()
        self.assertIsNone(server.error)

    def test_multiple_and_partial_extra_reject_without_next_command(self):
        for extra in [b'\0',b'ACK\0',b'NAI']:
            server=WireServer([('US',[(0,b'\0'+extra)])]);c=KXCI('127.0.0.1',server.port,.2,True);c.connect()
            try:
                with self.assertRaises(ProtocolError):c.ack('US')
                self.assertTrue(c.broken);self.assertEqual(c.last_exchange['rx_hex'],(b'\0'+extra).hex())
                with self.assertRaises(ProtocolError):c.ack('DV1')
                self.assertEqual(server.commands,['US'])
            finally:c.close();server.close()

    def test_delayed_stale_reply_before_next_send_is_not_ack(self):
        server=WireServer([('US',[(0,b'\0'),(.02,b'ACK\0')])]);c=KXCI('127.0.0.1',server.port,.2,True);c.connect()
        try:
            c.ack('US');time.sleep(.06)
            with self.assertRaises(ProtocolError):c.ack('DV1')
            self.assertEqual(c.last_exchange['tx_state'],'not_sent');self.assertEqual(server.commands,['US']);self.assertTrue(c.broken)
        finally:c.close();server.close()

    def test_partial_timeout_disconnect_keep_hex_and_no_retry(self):
        for chunks in [[(0,b'AC'),(.4,b'K\0')],[(0,b'A'),(.01,None)],[(0,b'A'),(.08,b'C'),(.08,b'K'),(.08,b'\0')]]:
            server=WireServer([('US',chunks)]);c=KXCI('127.0.0.1',server.port,.1,True);c.connect()
            try:
                with self.assertRaises((OSError,TimeoutError,ProtocolError,ConnectionError)):c.ack('US')
                self.assertTrue(c.last_exchange['rx_hex'].startswith('41'));self.assertFalse(c.last_exchange['complete_frame']);self.assertTrue(c.broken)
                self.assertEqual(server.commands,['US'])
            finally:c.close();server.close()

    def test_complete_rejection_preserves_sync_for_cleanup(self):
        server=WireServer([('US',[(0,b'ERROR\0')]),('DV1',[(0,b'\0')]),('SP',[(0,b'1\0')])]);c=KXCI('127.0.0.1',server.port,.2,True);c.connect()
        try:
            with self.assertRaises(ResponseError):c.ack('US')
            self.assertFalse(c.broken);self.assertEqual(c.last_exchange['rx_hex'],'4552524f5200')
            c.ack('DV1');self.assertEqual(c.status()['raw'],1)
        finally:c.close();server.close()

    def test_concurrent_call_and_wrong_api_never_send(self):
        server=WireServer([('US',[(0,b'\0')])]);c=KXCI('127.0.0.1',server.port,.2,True);c.connect()
        try:
            c.lock.acquire()
            with self.assertRaises(ProtocolError):c.ack('US')
            c.lock.release()
            with self.assertRaises(ProtocolError):c.ack('SP')
            c.ack('US');self.assertEqual(server.commands,['US'])
        finally:c.close();server.close()

    def test_oversize_frame_bounded_and_preserved(self):
        server=WireServer([('US',[(0,b'A'*17000+b'\0')])]);c=KXCI('127.0.0.1',server.port,.2,True);c.connect()
        try:
            with self.assertRaises(ProtocolError):c.ack('US')
            self.assertTrue(c.broken);self.assertGreater(len(c.last_exchange['rx_hex']),32768)
        finally:c.close();server.close()

    def test_status_errors_never_become_completions(self):
        for payload in [b'2\0',b'16\0',b'4\0',b'256\0',b'\0',b'ACK\0']:
            server=WireServer([('SP',[(0,payload)])]);c=KXCI('127.0.0.1',server.port,.2,True);c.connect()
            try:
                with self.subTest(payload=payload),self.assertRaises(ProtocolError):c.status()
                self.assertFalse(c.broken);self.assertEqual(c.last_exchange['rx_hex'],payload.hex())
            finally:c.close();server.close()


class ControllerWireTests(unittest.TestCase):
    # Reuse only setup helpers, without re-registering the baseline test methods.
    setUp=core_fixtures.CoreTests.setUp
    tearDown=core_fixtures.CoreTests.tearDown
    def run_with(self,factory,store=RunStore):
        c=Controller(mock_factory=factory,store_factory=store);c.start(self.s);c.thread.join(15)
        self.assertFalse(c.active);return c.last_result

    def test_all_completion_styles_complete_and_cleanup_unverified(self):
        for framing in ['mixed','nul','crlf']:
            with self.subTest(framing=framing):
                result=self.run_with(lambda **kw:MockKXCI(framing=framing,**kw))
                self.assertEqual(result['status'],'completed',result)
                self.assertFalse(result['cleanup']['physical_output_verified_off']);self.assertEqual(len(result['cleanup']['commands']),6)
                journal=(Path(result['path'])/'commands.jsonl').read_text(encoding='utf-8')
                self.assertIn('cleanup_receive_bytes',journal)

    def test_unknown_completion_stops_before_bias_and_records_failure(self):
        class Unknown(MockKXCI):
            def _reply(self,command):return 'WHAT' if command=='US' else super()._reply(command)
        result=self.run_with(lambda **kw:Unknown(**kw))
        self.assertEqual(result['status'],'error');self.assertEqual(result['rows_in_memory'],[])
        self.assertEqual(result['failure_exchange']['command'],'US');self.assertEqual(result['failure_exchange']['rx_hex'],'5748415400')
        self.assertEqual(len(result['cleanup']['commands']),6);self.assertFalse(result['cleanup']['physical_output_verified_off'])
        self.assertEqual(json.loads((Path(result['path'])/'settings.json').read_text())['failure_exchange'],result['failure_exchange'])

    def test_receive_journal_failure_still_permits_cleanup(self):
        class Fail(RunStore):
            def event(self,kind,text):
                if kind=='receive_bytes' and text['command']=='US':raise OSError('injected raw journal failure')
                super().event(kind,text)
        result=self.run_with(lambda **kw:MockKXCI(**kw),Fail)
        self.assertEqual(result['status'],'error');self.assertEqual(result['failure_exchange']['rx_hex'],'00')
        self.assertEqual(len(result['cleanup']['commands']),6)

    def test_cleanup_disconnect_marks_unknown(self):
        class BreakCleanup(MockKXCI):
            def _reply(self,command):
                if command.startswith('DV1,0,0,') and any(c.startswith('TI') for c in self.commands):
                    self.clients[-1].shutdown(socket.SHUT_RDWR);return 'ACK'
                return super()._reply(command)
        result=self.run_with(lambda **kw:BreakCleanup(**kw))
        self.assertIn('UNKNOWN',result['cleanup']['status']);self.assertFalse(result['cleanup']['physical_output_verified_off'])

