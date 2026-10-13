"""Conservative read-only check; not a replacement for analyzer metadata review."""
import math
import re
from pathlib import Path
from .metadata import read_document, yaml


def json_safe(value):
    if isinstance(value,dict):return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value,list):return [json_safe(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if value is None or isinstance(value,(str,bool,int,float)):return value
    return str(value)


def confirmed_light(settings):
    item=settings['illumination']
    value=item['description'].strip().lower()
    return value if item['status']=='confirmed' and value in ('light','dark') else None


def timing_token(settings):
    # The unchanged reader's regex does not support exponents.
    from decimal import Decimal
    return 'delay'+format(Decimal(str(settings['point_delay_s'])), 'f')+'s'


def audit_metadata(destination, settings, filename):
    """Check every ancestor because the eventual analyzer metadata root is unknown.

    A more local override may make a reported ancestor harmless. We deliberately
    require review instead of guessing that user's future analyzer configuration.
    Notes are read only. No analyzer state, override, or note is written.
    """
    folder=Path(destination).absolute()
    blockers=[];notes=[]
    temp=settings['temperature'];current=None
    if temp['status']=='confirmed':
        try:
            current=float(temp['description'])
            if not math.isfinite(current):current=None
        except (ValueError,TypeError):pass
    for ancestor in [folder]+list(folder.parents):
        note=ancestor/'device.md'
        try:
            # lstat distinguishes inaccessible paths from absence.
            try:note.lstat()
            except FileNotFoundError:continue
            if note.is_symlink():raise ValueError('symlink metadata requires review')
            data,receipt,_=read_document(note);notes.append(receipt)
            conditions=data.get('run_conditions',{})
            if not isinstance(conditions,dict):raise ValueError('run_conditions must be a mapping')
            inherited=conditions.get('temperature')
            if inherited is not None:
                if not isinstance(inherited,dict):raise ValueError('temperature must be a value record')
                value=inherited.get('value')
                if value is not None:
                    try:equal=not isinstance(value,bool) and math.isfinite(float(value)) and current is not None and float(value)==current and inherited.get('unit') in ('degC','C','°C')
                    except (ValueError,TypeError,OverflowError):equal=False
                    if not equal:blockers.append({'path':str(note),'field':'run_conditions.temperature','reason':'Inherited temperature conflicts with, or is not supported by, this run\'s confirmed numeric Celsius entry','inherited':inherited,'run_condition':temp})
            for key,value in conditions.items():
                if key in ('temperature','IdVd','IdVg') or value is None or value=={}:continue
                # Global units alone cannot introduce a wrong physical condition.
                if key=='units' and isinstance(value,dict) and value.get('voltage')=='V' and value.get('current')=='A':continue
                blockers.append({'path':str(note),'field':'run_conditions.'+key,'reason':'Shared run condition requires per-file review','inherited':value})
            for role in ('IdVd','IdVg'):
                run=conditions.get(role,{})
                if not isinstance(run,dict):raise ValueError(role+' must be a mapping')
                if run.get('file') and filename in str(run['file']):
                    blockers.append({'path':str(note),'field':'run_conditions.'+role,'reason':'Existing file-scoped declaration requires review before publication'})
        except (OSError,ValueError,TypeError,UnicodeError,RecursionError,yaml.YAMLError) as exc:
            blockers.append({'path':str(note),'field':'device.md','reason':'Cannot safely inspect metadata: '+str(exc)})
    # Legacy reader can infer lighting from a device name as well as a label.
    text=filename.replace('_',' ').replace('-',' ').casefold()
    lights=[]
    if re.search(r'\bdark\b|빛없음|암실',text):lights.append('dark')
    if re.search(r'\b(?:with\s*light|light)\b|빛있음|조명',text):lights.append('light')
    expected=confirmed_light(settings)
    if lights and lights!=[expected]:
        blockers.append({'field':'filename.illumination','reason':'Filename lighting tokens disagree with the confirmed run condition','inferred':lights,'confirmed':expected})
    delays=re.findall(r'(?i)(?:sweep[ _-]*)?delay[ _=:-]*(\d+(?:\.\d+)?)\s*s',filename)
    if any(float(x)!=settings['point_delay_s'] for x in delays):
        blockers.append({'field':'filename.delay','reason':'Ambiguous filename delay tokens','inferred':delays})
    return json_safe({'status':'review_required' if blockers else 'no_conflict_detected',
            'checked_ancestors':[str(p) for p in [folder]+list(folder.parents)],
            'notes':notes,'blockers':blockers,'checked_before_publication':True,
            'scope_limit':'Conservative ancestor snapshot only. Analyzer custom path rules, overrides, later edits and moves are not inspected. No-conflict is not complete metadata validation.',
            'unimported_conditions':['temperature','host_requested_hold_s','PC acquisition timestamps with timezone','illumination confirmation evidence','per-point timing/status provenance']})


def completion_notice(result):
    exports=[r.get('wide_export',{}) for r in result.get('runs',[])]+list(result.get('mean_exports',{}).values())
    receipts=[p for e in exports for p in e.get('receipts',[])]
    if not receipts:return ''
    blocked=any(e.get('status')=='metadata_review_required' for e in exports)
    return ('조건 검토 필요: 상위 메모 충돌로 분석 CSV 공개 보류. 원시 데이터는 보존됩니다.' if blocked else
            '조건 확인: 분석기 1.9.1은 파일별 JSON 온도·hold·확인 이력을 자동으로 읽지 않습니다.')+'\n상위 device.md의 공통 온도/조건을 그대로 적용하지 마세요. 파일별 기록: '+receipts[0]+(' (그 외 %d개)'%(len(receipts)-1) if len(receipts)>1 else '')
