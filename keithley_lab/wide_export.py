"""Flat indexed wide CSV supported by the unchanged analyzer 1.9.1.

Only exact planned positions are aligned. No sorting of acquisitions, interpolation,
or merging of branch/repeat/family visits. Companion JSON is not an analysis input.
"""
import collections
import copy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from . import __version__
from .engine import ROLES, atomic_json, now
from .metadata import safe_name
from .export_review import audit_metadata, confirmed_light, timing_token


def number(value):
    return format(float(value), '.12g')


def family_label(settings):
    values=settings['family_V'] if settings['workflow']!='Spot' else [settings['fixed_V']['gate']]
    prefix='Vd' if settings['workflow']=='Id-Vg' else 'Vg'
    if len(values)>1:
        step=values[1]-values[0]
        if step and all(math.isclose(b-a,step,rel_tol=1e-10,abs_tol=1e-12) for a,b in zip(values,values[1:])):
            return prefix+number(values[0])+'to'+number(values[-1])+'_step'+number(step)+'V'
    text=prefix+'['+','.join(number(v) for v in values)+']V'
    return text if len(text)<70 else prefix+'_'+str(len(values))+'values_see_provenance'


def valid(row):
    return row and row.get('point_status')=='normal' and all(row.get(r+'_status')=='normal' and isinstance(row.get(r+'_current_A'),(float,int)) and math.isfinite(row[r+'_current_A']) for r in ROLES)


def load_saved_rows(path):
    """Read existing point CSV without changing it; retain original raw/status strings."""
    with Path(path).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    for row in rows:
        for key,value in list(row.items()):
            if value=='':row[key]=None
            elif key in ('schema_version','point_index','curve','SP') or key.endswith('_SMU'):row[key]=int(value)
            elif key.endswith(('_V','_A','_s')):row[key]=float(value)
            elif key=='simulation':row[key]=value=='True'
    return rows


def validate_rows(plan, rows, settings):
    found={};previous=0
    for row in rows:
        index=row['point_index']
        if type(index)!=int or index<=previous or not 1<=index<=len(plan):
            raise ValueError('Wide export refuses duplicate/out-of-order/out-of-plan point indices')
        previous=index;p=plan[index-1]
        if row['curve']!=p['curve'] or row['branch']!=p['branch'] or any(row[r+'_SMU']!=settings['mapping'][r] or row[r+'_programmed_V']!=p[r] for r in ROLES):
            raise ValueError('Wide export refuses mismatched planned grid, branch, mapping or bias')
        found[index]=row
    return found


