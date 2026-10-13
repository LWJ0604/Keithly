"""Non-destructive device metadata compatible with local research-automation 1.9.1."""
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

# PyYAML is installed from requirements.txt; no local research-tool dependency.
import yaml

from .engine import now


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    out = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in out:
            raise ValueError('Duplicate metadata key: ' + str(key))
        out[key] = loader.construct_object(value_node, deep=deep)
    return out


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def safe_name(name):
    if not isinstance(name, str):
        raise ValueError('Device ID는 문자열로 입력하세요.')
    if not name.strip():
        raise ValueError('Device ID가 비어 있습니다. 소자 탭에서 이름을 입력하세요 (1~80자).')
    if name != name.strip():
        raise ValueError('Device ID 앞뒤 공백을 제거하세요. 입력값은 자동 변경하지 않습니다.')
    if len(name) > 80:
        raise ValueError('Device ID는 80자 이하여야 합니다 (현재 %d자).' % len(name))
    if name in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith('.'):
        raise ValueError('Device ID에 Windows 파일명 금지 문자 또는 끝 마침표를 사용할 수 없습니다.')
    if name.split('.')[0].upper() in ['CON','PRN','AUX','NUL']+['COM%d'%i for i in range(1,10)]+['LPT%d'%i for i in range(1,10)]:
        raise ValueError('Device ID에 Windows 예약 이름(CON, NUL, COM1 등)을 사용할 수 없습니다.')
    return name


def field(value=None, unit=None, confirmed=False, source='Not supplied'):
    confirmed = confirmed and value is not None
    result = {'value': value, 'source': source, 'verification': 'user_confirmed' if confirmed else 'unconfirmed'}
    if unit is not None: result['unit'] = unit
    return result


def device_document(device):
    name = safe_name(device['id'])
    for key in ('L_um','W_um','oxide_nm'):
        value = device.get(key)
        if value is not None and (isinstance(value,bool) or not math.isfinite(value) or value <= 0):
            raise ValueError(key+' must be positive, finite, or unknown')
    confirmed = bool(device.get('confirmed'))
    src = 'Explicit local device metadata review' if confirmed else 'Unconfirmed local entry'
    pair = device.get('pair', 'custom')
    safe_name(pair)
    return {'schema_version': 1, 'device_name': field(name, confirmed=confirmed, source=src),
            'active_electrode_pair': field(pair, confirmed=confirmed, source=src),
            'structure': {'material': field(device.get('material') or None, confirmed=False),
                          'gate_geometry': field('Si back gate' if device.get('preset') else None, confirmed=False),
                          'thickness': field(None, 'nm'),
                          'gate_dielectric': {'material': field(device.get('oxide') or None,confirmed=confirmed,source=src),
                                             'thickness': field(device.get('oxide_nm'), 'nm', confirmed, src),
                                             'relative_permittivity': field(None)},
                          'electrodes': {'metal': 'Au' if device.get('metal')=='Au only' else device.get('metal') or 'unknown',
                                         'adhesion_layer': 'none' if device.get('metal') == 'Au only' else 'unknown',
                                         'source': src, 'verification': 'user_confirmed' if confirmed else 'unconfirmed'}},
            'electrode_pairs': {pair: {'L': field(device.get('L_um'), 'um', confirmed, src),
                                      'W': field(device.get('W_um'), 'um', confirmed, src),
                                      'wiring': field(None)}},
            'reference_inputs': {'mobility_cm2_Vs': field(None)}}


def render(data, body='Stable device metadata. Per-run conditions are recorded separately.\n'):
    return '---\n'+json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n---\n\n'+body


