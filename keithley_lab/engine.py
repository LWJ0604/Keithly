"""Validated plans, single-run controller and incremental, provenance-rich storage."""
import copy
import csv
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import queue
import sys
import threading
import time
import uuid

from . import __version__
from .mock import MockKXCI
from .protocol import EXPECTED_ID, KXCI, ProtocolError, parse_reading
from .planning import canonicalize, validate_family

ROLES = ('drain', 'source', 'gate')
APP_V_CAP = 20.0  # Software policy only, NOT evidence of DUT safety.
APP_I_CAP = 1e-4  # User-approved ceiling on this instrument/workflow.
MAX_POINTS = 10000
MANUAL = 'https://download.tek.com/manual/4200-901-01S_May2017_Ref_0.pdf'
TIMING = ('Host-stepped: zero drain/gate/source between curves; establish source, gate, drain at first point; hold once per curve; '
          'after each point bias completion response and SP, wait point_delay, then sequential TI drain/source/gate. '
          'Return branch excludes duplicate turning point. No simultaneous sampling. '
          'PC timestamps mark command/reply times, not instrument conversion time.')


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='milliseconds')


def app_root():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def defaults(root):
    return {'mode': 'simulation', 'host': '192.0.2.1', 'port': 1225,
            'workflow': 'Spot', 'mapping': dict(zip(ROLES, (1, 2, 3))),
            'limits': {'drain': {'min_V': 0., 'max_V': .05, 'compliance_A': 1e-4},
                       'source': {'min_V': 0., 'max_V': 0., 'compliance_A': 1e-4},
                       'gate': {'min_V': 0., 'max_V': 0., 'compliance_A': 1e-4}},
            'fixed_V': {'drain': .05, 'source': 0., 'gate': 0.},
            'family_V': [0.], 'start_V': 0., 'stop_V': .05, 'step_V': .01,
            'direction': 'ascending', 'return_sweep': False, 'hold_s': .2,
            'point_delay_s': .1, 'integration': 2, 'line_Hz': 60, 'timeout_s': 3.,
            'save_root': str(Path(root).absolute() / 'data'), 'sample': '',
            'illumination': {'status': 'unknown', 'description': ''},
            'temperature': {'status': 'unknown', 'description': ''}, 'notes': ''}


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(name + ': finite numeric value required')
    return value


