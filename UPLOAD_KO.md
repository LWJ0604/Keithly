# GitHub 브라우저 업로드

이 폴더는 **KeithleyLabv1.2.1 소스 저장소용**입니다. 기존 분석기 `hi`와 별개입니다.

1. 소스 ZIP을 풀고 `README.md`가 보이는 폴더를 엽니다.
2. GitHub의 원하는 **KeithleyLab 저장소**에서 Add file → Upload files를 엽니다.
3. 이 폴더 자체가 아니라 **안의 파일과 폴더 전체**를 선택하여 끌어 넣습니다. `keithley_lab/`, `tests/`, `docs/`의 내부 구조는 유지합니다.
4. 저장소 첫 화면에 `README.md`, `keithley_lab/`, `tests/`, `requirements.txt`, `KeithleyLab.spec`가 바로 보이면 맞습니다. `.gitignore`, `.gitattributes`도 포함하세요.

ZIP 자체를 코드 업로드 화면에 넣으면 압축 파일 한 개로 저장될 뿐 자동으로 풀리지 않습니다. 폴더 단위 업로드가 안 되는 브라우저에서는 GitHub Desktop 또는 Git을 사용하세요. 자동 생성된 qa/data/dist/.venv 파일은 업로드하지 마세요. **브라우저 업로드는 .gitignore가 대신 걸러주지 않습니다.** 검증된 이 소스 전용 폴더만 사용하세요.

EXE 배포가 목적이면 기존 Windows ZIP을 **Releases의 asset으로 그대로 첨부**합니다. 소스 ZIP과 실행용 ZIP의 목적이 다릅니다. 여기서는 GitHub에 게시하거나 저장소를 수정하지 않았습니다.
