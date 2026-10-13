import copy
import csv
import datetime as dt
import json
import os
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import patch
import uuid

from keithley_lab.engine import defaults, Controller
from keithley_lab.mock import MockKXCI
from keithley_lab.metadata import device_document, save_new, snapshot, render, read_document
from keithley_lab.sequence import review_queue, SequenceController, digest, aggregate

ROOT=Path(__file__).absolute().parent.parent
DEVICE={'id':'QA-device','pair':'custom','L_um':None,'W_um':None,'oxide_nm':None,'confirmed':False}


class SequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['KXCI_SIM_ONLY']='1'
        cls.connect=socket.create_connection
        def guarded(address,*args,**kwargs):
            if address[0]!='127.0.0.1':raise AssertionError('Non-loopback blocked')
            return cls.connect(address,*args,**kwargs)
        cls.guard=patch('socket.create_connection',guarded);cls.guard.start()

    @classmethod
    def tearDownClass(cls):cls.guard.stop()

    def setUp(self):
        self.workspace=ROOT/'qa'/'v11_data'/uuid.uuid4().hex[:10];self.workspace.mkdir(parents=True)
        self.s=defaults(self.workspace);self.s.update(workflow='Id-Vd',hold_s=0,point_delay_s=0,start_V=0,stop_V=.02,step_V=.01)

    def review(self,items=None,settle=0,device=None):
        return review_queue(items or [{'settings':self.s,'repeats':1}],device or DEVICE,str(self.workspace/'UNUSED_REAL'),self.workspace,settle)

    def launch(self,reviewed,factory=None,clock=None):
        def fast(**kwargs):return Controller(mock_factory=lambda **kw:MockKXCI(fragment=False,**kw),**kwargs)
        c=SequenceController(self.workspace,controller_factory=factory or fast,clock=clock)
        c.start(reviewed,{'queue_hash':digest(reviewed),'confirmed_at':'QA simulation review'});return c

    def wait(self,c):
        c.thread.join(180);self.assertFalse(c.active,'deadline');return c.last_result

    def test_100_complete_sweeps_separate_files_and_statistics(self):
        r=self.review([{'settings':self.s,'repeats':100}]);c=self.launch(r);result=self.wait(c)
        self.assertEqual(result['status'],'completed',result['error']);self.assertEqual(len(result['runs']),100)
        self.assertEqual(len({x['run_id'] for x in result['runs']}),100)
        stats=json.loads((Path(result['path'])/'step01_statistics.json').read_text())
        self.assertTrue(all(x['n_valid']==100 and x['n_missing']==0 and x['sample_std_A']==0 for x in stats['means']))
        self.assertEqual(len(list((self.workspace/'simulation_data').glob('*/*/기록/*/*/points.csv'))),100)
        self.assertFalse((self.workspace/'UNUSED_REAL').exists())
        (ROOT/'qa'/'repeat100.json').write_text(json.dumps({'passed':True,'sweeps':100,'points':300,'result_path':result['path'],'statistics':stats['means']},indent=2),encoding='utf-8')

    def test_queue_order_and_explicit_limits_for_later_step(self):
        second=copy.deepcopy(self.s);second.update(workflow='Id-Vg',family_V=[.02]);second['limits']['gate']['max_V']=.02
        items=[{'settings':self.s,'repeats':2},{'settings':second,'repeats':1},{'settings':self.s,'repeats':1}]
        r=self.review(items);result=self.wait(self.launch(r));self.assertEqual(result['status'],'completed',result['error'])
        self.assertEqual([(x['step'],x['repeat']) for x in result['runs']],[(1,1),(1,2),(2,1),(3,1)])
        bad=copy.deepcopy(items);bad[2]['settings']['stop_V']=2
        with self.assertRaises(ValueError):self.review(bad)

    def test_cancel_between_steps_double_start_restart(self):
        r=self.review([{'settings':self.s,'repeats':3}],settle=2);c=self.launch(r)
        with self.assertRaises(RuntimeError):c.start(r,{'queue_hash':digest(r),'confirmed_at':'QA'})
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            event=c.events.get(timeout=5)
            if event[0]=='phase' and '전환:' in event[1]:break
        c.stop();result=self.wait(c);self.assertEqual(result['status'],'cancelled');self.assertEqual(len(result['runs']),1)
        fresh=SequenceController(self.workspace);self.assertFalse(fresh.active);self.assertIsNone(fresh.thread)

    def test_midnight_next_repeat_uses_new_date_and_collision_free(self):
        times=iter([dt.datetime(2026,10,13,23,59,50,tzinfo=dt.timezone.utc),dt.datetime(2026,10,13,23,59,59,tzinfo=dt.timezone.utc),dt.datetime(2026,10,14,0,0,1,tzinfo=dt.timezone.utc)])
        r=self.review([{'settings':self.s,'repeats':2}]);result=self.wait(self.launch(r,clock=lambda:next(times)))
        self.assertEqual([x['acquisition_start_date'] for x in result['runs']],['2026-10-13','2026-10-14'])
        another=self.wait(self.launch(r));self.assertNotEqual(result['sequence_id'],another['sequence_id'])
        self.assertIn('2026-10-14',result['runs'][1]['path'])

    def test_compliance_halts_queue_preserves_raw_export_flags(self):
        def fault(**kwargs):return Controller(mock_factory=lambda **kw:MockKXCI(fragment=False,fault='compliance',**kw),**kwargs)
        result=self.wait(self.launch(self.review([{'settings':self.s,'repeats':3}]),factory=fault))
        self.assertEqual(result['status'],'error');self.assertEqual(len(result['runs']),1)
        self.assertEqual(len(result['runs'][0]['cleanup']['commands']),6)
        flat=result['runs'][0]['wide_export'];self.assertFalse(flat['files'])
        receipt=json.loads(Path(flat['receipts'][0]).read_text(encoding='utf-8'))
        self.assertEqual(receipt['point_qc'][0]['status'],'compliance')
        self.assertFalse(receipt['analysis_ready'])
        stats=json.loads((Path(result['path'])/'step01_statistics.json').read_text())
        self.assertTrue(all(x['n_valid']==0 for x in stats['means']))

    def test_export_disk_failure_halts_next_repeat_after_cleanup(self):
        with patch('keithley_lab.wide_export.export_repeat',side_effect=OSError('injected export disk failure')):
            result=self.wait(self.launch(self.review([{'settings':self.s,'repeats':2}])))
        self.assertEqual(result['status'],'error');self.assertEqual(len(result['runs']),1)
        self.assertEqual(len(result['cleanup']['commands']),6)
        self.assertTrue((Path(result['runs'][0]['path'])/'points.csv').is_file())

    def test_preserve_existing_metadata_and_detect_review_race(self):
        device_dir=self.workspace/'simulation_data'/DEVICE['id'];document=device_document(DEVICE)
        document['structure']['material']={'value':'ExistingUserMaterial','verification':'user_confirmed'}
        document['run_conditions']={'temperature':{'value':25,'unit':'C','verification':'user_confirmed'}}
        path=save_new(device_dir,document);original=path.read_bytes()
        with self.assertRaises(FileExistsError):save_new(device_dir,device_document(DEVICE))
        self.assertEqual(path.read_bytes(),original)
        r=self.review();self.assertEqual(r['stable_metadata']['structure']['material']['value'],'ExistingUserMaterial')
        self.assertNotIn('run_conditions',r['stable_metadata'])
        path.write_text(render(document,'User edit retained.'),encoding='utf-8')
        with self.assertRaises(ValueError):self.launch(r)

    def test_sparse_root_shadows_historical_material_unknowns(self):
        folder=self.workspace/'simulation_data'/DEVICE['id']
        sparse={'schema_version':1,'device_name':{'value':DEVICE['id']},'structure':{'electrodes':{'metal':'Au'}}}
        save_new(folder,sparse);stable,receipt=snapshot(folder,device_document(DEVICE))
        self.assertIsNone(stable['structure']['material']['value'])
        self.assertIsNone(stable['structure']['thickness']['value'])
        self.assertIsNone(stable['structure']['gate_dielectric']['relative_permittivity']['value'])
        self.assertEqual(stable['structure']['electrodes']['metal'],'Au')

    def test_aggregation_drift_missing_invalid_branch_alignment(self):
        self.s['return_sweep']=True;r=self.review([{'settings':self.s,'repeats':4}]);item=r['items'][0]
        results=[]
        for repeat in range(1,4):
            rows=[]
            for i,p in enumerate(item['plan'],1):
                row={'point_index':i,'curve':p['curve'],'branch':p['branch'],'point_status':'normal'}
                for role in ('drain','source','gate'):row.update({role+'_SMU':self.s['mapping'][role],role+'_programmed_V':p[role],role+'_status':'normal',role+'_current_A':repeat*1e-9})
                if repeat==2 and i==2:row['point_status']='compliance'
                rows.append(row)
            results.append({'repeat':repeat,'rows':rows})
        means,views,warnings=aggregate(item,results);self.assertTrue(warnings)
        p2=next(x for x in means if x['point_index']==2 and x['role']=='drain')
        self.assertEqual((p2['n_valid'],p2['n_invalid'],p2['n_missing']),(2,1,1));self.assertAlmostEqual(p2['mean_A'],2e-9)
        self.assertEqual(set(x['branch'] for x in means),{'forward','reverse'})
        results[0]['rows'][0]['drain_SMU']=3
        with self.assertRaises(ValueError):aggregate(item,results)

    def test_invalid_names_and_repeat_limit(self):
        for name in ('..','a/b','CON','bad.',''):
            d=dict(DEVICE,id=name)
            with self.assertRaises(ValueError):self.review(device=d)
        for n in (0,101,True):
            with self.assertRaises(ValueError):self.review([{'settings':self.s,'repeats':n}])

    def test_mean_export_separate_grid_provenance_and_raw_preservation(self):
        self.s['return_sweep']=True;self.s['family_V']=[0,.01];self.s['limits']['gate']['max_V']=.01
        result=self.wait(self.launch(self.review([{'settings':self.s,'repeats':2}])))
        self.assertEqual(result['status'],'completed',result['error'])
        means=result['mean_exports']['1'];self.assertEqual(len(means['files']),2)
        self.assertIn('평균_분석입력',means['folder']);self.assertNotIn('원본',means['folder'])
        for file in means['files']:
            path=Path(file)
            with path.open(encoding='utf-8-sig') as stream:
                next(stream);rows=list(csv.DictReader(stream))
            self.assertTrue(all(x['data_kind']=='aggregate_mean_not_raw' for x in rows))
            provenance=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf-8'))
            self.assertEqual(len(provenance['sources']),2)
            self.assertTrue(all(Path(x['raw_points_path']).is_file() and x['raw_points_sha256'] for x in provenance['sources']))
            self.assertTrue(all(x['n_valid']==2 for x in provenance['statistics']))
            self.assertEqual(len(provenance['included_family_indices']),2)
        self.assertEqual(len(result['runs']),2)
        self.assertTrue(all((Path(r['path'])/'points.csv').is_file() for r in result['runs']))

    def test_mean_zero_valid_grid_preserved_and_spot_not_curve(self):
        def fault(**kwargs):return Controller(mock_factory=lambda **kw:MockKXCI(fragment=False,fault='compliance',**kw),**kwargs)
        result=self.wait(self.launch(self.review([{'settings':self.s,'repeats':3}]),factory=fault))
        export=result['mean_exports']['1']
        self.assertEqual(export['status'],'qc_only_no_valid_measurements');self.assertFalse(export['files'])
        p=json.loads(Path(export['receipts'][0]).read_text(encoding='utf-8'))
        self.assertFalse(p['analysis_ready']);self.assertEqual(len(p['sources']),1)
        self.assertTrue(all(x['n_valid']==0 for x in p['statistics']))
        self.s['workflow']='Spot';result=self.wait(self.launch(self.review()))
        self.assertEqual(result['mean_exports']['1']['status'],'not_exported_spot')

    def test_mean_disk_failure_stops_later_step_without_retry(self):
        r=self.review([{'settings':self.s,'repeats':1},{'settings':self.s,'repeats':1}])
        with patch('keithley_lab.wide_export.export_means',side_effect=OSError('mean destination failure')) as export:
            result=self.wait(self.launch(r))
        self.assertEqual(result['status'],'error');self.assertEqual(len(result['runs']),1);self.assertEqual(export.call_count,1)


if __name__=='__main__':unittest.main()
