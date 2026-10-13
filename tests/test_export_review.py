import json, unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_wide_export as fixture
from keithley_lab.metadata import render
from keithley_lab.export_review import audit_metadata, completion_notice

class MetadataExportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.WideTests();self.f.setUp()
        self.note=self.f.root/'device.md'

    def note_data(self,conditions):
        self.note.write_text(render({'schema_version':1,'run_conditions':conditions}),encoding='utf-8')
        return self.note.read_bytes()

    def held(self):
        r=self.f.export();self.assertEqual(r['status'],'metadata_review_required')
        self.assertFalse(r['files']);self.assertEqual(len(r['held_files']),2)
        self.assertFalse(list((self.f.root/'flat').glob('*.csv')))
        for p in r['receipts']:
            data=json.loads(Path(p).read_text(encoding='utf-8'))
            self.assertFalse(data['analysis_ready']);self.assertTrue(data['metadata_review']['blockers'])
        return r

    def test_unknown_temperature_blocks_inherited_value_without_writes(self):
        old=self.note_data({'temperature':{'value':25,'unit':'degC'}})
        self.held();self.assertEqual(self.note.read_bytes(),old)

    def test_different_confirmed_temperature_blocked(self):
        self.note_data({'temperature':{'value':25,'unit':'degC'}})
        self.f.s['temperature']={'status':'confirmed','description':'30'};self.held()

    def test_matching_confirmed_temperature_allowed(self):
        self.note_data({'temperature':{'value':25,'unit':'degC'}})
        self.f.s['temperature']={'status':'confirmed','description':'25'}
        self.assertEqual(self.f.export()['status'],'exported')

    def test_null_temperature_and_other_file_scoped_conditions_allowed(self):
        self.note_data({'temperature':{'value':None},'IdVd':{'file':'historic.csv','illumination':{'value':'light'}}})
        self.assertEqual(self.f.export()['status'],'exported')

    def test_bad_yaml_and_unreadable_note_blocked(self):
        self.note.write_text('---\nschema_version: [\n---\n',encoding='utf-8');self.held()
        self.note_data({})
        with patch('keithley_lab.export_review.read_document',side_effect=PermissionError('test denied')):self.held()

    def test_global_noise_and_units_require_review(self):
        self.note_data({'measured_noise_floor_A':1e-9});self.held()
        self.note_data({'units':{'voltage':'mV','current':'mA'}});self.held()

    def test_nonfinite_and_yaml_date_recorded_without_save_failure(self):
        for value in ('.nan','2026-10-13'):
            self.note.write_text('---\nschema_version: 1\nrun_conditions:\n  temperature:\n    value: '+value+'\n    unit: degC\n---\n',encoding='utf-8')
            self.held()

    def test_child_unknown_does_not_silently_dismiss_parent_risk(self):
        self.note_data({'temperature':{'value':25,'unit':'degC'}})
        flat=self.f.root/'flat';flat.mkdir()
        (flat/'device.md').write_text(render({'schema_version':1,'run_conditions':{'temperature':{'value':None}}}),encoding='utf-8')
        self.held()

    def test_confirmed_light_and_decimal_delay_tokens(self):
        self.f.s['illumination']={'status':'confirmed','description':'light'};self.f.s['point_delay_s']=0.000001
        r=self.f.export();self.assertTrue(all('_delay0.000001s_light.csv' in f for f in r['files']))
        p=json.loads(Path(r['receipts'][0]).read_text(encoding='utf-8'))
        self.assertEqual(p['filename_metadata']['illumination'],'light')
        self.assertEqual(p['settings']['illumination']['status'],'confirmed')

    def test_unknown_lighting_device_name_cannot_infer_dark(self):
        self.f.stable['device_name']['value']='dark-device';self.held()

    def test_completion_notice_contains_receipt_and_blocking_reason(self):
        self.note_data({'temperature':{'value':25,'unit':'degC'}})
        r=self.held();notice=completion_notice({'runs':[{'wide_export':r}]})
        self.assertIn(r['receipts'][0],notice);self.assertIn('공개 보류',notice)

if __name__=='__main__':unittest.main()
