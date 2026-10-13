"""Reviewed, finite queues. Each repeat is a complete, separately preserved sweep."""
import copy
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import statistics
import threading
import time
import uuid

from .engine import Controller, RunStore, ROLES, validate, estimate, now, atomic_json, app_root
from .metadata import safe_name, device_document, snapshot, render, run_document


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def review_queue(items, device, real_root, workspace, settle_s=.2):
    if not items or len(items)>50:raise ValueError('Queue requires 1..50 steps')
    if not isinstance(settle_s,(int,float)) or not math.isfinite(settle_s) or not 0<=settle_s<=3600:raise ValueError('Invalid transition settle time')
    proposed=device_document(device)
    validated=[];count=0;seconds=0.;mode=None;endpoint=None
    for index,item in enumerate(items,1):
        s,plan=validate(item['settings'])
        repeats=item['repeats']
        if type(repeats) is not int or not 1<=repeats<=100:raise ValueError('Repeat count must be 1..100')
        if mode is not None and mode!=s['mode']:raise ValueError('A queue cannot mix simulation and real')
        if endpoint is not None and endpoint!=(s['host'],s['port']):raise ValueError('Queue must use one endpoint')
        mode=s['mode'];endpoint=(s['host'],s['port'])
        count+=len(plan)*repeats;seconds+=estimate(s,plan)*repeats
        validated.append({'step':index,'settings':s,'plan':plan,'repeats':repeats})
    if count>100000:raise ValueError('Whole queue exceeds 100,000 points')
    total=sum(item['repeats'] for item in validated)
    if total>1000:raise ValueError('Whole queue exceeds 1,000 sweeps')
    root=Path(workspace)/'simulation_data' if mode=='simulation' else Path(real_root)
    # Reuse strict Windows path validation; simulation ignores any real-root selection.
    check=copy.deepcopy(validated[0]['settings']);check['save_root']=str(root);validate(check)
    device_dir=root/safe_name(device['id'])
    if mode=='simulation' and Path(workspace).resolve() not in device_dir.resolve().parents:
        raise ValueError('Simulation destination must remain inside the new app workspace')
    if device_dir.exists() and not device_dir.is_dir():raise ValueError('Device folder is a file')
    stable,receipt=snapshot(device_dir,proposed)
    for item in validated:analysis_filename(stable,item['settings']['workflow'])
    result={'schema_version':2,'items':validated,'device':copy.deepcopy(device),'stable_metadata':stable,
            'device_receipt':receipt,'root':str(root),'device_dir':str(device_dir),'mode':mode,
            'settle_s':float(settle_s),'point_count':count,'sweep_count':total,
            'minimum_seconds':seconds+max(0,total-1)*settle_s,
            'estimated_storage_bytes':count*16000+total*20000,
            'date_policy':'Each complete sweep stays under its actual PC local start date; point timestamps retain offsets. Later repeats crossing midnight use their new date. Historical files are never changed.'}
    return result