def read_document(path):
    raw = Path(path).read_bytes()
    if len(raw) > 256*1024: raise ValueError('Metadata exceeds 256 KiB')
    text = raw.decode('utf-8-sig')
    match = re.match(r'\A---\s*\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
    if not match: raise ValueError('Missing YAML front matter')
    # Reject aliases before constructing to prevent cyclic / expansion surprises.
    if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(match.group(1))):
        raise ValueError('YAML anchors/aliases are not supported by device preview')
    data = yaml.load(match.group(1), Loader=UniqueLoader)
    if not isinstance(data,dict) or data.get('schema_version') != 1: raise ValueError('Unsupported device schema')
    def check(value, depth=0):
        if depth > 12: raise ValueError('Metadata nesting too deep')
        if isinstance(value, (dict,list)):
            if len(value)>1000: raise ValueError('Metadata collection too large')
            for item in (value.values() if isinstance(value,dict) else value):check(item, depth+1)
    check(data)
    return data, {'path':str(path), 'sha256':hashlib.sha256(raw).hexdigest()}, text


def merge(parent, child):
    """Evidence records and units replace atomically, matching actual reader semantics."""
    result = copy.deepcopy(parent)
    for key, value in child.items():
        if isinstance(value,dict) and isinstance(result.get(key),dict) and 'value' not in value and key not in ('units','electrodes'):
            if key in ('IdVd','IdVg') and 'file' in value: result[key] = copy.deepcopy(value)
            else: result[key] = merge(result[key],value)
        else: result[key] = copy.deepcopy(value)
    return result


def snapshot(device_dir, proposed):
    path = Path(device_dir)/'device.md'
    if path.exists():
        data, receipt, _ = read_document(path)
        # Stable facts only: never inherit historical temperature, dates or illumination.
        allowed = ('schema_version','device_name','structure','electrode_pairs','active_electrode_pair','reference_inputs')
        # Explicit unknown fields also shadow date-level historical assumptions.
        # Do not let a missing root material/thickness inherit an unrelated old run's value.
        unknown=device_document({'id':Path(device_dir).name,'pair':'custom'})
        unknown['electrode_pairs']={}
        stable={k:copy.deepcopy(data[k]) for k in allowed if k in data}
        return merge(unknown,stable), receipt
    return copy.deepcopy(proposed), {'path':str(path), 'sha256':None, 'source':'reviewed GUI snapshot; device.md not yet saved'}


def save_new(device_dir, data):
    path = Path(device_dir)/'device.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation also prevents a preview/save race from overwriting user edits.
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(render(data))
        stream.flush()
        import os
        os.fsync(stream.fileno())
    return path


def run_document(stable, settings, date, filename):
    result = copy.deepcopy(stable)
    role = 'IdVg' if settings['workflow']=='Id-Vg' else 'IdVd'
    illum = settings['illumination']
    light = illum['description'].strip().lower() if illum['status']=='confirmed' else None
    light = light if light in ('light','dark') else None
    temp = settings['temperature']
    try: temperature = float(temp['description']) if temp['status']=='confirmed' else None
    except ValueError: temperature = None
    if temperature is not None and not math.isfinite(temperature):temperature=None
    result['run_conditions'] = {'units': {'voltage':'V','current':'A','source':'Keithley Lab explicit CSV (V)/(A) headers','verification':'user_confirmed','applies_to':filename},
                                'temperature':field(temperature,'C',temperature is not None,'Manual per-run entry; see settings.json'),
                                role:{'file':filename,'measurement_date':field(date,confirmed=True,source='PC local acquisition start date with timezone in settings.json'),
                                      'illumination':field(light,confirmed=light is not None,source='Manual per-run condition; see settings.json'),
                                      'sweep_delay_user_s':settings['point_delay_s'],
                                      'hold_time_settings_s':settings['hold_s'],
                                      'delay_verification':'host_requested_not_instrument_settings'}}
    result['run_conditions']['IdVd' if role=='IdVg' else 'IdVg']={'file':None}
    result['run_conditions']['measured_noise_floor_A']=None
    result['run_conditions'][role]['measurement_date']['verification']='pc_recorded'
    return result
