# ERP Gmail 알림 검증

2026-11-13 KST. 기준 `bd6b110`. [구현 계획](../archives/erp-gmail-digest.md)

| 항목 | 결과 |
| --- | --- |
| 아침 요약 | 가짜 Qwen 호출 1회에서 기존 Markdown과 통합 실행 ID별 구조화 결과가 나왔다. 조문과 별표의 원천 이벤트 해시를 기록했다. 캘린더의 현재 공개 판과 일치하는 승인 문구만 재사용했다. 판 불일치·부분 승인·비활성·철회에서는 승인된 공개 필드와 링크로 대체했다. |
| 공개 스냅샷과 메일 | 스냅샷 해시가 한글 HTML·텍스트 MIME의 `X-ERP-Snapshot`과 일치했다. 제목·게시일·링크만 있는 공지는 ‘내용 미확인’으로 남았다. 드로어에만 있는 과거 글은 당일 신규에서 빠졌다. 첨부 해시가 바뀌면 첨부 변경으로 표시했다. |
| Gmail 대역 | 공식 OAuth 갱신과 발송 API 요청 형식, 수락 ID, 429 재시도, 영구 거절과 수락 불명 상태를 확인했다. 재시도 MIME이 원본과 바이트 단위로 같았다. 발송 중단 후 수락 불명 재발송 0건, 정상 재실행 중복 0건, 스냅샷 교체 후 이전 판 발송 0건을 확인했다. |
| 실제 Qwen 합성 자료 | 2026-11-13 18:57 KST경 Qwen3.8-27B 생성 요청 1건, 4.39초, 재시도 0건. 요청 전 기존 상주 모델과 동시 생성 상한 1, 루프백 연결 0건을 확인했다. 생성 전후 모델 서버 커밋 `69a2dd338ec289176046ab343f6f27bb9eb6fd78`, 모델 revision `sha256:3117f3d1ce819e8b`, 프롬프트 해시 `8b7f25e681455ec381d5ae7d27d33d3527d4c25036f0c44aba247ab74a8fe469`가 일치했다. 설정은 temperature 0, 출력 최대 800토큰, 대기 최대 300초였다. ERP 공개 발췌 1건과 메타데이터 전용 공지 1건을 사용했다. 조문 DB와 챗봇 검색·프롬프트는 이 생성에 쓰이지 않았다. |
| 기본 전체 | 첫 실행에서 기존 독립 `daily`의 조문 갱신 순서 검사 1건이 실패했다. 독립 호출의 인자 계약을 복원하고 수신 주소 파싱 오류를 보완한 뒤 최종 전체 실행에서 `1974 passed, 13 deselected in 154.08s`. 단위·계약·통합·브라우저 대역을 포함한다. Gmail·캘린더 집중 검사 `14 passed`. |
| PR #139 리뷰 후속 | 요약 파일이 없어도 공개 법령 변경이 메일에 들어오는 경로를 캘린더 DB와 대체 본문으로 검증했다. 이전에 가져온 뒤 당일 승인한 변경, 날짜 경계·당일 재발행, 비활성·부분 승인·철회를 검증했다. 집중 검사 25건과 기본 전체 `1975 passed, 13 deselected in 154.56s`. 새 실제 모델 호출과 Gmail 실발송은 각각 0회다. |
| 프런트엔드 | 챗봇 Vitest 55건과 캘린더 29건, 양쪽 typecheck와 빌드 통과. |
| 정적 검사 | Ruff 검사·포맷, Pyright, compileall, 캘린더 스키마, uv 잠금 파일 4개 검사 통과. |
| 실발송·운영 | Gmail 실발송, 운영 전환, Telegram, 챗봇 실제 모델 E2E, 모델 스모크와 품질 평가는 실행하지 않았다. |

합성 자료 입력은 `가상 시험 자료입니다. 2026년 12월 지급분부터 제출 서식에 사업장 식별란을 추가합니다. 제출 기한은 그대로입니다.`였다. Qwen은 `erp:fixture-form`에 `2026년 12월 지급분부터 제출 서식에 사업장 식별란을 추가합니다.`를 반환했다. 문장은 승인 입력에 그대로 있었고, 메타데이터만 있는 가상 기관 공지의 날짜·공식 링크·‘내용 미확인’이 메일에 남았다.

```fish
uv run pytest -q services/source-hub/tests/test_digest.py services/source-hub/tests/test_daily.py services/law-calendar/tests/test_erp_capture_summary.py services/monitor/tests/test_mon_summary.py services/monitor/tests/test_mon_checks.py
uv run pytest -q --tb=short
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pyright services/source-hub/src services/law-calendar/src services/erp-updates/src
```
