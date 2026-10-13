# Independent source validation — v1.2.1

- Windows / Python 3.7.8, fresh virtual environment, declared dependencies installed successfully.
- Module-path checks confirmed the independent copy and its own installed PyYAML were used; the original app and analyzer were not imported.
- All 76 unit/integration tests passed with loopback-only simulated transport.
- Supplied relative-path build script and PyInstaller spec produced a standalone EXE successfully.
- Both `python launch.py --self-test` and the independently rebuilt EXE `--self-test` passed: GUI rendered, two simulated sweeps, 12 points, one mean export, no real endpoint access.
- Source privacy scan and ZIP integrity/hash verification passed. The source ZIP has README.md directly at its root. No EXE, environment, cache, measurement data or private QA logs are included.
- Original application source and existing Windows distribution ZIP retained identical SHA256 hashes.

## Disclosed limitation
An initial deeply nested test copy hit Windows path-length limits: six suite cases and the rebuilt EXE smoke failed during export. The source GUI smoke passed there. After moving the independent copy to a short temporary path, the full suite and both smoke tests passed. This release does not add long-path support: use short extraction/save paths and device names. This is a runtime limitation, not a claimed all-path pass.

Other Python versions, actual instrument measurements, analyzer interoperability on a new installation, and GitHub upload were not tested/performed in this source-layout task. Prior v1.2.1 transport behavior is preserved; only public example IP, standard dependency loading and clean-test initialization differ from the private source. The original running application was not altered.
