# 통합 수집기 v1.1 PR-A 검증

2026-11-13 KST, 기준 `d34c74987f5bd29a7c46387e9c31414cf943e95c`. 개발 트리의 임시 Git 사본·DB와 대역만 사용했다. [전체 계획](../plans/source-hub-erp-v1.1.md) · [기존 동작 기준선](2026-11-13-source-hub-baseline.md)

## 표 A. 원천과 등록 상태

| 범위 | 등록 수 | 이번 실측 |
| --- | ---: | --- |
| 국가법령정보센터·기존 법령 및 ERP 입력 | 5 | 기존 법령·행정규칙·ERP 사본 설정 3개가 dry-run 조건을 충족했다. 실제 수집 0회. |
| 국회 | 2 | 기존 법률안·입법예고 소스 등록. 기본 설정 OFF. 실제 수집 0회. |
| 고용노동부 | 8 | 공식 RSS 후보 3개와 게시판 범위 5개. 외부 확인 전 비활성. |
| 재정경제부 | 7 | RSS 채널·게시판 후보. 외부 확인 전 비활성. |
| 국세청 | 9 | TAX-02를 포함한 게시판 범위. 외부 확인 전 비활성. |
| 홈택스 | 4 | 공개 메타데이터 후보. 외부 확인 전 비활성. |

독립 환경 설치는 잠금 파일의 패키지 17개를 설치했다. `validate`는 `valid=true`, `config_version=0`을 반환했다. `dry-run`은 35개 수집원 중 미검증 30개를 보고했고 DB를 생성하지 않았다. wheel에서 초기 YAML과 관리자 HTML·JS·CSS가 포함된 것을 확인했다.

## 표 B. 기능 검증

| 항목 | 결과 |
| --- | --- |
| 챗봇 조문 자동 갱신 계약 | 대역 실행에서 국회 소스가 일부 실패한 뒤에도 `collect → calendar sync → lawbot-articles-update → summarize` 순서를 확인했다. 갱신 명령은 같은 Git 사본 경로, 보고서 경로, `--restart` 인자를 받았다. |
| 등록부·관리자 | 단위/API 14건 통과. 그룹·기관·법령 우선순위, 저장과 감사, 검증 실패, 다른 DB 쓰기 차단, 인증·Origin, 버전 충돌, 재실행 멱등과 실패 후 마지막 성공 시각을 확인했다. |
| 관리자 브라우저 | 초기 대역에서 기관 필터 접근성 이름 오류를 재현해 명시적 라벨을 추가했다. 최종 대역에서 관리자 로그인·ERP OFF 저장·기관 필터·감사 기록을 확인했다. |
| 전체 기본 pytest | `1886 passed, 13 deselected in 148.13s`. 단위, 모델 대역 계약, 통합, 브라우저 대역 E2E 포함. 샌드박스에서 별도로 실행한 수집 목록 집계는 MLX import 중 중단됐다. 전체 기본 실행은 Chromium·루프백·MLX를 사용할 수 있는 환경에서 통과했다. |
| 프런트엔드 | 챗봇 Vitest 55건, 캘린더 Vitest 26건. 두 서비스 타입 검사와 빌드 통과. 챗봇 Vite의 기존 설정 경고가 출력됐다. |
| 정적 검사 | Ruff·포맷, 루트와 독립 서비스 Pyright, 캘린더 스키마, compileall, JavaScript 문법, 잠금 파일 검사 통과. |
| 실제 모델 E2E·모델 스모크·품질 평가 | 실행 안 함. |
| 실제 사이트·첨부·이메일·Telegram | 실행 안 함. |

재현 명령은 fish에서 실행할 수 있다. 이번 전체 검증은 이미 설치된 루트 환경에 새 패키지의 소스 경로를 전달했다. CI는 편집 가능 패키지를 설치한다.

```fish
env PYTHONPATH=services/source-hub/src uv --no-cache run --no-sync pytest -q --tb=short
uv --no-cache run --no-sync ruff check
uv --no-cache run --no-sync ruff format --check
uv --no-cache run --no-sync pyright
uv --no-cache run --no-sync pyright services/source-hub/src services/law-calendar/src services/erp-updates/src
uv --no-cache run --no-sync python -m law_calendar.schema --check
uv --no-cache run --no-sync python -m compileall -q services packages tools
uv --no-cache lock --check --offline --project services/source-hub
node --check services/source-hub/src/source_hub/web/admin.js
for frontend in services/chatbot/frontend services/law-calendar/frontend
    pushd $frontend
    npm run typecheck; and npm test; and npm run build
    popd
end
```

## 표 C·D. 후속 검증

통합 실행과 실제 ON/OFF 요청 수, 외부 원천별 HTTP·첨부, 운영 백업·이행, 일일 전송, 현행·예정판과 5기관 드로어는 후속 PR에서 검증한다. PR-A의 관리자 실행 이력은 합성 결과로 검사했다. 운영 모델 호출·운영 트리 변경·스케줄 변경은 0회다.