def validate(settings):
    s = canonicalize(settings)
    if s['mode'] not in ('simulation', 'real'):
        raise ValueError('Invalid mode')
    # Literal IPv4 prevents unexpected hostname resolution; never opened by validation.
    ipaddress.IPv4Address(s['host'])
    if type(s['port']) is not int or not 1 <= s['port'] <= 65535:
        raise ValueError('Invalid port')
    if s['workflow'] not in ('Spot', 'Id-Vd', 'Id-Vg'):
        raise ValueError('Invalid workflow')
    if sorted(s['mapping'].values()) != [1, 2, 3] or any(type(c) is not int for c in s['mapping'].values()):
        raise ValueError('SMU1, SMU2, SMU3 must be used exactly once (confirmed hardware only)')
    if s['integration'] not in (1, 2, 3) or s['line_Hz'] not in (50, 60):
        raise ValueError('Integration IT1..3 and line frequency 50/60 Hz required')
    for key in ('hold_s', 'point_delay_s', 'timeout_s'):
        val = finite(s[key], key)
        low, high = (.1, 10) if key == 'timeout_s' else (0, 3600)
        if not low <= val <= high:
            raise ValueError(key + ' outside permitted software range')
    for role in ROLES:
        lim = s['limits'][role]
        for key in ('min_V', 'max_V', 'compliance_A'):
            finite(lim[key], role + ' ' + key)
        if not -APP_V_CAP <= lim['min_V'] <= 0 <= lim['max_V'] <= APP_V_CAP:
            raise ValueError(role + ': bounds must contain zero and stay within app ±20 V cap')
        if not 1e-7 <= lim['compliance_A'] <= APP_I_CAP:
            raise ValueError(role + ': compliance must be 100 nA..100 µA (preamp unknown)')
        fixed = finite(s['fixed_V'][role], role + ' fixed V')
        if not lim['min_V'] <= fixed <= lim['max_V']:
            raise ValueError(role + ': fixed voltage exceeds device-approved bounds')
    for condition in ('illumination', 'temperature'):
        if s[condition]['status'] not in ('unknown', 'confirmed'):
            raise ValueError('Condition status must be unknown or confirmed')
        if s[condition]['status'] == 'confirmed' and not s[condition]['description'].strip():
            raise ValueError('Confirmed condition requires a manual description')
    root_text = s['save_root']
    if not root_text or '\0' in root_text:
        raise ValueError('Save path is required')
    root = Path(root_text)
    if not root.is_absolute():
        raise ValueError('Save path must be absolute')
    if os.name == 'nt':
        for part in root.parts[1:]:
            if any(c in part for c in '<>:"|?*') or part.endswith((' ', '.')) or part.split('.')[0].upper() in (
                    'CON', 'PRN', 'AUX', 'NUL', *['COM%d' % i for i in range(1, 10)], *['LPT%d' % i for i in range(1, 10)]):
                raise ValueError('Invalid Windows save path')
    if any(p.exists() and not p.is_dir() for p in [root] + list(root.parents)):
        raise ValueError('Save path contains a file instead of a directory')
    # Force read validation to be independent from arbitrary user dictionary edits.
    plan = build_plan(s)
    for point in plan:
        for role in ROLES:
            lim = s['limits'][role]
            if not lim['min_V'] <= point[role] <= lim['max_V']:
                raise ValueError('%s planned %.9g V exceeds device-approved bounds [%g, %g]' % (
                    role, point[role], lim['min_V'], lim['max_V']))
    if len(plan) > MAX_POINTS:
        raise ValueError('Total plan exceeds 10,000 points')
    return s, plan


def build_plan(s):
    if s['workflow'] == 'Spot':
        return [dict(s['fixed_V'], curve=1, branch='spot', sweep_V=s['fixed_V']['drain'])]
    start, stop, step = [finite(s[k], k) for k in ('start_V', 'stop_V', 'step_V')]
    if start == stop or step == 0 or (stop-start)*step <= 0:
        raise ValueError('Sweep requires distinct endpoints and signed step toward stop')
    direction = 'ascending' if stop > start else 'descending'
    if s['direction'] != direction:
        raise ValueError('Direction conflicts with start/stop/step')
    intervals = (stop-start)/step
    if not math.isfinite(intervals) or intervals > MAX_POINTS:
        raise ValueError('Excessive point count')
    if abs(intervals-round(intervals)) > 1e-7 or round(intervals) < 1:
        raise ValueError('Step must divide stop-start exactly; endpoint is never silently changed')
    n = int(round(intervals))
    family = s['family_V']
    if not isinstance(family, list) or not 1 <= len(family) <= 100:
        raise ValueError('Enter 1..100 fixed family voltages')
    for value in family:
        finite(value, 'family voltage')
    validate_family(family)
    count = len(family) * (2*n+1 if s['return_sweep'] else n+1)
    if count > MAX_POINTS:
        raise ValueError('Total family plan exceeds 10,000 points')
    sweep_role, fixed_role = ('drain', 'gate') if s['workflow'] == 'Id-Vd' else ('gate', 'drain')
    sweep = [start+i*step for i in range(n+1)]
    sweep[0], sweep[-1] = start, stop
    # Avoid binary float endpoint drift against explicitly entered limits.
    sweep = [float('%.12g' % x) for x in sweep]
    plan = []
    for curve, fixed in enumerate(family, 1):
        branches = [('forward', sweep)]
        if s['return_sweep']:
            branches.append(('reverse', list(reversed(sweep[:-1]))))
        for branch, values in branches:
            for value in values:
                point = dict(s['fixed_V'], curve=curve, branch=branch, sweep_V=value)
                point[sweep_role], point[fixed_role] = value, fixed
                plan.append(point)
    return plan