def write_wide(destination, stable, settings, plan, rows, source_runs, sequence_id,
               step, repeat, date, run_status, data_kind='raw', statistics_rows=None):
    """Publish a CSV + receipt per branch, directly inside destination.

    Raw records remain in their original immutable run folders. A failed export
    cannot alter them. Files use new UUIDs and exclusive temporary creation.
    """
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    mapped=validate_rows(plan,rows,settings)
    groups=collections.OrderedDict()
    axis='gate' if settings['workflow']=='Id-Vg' else 'drain'
    fixed='drain' if axis=='gate' else 'gate'
    for index,p in enumerate(plan,1):groups.setdefault(p['branch'],collections.OrderedDict()).setdefault(p['curve'],[]).append((index,p))
    receipts=[];files=[];held=[]
    sources=[]
    for run in source_runs:
        entry={k:v for k,v in run.items() if k!='rows'}
        raw=Path(run['path'])/'points.csv' if run.get('path') else None
        entry.update(raw_points_path=str(raw) if raw else None,raw_points_sha256=hashlib.sha256(raw.read_bytes()).hexdigest() if raw and raw.is_file() else None)
        sources.append(entry)
    name=safe_name(stable['device_name']['value'])
    stamp=sequence_id.split('_')[2] if sequence_id.startswith('seq_') and len(sequence_id.split('_'))>2 else 'time_unknown'
    for branch,curves in groups.items():
        grid=next(iter(curves.values()))
        xvalues=[p[axis] for _,p in grid]
        for points in curves.values():
            if [p[axis] for _,p in points]!=xvalues:
                raise ValueError('Wide export refuses unequal family grids; raw points remain preserved')
        # Do not hand the legacy reader an entirely empty trace (it rejects it).
        included=[curve for curve,points in curves.items() if any(valid(mapped.get(i)) for i,_ in points)]
        omitted=[curve for curve in curves if curve not in included]
        qc=[]
        for curve,points in curves.items():
            for position,(i,p) in enumerate(points,1):
                row=mapped.get(i)
                qc.append({'point_index':i,'family_index':curve,'branch':branch,'grid_position':position,
                           'planned_voltage_V':p[axis],'fixed_bias_V':p[fixed],
                           'status':row.get('point_status') if row else 'missing_not_acquired',
                           'valid_for_analysis':bool(valid(row)),
                           'pc_time':row.get('pc_point_start') if row else None,
                           'raw_statuses':{r:row.get(r+'_status') for r in ROLES} if row else {},
                           'excluded_raw_currents_A':{r:(str(row.get(r+'_current_A')) if isinstance(row.get(r+'_current_A'),float) and not math.isfinite(row[r+'_current_A']) else row.get(r+'_current_A')) for r in ROLES} if row and not valid(row) else {}})
        partial=run_status!='completed' or any(not q['valid_for_analysis'] for q in qc)
        tag='AGGREGATE_MEAN' if data_kind!='raw' else 'RAW'
        filename='device=%s_%s_%s_%s_%s_s%02d_%s_%s%s_%s'%(name,settings['workflow'],family_label(settings),tag,stamp,step,
            'mean' if repeat is None else 'repeat%03d'%repeat,branch,'_PARTIAL' if partial else '',uuid.uuid4().hex[:8])
        filename+='_'+timing_token(settings)
        if confirmed_light(settings):filename+='_'+confirmed_light(settings)
        receipt=destination/(filename+'.provenance.json');path=destination/(filename+'.csv')
        review=audit_metadata(destination,settings,path.name)
        blocked=bool(review['blockers'])
        if blocked:path=destination/(filename+'.csv.review_required')
        provenance={'schema_version':1,'exporter_version':__version__,'data_kind':data_kind,'simulation':settings['mode']=='simulation',
           'sequence_id':sequence_id,'step':step,'repeat':repeat,'branch':branch,'acquisition_date':date,'created_at':now(),
           'run_status':run_status,'partial':partial,'analysis_ready':bool(included) and not blocked,'planned_families':list(curves),
           'metadata_review':review,'data_file':str(path) if included else None,
           'filename_metadata':{'delay_s':settings['point_delay_s'],'delay_origin':'host requested wait before each point; not measured total interval or instrument SweepDelay','illumination':confirmed_light(settings),'reader_status':'inferred; confirmation evidence remains in settings'},
           'included_family_indices':included,'omitted_no_valid_data_family_indices':omitted,
           'settings':copy.deepcopy(settings),'stable_metadata':copy.deepcopy(stable),'sources':sources,'point_qc':qc,
           'statistics':statistics_rows or [],'csv_columns':[],
           'units':{'voltage':'V','current':'A','voltage_origin':'programmed_setpoints_not_measured'},
           'reader_compatibility':'Indexed channels for analyzer 1.9.1; descriptive row then machine header. Blank currents are excluded; omitted empty families retained in this QC receipt.',
           'metadata_limit':'Analyzer 1.9.1 does not consume this JSON. Filename delay and confirmed light/dark are inferred only. Temperature, host hold, PC time and confirmation evidence require per-file review. Shared ancestor device.md can apply wrong run conditions; never copy mixed per-file temperatures into common metadata.',
           'raw_location_policy':'Full source/gate/drain currents and statuses remain in source points.csv; CSV also includes gate/source current columns.'}
        if not included:
            atomic_json(receipt,provenance);receipts.append(str(receipt));continue
        machine=[('GateV' if axis=='gate' else 'DrainV')+' (V)']
        human=[('Gate' if axis=='gate' else 'Drain')+' bias [V] - programmed']
        column_specs=[]
        for role,canonical,unit in [('drain','DrainI','A'),(fixed,'DrainV' if fixed=='drain' else 'GateV','V'),('gate','GateI','A'),('source','SourceI','A')]:
            is_voltage=unit=='V'
            for curve in included:
                p=curves[curve][0][1];label=('Vd' if fixed=='drain' else 'Vg')+'='+number(p[fixed])+' V'
                machine.append('%s (%s) (%d)'%(canonical,unit,curve))
                human.append(('%s at %s [%s] family %d'%(canonical,label,unit,curve)))
                column_specs.append((curve,role,is_voltage))
        machine+=['source_programmed_V','data_kind','simulation','branch','repeat','acquisition_date','row_quality']
        human+=['Source setpoint [V]','Data kind','SIMULATION - synthetic' if settings['mode']=='simulation' else 'REAL acquisition','Branch','Repeat','Acquisition date','Quality - see JSON']
        provenance['csv_columns']=[{'header':c,'description':h} for c,h in zip(machine,human)]
        temp=path.with_suffix('.csv.tmp')
        try:
            with temp.open('x',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f)
                # This row deliberately avoids recognized measurement aliases.
                writer.writerow(human);writer.writerow(machine)
                for position,x in enumerate(xvalues):
                    values=[x];qualities=[]
                    for curve,role,is_voltage in column_specs:
                        i,p=curves[curve][position];row=mapped.get(i)
                        values.append(p[role] if is_voltage else row[role+'_current_A'] if valid(row) else None)
                        qualities.append('normal' if valid(row) else row.get('point_status','invalid') if row else 'missing')
                    values += [settings['fixed_V']['source'],data_kind,settings['mode']=='simulation',branch,repeat,date,'normal' if all(q=='normal' for q in qualities) else 'partial_see_provenance']
                    writer.writerow(values)
                f.flush();os.fsync(f.fileno())
            provenance['csv_sha256']=hashlib.sha256(temp.read_bytes()).hexdigest()
            atomic_json(receipt,provenance)
            # UUID + explicit existence check; Windows rename never replaces an existing destination.
            if path.exists():raise FileExistsError(str(path))
            os.rename(str(temp),str(path))
        except Exception:
            # A .tmp is never an analyzer input. Retain it for diagnosis.
            raise
        (held if blocked else files).append(str(path));receipts.append(str(receipt))
    return {'status':'metadata_review_required' if held else 'exported' if files else 'qc_only_no_valid_measurements','files':files,'held_files':held,'receipts':receipts,
            'folder':str(destination),'data_kind':data_kind}


