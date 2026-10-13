"""Workflow-specific canonical plans and portable queue drafts; no equipment I/O."""
import copy
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path


def canonicalize(settings):
    s=copy.deepcopy(settings)
    if s.get('workflow')=='Spot':
        s.update(family_V=[],start_V=0.,stop_V=0.,step_V=0.,direction='ascending',return_sweep=False)
    elif s.get('workflow') in ('Id-Vd','Id-Vg'):
        s['fixed_V']=dict(s['fixed_V'],drain=0.,gate=0.)
    return s


def bias_key(value):
    return format(value if value else 0.,'.12g')


def validate_family(values):
    if not isinstance(values,list) or not 1<=len(values)<=100:raise ValueError('고정 바이어스 목록은 1–100개여야 합니다.')
    seen=set()
    for value in values:
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):raise ValueError('고정 바이어스는 유한한 숫자여야 합니다.')
        key=bias_key(value)
        if key in seen:raise ValueError('중복 고정 바이어스 %s V: 삭제하거나 전체 반복 횟수를 사용하세요. 목록은 자동 정렬/중복 제거하지 않습니다.'%key)
        seen.add(key)
    return values


def parse_family(text):
    try:values=[float(part.strip()) for part in text.split(',')]
    except (ValueError,TypeError):raise ValueError('고정 바이어스는 쉼표로 구분한 숫자 목록입니다. 빈 항목은 허용하지 않습니다.')
    return validate_family(values)


def generate_family(start,stop,step):
    try:a,b,h=[Decimal(str(x).strip()) for x in (start,stop,step)]
    except InvalidOperation:raise ValueError('범위의 시작/끝/간격은 숫자여야 합니다.')
    if not all(x.is_finite() for x in (a,b,h)):raise ValueError('범위에 NaN/무한대를 사용할 수 없습니다.')
    if h==0:raise ValueError('범위 간격은 0일 수 없습니다.')
    if a==b:return validate_family([float(a)])
    n=(b-a)/h
    if n<=0:raise ValueError('간격 부호가 시작→끝 방향과 다릅니다.')
    if n!=n.to_integral_value():raise ValueError('간격이 끝-시작을 정확히 나누어야 합니다. 끝값을 임의 변경하지 않습니다.')
    if n>99:raise ValueError('범위 생성은 고정 바이어스 최대 100개입니다.')
    return validate_family([float(a+i*h) for i in range(int(n)+1)])


def describe_plan(settings,repeats,full=True):
    from .engine import validate
    s,plan=validate(settings)
    if type(repeats) is not int or not 1<=repeats<=100:raise ValueError('전체 스윕 반복은 1–100입니다.')
    total=len(plan)*repeats
    if total>100000:raise ValueError('반복 포함 단계가 큐 상한 100,000점을 초과합니다.')
    if s['workflow']=='Spot':
        return ['Spot: Vd=%g V, Vg=%g V, Vs=%g V'%(s['fixed_V']['drain'],s['fixed_V']['gate'],s['fixed_V']['source']),
                '1 point × %d repeats = %d points (전류 읽기 %d회)'%(repeats,total,total*3),
                '반복 1 → %d; 각 점에서 TI drain → source → gate.'%repeats]
    axis,fixed=('drain','gate') if s['workflow']=='Id-Vd' else ('gate','drain')
    lines=['%s: %d families · %d points × %d repeats = %d points (전류 읽기 %d회)'%(s['workflow'],len(s['family_V']),len(plan),repeats,total,total*3),
           '순서: 반복 1 → %d; 각 반복에서 family 입력 순서 → forward → reverse. Vs=%g V.'%(repeats,s['fixed_V']['source'])]
    for curve,bias in enumerate(s['family_V'],1):
        points=[p for p in plan if p['curve']==curve]
        counts={branch:sum(p['branch']==branch for p in points) for branch in ('forward','reverse')}
        lines.append('Family %d: 고정 %s=%g V · forward %d + reverse %d = %d points/반복 · 전체 %d'%(curve,'Vg' if fixed=='gate' else 'Vd',bias,counts['forward'],counts['reverse'],len(points),len(points)*repeats))
        if full:
            for branch in ('forward','reverse'):
                values=[p[axis] for p in points if p['branch']==branch]
                if values:lines.append('  %s %s: '%(branch,'Vd' if axis=='drain' else 'Vg')+' → '.join('%.12g'%x for x in values)+' V')
    return lines


def save_queue_file(path,items):
    from .engine import validate
    if not 1<=len(items)<=50:raise ValueError('저장 큐는 1–50 단계여야 합니다.')
    clean=[];total=0;sweeps=0
    for item in items:
        s,plan=validate(item['settings']);n=item['repeats'];describe_plan(s,n,False)
        clean.append({'settings':s,'repeats':n});total+=len(plan)*n;sweeps+=n
    if total>100000 or sweeps>1000:raise ValueError('큐 전체 점수/스윕 상한 초과')
    payload={'queue_schema_version':1,'kind':'draft_not_execution_authorization','items':clean}
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(payload,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())


def load_queue_file(path,mode):
    from .engine import validate
    p=Path(path)
    if p.stat().st_size>2*1024*1024:raise ValueError('큐 파일은 최대 2 MiB입니다.')
    raw=json.loads(p.read_text(encoding='utf-8-sig'))
    if raw.get('queue_schema_version')!=1 or raw.get('kind')!='draft_not_execution_authorization':raise ValueError('지원하지 않는 큐 파일')
    items=raw.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=50:raise ValueError('큐는 1–50 단계여야 합니다.')
    result=[];total=0;sweeps=0
    for item in items:
        s=copy.deepcopy(item['settings']);s['mode']=mode  # Loading cannot silently change the selected connection mode.
        s,plan=validate(s);n=item['repeats'];describe_plan(s,n,False)
        result.append({'settings':s,'repeats':n});total+=len(plan)*n;sweeps+=n
    if total>100000 or sweeps>1000:raise ValueError('큐 전체 점수/스윕 상한 초과')
    return result
