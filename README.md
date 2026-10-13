# KeithleyLabv1.2.1

Windows용 legacy Keithley 4200-SCS / KXCI 측정 GUI 소스입니다. Spot, Id–Vd, Id–Vg, family/반복 큐, 실시간 플롯, 취소·정리, CSV/JSON 저장을 제공합니다.

## 실행·테스트·빌드

**경로 제한:** 이 버전의 긴 중첩 파일 경로는 일부 Windows 환경에서 저장 실패를 일으킵니다. 드라이브 루트 가까운 `KLab` 같은 짧은 폴더에 풀고 저장 폴더와 Device ID도 짧게 정하세요. 깊은 폴더에서의 테스트/EXE 내보내기는 이번 검증에서 실패했고, 짧은 경로로 분리해 재검증했습니다. 긴 경로 지원을 수정한 버전은 아닙니다.

검증 환경은 Windows와 Python 3.7.8 (Tk 포함)입니다. 다른 Python 버전에서의 실행/빌드는 이번 배포에서 검증하지 않았습니다. Python 3.7을 쓰는 기존 환경과 맞춘 의존성 핀이며, Python 자체는 이 소스에 포함하지 않습니다.

1. `setup_windows.cmd`: 독립 `.venv`를 만들고 PyYAML을 설치합니다. 의존성 다운로드에는 인터넷이 필요합니다.
2. `run_windows.cmd`: 앱을 simulation 모드로 시작합니다.
3. `test_windows.cmd`: 모의 TCP 서버로 테스트합니다. 실제 IP 접속은 테스트에서 차단합니다.
4. `build_windows.cmd`: 빌드 의존성을 설치한 뒤 `dist/KeithleyLab/KeithleyLab.exe`를 만듭니다. 배포하려면 그 폴더 전체를 함께 전달하세요.

이미 준비된 환경에서는 `python launch.py`, `python -m unittest discover -s tests -v`를 사용할 수 있습니다. `python launch.py --self-test`는 GUI를 포함한 모의 측정 검사입니다. 실제 측정 CLI/자동 예약/재시작 후 자동 재개 기능은 없습니다.

## 장비 연결과 한계

항상 simulation으로 시작하며 자동 연결하지 않습니다. 공개 소스의 기본 주소 `192.0.2.1`은 **문서용 예제 주소**입니다. real 모드를 사용하려면 현장에서 확인한 장비 IPv4를 직접 입력하고, 매 실행마다 배선·채널·compliance·전압 한계를 검토한 후 Start를 눌러야 합니다. 이 저장소의 기본값이나 예제는 새 소자의 안전 범위를 증명하지 않습니다.

대상 firmware는 `KI4200 V8.2-SP4`입니다. 전송은 ASCII + NUL, 응답은 NUL 단위 완전 프레임으로 처리합니다. bias 명령의 자동 재시도나 재연결을 하지 않습니다. ACK는 실제 출력 OFF의 증거가 아니며 연결 손실 때 현장 확인이 필요합니다. [프로토콜 근거](docs/PROTOCOL.md).

## 저장과 분석

기본 저장은 앱 폴더 아래이며 simulation 데이터는 별도 `simulation_data/`에 들어갑니다. 실제 저장 폴더는 사용자가 선택합니다. pointwise 원본·설정·로그·부분 측정을 보존하며, CSV 숫자는 V/A입니다. 세 채널은 순차 측정됩니다. 소스에 로그는 없지만 실행하면 호스트 이름·장비 주소·선택한 경로 등이 로컬 provenance에 기록될 수 있으므로 생성 결과를 공개 저장소에 올리지 마세요.

기존 분석기 1.9.1용 wide CSV는 family 열을 한 파일에 모읍니다. 요청한 PC point delay와 명시적으로 confirmed인 light/dark는 파일명에서 **inferred**로 전달됩니다. 온도·host hold·타임존·확인 이력은 JSON에 남으며 기존 분석기가 자동으로 읽지 않습니다. 상위 device.md의 공통 온도가 다른 실행에 잘못 상속될 수 있습니다. 내보내기 시 읽기 전용 검사에서 충돌하면 `.csv.review_required`로 보류하고 raw/JSON을 보존합니다. 사용자 메모나 분석기를 수정하지 않습니다. 메모 이동·편집 후에는 다시 검토해야 합니다. [CSV 스키마](docs/WIDE_SCHEMA.md).

## 범위와 검증

소스 테스트와 GUI self-test는 모의 장비만 사용했습니다. 장비 실측, 외부 분석기 설치, 실제 데이터, 게시 자동화는 이 소스 검증에 포함하지 않습니다. [독립 검증 요약](docs/VALIDATION.md).

이 소스 전용 구성을 위해 바꾼 것은 공개 예제 IP, 표준 PyYAML 의존성, 깨끗한 폴더에서의 테스트 초기화, 빌드/문서 구성입니다. 측정 프로토콜과 실행 엔진의 나머지 동작은 v1.2.1과 같습니다. 원본 측정 앱과 기존 Windows ZIP은 변경하지 않았습니다.

프로젝트 전체에 대한 재배포 라이선스는 별도로 선택되지 않았습니다. 공개 저장소 업로드는 라이선스 선택과 별개이며, 오픈소스 사용 허가가 자동으로 추가되지는 않습니다. 의존성은 각 라이선스를 따릅니다.
