# 법령 변경 캘린더

로그인 없이 별도 서브도메인의 루트 `/`에서 제공하는 독립 웹앱이다. 챗봇은 다른 서브도메인의 로그인 서비스로 유지한다.

## Phase 1 구성

```text
공개 캘린더 서브도메인 → 정적 호스팅 → frontend/dist
비공개 챗봇 서브도메인 → 기존 챗봇 서버 → Mac mini 모델 서버
```

두 앱은 소스 진입점, npm 의존성·lock, 빌드 출력과 실행 프로세스가 분리되어 있다. 캘린더는 챗봇 소스·CSS·쿠키·로그인 API·모델 서버를 사용하지 않는다. 챗봇·모델 서버·monitor가 실행되지 않아도 캘린더 UI를 실행할 수 있다. Phase 1에는 Python 런타임이나 실제 조회 API가 없다.

## 개발 및 빌드

Node 버전은 `frontend/.nvmrc`, 패키지 버전은 `frontend/package-lock.json`을 따른다. 챗봇의 `.env`나 인증·모델 설정은 필요하지 않다.

```fish
cd services/law-calendar/frontend
npm ci
npm run dev
```

Vite가 표시하는 로컬 개발 주소의 `/`로 접속한다. 개발 모드에만 합성 예시가 있고 한국 표준시 오늘에 배치된다. 모든 예시는 실제 법령이 아니라고 표시한다.

```fish
npm run typecheck
npm test
npm run build
```

`frontend/dist/` 전체가 독립적인 정적 배포 산출물이다. 별도 캘린더 호스트의 루트에서 제공하면 되며 chatbot의 `dist`와 섞지 않는다. production은 예시 모듈을 제외하고 승인 데이터 연결 전 빈 화면을 제공한다. `?demo=true`로 예시를 켤 수 없다.

## 독립 브라우저 검사

`tests/`는 Vite 개발 서버와 Python 표준 라이브러리 정적 파일 서버만 실행한다. 챗봇·모델 서버 fixture나 Python 서비스 패키지를 import하지 않는다. 아래는 저장소 루트에서 모델 라이브러리 없이 실행하는 명령이다. 먼저 위 frontend를 빌드한다.

```fish
uv run --no-project --python 3.14 --with pytest==9.1.1 --with httpx==0.28.1 --with playwright==1.64.1 playwright install chromium
uv run --no-project --python 3.14 --with pytest==9.1.1 --with httpx==0.28.1 --with playwright==1.64.1 python -m pytest -c pyproject.toml services/law-calendar/tests
```

기존 프로젝트의 기본 `uv run pytest`와 CI에도 이 독립 검사를 포함한다. 일반 챗봇 회귀 테스트는 기존 위치에서 실행한다.

## 후속 API와 배포 경계

Phase 2 조회 API는 별도 calendar 서비스에 추가한다. 브라우저는 calendar origin의 공개 승인 데이터만 읽고 모델·RAG·챗봇 세션 API를 호출하지 않는다. monitor의 수집·검토 결과를 오프라인으로 전달하며 날짜 선택으로 수집이나 LLM 호출이 발생하지 않도록 한다.

공개 호스트명, DNS·TLS·호스팅은 후속 배포 때 확정한다. 캘린더 호스트를 챗봇 서버로 전달하거나 챗봇 인증 쿠키의 Domain을 부모 도메인으로 확장하지 않는다. 배포 서버에서 캘린더 정적 파일과 향후 공개 조회 API만 노출해야 한다. 기존 Mac mini의 로그인·모델 API는 공개하지 않는다.

현재 PR에는 DNS·프록시·운영 설정 변경이나 실제 배포가 없다. 병합은 사용자 명시적 승인 후에만 수행한다.
