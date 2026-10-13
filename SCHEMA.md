# v1.2.0 default export update

Flat wide CSV is now the default analysis input. See WIDE_SCHEMA.md. Pointwise records remain unchanged. Older per-curve descriptions below are historical/legacy and do not describe new run output placement.

# Local measurement schema — raw v1, sequence v2 (app 1.1)

## App 1.1.1 compatible derived means

Mean-only Inbox: `device/queue-start-date/평균_분석입력/sequence/stepNN/curve_branch/`. Raw-repeat Inbox remains `device/sweep-start-date/원본/sequence/sNN_rNNN_unique/curve_branch/`. These are sibling input roots and must not be combined. Raw filenames now explicitly identify the device: `device=<id>_RAW_IdVd.csv` (or IdVg), avoiding the legacy analyzer's treatment of sequence directories as device names.

Mean filename: `device=<id>_AGGREGATE_MEAN_IdVd.csv` or IdVg. Canonical voltage/current headers load through the actual reader. `data_kind=aggregate_mean_not_raw`, simulation, sequence_id, step, curve, branch, planned_point_index, programmed source voltage and aggregation_created_at identify the derivative. Every role adds sample_std_A/n_valid/n_invalid/n_missing. No-valid grid positions retain voltages/order and have blank currents plus excluded_no_valid_repeats. Counts and standard deviations are supplementary columns, not automatically used as analyzer weights.

`aggregate_provenance.json` includes exact plan hash/mapping, source dates, contributing repeat/run IDs, raw points.csv absolute and relative links plus SHA256, per-point accepted/excluded/missing repeats and statistics, queue state, and analysis_ready. Normalize relative filesystem paths before opening on Windows. `device.md` remains supported schema 1; its body explicitly labels derived mean / not raw. Its date denotes the first contributing acquisition date, with full date range in provenance, not aggregation creation time. The opposite measurement-role link is null. Each curve/family/branch stays separate. Spot retains raw/statistics but has no mean-curve export.

Aggregate files stage under records and are published by a single directory rename only when complete. Failed aggregate writes halt following steps and are not retried. Cancelled/error sequences can publish explicitly partial means; raw records remain independent. `sequence.json.mean_exports` links successful derivative outputs.

The previous v1.1 import description below uses the former generic filename `measurement.csv`; v1.1.1 uses the explicit RAW filename above with otherwise compatible columns.

The raw point schema below remains v1. App 1.1 uses `sequence.json` schema 2 and a device/date layout documented in README_KO.md. All raw numeric units remain V/A.

## Sequence v2 additions

- `sequence_id`: local start timestamp plus random UUID suffix. Each run ID adds immutable step/repeat indices and another UUID suffix. Existing runs are never reused.
- `review`: entire validated queue (each step settings + exact plan + repeat count), stable metadata snapshot, device.md SHA256 receipt, effective root/mode, requested transition settle, counts, minimum duration, estimated bytes, date policy.
- `preflight`: payload SHA256 and local review timestamp. The entire queue and device receipt are rechecked before starting. No automatic restart/resume.
- `runs`: step/repeat/run_id, actual acquisition start date, point date list, raw path, compatible export paths, status/error, cleanup, point_count. A sequence spanning midnight may reference multiple date folders.
- `events`: timezone-aware PC timestamps for queue start, sweep start/finish, zero-stop-ACK transition with requested/elapsed settle, export completion. ACK never means physical OFF verification.
- `stepNN_statistics.json`: means, repeat_view, completed_repeats, warnings. Replaced atomically after each attempt. Final completed-step CSV views accompany it. Partial/error runs still retain JSON statistics if persistence succeeds.
- Statistics key: step-local exact point index + curve/branch + programmed role voltages + role mapping. Duplicate/mismatched grids are rejected. `mean_A`, `sample_std_A` (null for n<2), `n_valid`, `n_invalid`, `n_missing`, `n_requested`, first-last delta and relative drift. A non-normal point is excluded for every role. Missing future repeats count as missing, never zero. No interpolation. All generated CSV views carry a simulation flag.

## Analysis import CSV

`원본/sequence/run/curve_branch/measurement.csv` is one repeat/curve/branch, retaining source order. Columns `DrainV (V)`, `GateV (V)`, `DrainI (A)`, `GateI (A)` match actual research-automation 1.9.1 aliases. Drain/Gate voltages are programmed instrument-referenced setpoints, not measured Vds/Vgs. `voltage_origin=programmed_not_measured`, `source_programmed_V`, source_point_index, pc_timestamp, point_status, compliance_flag, simulation and run_id retain context. Non-normal currents are blank only in this import view; rich points.csv retains the original readings. If no valid data remains, the existing reader may reject the file.

