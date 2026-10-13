# 1.2.1 — Per-file export metadata review

- Convey requested host point delay and confirmed light/dark through supported filename tokens, preserving inferred versus confirmed evidence.
- Read-only ancestor metadata check holds analyzer CSV publication on conflicting/unknown inherited temperatures, unreadable notes and ambiguous shared conditions. No note or analyzer modifications.
- Completion warning and scrollable exact JSON/held-file paths. No automatic release of held files.
- Protocol and measurement command sequence unchanged from 1.1.3. Existing releases and data preserved.

# 1.1.1 — 2026-10-13

## 1.2.0

- Flat wide CSV per repeat/branch directly under device/date/원본, separate means, exact family order, QC/provenance and raw preservation.
- Existing analyzer 1.9.1 unchanged: real 41-family/3321-point full pipeline and multi-Vd IdVg validation passed.
- Includes the Device ID improvements below. See README_V120_KO.md and QA_REPORT_V120.md.

## 1.1.4 — source-only work incorporated into 1.2.0

- Explain Device ID validation in Korean, highlight the field and select the Device tab on error. Preserve input instead of silently stripping whitespace.
- 20 dedicated validation/UI checks, no instrument connection. Keep the deployed v1.1.3 EXE, launcher, ZIP and user data unchanged.
- See DEVICE_ID_AND_USER_RUN_KO.md for the read-only review of the user's successful v1.1.3 Spot run. Agent hardware testing remains on HOLD.

## 1.1.3

- Replace universal CRLF assumption with response-specific NUL framing; distinguish empty completion from literal ACK and reject empty query data.
- Preserve raw hex before payload validation, failure exchange and best-effort cleanup journals; retain UNKNOWN physical output state.
- Diagnose three actual US-stage failures read-only; no DV was sent. Rejected historical wire bytes are unavailable.
- Separate dist_v113 build, 56 distinct unit tests, 67 GUI checks and packaged loopback-only self-test. Preserve running v1.1.2 and all original data.

## 1.1.2

- Spot / Id-Vd / Id-Vg 입력을 분리하고 숨긴 값은 canonical 계획에서 제외.
- 고정 bias 순서 목록과 Decimal 범위 생성, 중복/비정상 값 차단.
- 전체 측정 순서와 family별 정·역방향/반복/총 점 수 미리보기.
- 검증된 큐 초안 JSON 저장/불러오기; 자동 실행 없음.
- 42 회귀 검사, 36 신규 + 26 기존 GUI 검사, 분석기 호환/네 전체 분석 작업 검증.

- 평균 전용 평균_분석입력에 분석기 호환 파생 곡선 CSV 추가. 개별 반복과 입력 root 분리.
- family/branch/grid 유지, 유효 수·표본 표준편차·제외 상태·기여 반복 경로/해시를 보존. 평균을 단일 실제 취득으로 표현하지 않음.
- 파일명에 device ID와 RAW/AGGREGATE_MEAN 명시, 반복 export 폴더 축약. 실제 분석기 전체 시험에서 드러난 장치 오인/긴 출력 경로 문제 대응.
- IdVd/IdVg 개별 및 평균의 실제 분석기 전체 처리·HTML 보고서 검증 추가. Spot 피팅 미지원 범위 문서화.
- 기존 1.1.0 ZIP은 보존. 기존 분석기와 실제 장비에는 변경 없음.

# 1.1.0 — 2026-10-13

- 밝은 흰색/회색/청록색 UI, 소자 정보, 실행 큐, 반복별 보기.
- device.md 미리보기/새 파일만 명시적 저장. 기존 파일 보존, 검토 후 바뀌면 재검토.
- 실제 research-automation 1.9.1 reader/metadata로 합성 CSV 호환성 검증. 기존 분석기/연구 데이터 수정 없음.
- 전체 스윕 반복 1–100, 최대 50단계/1,000스윕/100,000점 큐. 전체 검토와 실패 시 후속 중단.
- 개별 raw/metadata/명령 기록, curve/branch별 SI 분석 CSV, unknown 광 보존.
- 실제 PC 시작일/시간대, 자정 정책, 과거 날짜 보정 보존.
- 정상 점 평균·표본 표준편차·n_valid·누락·드리프트 표와 경고. 보간/branch 병합 없음.
- 기존 NUL 프레이밍, unsigned 양수, SP1, 상태 검사, 정리/부분 저장/중복 실행 차단 유지.

실제 장비 검증이나 안전 바이어스 확대를 의미하지 않습니다. 모든 새 테스트는 loopback mock만 사용했습니다.