def estimate(s, plan):
    plc = {1: .1, 2: 1., 3: 10.}[s['integration']]
    minimum = len(s['family_V']) if s['workflow'] != 'Spot' else 1
    minimum = minimum*s['hold_s'] + len(plan)*(s['point_delay_s']+3*plc/s['line_Hz'])
    return minimum  # Lower bound; range settling/network/disk overhead is unknown.


CSV_FIELDS = ['schema_version', 'run_id', 'simulation', 'point_index', 'curve', 'branch',
              'sweep_V', 'pc_point_start', 'pc_point_end', 'elapsed_s', 'point_elapsed_s',
              'requested_hold_s', 'actual_hold_s', 'requested_delay_s', 'actual_delay_s',
              'instrument_time_s', 'SP', 'point_status']
for _r in ROLES:
    CSV_FIELDS += [_r+'_SMU', _r+'_programmed_V', _r+'_measured_V', _r+'_current_A',
                   _r+'_compliance_A', _r+'_status', _r+'_raw', _r+'_pc_query', _r+'_pc_reply']


def atomic_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(temp), str(path))


class RunStore:
    def __init__(self, settings, plan, path=None, run_id=None):
        self.run_id = run_id or dt.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]
        category = 'SIMULATION' if settings['mode'] == 'simulation' else 'REAL'
        self.path = Path(path) if path is not None else Path(settings['save_root']) / category / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.rows = []
        self.csvfile = self.logfile = None
        self.meta = {'schema_version': 1, 'app_version': __version__, 'run_id': self.run_id,
                     'simulation': settings['mode'] == 'simulation', 'created_at': now(),
                     'status': 'prepared', 'saved_points': 0, 'settings': settings, 'plan': plan,
                     'timing_definition': TIMING, 'estimated_minimum_s': estimate(settings, plan),
                     'firmware': None, 'instrument_time_available': False,
                     'physical_output_verified_off': False, 'cleanup': {'status': 'not_started'},
                     'provenance': {'manual': MANUAL, 'pages': '9-40..44, 9-48, 9-51..52, 9-55',
                                    'host': platform.node(), 'python': platform.python_version(),
                                    'physical_test_evidence': 'User reported: D +0.05 V, S/G 0 V; 100 uA each. Other biases not physically validated.',
                                    'mock_model': 'deterministic linear current model; not device physics' if category == 'SIMULATION' else None}}
        try:
            self.update()
            self.csvfile = (self.path/'points.csv').open('x', encoding='utf-8', newline='')
            self.writer = csv.DictWriter(self.csvfile, fieldnames=CSV_FIELDS)
            self.writer.writeheader()
            self._flush(self.csvfile)
            self.logfile = (self.path/'commands.jsonl').open('x', encoding='utf-8', newline='\n')
        except Exception:
            self.close()
            raise

    @staticmethod
    def _flush(f):
        f.flush()
        os.fsync(f.fileno())

    def event(self, kind, text):
        self.logfile.write(json.dumps({'pc_time': now(), 'kind': kind, 'text': text}, ensure_ascii=False)+'\n')
        self._flush(self.logfile)

    def append(self, row):
        self.writer.writerow(row)
        self._flush(self.csvfile)
        self.rows.append(dict(row))
        self.meta['saved_points'] = len(self.rows)
        self.update()

    def update(self):
        atomic_json(self.path/'settings.json', self.meta)

    def finish(self):
        self.update()
        summary = ('# %s — %s\n\n- Mode: %s\n- Saved points: %d / %d\n- Result: %s\n'
                   '- Error: %s\n- Cleanup: %s\n- Physical output OFF verified: **NO**\n\n'
                   'ACK means command acknowledgement only. Connection loss requires local instrument inspection.\n\n'
                   '## Timing\n%s\n\n## Provenance\n[Legacy manual](%s), printed pp. 9-40–44, 9-48, 9-51–52, 9-55.\n'
                   'See settings.json for complete settings, conditions, plan and cleanup; commands.jsonl for raw traffic.\n'
                   'Blank voltage readbacks/instrument time mean unavailable, never zero. No fitted parameters or mechanism inference.\n') % (
                       self.run_id, self.meta['settings']['workflow'],
                       'SIMULATED — NOT EXPERIMENTAL DATA' if self.meta['simulation'] else 'REAL',
                       len(self.rows), len(self.meta['plan']), self.meta['status'], self.meta.get('error',''),
                       self.meta['cleanup']['status'], TIMING, MANUAL)
        (self.path/'summary.md').write_text(summary, encoding='utf-8')
        self._plot_svg()

    def _plot_svg(self):
        # Dependency-free signed plot; curves and branches retain independent traces.
        groups = {}
        for row in self.rows:
            value = row.get('drain_current_A')
            if value is not None and row.get('drain_status') == 'normal':
                groups.setdefault((row['curve'], row['branch']), []).append((row['sweep_V'], value))
        points = [p for group in groups.values() for p in group]
        label = 'SIMULATION - NOT EXPERIMENTAL DATA' if self.meta['simulation'] else 'REAL DATA'
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="640" viewBox="0 0 1000 640">',
               '<rect width="1000" height="640" fill="#0c1525"/>',
               '<g fill="#dde8f5" font-family="sans-serif"><text x="70" y="40">Drain current (A) versus programmed sweep voltage (V)</text>',
               '<text x="70" y="65">'+label+'</text></g>']
        if points:
            xs, ys = zip(*points)
            xmin,xmax,ymin,ymax = min(xs),max(xs),min(ys),max(ys)
            dx,dy = xmax-xmin or 1.,ymax-ymin or max(abs(ymin)*.1,1e-12)
            for i in range(6):
                x,y = 100+i*135,520-i*80
                svg.append('<path d="M100 %g H775 M%g 120 V520" stroke="#233248"/>' % (y,x))
                svg.append('<g fill="#a6b7ca" font-family="monospace" font-size="12"><text x="20" y="%g">%.2e</text><text x="%g" y="550">%.3g</text></g>' % (y,ymin+i*dy/5,x,xmin+i*dx/5))
            colors = ['#4ddfbc','#60a5fa','#fbbf24','#c084fc','#fb7185']
            for i, (key, group) in enumerate(groups.items()):
                coords = ' '.join('%.2f,%.2f' % (100+(x-xmin)/dx*675,520-(y-ymin)/dy*400) for x,y in group)
                svg.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s" %s/>' % (colors[(key[0]-1)%5],coords,'stroke-dasharray="6,4"' if key[1]=='reverse' else ''))
                for x,y in group:
                    svg.append('<circle cx="%.2f" cy="%.2f" r="2.5" fill="%s"/>' % (100+(x-xmin)/dx*675,520-(y-ymin)/dy*400,colors[(key[0]-1)%5]))
                if i<20:
                    svg.append('<text x="800" y="%d" fill="%s" font-family="sans-serif" font-size="12">Curve %d / %s</text>' % (120+i*20,colors[(key[0]-1)%5],key[0],key[1]))
        svg.append('</svg>')
        (self.path/'plot.svg').write_text('\n'.join(svg), encoding='utf-8')

    def close(self):
        for f in (self.csvfile, self.logfile):
            if f:
                f.close()