Each adjacent `device.md` is schema-1 YAML frontmatter (JSON subset) accepted by the actual reader. Stable facts come from the existing root device.md when present, otherwise reviewed GUI values. Historical run_conditions are not reused. Per-run measurement_date is `pc_recorded`, temperature/illumination are explicit manual values or null, and the opposite measurement role's file is reset to null to prevent accidental historical pairing. CSV avoids the legacy reader's XLS filename dark inference. Existing device.md is never overwritten or merged by the app; new file save uses exclusive creation.

Select only `원본` as the analysis Inbox. Set the analysis tool's optional metadata_root to the device folder when broader inheritance is desired. Do not import `기록` rich CSV/statistics as measurement sheets. The app never edits analysis configuration or runs its pipeline. Real source code compatibility fingerprint is in `qa/compatibility-tests.json`.

UTF-8 CSV/JSON. One directory per immutable run identity, unique timestamp + random suffix. Settings metadata changes atomically during a run; completed files are not reused. Consumers should require a known schema_version and inspect run, point and channel statuses. Do not assume every planned point or every channel was measured.

## points.csv

One row per **attempted** point, including a partially measured fault/cancel point. Values use volts and amperes, never microamp numeric scaling. Empty means unavailable. Python boolean literals `True`/`False` represent simulation. Preserve raw signed currents. No fitting or inferred physics.

| Field | Meaning |
|---|---|
| schema_version, run_id, simulation | Schema 1, unique acquisition identity, synthetic flag |
| point_index | One-based overall acquisition order |
| curve | One-based position in the user-entered fixed-voltage list; Spot=1 |
| branch | spot / forward / reverse. Forward means start→stop even for descending sweeps |
| sweep_V | Nominal programmed sweep coordinate; Spot uses drain setpoint |
| pc_point_start / pc_point_end | Actual host ISO 8601 timestamp with UTC offset |
| elapsed_s / point_elapsed_s | Monotonic elapsed time since run start / point attempt |
| requested_hold_s / actual_hold_s | Hold once per curve, requested / measured host wait, including partial cancelled wait |
| requested_delay_s / actual_delay_s | Requested / measured per-point host wait after bias ACK/SP |
| instrument_time_s | Blank; not supplied by implemented legacy TI command |
| SP | Last observed raw status byte for this point; not an output-state proof |
| point_status | normal, cancelled, error, or the failing reading status |
| {role}_SMU | Mapped channel, 1..3; role = drain, source, gate |
| {role}_programmed_V | Planned nominal setpoint, not voltage readback or proof of acceptance; for failure rows consult commands.jsonl |
| {role}_measured_V | Blank, no voltage measurement is issued |
| {role}_current_A | Parsed numeric current, possibly non-normal; consult status before use |
| {role}_compliance_A | Requested compliance |
| {role}_status | normal, compliance, other_channel_compliance, interval_too_short, overflow, oscillation, unknown_status, malformed, wrong_channel, invalid_sentinel; blank if not obtained |
| {role}_raw | Exact TI reply excluding CR/LF/NUL framing, retained even for rejected readings |
| {role}_pc_query / {role}_pc_reply | PC time immediately before request / immediately after full reply; sequential channels, not simultaneous |

Readings with sentinel magnitude ≥9e37 or nonfinite values have a blank numeric value and explicit invalid_sentinel status. Malformed or wrong-channel readings have no accepted numeric current. A numeric non-normal value is retained only as raw evidence, not valid analysis data.

## settings.json

`settings` preserves all user inputs; `plan` preserves actual nesting/order and all electrode setpoints. `mode` is simulation or real. Limits are device-approved inputs bounded by separate application caps, not inferred from instrument capability. Conditions have status `unknown` or `confirmed` plus manual description. Confirmation does not constitute automatic sensor evidence.

Top-level firmware is the actual ID reply. `actual_endpoint` in simulation is loopback (the saved configured LAN target is not used). `provenance` identifies app version, host, Python, legacy manual and reported prior physical test scope. `preflight` contains the immutable settings hash and local review timestamp, or null in programmatic simulation tests. `created_at`, `finished_at`, `elapsed_s`, `saved_points` and `status` describe lifecycle.

