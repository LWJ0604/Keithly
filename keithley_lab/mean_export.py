"""Analysis-compatible derived means, isolated from raw repeat input folders."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from .engine import ROLES, atomic_json, now
from .metadata import render, run_document


def export_mean_curves(destination, staging_parent, stable, item, repeats, sequence_id, status):
    from .sequence import aggregate, write_csv, digest, normal_point, analysis_filename
    if item['settings']['workflow']=='Spot':
        return {'status':'not_exported_spot','reason':'A spot reading is not a sweep curve; raw and statistics retained.'}
    means,_,warnings=aggregate(item,repeats)
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(staging_parent)/('.mean_staging_'+uuid.uuid4().hex)
    stage.mkdir(parents=True,exist_ok=False)
    created=now();settings=item['settings'];simulation=settings['mode']=='simulation'
    by_point={(row['point_index'],row['role']):row for row in means}
    groups={}
    for index,point in enumerate(item['plan'],1):groups.setdefault((point['curve'],point['branch']),[]).append((index,point))
    contributors=[];date_values=[]
    for repeat in repeats:
        raw=Path(repeat['path'])/'points.csv' if repeat.get('path') else None
        timestamps=[r['pc_point_start'] for r in repeat['rows'] if r.get('pc_point_start')]
        dates=[t[:10] for t in timestamps]
        if repeat.get('date'):dates.append(repeat['date'])
        date_values.extend(dates)
        contributors.append({'repeat':repeat['repeat'],'run_id':repeat.get('run_id'),
                             'raw_points_path':str(raw) if raw else None,
                             'raw_points_sha256':hashlib.sha256(raw.read_bytes()).hexdigest() if raw and raw.is_file() else None,
                             'pc_first_point':min(timestamps) if timestamps else None,'pc_last_point':max(timestamps) if timestamps else None,
                             'status':repeat.get('status'),'attempted_points':len(repeat['rows'])})
    dates=sorted(set(date_values));files=[]
    for (curve,branch),grid in groups.items():
        name='curve%03d_%s'%(curve,branch);folder=stage/name;folder.mkdir()
        final_folder=destination/name
        filename=analysis_filename(stable,settings['workflow'],aggregate=True)
        rows=[];point_provenance=[]
        for index,point in grid:
            stats={r:by_point[(index,r)] for r in ROLES};drain=stats['drain']
            row={'DrainV (V)':point['drain'],'GateV (V)':point['gate'],'DrainI (A)':drain['mean_A'],
                 'GateI (A)':stats['gate']['mean_A'],'data_kind':'aggregate_mean_not_raw',
                 'simulation':simulation,'sequence_id':sequence_id,'step':item['step'],'curve':curve,'branch':branch,
                 'planned_point_index':index,'source_programmed_V':point['source'],'voltage_origin':'programmed_not_measured',
                 'aggregate_status':'valid_mean' if drain['n_valid'] else 'excluded_no_valid_repeats',
                 'aggregation_created_at':created,'n_requested':item['repeats']}
            accepted=[];excluded=[]
            for repeat in repeats:
                source=next((r for r in repeat['rows'] if r['point_index']==index),None)
                if source and normal_point(source):
                    accepted.append({'repeat':repeat['repeat'],'run_id':repeat.get('run_id'),'source_point_index':index})
                else:excluded.append({'repeat':repeat['repeat'],'run_id':repeat.get('run_id'),'reason':('non_normal_or_nonfinite_reading: '+str(source.get('point_status'))) if source else 'missing_point'})
            for role in ROLES:
                st=stats[role]
                for key in ('sample_std_A','n_valid','n_invalid','n_missing'):
                    row[role+'_'+key]=st[key]
            rows.append(row)
            point_provenance.append({'planned_point_index':index,'planned_voltages':{r:point[r] for r in ROLES},
                                     'accepted_repeats':accepted,'excluded_repeats':excluded,
                                     'not_attempted_repeats':[i for i in range(1,item['repeats']+1) if i not in {x['repeat'] for x in repeats}],
                                     'statistics':stats})
        write_csv(folder/filename,list(rows[0]),rows)
        linked=[]
        for contributor in contributors:
            entry=dict(contributor)
            entry['raw_points_relative_path']=os.path.relpath(entry['raw_points_path'],str(final_folder)) if entry['raw_points_path'] else None
            linked.append(entry)
        provenance={'schema_version':1,'data_kind':'aggregate_mean_not_raw','simulation':simulation,'sequence_id':sequence_id,
                    'step':item['step'],'curve':curve,'branch':branch,'queue_status':status,'created_at':created,
                    'requested_repeats':item['repeats'],'attempted_repeats':len(repeats),'source_dates':dates,
                    'plan_sha256':digest(item['plan']),'mapping':settings['mapping'],'contributors':linked,'points':point_provenance,
                    'warnings':warnings,'analysis_ready':any(r['drain_n_valid']>0 for r in rows),
                    'method':'Arithmetic mean of normal readings at the exact planned grid, same step/mapping/curve/branch. Sample std uses n-1; null for n<2. No interpolation. Excluded or missing readings never become zero.',
                    'csv_sha256':hashlib.sha256((folder/filename).read_bytes()).hexdigest()}
        atomic_json(folder/'aggregate_provenance.json',provenance)
        doc=run_document(stable,settings,dates[0] if dates else None,filename)
        role='IdVg' if settings['workflow']=='Id-Vg' else 'IdVd'
        doc['run_conditions'][role]['measurement_date'].update(source='First contributing acquisition date, not aggregation creation date; complete date range in aggregate_provenance.json',verification='derived')
        body=('# DERIVED AGGREGATE MEAN — NOT A RAW ACQUISITION\n\n'+
              ('SIMULATION: synthetic currents, not instrument data.\n\n' if simulation else '')+
              'This file is a mean curve across separate repeated sweeps. Do not combine it with those raw repeats in one analysis Inbox.\n'
              'Requested per-sweep timing and manual conditions are inherited from the reviewed step; no single aggregate acquisition time exists.\n'
              'See aggregate_provenance.json for contributing run IDs, relative raw-file links, hashes, per-point accepted/excluded repeats, counts and sample standard deviations.\n'
              'Programmed voltages are not measured voltages. Zero-valid grid positions remain present with blank currents, never interpolated.\n')
        (folder/'device.md').write_text(render(doc,body),encoding='utf-8')
        files.append(str(final_folder/filename))
    atomic_json(stage/'aggregate_index.json',{'schema_version':1,'data_kind':'aggregate_mean_not_raw','simulation':simulation,
                                           'sequence_id':sequence_id,'step':item['step'],'status':status,'files':files,'created_at':created})
    # Stage under records, outside the analysis Inbox. Publish a complete directory once.
    os.rename(str(stage),str(destination))
    return {'status':'exported','folder':str(destination),'files':files,'data_kind':'aggregate_mean_not_raw'}
