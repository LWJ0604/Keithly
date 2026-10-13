import copy
import json
from pathlib import Path
import unittest
import uuid

from keithley_lab.engine import defaults,validate
from keithley_lab.planning import generate_family,parse_family,describe_plan,save_queue_file,load_queue_file

ROOT=Path(__file__).absolute().parent.parent

class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'qa'/'planning_data'/uuid.uuid4().hex[:8];self.root.mkdir(parents=True)
        self.s=defaults(self.root)

    def sweep(self,mode='Id-Vd'):
        s=copy.deepcopy(self.s);s.update(workflow=mode,start_V=0,stop_V=.02,step_V=.01,family_V=[.03,.01],return_sweep=True)
        s['limits']['gate']['max_V']=.05
        return s

    def test_family_preserves_order_and_decimal_range(self):
        self.assertEqual(parse_family('.03,0,.01'),[.03,0,.01])
        self.assertEqual(generate_family('.03','0','-.01'),[.03,.02,.01,0])
        self.assertEqual(generate_family('0','.3','.1'),[0,.1,.2,.3])
        self.assertEqual(generate_family('.02','.02','.01'),[.02])

    def test_range_rejects_invalid_inputs(self):
        for args in [('0','1','0'),('0','1','-.1'),('1','0','.1'),('0','1','.3'),('0','1','.0001'),('nan','1','1'),('0','inf','1'),('x','1','1')]:
            with self.subTest(args=args),self.assertRaises(ValueError):generate_family(*args)

    def test_duplicate_or_nonfinite_family_rejected(self):
        for value in ['0,-0','.1,.1','.10000000000001,.1','NaN','.1,inf','0,','',','.join(str(x) for x in range(101))]:
            with self.subTest(value=value),self.assertRaises(ValueError):parse_family(value)
        s=self.sweep();s['family_V']=[0,0]
        with self.assertRaises(ValueError):validate(s)

    def test_spot_ignores_all_hidden_sweep_values(self):
        self.s.update(family_V=[float('nan')],start_V=float('nan'),stop_V=float('inf'),step_V=0,direction='bad',return_sweep=True)
        s,plan=validate(self.s)
        self.assertEqual(s['family_V'],[]);self.assertFalse(s['return_sweep'])
        self.assertEqual(len(plan),1);self.assertEqual((plan[0]['drain'],plan[0]['gate']),(0.05,0))
        json.dumps(s,allow_nan=False)

    def test_sweep_ignores_hidden_fixed_but_source_is_active(self):
        for mode in ('Id-Vd','Id-Vg'):
            s=self.sweep(mode);s['fixed_V'].update(drain=float('nan'),gate=float('inf'))
            clean,plan=validate(s)
            self.assertEqual(clean['fixed_V'],{'drain':0,'gate':0,'source':0})
            axis,fixed=('drain','gate') if mode=='Id-Vd' else ('gate','drain')
            self.assertEqual([p[axis] for p in plan],[0,.01,.02,.01,0]*2)
            self.assertEqual([p[fixed] for p in plan],[.03]*5+[.01]*5)
            s['fixed_V']['source']=.001
            with self.assertRaises(ValueError):validate(s)

    def test_preview_counts_and_branches(self):
        lines=describe_plan(self.sweep(),3)
        self.assertIn('10 points × 3 repeats = 30 points',lines[0])
        self.assertIn('forward 3 + reverse 2 = 5 points',lines[2])
        self.assertIn('Vg=0.03',lines[2]);self.assertIn('Vg=0.01',lines[5])
        self.assertIn('0.02',lines[3]);self.assertNotIn('0.02',lines[4])
        self.assertIn('3 repeats = 3 points',describe_plan(self.s,3)[1])

    def test_queue_roundtrip_canonical_no_mode_switch_or_overwrite(self):
        items=[{'settings':self.s,'repeats':2},{'settings':self.sweep(),'repeats':3},{'settings':self.sweep('Id-Vg'),'repeats':4}]
        for item in items:item['settings']['mode']='real'
        path=self.root/'queue.json';save_queue_file(path,items)
        loaded=load_queue_file(path,'simulation')
        self.assertEqual([i['repeats'] for i in loaded],[2,3,4])
        for before,after in zip(items,loaded):
            self.assertEqual(validate(before['settings'])[1],validate(after['settings'])[1])
            self.assertEqual(after['settings']['mode'],'simulation')
            self.assertEqual(after['settings']['limits'],before['settings']['limits'])
        original=path.read_bytes()
        with self.assertRaises(FileExistsError):save_queue_file(path,items)
        self.assertEqual(path.read_bytes(),original)

    def test_queue_limits_and_untrusted_file(self):
        path=self.root/'queue.json';s=self.sweep()
        with self.assertRaises(ValueError):save_queue_file(path,[{'settings':s,'repeats':101}])
        with self.assertRaises(ValueError):save_queue_file(path,[{'settings':s,'repeats':1}]*51)
        with self.assertRaises(ValueError):save_queue_file(path,[{'settings':s,'repeats':100}]*11)
        path.write_text('{"queue_schema_version":7}',encoding='utf-8')
        with self.assertRaises(ValueError):load_queue_file(path,'simulation')

    def test_sweep_invalid_direction_nonfinite_and_point_cap(self):
        for changes in [{'direction':'descending'},{'step_V':0},{'start_V':float('nan')},{'step_V':.0000001},{'step_V':.003}]:
            s=self.sweep();s.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate(s)