def write_csv(path, fields, rows):
    with Path(path).open('x',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        stream.flush();os.fsync(stream.fileno())


def analysis_filename(stable, workflow, aggregate=False):
    name=stable.get('device_name',{}).get('value')
    safe_name(name)
    return 'device='+name+('_AGGREGATE_MEAN_' if aggregate else '_RAW_')+workflow.replace('-','')+'.csv'


def exports(base, stable, settings, date, rows):
    """One file per curve/branch; volts are explicitly programmed, never measured."""
    groups={}
    for row in rows:groups.setdefault((row['curve'],row['branch']),[]).append(row)
    paths=[]
    for (curve,branch),points in groups.items():
        folder=Path(base)/('curve%03d_%s'%(curve,branch));folder.mkdir(parents=True,exist_ok=False)
        fields=['DrainV (V)','GateV (V)','DrainI (A)','GateI (A)','compliance_flag','point_status','pc_timestamp','source_programmed_V','voltage_origin','source_point_index','simulation','run_id']
        exported=[]
        for row in points:
            valid=row.get('point_status')=='normal'
            exported.append({'DrainV (V)':row['drain_programmed_V'],'GateV (V)':row['gate_programmed_V'],
                             'DrainI (A)':row.get('drain_current_A') if valid else None,
                             'GateI (A)':row.get('gate_current_A') if valid else None,
                             'compliance_flag':int(any(row.get(r+'_status')=='compliance' for r in ROLES)),
                             'point_status':row['point_status'],'pc_timestamp':row['pc_point_start'],
                             'source_programmed_V':row['source_programmed_V'],'voltage_origin':'programmed_not_measured',
                             'source_point_index':row['point_index'],'simulation':settings['mode']=='simulation','run_id':row.get('run_id','')})
        filename=analysis_filename(stable,settings['workflow'])
        write_csv(folder/filename,fields,exported)
        (folder/'device.md').write_text(render(run_document(stable,settings,date,filename),
            ('SIMULATION - SYNTHETIC DATA, NOT AN INSTRUMENT MEASUREMENT.\n' if settings['mode']=='simulation' else 'REAL acquisition export; inspect run status.\n')+
            'Per-run immutable snapshot. Voltages are programmed setpoints, not measured voltages.\n'
            'Unknown illumination is preserved (CSV avoids legacy XLS dark-name inference).\n'
            'Non-normal readings are blank in this import view; see raw points.csv for exact readings/statuses.\n'),encoding='utf-8')
        paths.append(str(folder/filename))
    return paths


def aggregate(item, results):
    """Align exact planned positions, roles, curves, branches and mapping; no interpolation."""
    means=[];repeat_view=[];warnings=[]
    s=item['settings'];plan=item['plan'];n_requested=item['repeats']
    aligned={}
    repeat_ids=set()
    for result in results:
        if type(result['repeat']) is not int or not 1<=result['repeat']<=n_requested or result['repeat'] in repeat_ids:
            raise ValueError('Duplicate/out-of-range repeat identifier')
        repeat_ids.add(result['repeat'])
        seen=set()
        for row in result['rows']:
            index=row['point_index']
            if index in seen or not 1<=index<=len(plan):raise ValueError('Duplicate/out-of-range repeat point index')
            seen.add(index);point=plan[index-1]
            if any(row[r+'_SMU']!=s['mapping'][r] or row[r+'_programmed_V']!=point[r] for r in ROLES) or row['curve']!=point['curve'] or row['branch']!=point['branch']:
                raise ValueError('Repeat grid/mapping/branch mismatch; averaging refused')
            aligned[(result['repeat'],index)]=row
    for index,point in enumerate(plan,1):
        for role in ROLES:
            valid=[];invalid=0;missing=0
            for repeat in range(1,n_requested+1):
                row=aligned.get((repeat,index));value=None
                if row is None:missing+=1;state='missing'
                else:
                    raw=row.get(role+'_current_A')
                    good=normal_point(row)
                    if good:value=float(raw);valid.append((repeat,value));state='normal'
                    else:invalid+=1;state=row.get('point_status','invalid')
                repeat_view.append({'simulation':s['mode']=='simulation','point_index':index,'curve':point['curve'],'branch':point['branch'],'role':role,'repeat':repeat,'current_A':value,'status':state})
            values=[v for _,v in valid]
            delta=values[-1]-values[0] if len(values)>=2 else None
            denominator=max(abs(statistics.mean(values)),1e-15) if values else None
            drift=delta/denominator if delta is not None else None
            if drift is not None and abs(drift)>.1:warnings.append('point %d / %s: first-to-last drift exceeds 10%% of |mean| (floor 1 fA); inspect repeat order'%(index,role))
            means.append({'simulation':s['mode']=='simulation','point_index':index,'curve':point['curve'],'branch':point['branch'],'role':role,
                          'SMU':s['mapping'][role],'drain_programmed_V':point['drain'],'source_programmed_V':point['source'],'gate_programmed_V':point['gate'],
                          'mean_A':statistics.mean(values) if values else None,'sample_std_A':statistics.stdev(values) if len(values)>1 else None,
                          'n_valid':len(values),'n_invalid':invalid,'n_missing':missing,'n_requested':n_requested,
                          'first_last_delta_A':delta,'relative_drift':drift})
    return means,repeat_view,warnings


def normal_point(row):
    return row.get('point_status')=='normal' and all(
        row.get(r+'_status')=='normal' and isinstance(row.get(r+'_current_A'),(int,float)) and math.isfinite(row[r+'_current_A']) for r in ROLES)


class SequenceController:
    def __init__(self, workspace=None, controller_factory=Controller, clock=None):
        self.workspace=Path(workspace or app_root());self.factory=controller_factory
        self.clock=clock or (lambda:dt.datetime.now().astimezone())
        self.events=queue.Queue();self.lock=threading.Lock();self.dispatch_lock=threading.Lock();self.cancel=threading.Event()
        self.active=False;self.child=None;self.thread=None;self.last_result=None

    def start(self, reviewed, approval):
        # Validate the entire immutable payload again, before even opening a session.
        fresh=review_queue([{'settings':x['settings'],'repeats':x['repeats']} for x in reviewed['items']],
                           reviewed['device'],reviewed['root'],self.workspace,reviewed['settle_s'])
        # Stable snapshot and receipt may be stale because a human changed device.md.
        if digest(fresh)!=digest(reviewed):raise ValueError('Preflight changed or device.md changed; review again')
        if not approval or approval.get('queue_hash')!=digest(reviewed) or not approval.get('confirmed_at'):
            raise ValueError('Explicit current queue preflight required')
        if not self.lock.acquire(False):raise RuntimeError('A queue is already active')
        self.active=True;self.cancel.clear();self.last_result=None
        self.thread=threading.Thread(target=self._run,args=(copy.deepcopy(reviewed),copy.deepcopy(approval)),daemon=False);self.thread.start()

    def stop(self):
        with self.dispatch_lock:
            self.cancel.set()
            if self.child:self.child.stop()

    def _run(self, reviewed, approval):
        started=self.clock();seq='seq_'+started.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:10]
        sequence_dir=Path(reviewed['device_dir'])/started.date().isoformat()/'기록'/seq
        result={'status':'error','error':'','path':str(sequence_dir),'cleanup':{'status':'not_required_no_commands'},'rows_in_memory':[],
                'sequence_id':seq,'runs':[],'warnings':[]}
        meta={'schema_version':2,'sequence_id':seq,'status':'prepared','created_at':started.isoformat(),'preflight':approval,'review':reviewed,'runs':[],'events':[]}
        completed=0;child_result=None
        attempted={};mean_exports={};mean_failures=set()
        def finish_means(item,step_results,state):
            from .wide_export import export_means
            target=Path(reviewed['device_dir'])/step_results[0]['date']/'평균_분석입력'
            mean_exports[str(item['step'])]=export_means(target,reviewed['stable_metadata'],item,step_results,seq,state)
            meta['mean_exports']=mean_exports;result['mean_exports']=mean_exports
            atomic_json(sequence_dir/'sequence.json',meta)
        def event(kind,**details):
            meta['events'].append(dict(details,kind=kind,pc_time=now()))
            atomic_json(sequence_dir/'sequence.json',meta)
        try:
            sequence_dir.mkdir(parents=True,exist_ok=False)
            event('queue_started');meta['status']='running'
            for item in reviewed['items']:
                step_results=[]
                attempted[item['step']]=step_results
                for repeat in range(1,item['repeats']+1):
                    if self.cancel.is_set():break
                    current=self.clock();date=current.date().isoformat()
                    run_id='%s_step%02d_repeat%03d_%s'%(seq,item['step'],repeat,uuid.uuid4().hex[:8])
                    raw_dir=Path(reviewed['device_dir'])/date/'기록'/seq/run_id
                    export_dir=Path(reviewed['device_dir'])/date/'원본'
                    s=copy.deepcopy(item['settings']);s['save_root']=reviewed['root']
                    def factory(settings,plan):
                        store=RunStore(settings,plan,path=raw_dir,run_id=run_id)
                        store.meta.update(sequence_id=seq,step=item['step'],repeat=repeat,acquisition_start_date=date,
                                          stable_metadata=reviewed['stable_metadata'],device_receipt=reviewed['device_receipt'],date_policy=reviewed['date_policy'])
                        return store
                    self.child=self.factory(store_factory=factory)
                    event('sweep_started',step=item['step'],repeat=repeat,run_id=run_id)
                    self.events.put(('phase','Step %d/%d · repeat %d/%d · %s'%(item['step'],len(reviewed['items']),repeat,item['repeats'],s['workflow'])))
                    child_approval={'settings_hash':digest(s),'confirmed_at':approval['confirmed_at'],'parent_queue_hash':approval['queue_hash']}
                    with self.dispatch_lock:
                        if self.cancel.is_set():break
                        self.child.start(s,child_approval)
                    while self.child.active:
                        if self.cancel.is_set():self.child.stop()
                        try:
                            kind,data=self.child.events.get(timeout=.02)
                            if kind=='point':
                                row=dict(data,sequence_step=item['step'],repeat=repeat,queue_point=completed+data['point_index'])
                                self.events.put(('point',row))
                            elif kind in ('identity','phase'):self.events.put((kind,data))
                        except queue.Empty:pass
                    self.child.thread.join()
                    # Drain the final point event if worker finished before the consumer woke.
                    while not self.child.events.empty():
                        kind,data=self.child.events.get()
                        if kind=='point':self.events.put(('point',dict(data,sequence_step=item['step'],repeat=repeat,queue_point=completed+data['point_index'])))
                    child_result=self.child.last_result;result['cleanup']=child_result['cleanup']
                    rows=child_result['rows_in_memory'];result['rows_in_memory'].extend(rows)
                    completed+=len(rows)
                    record={k:v for k,v in child_result.items() if k!='rows_in_memory'}
                    record.update(step=item['step'],repeat=repeat,run_id=run_id,acquisition_start_date=date,
                                  point_count=len(rows),
                                  point_dates=sorted(set(r['pc_point_start'][:10] for r in rows)),export_paths=[])
                    meta['runs'].append(record);result['runs'].append(record)
                    step_results.append({'repeat':repeat,'rows':rows,'run_id':run_id,'path':str(raw_dir),'date':date,'status':child_result['status']})
                    event('sweep_finished',step=item['step'],repeat=repeat,status=child_result['status'],cleanup=child_result['cleanup'])
                    from .wide_export import export_repeat
                    flat=export_repeat(export_dir,reviewed['stable_metadata'],item,step_results[-1],seq)
                    record['export_paths']=flat['files'];record['wide_export']=flat
                    means,repeat_view,warnings=aggregate(item,step_results)
                    # Atomic aggregate snapshots; raw sweeps remain immutable and independently usable.
                    atomic_json(sequence_dir/('step%02d_statistics.json'%item['step']),{'means':means,'repeat_view':repeat_view,'warnings':warnings,'completed_repeats':repeat})
                    result['warnings']=warnings
                    event('exports_saved',step=item['step'],repeat=repeat)
                    if child_result['status']!='completed' or child_result.get('save_error') or child_result.get('close_error') or 'UNKNOWN' in child_result['cleanup']['status']:
                        result['status']=child_result['status'] if child_result['status']!='completed' else 'error'
                        result['error']=child_result.get('error') or child_result.get('save_error') or child_result.get('close_error') or 'Cleanup uncertain'
                        raise QueueHalt()
                    is_last=item['step']==len(reviewed['items']) and repeat==item['repeats']
                    if not is_last:
                        # Child has closed its socket after zero/stop ACK; never reconnect after failure.
                        event('transition_zero_stop_ack_settle',requested_s=reviewed['settle_s'])
                        t=time.monotonic();self.events.put(('phase','전환: zero / stop ACK 후 %.3g s 대기'%reviewed['settle_s']))
                        self.cancel.wait(reviewed['settle_s'])
                        event('transition_finished',elapsed_s=time.monotonic()-t,cancelled=self.cancel.is_set())
                if self.cancel.is_set():break
                if step_results:
                    means,repeat_view,warnings=aggregate(item,step_results)
                    write_csv(sequence_dir/('step%02d_mean.csv'%item['step']),list(means[0]),means)
                    write_csv(sequence_dir/('step%02d_repeat_view.csv'%item['step']),list(repeat_view[0]),repeat_view)
                    try:finish_means(item,step_results,'completed_step')
                    except Exception:
                        mean_failures.add(item['step']);raise
            result['status']='cancelled' if self.cancel.is_set() else 'completed'
        except QueueHalt:pass
        except Exception as exc:
            result.update(status='error',error='%s: %s'%(type(exc).__name__,exc))
        finally:
            self.child=None
            # Partial/cancelled/error means remain explicitly partial and separately labeled.
            # Never retry a failed aggregate write or continue the queue after it.
            for item in reviewed['items']:
                step_results=attempted.get(item['step'],[])
                if step_results and str(item['step']) not in mean_exports and item['step'] not in mean_failures:
                    try:finish_means(item,step_results,result['status'])
                    except Exception as exc:result.update(status='error',save_error='Mean export: '+str(exc))
            meta.update(status=result['status'],error=result['error'],finished_at=now(),cleanup=result['cleanup'])
            if result.get('save_error'):meta['save_error']=result['save_error']
            try:
                atomic_json(sequence_dir/'sequence.json',meta)
                (sequence_dir/'summary.md').write_text('# '+seq+'\n\nMode: '+reviewed['mode']+'\n\nStatus: '+result['status']+'\n\nError: '+result['error']+'\n\n'+reviewed['date_policy']+'\n\nOutput OFF physically verified: NO.\n',encoding='utf-8')
            except Exception as exc:
                result.update(status='error',save_error=str(exc))
                try:
                    fallback=self.workspace/'recovery';fallback.mkdir(exist_ok=True)
                    file=fallback/(seq+'_sequence_recovery.json');atomic_json(file,{'metadata':meta,'result':result});result['recovery_path']=str(file)
                except Exception as recovery:result['recovery_error']=str(recovery)
            self.last_result=result;self.active=False;self.lock.release();self.events.put(('done',result))


class QueueHalt(Exception):
    pass
