# 법령 변경 캘린더 Phase 1 UI 검증

- 작성일: 2026-11-05 (UTC)
- 브랜치: `codex/law-calendar-phase0-20261105`
- 기준 커밋: `0030cda` + 이 기록과 함께 검증한 Phase 1 변경분
- 계획: [LAW_CALENDAR_PLAN.md](../plans/LAW_CALENDAR_PLAN.md)
- 범위: UI 목업. DB·공개 API·입법예고 수집·실제 법적 효력 검증은 구현하지 않았다.
- 실제 모델 호출, 모델 가중치 다운로드, 학습·평가셋 변경, 운영 배포·서비스 재시작은 수행하지 않았다.

## 구현과 확인 방식

`/law-calendar`는 로그인 확인을 호출하지 않고 독립 렌더링한다. `/`의 챗봇 로그인과 질문 스트리밍은 기존 코드를 유지한다. 날짜 선택·월 이동·오늘·두 목록·더 보기·상세 비교·공식 사이트 안내·빈 상태·로딩·오류·갱신 지연을 구현했다. 개발용 합성 예시는 별도 모듈이며 production에서 제거한다. 실제 법령명과 원문을 합성 문구로 대체해 공개하지 않는다.

날짜 유틸 테스트를 먼저 작성해 미구현 동작 3건의 실패를 확인한 뒤 구현했다. 페이지 테스트도 달력·목록·상태가 없는 초기 구현에서 5건 실패를 확인했다. 브라우저에서 StrictMode의 상세창 즉시 닫힘과 Tab 경계 이탈을 발견해 수정했고, 실제 Chromium에서 회귀 검증했다.

## 환경

- 분리된 Linux 개발 체크아웃. Node 26, Python 3.14.8, Playwright 1.64.1 Chromium.
- npm은 기존 package-lock.json을 그대로 사용. package.json·lock 파일 변경 없음.
- uv workspace의 macOS 전용 잠금 설정 때문에 별도 테스트 venv에 로컬 패키지를 editable/no-deps로 설치하고, 사용한 공통 패키지 버전은 uv.lock에 맞췄다. torch는 같은 2.14.1의 Linux CPU 빌드, MLX는 0.32.4 CPU backend를 사용했다. macOS 운영 환경 검증으로 표현하지 않는다.
- HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE을 설정했다. 기존 기본 테스트의 작은 무작위 모델·가짜 서버만 사용했으며 운영 모델 서버는 호출하지 않았다.
- 최초 설치의 cache 쓰기 경로 문제를 전용 /tmp 경로로 해결했다. 이후 GPU 라이브러리 설치로 디스크가 부족해 전체 검사를 중단했고, 직접 만든 GPU 패키지와 전용 cache를 제거한 후 CPU 환경으로 복구했다. 이 중단 결과를 통과로 세지 않았다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| frontend `npm run typecheck` | 통과 |
| frontend `npm test` | 32개 통과 (기존 챗봇 테스트 포함) |
| frontend `npm run build` | 통과, 캘린더 코드·CSS 별도 chunk |
| `pytest services/chatbot/tests/test_e2e_law_calendar.py -q` | 대역 E2E 4개 통과 |
| `ruff check` / `ruff format --check` | 통과 |
| `pyright` | 0 errors / 0 warnings |
| `python -m compileall -q services packages tools` | 통과 |
| 전체 기본 `pytest` | 최종 753 통과 / 기본 제외 12개 (99.64초). 단위·계약·통합·대역 E2E 포함 |
| 실제 모델 E2E / 모델 스모크 / 품질 평가 | 실행 안 함 |
| 운영 반영 / 일반 공개 배포 | 수행 안 함 |

앞선 전체 검사(752 통과 / 1 실패)에서 변경하지 않은 `test_two_streams_interleave_within_limit`의 종료 직후 `s.pending() == 0` 검사가 2를 받아 실패했다. pending은 코드상 대략값이며 스레드가 종료 알림을 전달한 뒤 집계가 갱신되는 순서라 시점 의존 가능성이 있다. 직접 관련 없는 모델 서버 코드나 테스트는 수정하지 않았다. 동일 테스트는 네트워크 권한 없는 샌드박스 실행에서 타임아웃이 있었으나, 로컬 소켓 권한을 준 단독 실행과 기준 커밋의 별도 worktree 실행에서는 통과했다. 최종 전체 재실행은 753개 모두 통과했다. 최초 실패를 숨기지 않으며 스케줄러 집계 검사에 대한 간헐 실패 가능성은 후속 확인 사항으로 남긴다.

## 사용자 흐름별 확인

| ID | 확인 결과 |
| --- | --- |
| UI-01 | 월 이동·오늘·날짜 선택, KST 경계·윤년·월말, 키보드 월 경계 이동 통과 |
| UI-02 | 같은 날 입법예고와 시행 조문을 별도 건수와 목록으로 표시. 모바일도 색과 함께 유형 텍스트 유지 |
| UI-03 | 이벤트 없는 날짜의 두 목록 빈 상태 통과. production은 승인 데이터 미연결 안내 표시 |
| UI-04 | 상세 초기 포커스·Tab/Shift+Tab 경계·Escape·닫기 후 원래 항목 포커스 복귀 통과 |
| 표시 | 원문 줄바꿈·유니코드 보존, 원문 HTML 실행 방지, 비교 불가·신설·삭제 상태 구현 |
| 출처 | 합성 예시에 공식 개별 원문이 없음을 명시, 수집·검증 미확인 표시, 공식 사이트 링크 제공 |
| 반응형 | Chromium에서 모바일/데스크톱 목록 및 Before/After 배치 확인, 가로 넘침 없음. 수동 화면 확인도 수행 |
| 배포 보호 | production JS에 합성 고유 마커와 가상 원문 부재 확인, `?demo=true`도 예시를 노출하지 않음 |
| 챗봇 회귀 | 비로그인 캘린더 진입 시 API 요청 0건, 챗봇 링크 이동 후 로그인·대역 답변 완료, 보호 API 401 및 미등록 API 404 유지 |

DATA/PUB/OPS의 실제 수집·승인·시행일 테스트는 이 UI 단계의 통과 항목이 아니다. 공개 API·승인·실제 원문 연결은 Phase 2 이후에 검증한다. 고지문 법률 검토와 재이용 조건 검토도 완료하지 않았다.

## 실행 명령

저장소 지원 환경(macOS)에서는 기존 문서대로 아래 명령을 사용한다. 이 실행에서는 별도 venv의 동일 도구를 사용했고, 로컬 테스트 서버용 네트워크 권한과 위 CPU 환경 차이가 있다.

```fish
cd services/chatbot/frontend
npm ci
npm run typecheck
npm test
npm run build
cd ../../..
uv run pytest
uv run ruff check
uv run ruff format --check
uv run pyright
uv run python -m compileall -q services packages tools
```

배포 절차: 후속 API 연결과 검토를 끝낸 뒤 사용자가 진행 중인 모델 학습 작업과 함께 반영한다. UI 소스 변경이므로 그때 frontend 빌드를 갱신해야 한다. 이번 작업에서 모델 서버 재시작이나 데이터 전환을 실행하지 않는다. PR 병합은 해당 PR의 사용자 명시적 승인이 있어야 한다.