`status` = prepared / running / completed / cancelled / error. `cleanup.status` is acknowledged_only_not_physically_verified, UNKNOWN_OUTPUT_STATE_CONNECTION_LOST, UNKNOWN_OUTPUT_STATE_CLEANUP_FAILED, or not_required_no_commands. Inspect individual cleanup commands and SP. **physical_output_verified_off is always false.** An ACK cannot establish physical state.

If metadata remains prepared/running after a crash, treat the run as incomplete and output state as unknown. Do not resume automatically. CSV may contain more durable rows than saved_points if metadata update failed; preserve and reconcile both files without rewriting the original. A partial final CSV record after sudden power loss must not be silently interpreted as a complete point.

## commands.jsonl and plots

Each command/reply event is `{"pc_time": "ISO8601+offset", "kind": "send|receive", "text": "raw ASCII"}`. A send record means intent logged before socket send, not proof of transmission or hardware execution. Cleanup bypasses a broken journal and is recorded in settings metadata. plot.svg uses only normal drain readings, independent traces per curve/branch, linear signed axes. For quantitative use, read CSV and settings together.

## Example import

```python
import csv, json
from pathlib import Path
run = Path('chosen/run/directory')
meta = json.loads((run/'settings.json').read_text(encoding='utf-8'))
with (run/'points.csv').open(encoding='utf-8', newline='') as stream:
    raw = list(csv.DictReader(stream))
valid = [r for r in raw if r['point_status'] == 'normal'
         and r['drain_status'] == 'normal' and r['drain_current_A']]
# Keep simulation and real datasets separate; preserve curve + branch + point_index.
```


## v1.1.2 canonical workflow plans and queue draft JSON

`planning.canonicalize` removes inactive UI values before validation and persistence. Spot retains fixed_V drain/source/gate and replaces family_V with [], start_V/stop_V/step_V with 0, direction ascending, return_sweep false. For either sweep, fixed_V.source is active; fixed_V.drain/gate are canonical 0 placeholders, not the commanded curve biases. Actual settings are start_V/stop_V/step_V for the swept axis and the ordered family_V for the other axis. The `plan` and pointwise programmed columns are the authoritative voltage sequence.

Id-Vd maps swept axis to drain, fixed family to gate. Id-Vg reverses these roles. No sorting or silent deduplication occurs. Family duplicates are rejected at 12 significant digits, matching command serialization. Generator values become effective only when explicitly applied to the visible family list.

Queue draft JSON has `queue_schema_version: 1`, `kind: "draft_not_execution_authorization"`, `items: [{"settings": <canonical measurement settings>, "repeats": <1..100>}]`. Saving uses exclusive file creation and never overwrites. Loading validates all items before replacing the queue, caps file size at 2 MiB, and overrides each item's mode with the GUI's current selected connection mode. It never connects, starts or carries over approval. Device-tab stable metadata and queue-wide settle_s are not stored; they remain current GUI values. Final reviewed queue root is selected from the current save-root field for real runs; simulation always uses the workspace simulation_data root. Entire queue limits: 50 steps, 1,000 full sweeps, 100,000 points.

Saved per-run settings.json and sequence.json remain schema version 1; app_version identifies 1.1.2. Raw/mean analysis CSV schemas and explicit derived-data provenance are unchanged.

## v1.1.3 raw transport evidence

Existing point CSV and mean schemas remain unchanged. commands.jsonl adds `receive_bytes` and `exchange_error` events (structured object in `text`), and best-effort `cleanup_send`, `cleanup_receive_bytes`, `cleanup_receive`, `cleanup_exchange_error`. The object contains command, expected_kind, tx_hex, tx_state (not_sent/send_attempted_possibly_partial/sent), rx_hex, chunks_hex, complete_frame and, when available, frame_hex, extra_hex/extra_peek_hex/stale_peek_hex, trailing_crlf, reply_kind and error. A send journal event alone records intent; tx_state clarifies socket send progress. A raw receive journal event occurs before payload validation.

On failure, settings.json and the controller result contain `failure_exchange`, copied before cleanup. This may be null if no exchange occurred. It is in-memory evidence and can still be included in the recovery JSON if the journal write fails. Cleanup command items now include their own `exchange` record and result `ACK` for literal text or `NUL_COMPLETION` for empty completion. These are communication outcomes; `physical_output_verified_off` remains false. A malformed but complete payload may leave the connection synchronized for best-effort cleanup; missing boundaries, extra data and connection failure mark the session broken with no reconnect. Raw rejected bytes from old releases are not reconstructed or fabricated.
