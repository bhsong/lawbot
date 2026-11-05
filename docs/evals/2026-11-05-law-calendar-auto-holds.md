# 시행일 자동 처리·보류 목록 다이얼로그 검증

- 기준: PR #70 head `b534c31`, 새 브랜치 `codex/law-calendar-auto-holds-20261105`. 사용자가 건별 수동 승인 정책을 자동 처리·예외 보류·표본 확인으로 변경했다. PR #70이 미병합인 상태에서 시작했으며 그 브랜치를 기반으로 후속 변경을 분리한다.
- import는 저장된 monitor의 공식 출처 링크·공포일·최신 부칙 및 조문 전체 시행 메타데이터를 판정한다. 명시적인 날짜, 공포한 날 시행, 부칙과 충돌하지 않는 전체 조문 시행 필드를 지원한다. 상대 기간 계산·부분 조문 시행·위임 표현·복잡하거나 지원하지 않는 부칙·여러 신규 부칙·날짜 상충은 보류한다.
- 자동 공개는 날짜만 확인하며 comparable=false를 유지한다. 화면 상태는 '시행일 자동 확인 · 비교 미확인'이다. Git 버전을 실제 시행 전후 유효 원문으로 자동 인증하지 않는다. 요약은 조문명과 신설/삭제/변경을 나타내는 규칙 기반 문구다.
- 자동 판정의 규칙 버전·snapshot·조문별 날짜·판정 근거를 감사 이력에 기록한다. 원천 정정은 새 snapshot으로 재판정하며 명시적 반려·철회는 자동으로 되살리지 않는다. 같은 입력은 무변경이다.
- SQLite v3는 보류 이유와 공개용 view를 추가한다. v1/v2 DB는 .pre-v3.backup 사본 후 명시적 마이그레이션이 필요하다. 공개 서버는 DB를 생성·변경하지 않는다.
- 보류 API는 자동 판정기가 등록한 현재 snapshot의 대기 조문만 조회한다. 법령명·조문·사유·공식 출처·수집 시각만 반환하며 일반 미승인 행, 반려·철회·승인·과거 snapshot, 원문·내부 경로·오류·관리 정보를 노출하지 않는다. 달력 집계와 섞지 않는다.
- 상단 '보류 목록' 버튼으로 전체 날짜의 항목을 보는 읽기 전용 dialog를 제공한다. 로딩·0건·실패 재시도·페이지 이동, 모바일과 데스크톱, Escape·Tab 포커스와 닫은 뒤 복귀를 지원한다. 수정·확정 버튼과 보류 전용 쓰기 API는 없다. 개발 예시는 production 자산에 포함하지 않는다.

## 검사

- 캘린더 Python 검사 68개 통과: 기존 API 20·워크플로 23, 자동 판정·보류 API·마이그레이션 17, 독립 브라우저 E2E 8개. 확정 날짜·공포일 시행·조문별 다른 시행일, 위임/복잡/누락/상충/부분 시행, 자동 공개와 보류 이동, 철회·반려 후 정정, snapshot·정보 제한·페이지 검사를 포함한다.
- frontend: 캘린더 typecheck·Vitest 16개·production build, 기존 챗봇 typecheck·Vitest 19개·build 통과. 신규 dialog 단위 검사는 목록·페이지·0건·실패 재시도와 편집 기능 부재를 확인한다. 실제 native modal 동작은 Chromium E2E에서 검증한다.
- 전체 기본 pytest 최종: **817 통과 / 기본 제외 12개, 99.61초**. 1차 817 통과(135.94초) 후 미지원 부칙 표현 보류를 보완하고 전체를 재실행했다. 단위·계약·통합·대역 E2E를 포함하며 실제 모델 검사는 제외한다.
- ruff check/format, 캘린더 pyright, 루트 pyright, compileall, API 타입 생성 최신성, 두 lock check 통과. 챗봇·모델 서버·기존 monitor·루트 README/설정/lock·CI는 기준 대비 변경 0건을 재확인했다. macOS CI는 PR의 최종 head로 별도 확인한다.
- 실제 데스크톱 Chromium 1440px 개발 화면에서 캘린더·보류 dialog·Before/After 3장을 캡처하고 확인했다. 사용자 요청 결과물은 저장소 밖 작업 공간에 보관하고 커밋하지 않는다. 합성 예시 배지를 유지했고 production·실제 법령 화면으로 제시하지 않았다.

TDD에서 미구현 모듈/화면 부재와 여러 부칙·지원하지 않는 위임 표현을 메타데이터만으로 확정하던 경우를 재현하고 수정했다. jsdom은 showModal을 제공하지 않아 단위 검사에만 모달 API 대역을 두었고 실제 포커스 검증은 브라우저에서 수행했다. 중간 실행이 사용자 메시지로 중단된 검사는 통과로 세지 않고 다시 실행했다.

## 실행과 한계

두 frontend에서 각각 npm run typecheck / npm test / npm run build 후 루트 기본 uv run pytest를 실행한다. uv run ruff check, uv run ruff format --check, uv run pyright, uv run pyright services/law-calendar/src, uv run python -m law_calendar.schema --check, uv run python -m compileall -q services packages tools, 두 프로젝트 uv lock --check를 함께 사용한다. Linux 로컬 검증은 기존 임시 Python 3.14.8·Node 26 환경과 CPU torch/MLX를 사용하며 실제 모델 호출은 하지 않는다.

외부 법령 수집·입법예고 실수집·import 스케줄 등록·운영 DB/배포·서비스 재시작·실제 모델 E2E/스모크/품질 평가는 미실행이다. 이번 자동화 범위는 저장된 입력의 판정이며 운영 수집에서 브라우저까지 자동 실행되는 스케줄을 설치한 것은 아니다. 공식 시행 전후 버전 자동 결정, 보류 전용 CLI 확정 흐름과 웹 수정 기능은 후속 범위다. 기존 챗봇·모델 서버·monitor 및 다른 세션 계획서는 변경하지 않는다.
