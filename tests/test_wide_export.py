import copy,csv,json,math,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from keithley_lab.engine import defaults,validate
from keithley_lab.metadata import device_document
from keithley_lab.wide_export import write_wide,export_means

class WideTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).absolute().parent.parent/'qa/wide_unit'/uuid.uuid4().hex[:8];self.root.mkdir(parents=True)
        self.s=defaults(self.root);self.s.update(workflow='Id-Vd',family_V=[.02,0,.01],start_V=0,stop_V=.02,step_V=.01,return_sweep=True)
        self.s['limits']['gate']['max_V']=.02
        self.s,self.plan=validate(self.s);self.stable=device_document({'id':'QA-wide','pair':'custom','confirmed':False})
        self.rows=[]
        for i,p in enumerate(self.plan,1):
            r={'point_index':i,'curve':p['curve'],'branch':p['branch'],'point_status':'normal','pc_point_start':'2026-10-13T12:00:00+09:00'}
            for role in ('drain','source','gate'):r.update({role+'_SMU':self.s['mapping'][role],role+'_programmed_V':p[role],role+'_status':'normal',role+'_current_A':i*1e-12})
            self.rows.append(r)
    def export(self,rows=None,plan=None):
        return write_wide(self.root/'flat',self.stable,self.s,plan or self.plan,self.rows if rows is None else rows,[],'seq_20261013_120000_test',1,1,'2026-10-13','completed')
    def read(self,f):
        with Path(f).open(encoding='utf-8-sig',newline='') as stream:
            human=next(csv.reader(stream));reader=csv.DictReader(stream);return human,list(reader)
    def test_flat_order_branch_exact_cells_and_units(self):
        result=self.export();self.assertEqual(len(result['files']),2)
        for f in result['files']:
            self.assertEqual(Path(f).parent,self.root/'flat')
            p=json.loads(Path(f).with_suffix('.provenance.json').read_text(encoding='utf-8'))
            h,table=self.read(f);self.assertIn('Vg=0.02',h[1]);self.assertIn('Vg=0 V',h[2]);self.assertEqual(p['included_family_indices'],[1,2,3])
            for c in [1,2,3]:
                rows=[r for r in self.rows if r['curve']==c and r['branch']==p['branch']]
                self.assertEqual([float(r['DrainI (A) (%d)'%c]) for r in table],[r['drain_current_A'] for r in rows])
    def test_partial_missing_and_non_normal_remain_explicit(self):
        rows=copy.deepcopy(self.rows[:7]);rows[1]['point_status']='compliance';rows[1]['drain_status']='compliance'
        result=self.export(rows)
        f=result['files'][0];h,table=self.read(f);p=json.loads(Path(f).with_suffix('.provenance.json').read_text(encoding='utf-8'))
        self.assertTrue(p['partial']);self.assertEqual(table[1]['DrainI (A) (1)'],'');self.assertEqual(p['omitted_no_valid_data_family_indices'],[3])
        self.assertTrue(any(q['status']=='missing_not_acquired' for q in p['point_qc']))
        self.assertIn('_PARTIAL_',f)
    def test_all_invalid_no_fake_analyzable_csv(self):
        rows=copy.deepcopy(self.rows)
        for r in rows:r['point_status']='invalid'
        r=self.export(rows);self.assertFalse(r['files']);self.assertEqual(len(r['receipts']),2)
    def test_duplicate_order_grid_mapping_refused(self):
        variants=[]
        rows=copy.deepcopy(self.rows);rows[1]['point_index']=1;variants.append(rows)
        rows=copy.deepcopy(self.rows);rows[1]['gate_programmed_V']=5;variants.append(rows)
        rows=copy.deepcopy(self.rows);rows[1]['drain_SMU']=2;variants.append(rows)
        for rows in variants:
            with self.assertRaises(ValueError):self.export(rows)
    def test_unequal_family_grid_refused(self):
        p=copy.deepcopy(self.plan);p[5]['drain']=.001
        rows=copy.deepcopy(self.rows);rows[5]['drain_programmed_V']=.001
        with self.assertRaises(ValueError):self.export(rows,p)
    def test_revisited_bias_not_merged(self):
        p=copy.deepcopy(self.plan);rows=copy.deepcopy(self.rows)
        for point in p:
            if point['curve']==3:point['gate']=.02
        for row in rows:
            if row['curve']==3:row['gate_programmed_V']=.02
        result=self.export(rows,p);h,table=self.read(result['files'][0]);self.assertEqual(len([k for k in table[0] if k.startswith('DrainI')]),3)
    def test_idvg_shared_gate_and_fixed_drain(self):
        self.s['workflow']='Id-Vg';self.s['family_V']=[.01,.02];self.s,self.plan=validate(self.s)
        self.rows=[]
        for i,p in enumerate(self.plan,1):
            r={'point_index':i,'curve':p['curve'],'branch':p['branch'],'point_status':'normal'}
            for role in ('drain','source','gate'):r.update({role+'_SMU':self.s['mapping'][role],role+'_programmed_V':p[role],role+'_status':'normal',role+'_current_A':i*1e-12})
            self.rows.append(r)
        f=self.export()['files'][0];h,table=self.read(f);self.assertIn('GateV (V)',table[0]);self.assertEqual(float(table[0]['DrainV (V) (1)']),.01)
    def test_mean_counts_sd_and_raw_contributors(self):
        rows2=copy.deepcopy(self.rows)
        for r in rows2:
            for role in ('drain','source','gate'):r[role+'_current_A']*=3
        item={'settings':self.s,'plan':self.plan,'step':1,'repeats':2}
        runs=[{'repeat':i,'rows':r,'date':'2026-10-13','status':'completed','run_id':str(i)} for i,r in [(1,self.rows),(2,rows2)]]
        result=export_means(self.root/'mean',self.stable,item,runs,'seq_20261013_120000_x','completed_step')
        p=json.loads(Path(result['receipts'][0]).read_text(encoding='utf-8'));self.assertEqual(p['statistics'][0]['n_valid'],2)
        self.assertAlmostEqual(p['statistics'][0]['sample_std_A'],math.sqrt(2)*1e-12);self.assertEqual(len(p['sources']),2)
    def test_no_overwrite_and_failed_publish_not_csv(self):
        a=self.export();b=self.export();self.assertFalse(set(a['files'])&set(b['files']))
        with patch('keithley_lab.wide_export.atomic_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.export()
        self.assertEqual(len(list((self.root/'flat').glob('*.csv'))),4)

if __name__=='__main__':unittest.main()