def export_repeat(destination, stable, item, result, sequence_id):
    return write_wide(destination,stable,item['settings'],item['plan'],result['rows'],[result],sequence_id,item['step'],result['repeat'],result['date'],result['status'])


def export_means(destination, stable, item, results, sequence_id, state):
    if item['settings']['workflow']=='Spot':return {'status':'not_exported_spot','files':[],'reason':'Spot is not a sweep curve'}
    from .sequence import aggregate
    means,_,warnings=aggregate(item,results);lookup={(s['point_index'],s['role']):s for s in means}
    rows=[]
    for i,p in enumerate(item['plan'],1):
        good=all(lookup[i,r]['n_valid'] for r in ROLES)
        row={'point_index':i,'curve':p['curve'],'branch':p['branch'],'point_status':'normal' if good else 'no_valid_repeats','pc_point_start':None}
        for role in ROLES:row.update({role+'_programmed_V':p[role],role+'_SMU':item['settings']['mapping'][role],role+'_status':'normal' if good else 'missing',role+'_current_A':lookup[i,role]['mean_A']})
        rows.append(row)
    complete=len(results)==item['repeats'] and all(r['status']=='completed' for r in results) and state=='completed_step'
    result=write_wide(destination,stable,item['settings'],item['plan'],rows,results,sequence_id,item['step'],None,results[0]['date'],
                      'completed' if complete else 'partial_'+state,'aggregate_mean_not_raw',means)
    result['warnings']=warnings
    return result