class Cancelled(Exception):
    pass


class Controller:
    def __init__(self, store_factory=RunStore, mock_factory=MockKXCI):
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.store_factory, self.mock_factory = store_factory, mock_factory
        self.active = False
        self.last_result = None

    def start(self, settings, approval=None):
        s, plan = validate(settings)
        if s['mode'] == 'real':
            expected = hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()
            if not approval or approval.get('settings_hash') != expected or not approval.get('confirmed_at'):
                raise ValueError('Real run requires explicit current local preflight approval')
        if not self.lock.acquire(False):
            raise RuntimeError('A run is already active')
        self.active = True
        self.cancel.clear()
        self.last_result = None
        self.thread = threading.Thread(target=self._run, args=(s, plan, approval), daemon=False)
        self.thread.start()

    def stop(self):
        self.cancel.set()

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled('User requested stop/close')

    def _wait(self, seconds):
        start = time.monotonic()
        if self.cancel.wait(seconds):
            raise Cancelled('User requested stop/close')
        return time.monotonic()-start

    def _timed_wait(self, seconds, row, key):
        start = time.monotonic()
        try:
            self._wait(seconds)
        finally:
            row[key] = time.monotonic()-start

    def _run(self, s, plan, approval):
        store = client = mock = None
        result = {'status': 'error', 'error': '', 'path': None, 'cleanup': {'status': 'not_required_no_commands'}}
        touched = False
        started = time.monotonic()
        memory_rows = []
        try:
            # Prove durable destination before opening any instrument connection.
            store = self.store_factory(s, plan)
            result['path'] = str(store.path)
            store.meta['preflight'] = approval
            store.meta['status'] = 'running'
            store.update()
            self._check()
            host, port = s['host'], s['port']
            if s['mode'] == 'simulation':
                mock = self.mock_factory(mapping=s['mapping'])
                host, port = '127.0.0.1', mock.port
            client = KXCI(host, port, s['timeout_s'], s['mode']=='simulation', store.event)
            client.connect()
            identity = client.exchange('ID')
            self.events.put(('identity', identity))
            store.meta['firmware'] = identity
            store.meta['actual_endpoint'] = {'host': host, 'port': port}
            store.update()
            if identity != EXPECTED_ID:
                raise ProtocolError('Firmware mismatch: expected exactly ' + EXPECTED_ID)
            self._check()
            touched = True
            client.ack('US')
            client.status()
            for role in ('drain','gate','source'):
                self._check()
                client.ack('DV%d' % s['mapping'][role])
                client.status()
            client.ack('IT%d' % s['integration'])
            client.status()

            def bias(role, value):
                self._check()
                client.ack('DV%d,0,%.12g,%.12g' % (s['mapping'][role], value, s['limits'][role]['compliance_A']))
                return client.status()

            for role in ('source','gate','drain'):
                bias(role, 0.)
            previous = None
            for index, point in enumerate(plan, 1):
                self._check()
                point_start = time.monotonic()
                row = dict.fromkeys(CSV_FIELDS, None)
                row.update(schema_version=1, run_id=store.run_id, simulation=s['mode']=='simulation',
                           point_index=index, curve=point['curve'], branch=point['branch'], sweep_V=point['sweep_V'],
                           pc_point_start=now(), requested_hold_s=s['hold_s'] if previous is None or previous['curve']!=point['curve'] else 0,
                           requested_delay_s=s['point_delay_s'], point_status='incomplete', actual_hold_s=0., actual_delay_s=0.)
                for role in ROLES:
                    row[role+'_SMU'] = s['mapping'][role]
                    row[role+'_programmed_V'] = point[role]
                    row[role+'_compliance_A'] = s['limits'][role]['compliance_A']
                point_error = None
                try:
                    if previous is not None and previous['curve'] != point['curve']:
                        for role in ('drain', 'gate', 'source'):
                            bias(role, 0.)
                    for role in ('source','gate','drain'):
                        if previous is None or previous['curve'] != point['curve'] or point[role]!=previous[role]:
                            row['SP'] = bias(role, point[role])['raw']
                    self._timed_wait(row['requested_hold_s'], row, 'actual_hold_s')
                    self._timed_wait(s['point_delay_s'], row, 'actual_delay_s')
                    for role in ROLES:
                        self._check()
                        row[role+'_pc_query'] = now()
                        raw = client.exchange('TI%d' % s['mapping'][role])
                        row[role+'_pc_reply'] = now()
                        reading = parse_reading(raw, s['mapping'][role])
                        row[role+'_raw'] = raw
                        row[role+'_status'] = reading['status']
                        row[role+'_current_A'] = reading['value_A']
                        if not reading['valid']:
                            row['point_status'] = reading['status']
                            raise ProtocolError('Unsafe/invalid reading: '+reading['status'])
                        row['SP'] = client.status()['raw']
                    row['point_status'] = 'normal'
                except Exception as exc:
                    point_error = exc
                    if row['point_status'] == 'incomplete':
                        row['point_status'] = 'cancelled' if isinstance(exc, Cancelled) else 'error'
                finally:
                    row.update(pc_point_end=now(), elapsed_s=time.monotonic()-started,
                               point_elapsed_s=time.monotonic()-point_start)
                    memory_rows.append(dict(row))
                    store.append(row)
                    self.events.put(('point', dict(row)))
                if point_error:
                    raise point_error
                previous = point
            result['status'] = 'completed'
        except Cancelled as exc:
            result.update(status='cancelled', error=str(exc))
        except Exception as exc:
            result.update(status='error', error='%s: %s' % (type(exc).__name__, exc))
        finally:
            if client and result['status']=='error':
                result['failure_exchange']=copy.deepcopy(client.last_exchange)
                if store:store.meta['failure_exchange']=result['failure_exchange']
            self.events.put(('phase', '정리 중 · zero / output-stop'))
            cleanup = {'status': 'not_required_no_commands', 'commands': [], 'physical_output_verified_off': False}
            if touched:
                cleanup['status'] = 'acknowledged_only_not_physically_verified'
                # A broken socket is never reconnected or reused for cleanup ACKs.
                if client is None or client.broken or client.sock is None:
                    cleanup['status'] = 'UNKNOWN_OUTPUT_STATE_CONNECTION_LOST'
                else:
                    def cleanup_audit(kind,text):
                        try:store.event('cleanup_'+kind,text)
                        except Exception:pass  # Disk failure must not block zero/stop.
                    client.audit = cleanup_audit
                    for stop_only in (False, True):
                        for role in ('drain', 'gate', 'source'):
                            command = 'DV%d' % s['mapping'][role]
                            if not stop_only:
                                command += ',0,0,%.12g' % s['limits'][role]['compliance_A']
                            item = {'command': command, 'pc_time': now(), 'result': 'not_sent'}
                            try:
                                completion=client.ack(command)
                                item['result'] = 'ACK' if completion=='literal_ACK' else 'NUL_COMPLETION'
                            except Exception as exc:
                                item['result'] = str(exc)
                                cleanup['status'] = 'UNKNOWN_OUTPUT_STATE_CLEANUP_FAILED'
                            item['exchange']=copy.deepcopy(client.last_exchange)
                            cleanup['commands'].append(item)
                    if client.sock and not client.broken:
                        try:
                            cleanup['SP'] = client.status()
                        except Exception as exc:
                            cleanup['status'] = 'UNKNOWN_OUTPUT_STATE_CLEANUP_FAILED'
                            cleanup['error'] = str(exc)
            result['cleanup'] = cleanup
            if client:
                client.close()
            if mock:
                mock.close()
            result['rows_in_memory'] = memory_rows
            if store:
                store.meta.update(status=result['status'], error=result['error'], cleanup=cleanup,
                                  finished_at=now(), elapsed_s=time.monotonic()-started,
                                  physical_output_verified_off=False)
                try:
                    store.finish()
                except Exception as exc:
                    result['save_error'] = str(exc)
                    result['status'] = 'error'
                    store.meta.update(status='error', save_error=str(exc))
                    try:
                        store.update()
                    except Exception:
                        pass
                try:
                    store.close()
                except Exception as exc:
                    result['close_error'] = str(exc)
            # Recovery copy ONLY on persistence errors, in app workspace, unique; no overwrite.
            if store and (len(store.rows) != len(memory_rows) or result.get('save_error')):
                try:
                    fallback = app_root()/'recovery'
                    fallback.mkdir(exist_ok=True)
                    path = fallback/(store.run_id+'_recovery_'+uuid.uuid4().hex[:8]+'.json')
                    atomic_json(path, {'result': result, 'metadata': store.meta, 'rows': memory_rows})
                    result['recovery_path'] = str(path)
                except Exception as exc:
                    result['recovery_error'] = str(exc)
            self.last_result = result
            self.active = False
            self.lock.release()
            self.events.put(('done', result))
