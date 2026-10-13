import copy
import csv
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ['KXCI_SIM_ONLY'] = '1'
from keithley_lab.engine import Controller, RunStore, defaults, validate, estimate
from keithley_lab.mock import MockKXCI
from keithley_lab.protocol import KXCI, ProtocolError, parse_reading, parse_sp, AuditError

# Defense in depth: every outbound socket in this test process MUST be loopback.
_connect = socket.create_connection
def loopback_only(address, *args, **kwargs):
    if address[0] != '127.0.0.1':
        raise AssertionError('QA attempted non-loopback connection: '+str(address))
    return _connect(address,*args,**kwargs)
socket.create_connection = loopback_only


class CoreTests(unittest.TestCase):
    def setUp(self):
        (Path(__file__).resolve().parent.parent/'qa').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parent.parent/'qa'))
        self.s = defaults(self.tmp.name)
        self.s.update(hold_s=0., point_delay_s=0., timeout_s=.2)

    def tearDown(self):
        self.tmp.cleanup()

    def run_measurement(self,s=None, fault=None, fault_at=1, factory=RunStore):
        controller=Controller(store_factory=factory, mock_factory=lambda **kw:MockKXCI(fault=fault,fault_at=fault_at,**kw))
        controller.start(s or self.s)
        controller.thread.join(25)
        if controller.thread.is_alive():
            controller.stop()
            controller.thread.join(5)
            self.fail('Run exceeded test deadline; stopped safely')
        self.assertFalse(controller.thread.is_alive(),'Run failed to finish within bound')
        return controller.last_result

    def test_unsigned_and_signed_readings(self):
        for raw,value in [('NAI 2.994974E-09',2.994974e-9),('NAI +2.994974E-09',2.994974e-9),('NAI -2.994974 E-09',-2.994974e-9)]:
            r=parse_reading(raw,1);self.assertTrue(r['valid']);self.assertEqual(r['value_A'],value)

    def test_all_statuses_invalid_unknown_channel(self):
        for code,status in [('C','compliance'),('T','other_channel_compliance'),('L','interval_too_short'),('V','overflow'),('X','oscillation'),('Q','unknown_status')]:
            r=parse_reading(code+'AI 1E-9',1);self.assertFalse(r['valid']);self.assertEqual(r['status'],status)
        self.assertEqual(parse_reading('NAI 9E37',1)['status'],'invalid_sentinel')
        self.assertEqual(parse_reading('NBI 1E-9',1)['status'],'wrong_channel')
        for text in ['NAI NaN','NAI inf','garbage','NAI 2E-9 trailing','NAI 1E999']:
            self.assertFalse(parse_reading(text,1)['valid'])

    def test_sp_data_ready_one_not_error(self):
        self.assertEqual(parse_sp('1')['data_ready'],True)
        self.assertEqual(parse_sp('1')['syntax_error'],False)
        self.assertEqual(parse_sp('2')['syntax_error'],True)
        for raw in ['ACK','256','-1','1 garbage']:
            with self.assertRaises(ProtocolError):parse_sp(raw)

    def test_fragments_full_run_cleanup_and_saved_units(self):
        result=self.run_measurement()
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['cleanup']['SP']['raw'],1)
        self.assertEqual(len(result['cleanup']['commands']),6)
        path=Path(result['path'])
        with (path/'points.csv').open(newline='',encoding='utf-8') as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),1)
        self.assertEqual(float(rows[0]['drain_current_A']),3e-9)
        self.assertEqual(rows[0]['drain_measured_V'],'')
        self.assertEqual(rows[0]['instrument_time_s'],'')
        self.assertRegex(rows[0]['drain_pc_reply'],r'[+-]\d\d:\d\d$')
        self.assertEqual(rows[0]['SP'],'1')
        meta=json.loads((path/'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['firmware'],'KI4200 V8.2-SP4')
        self.assertFalse(meta['physical_output_verified_off'])
        self.assertTrue((path/'summary.md').exists());self.assertTrue((path/'plot.svg').exists())

    def test_family_order_return_and_idvg(self):
        s=copy.deepcopy(self.s);s.update(workflow='Id-Vd',family_V=[0.,.01],return_sweep=True)
        s['limits']['gate']['max_V']=.01
        _,plan=validate(s)
        self.assertEqual(len(plan),22)
        self.assertEqual([p['sweep_V'] for p in plan[:11]],[0.,.01,.02,.03,.04,.05,.04,.03,.02,.01,0.])
        self.assertEqual(plan[11]['gate'],.01)
        result=self.run_measurement(s)
        self.assertEqual(result['status'],'completed',result)
        s.update(workflow='Id-Vg',stop_V=.01,step_V=.01,family_V=[.025,.05])
        _,plan=validate(s)
        self.assertEqual(len(plan),6);self.assertEqual(plan[3]['drain'],.05)
        result=self.run_measurement(s);self.assertEqual(result['status'],'completed',result)

    def test_descending_and_limits_unchanged(self):
        self.s.update(workflow='Id-Vd',start_V=.05,stop_V=0.,step_V=-.01,direction='descending')
        before=copy.deepcopy(self.s['limits']);_,plan=validate(self.s)
        self.assertEqual(plan[0]['drain'],.05);self.assertEqual(plan[-1]['drain'],0.)
        self.assertEqual(self.s['limits'],before)

    def test_validation_rejections(self):
        cases=[]
        s=copy.deepcopy(self.s);s['mapping']['gate']=1;cases.append(s)
        s=copy.deepcopy(self.s);s['limits']['gate']['compliance_A']=0;cases.append(s)
        s=copy.deepcopy(self.s);s['limits']['drain']['max_V']=float('nan');cases.append(s)
        s=copy.deepcopy(self.s);s['fixed_V']['drain']=1;cases.append(s)
        s=copy.deepcopy(self.s);s.update(workflow='Id-Vd',step_V=-.01);cases.append(s)
        s=copy.deepcopy(self.s);s.update(workflow='Id-Vd',step_V=.0000001);cases.append(s)
        s=copy.deepcopy(self.s);s.update(workflow='Id-Vd',step_V=.03);cases.append(s)
        s=copy.deepcopy(self.s);s.update(workflow='Id-Vd',direction='descending');cases.append(s)
        s=copy.deepcopy(self.s);s.update(workflow='Id-Vd',family_V=[float('inf')]);cases.append(s)
        s=copy.deepcopy(self.s);s['save_root']='relative';cases.append(s)
        s=copy.deepcopy(self.s);s['temperature']['status']='confirmed';cases.append(s)
        for s in cases:
            with self.subTest(settings=s):
                with self.assertRaises(ValueError):validate(s)
        s=copy.deepcopy(self.s);del s['limits']['source']
        with self.assertRaises(KeyError):validate(s)

    def test_timeout_partial_disconnect_and_multiple(self):
        for fault in ['timeout','partial','disconnect','multiple']:
            with self.subTest(fault=fault):
                result=self.run_measurement(fault=fault)
                self.assertEqual(result['status'],'error')
                self.assertIn('UNKNOWN',result['cleanup']['status'])
                self.assertEqual(len(result['rows_in_memory']),1)
                self.assertTrue(Path(result['path'],'points.csv').exists())

    def test_disconnect_during_bias(self):
        result=self.run_measurement(fault='disconnect_bias')
        self.assertEqual(result['status'],'error')
        self.assertIn('UNKNOWN',result['cleanup']['status'])
        self.assertEqual(len(result['cleanup']['commands']),0)

    def test_fault_readings_preserve_prior_and_fault_point(self):
        self.s.update(workflow='Id-Vd')
        for fault in ['compliance','malformed','overflow','unknown','sentinel']:
            with self.subTest(fault=fault):
                result=self.run_measurement(fault=fault,fault_at=4)
                self.assertEqual(result['status'],'error')
                self.assertEqual(len(result['rows_in_memory']),2)
                self.assertEqual(result['rows_in_memory'][0]['point_status'],'normal')
                self.assertEqual(len(result['cleanup']['commands']),6)

    def test_syntax_fault_stops(self):
        result=self.run_measurement(fault='syntax')
        self.assertEqual(result['status'],'error')
        self.assertIn('SP',result['error'])

    def test_disk_failure_preserves_partial_and_cleanup(self):
        class FailingStore(RunStore):
            def append(self,row):
                if len(self.rows)>=1:raise OSError('Injected disk full')
                super().append(row)
        self.s.update(workflow='Id-Vd')
        result=self.run_measurement(factory=FailingStore)
        self.assertEqual(result['status'],'error')
        self.assertEqual(len(result['rows_in_memory']),2)
        self.assertEqual(len(result['cleanup']['commands']),6)
        self.assertTrue(Path(result['recovery_path']).exists())
        with Path(result['path'],'points.csv').open(encoding='utf-8',newline='') as f:self.assertEqual(len(list(csv.DictReader(f))),1)

    def test_command_journal_failure_allows_cleanup(self):
        class FailingJournal(RunStore):
            def event(self,kind,text):
                if kind=='send' and text=='TI1':raise OSError('Injected journal disk full')
                super().event(kind,text)
        result=self.run_measurement(factory=FailingJournal)
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['cleanup']['status'],'acknowledged_only_not_physically_verified')

    def test_destination_failure_before_connect(self):
        def fail(*a):raise OSError('Injected unwritable folder')
        with mock.patch('keithley_lab.engine.KXCI') as transport:
            result=self.run_measurement(factory=fail)
            transport.assert_not_called()
        self.assertEqual(result['status'],'error')

    def test_cancel_double_start_and_reopen_no_resume(self):
        self.s.update(workflow='Id-Vd',hold_s=5.)
        c=Controller();c.start(self.s)
        with self.assertRaises(RuntimeError):c.start(self.s)
        time.sleep(.18);c.stop();c.thread.join(5)
        self.assertFalse(c.thread.is_alive());self.assertEqual(c.last_result['status'],'cancelled')
        self.assertEqual(len(c.last_result['cleanup']['commands']),6)
        reopened=Controller();self.assertFalse(reopened.active);self.assertIsNone(reopened.thread)
        self.assertEqual(defaults(self.tmp.name)['mode'],'simulation')

    def test_cancel_after_saved_point_preserves_partial_data(self):
        self.s.update(workflow='Id-Vd',point_delay_s=.2)
        c=Controller();c.start(self.s)
        deadline=time.monotonic()+10
        saw_point=False
        while time.monotonic()<deadline:
            kind,data=c.events.get(timeout=10)
            if kind=='point':saw_point=True;break
            if kind=='done':break
        self.assertTrue(saw_point)
        c.stop();c.thread.join(5)
        self.assertEqual(c.last_result['status'],'cancelled')
        with Path(c.last_result['path'],'points.csv').open(encoding='utf-8',newline='') as f:
            rows=list(csv.DictReader(f))
        self.assertGreaterEqual(len(rows),1)
        self.assertEqual(rows[0]['point_status'],'normal')
        self.assertEqual(len(c.last_result['cleanup']['commands']),6)

    def test_real_blocked_and_sim_cannot_target_instrument(self):
        self.s['mode']='real'
        with self.assertRaises(ValueError):Controller().start(self.s)
        with self.assertRaises(RuntimeError):KXCI('192.0.2.1',1225,simulation=False)
        with self.assertRaises(ValueError):KXCI('192.0.2.1',1225,simulation=True)

    def test_unique_runs_no_overwrite(self):
        a=self.run_measurement();b=self.run_measurement()
        self.assertNotEqual(a['path'],b['path'])

    def test_partial_reading_after_good_channels(self):
        result=self.run_measurement(fault='disconnect',fault_at=3)
        row=result['rows_in_memory'][0]
        self.assertIsNotNone(row['drain_current_A']);self.assertIsNotNone(row['source_current_A'])
        self.assertIsNone(row['gate_current_A'])

    @unittest.skipUnless(os.name=='nt','Windows named mutex')
    def test_second_gui_instance_blocked_before_tk(self):
        import ctypes
        from keithley_lab import gui
        kernel=ctypes.windll.kernel32
        kernel.CreateMutexW.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.CreateMutexW(None,False,'Local\\KeithleyLab4200SCS_v1')
        self.assertTrue(handle)
        try:
            with mock.patch.object(gui.tk,'Tk') as root, mock.patch.object(ctypes.windll.user32,'MessageBoxW') as alert:
                gui.main()
                root.assert_not_called();alert.assert_called_once()
        finally:
            kernel.CloseHandle(handle)


if __name__=='__main__':unittest.main(verbosity=2)
