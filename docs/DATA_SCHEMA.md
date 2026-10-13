# Data schema

All numeric voltage/current values use V/A. `points.csv` records attempted points in acquisition order with point/curve/branch indices, programmed voltage, measured current, raw reply, per-channel status, compliance, PC timestamps with UTC offset, requested/elapsed waits and simulation flag. Instrument time and voltage readback are unavailable and remain empty. Invalid/missing data is never silently replaced by zero.

`settings.json` records the validated settings, plan, preflight, firmware, progress, errors and best-effort cleanup. `commands.jsonl` records command/reply evidence. `sequence.json` schema 2 records steps, repeats, source runs and export receipts. Runs are uniquely named, raw data saved incrementally and JSON updated atomically. Abrupt termination can leave a partial last CSV row; retain it as evidence and never auto-resume.

Wide CSV and per-file review receipts are defined in WIDE_SCHEMA.md. A completed acquisition does not imply `analysis_ready=true` or physical output OFF. `physical_output_verified_off` remains false. Simulated files are explicitly identified and kept separate.
