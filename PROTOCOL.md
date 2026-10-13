# Command provenance and implementation boundary

Primary reference: [4200-SCS Reference Manual 4200-901-01 Rev. S, May 2017](https://download.tek.com/manual/4200-901-01S_May2017_Ref_0.pdf). The official PDF was downloaded and its text inspected locally during implementation. Printed page numbers below are not PDF page indices.

| Function | Implemented commands / basis |
|---|---|
| ID | `ID`, printed p. 9-45; exact expected reply `KI4200 V8.2-SP4` comes from the user's verified instrument |
| User mode | `US`, p. 9-40 |
| Voltage source, autorange, explicit current compliance | `DVn,0,V,I`, p. 9-41 |
| Sequential current read | `TIn`, pp. 9-42–43; n=1..3 confirmed SMU mapping only |
| Integration | `IT1`, `IT2`, `IT3`, p. 9-44; 0.1, 1, 10 PLC user-mode table. Custom IT4 omitted |
| Ethernet status | `SP`, p. 9-48; status bits pp. 9-51–52 |
| Reading status | p. 9-51: N normal; C same-channel compliance; T other-channel compliance; L interval short; V ADC overflow; X oscillation |
| Stop outputs | bare `DVn`, p. 9-55 example, sent as separate commands with individual ACKs |

User-verified command framing is ASCII + NUL. The observed ID response has CR/LF + NUL; this must not be generalized to every reply. The legacy manual's inspected sections do not specify a universal raw-socket CRLF suffix or define NUL as literal ACK. Supporting modern [official Ethernet example](https://www.tek.com/de/documents/application-note/controlling-the-4200a-scs-parameter-analyzer-using-kxci-and-python-3) requires NUL commands. The vendor's [SMU user-mode example](https://github.com/tektronix/keithley/blob/main/Instrument_Examples/Model_4200A/KXCI_Examples/SMU/User%20Mode/Source%20voltage%20and%20measure%20current.py) sets read and write termination to NUL and queries US/DV. Modern examples are **not** proof of general legacy compatibility.

Since v1.1.3, NUL defines one response frame; exactly one trailing CRLF pair is optional. US/IT/DV completion accepts either a strictly empty payload or literal ASCII ACK, records them distinctly, and requires SP validation in normal acquisition before advancing. This bounded compatibility policy is not a claim that the missing legacy incident bytes were a successful ACK. Empty ID/SP/TI replies are rejected. ASCII 0, control ACK byte 0x06, ERROR and arbitrary strings are not coerced to ACK. ID format and exact firmware, SP numeric byte/status bits, TI grammar/channel/reading status are checked separately. Embedded line breaks and control/nonASCII payloads are rejected.

The transport has one outstanding request, bounded whole-frame deadline and 16 KiB maximum response size, rejects unexpected buffered/multiple/partial frames, and closes a desynchronized socket. It never retries a bias command, drains arbitrary replies to pretend synchronization, or reconnects. A fully delimited invalid payload halts acquisition but leaves the synchronized connection available for best-effort zero/stop. SP bit 0 (1) is data ready; bit 1 (2) is syntax error; bit 4 is busy; bit 6 is request for service. Unknown bits or busy at synchronous checkpoints stop the run conservatively. The protocol has no transaction IDs, so arbitrary third-party clients and corrupted/instrument-generated delayed duplicate messages cannot be made safe by software correlation alone; close other clients and inspect the instrument on any fault.

Raw receive hex, packet chunks, intended/sent command state and frame details are journaled before payload validation. Failure evidence is copied into settings.json before cleanup can replace the last exchange. Cleanup exchanges are journaled best-effort and copied into metadata; a logging failure never blocks zero/stop. A lost or desynchronized connection means UNKNOWN, no reconnect. Completion/ACK/SP never proves physical voltage or OFF.

Unsigned positive mantissas are accepted (`NAI 2.994974E-09`); channel/mode and status are checked. Unknown/malformed values are retained as evidence and stop the run. Values ≥9e37 in magnitude or nonfinite readings are treated as invalid sentinels; these defensive policies do not invent an additional documented status code.

No TV, MP, DS, system-mode sweeps, KULT calls, instrument timestamps, source readback, auto-calibration, laser, temperature or external analysis routines are used. All sweeps are explicit host DV/TI loops in the same engine.

Reported prior physical experiment: US; stop DV1/2/3; IT2; zero source/gate/drain at 100 µA; SP=0; drain +0.05 V; SP=0; TI1=2.994974 nA; zero and stop D/G/S ACK; final SP=1. This app itself has **only been tested against the deterministic loopback simulator**.

Library reference `libfile_1eaa87c3254c8191bad2ee9eea265139` (`kxci_spot_test_v2.txt`) could not be materialized: the official current transfer helper requires Python newer than DD's default 3.7; retry with existing FreeCAD Python 3.11 downloaded data but failed at Windows-unavailable `os.setxattr` before atomic installation. No readable local prototype was accepted. Implementation uses the self-contained verified protocol and primary manual, not an assumed cloud path or execution of that prototype.
