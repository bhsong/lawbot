# 열린국회정보 수집 및 법령 캘린더 연동 계획

- 작성일 2026-11-05 · 개발 구현과 검증 완료, 운영 반영 대기
- 조사 기준 `71e3739` (PR #72 병합 커밋). 개발 worktree의 기존 검증 기록을 보존했다.
- 범위는 국회 법률안과 국회 입법예고다. 유동 IP 제약이 있는 국민참여입법센터는 범위에서 제외한다.

## 1. 원천과 확인된 계약

아래 코드는 `https://open.assembly.go.kr/portal/openapi/`에 붙인다. 세 API 모두 공식 다운로드 명세와 개발 Monitor의 `LAW_ASSEMBLY_KEY`를 사용한 실제 JSON 응답을 대조했다. 표본, 호출 수, 판정 기준은 [검증 가이드](../evals/2026-11-05-assembly-api-validation-guide.md)에 기록했다.

| API | 요청 | 확인한 응답과 용도 |
| --- | --- | --- |
| 법률안 심사 및 처리(의안검색) `TVBPMBILL11` | `AGE` 필수. `BILL_ID`, `BILL_NO`, `BILL_NAME`, `PROPOSER_KIND` 등 선택 검색 | `BILL_ID`, `BILL_NO`, `AGE`, `BILL_NAME`, `PROPOSER_KIND`, `CURR_COMMITTEE`/ `CURR_COMMITTEE_ID`, `PROPOSE_DT`, `COMMITTEE_DT`, `COMMITTEE_PROC_DT`, `CMT_PRESENT_DT`/ `CMT_PROC_DT`/ `CMT_PROC_RESULT_CD`, `LAW_SUBMIT_DT`/ `LAW_PRESENT_DT`/ `LAW_PROC_DT`/ `LAW_PROC_RESULT_CD`, `PROC_DT`, `PROC_RESULT_CD`, `PASS_GUBUN`, `LINK_URL`. 의원·정부·위원장 및 계류·처리 행을 실응답에서 확인했다. [공식 명세](https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/O4K6HM0012064I15889) |
| 진행중 입법예고 `nknalejkafmvgzmpt` | `BILL_ID`, `BILL_NO`, `BILL_NAME`, `NOTI_ED_DT` 등 선택 검색 | `BILL_ID`, `BILL_NO`, `AGE`, `BILL_NAME`, `PROPOSER_KIND_CD`, `CURR_COMMITTEE`/ `CURR_COMMITTEE_ID`, `NOTI_ED_DT`(게시종료일), `LINK_URL`. 진행 상태는 이 목록의 수록 여부에서 얻는다. [공식 명세](https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OXJ0OE001002XA11874) |
| 종료된 입법예고 `nohgwtzsamojdozky` | `AGE` 필수. `BILL_ID`, `BILL_NO`, `BILL_NAME` 등 선택 검색 | 진행 목록과 같은 핵심 필드. 종료 상태는 이 목록에서 확인한다. 시작·종료를 의안의 심사 상태와 독립적으로 다룬다. [공식 명세](https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OF8AJV000972OP11430) |

국회의원 발의법률안 `nzmimeepazxkubdpn`, 계류의안 `nwbqublzajtcqpdae`, 의안별 조회 `ALLBILL`도 대조했다. 일일 의안 수집에는 의원·정부·위원장과 계류·처리를 한 목록에서 읽을 수 있는 `TVBPMBILL11`을 선택한다. [열린국회정보 API 목록](https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OOBAOA001213RL17443)

- 공통 요청은 `KEY`, `Type=json`, `pIndex`(1부터), `pSize`다. 정상 응답은 서비스 코드 아래 `head`의 `list_total_count` 및 `RESULT.CODE=INFO-000`과 `row`를 반환했다. 결과 없음은 HTTP 200의 최상위 `RESULT.CODE=INFO-200`이었다. JSON 응답의 Content-Type이 `text/html`인 경우도 관측했다.
- 세 명세의 기본 페이지 크기는 100, 최대 1,000이다. 실키로 1,000을 요청해 정상 응답을 받았고 1,001 요청은 HTTP 200의 `ERROR-336`이었다. 명세에는 요청 제한 횟수 200,000과 일별 초과 코드 `ERROR-337`이 적혀 있다. 키별 집계 범위와 리셋 시각은 확인되지 않아, 도입 전 계정 정책을 확인하고 자체 실행 상한을 둔다. 키 없는 샘플과 실제 키의 페이지 제한은 다르다. [공식 개발 가이드](https://open.assembly.go.kr/portal/openapi/openApiDevPage.do)
- 두 예고 API의 날짜 필드는 게시종료일 하나다. `LINK_URL`이 가리키는 국회입법예고 공식 상세 페이지에서 진행 1건과 종료 1건의 `입법예고기간`을 확인했고, 두 종료일 모두 API와 일치했다. 예고시작일은 해당 페이지의 명시적 기간을 검증할 때만 보강하는 안을 제안한다. 별도 의견제출 기한은 명시된 구조화 필드가 확인되기 전까지 비워 둔다. [진행 원문](https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaOngoing/view.do?lgsltPaId=PRC_0C6D31F234114D9AA6602AD25614F6), [종료 원문](https://pal.assembly.go.kr/napal/lgsltpa/lgsltpaDone/view.do?lgsltPaId=PRC_398B1FB303674C00B2F4340D2F8185)
- 공식 목록은 세 데이터셋의 이용 조건을 출처표시로 안내한다. 공개 카드에 출처명과 원문 링크를 표시하고 검토 기록에서 API 응답과 상세 원문을 구분한다. [공식 API 목록](https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OOBAOA001213RL17443)

## 2. 현재 연결

1. [Monitor CLI](../../services/monitor/src/law_monitor/cli.py)의 `daily`는 실행 잠금 안에서 `collect → checks → summarize`를 수행한다. `legalize_law`와 `legalize_admrule`은 Git 변경을 찾고 법제처로 원문·첨부를 보강한다. [실행기](../../services/monitor/src/law_monitor/app.py)가 이벤트 파일을 쓴 뒤 `items`·이벤트 등록·실행 결과를 SQLite에 커밋한다.
2. [targets.yaml](../../services/monitor/config/targets.yaml)의 첫 점검이 독립 환경에서 `python -m law_calendar.sync`를 호출한다. [sync](../../services/law-calendar/src/law_calendar/sync.py)는 Monitor DB를 읽기 전용으로 열고 최신 정상 실행에서 `legalize_law`의 성공을 확인한 뒤 등록된 법령 이벤트만 가져온다. 요약용 `processed_at`과 독립적으로 재시도한다. 다음 점검은 챗봇 조문 자동 갱신이다.
3. [캘린더 DB](../../services/law-calendar/src/law_calendar/db.py)는 v3이며 `change_events.kind`에 `notice`가 있다. 공개 뷰는 승인된 이벤트와 조문 행의 조인을 전제로 한다. 기존 [import](../../services/law-calendar/src/law_calendar/ingest.py), [시행일 자동 등록](../../services/law-calendar/src/law_calendar/automation.py), [검토 CLI](../../services/law-calendar/src/law_calendar/review.py)는 모두 시행 조문 중심이다. 공개 API와 React에는 입법예고 목록과 월별 건수가 있지만 운영용 예고를 넣는 어댑터는 없다. [캘린더 사용법](../../services/law-calendar/README.md)

## 3. Monitor 수집 및 이벤트

- `assembly_bill`과 `assembly_notice`를 독립 수집기로 등록한다. 설정은 기본 비활성으로 두고, 초기 범위는 설정에 명시한 제22대다. 의안과 종료 목록은 해당 `AGE` 전체 페이지를 확인한다. 진행 목록은 전체를 받아 `AGE`를 확인한다. 다음 대수로 전환할 때는 추적 중인 이전 대수와의 중첩 기간 및 요청 예산을 정한 뒤 설정을 변경한다.
- `prelegislation.keywords`의 문자열을 제목과 함께 유니코드 NFKC·공백 정규화한 뒤 부분 일치시킨다. 키워드별 서버 검색 대신 세 목록을 각각 한 번 순회해 동일한 필터를 적용하고, 발의일 이후의 심사 변경도 다시 본다. 설정 변화는 기준선 변경으로 기록한다.
- 페이지당 최대 1,000행으로 읽고 최초/후속 총건수, 페이지의 순서·고유 행 수, 필수 식별자와 응답 구조를 검증한다. 총건수 변동, 반복 페이지, 내용이 충돌하는 동일 식별자 또는 요청 예산 초과 시 해당 수집기의 기존 기준선과 이벤트를 보존한다. 페이지 간 변경을 완전히 막는 서버 스냅샷 계약은 확인되지 않았으므로 다음 실행에서 전 범위를 재대조한다.
- 의안은 `BILL_ID`가 식별자다. 최초 관측 후 `assembly_bill_new`, 위원회·심사일·결과·분류 변경은 `assembly_bill_changed`로 기록한다. 원천이 제공한 날짜와 진행 상태를 저장하며, 날짜가 없는 상태 변경도 Monitor에서 추적한다.
- 예고는 공식 `LINK_URL`의 `lgsltPaId`를 게시물 식별자로 쓰고 `BILL_ID`로 의안과 연결한다. 진행·종료 경로가 달라도 같은 게시물 ID를 이어 붙인다. 같은 의안의 재예고는 별개 게시물이다. 진행 목록 첫 관측은 `assembly_notice_observed`, 공식 종료 확인은 `assembly_notice_closed`, 날짜·위원회 등 정정은 `assembly_notice_changed`로 기록한다. 진행 목록에서 빠진 사실만으로 종료를 확정하지 않는다. 동일 게시물이 두 목록에서 모순되는 경우 재조회하고 해소 전 기존 상태를 보존한다.
- 공식 원문 보강은 새로 관측되거나 예고 기간이 바뀐 관심 게시물에 한정한다. HTTPS 공식 상세 경로와 `lgsltPaId`·의안번호를 대조한 뒤 `입법예고기간`의 두 날짜가 유효하고 종료일이 API와 일치할 때만 시작일을 저장한다. 원문 요청 실패·문서 구조 변경은 API 수집과 분리해 재시도하며, 이전에 같은 기간에 대해 확인한 시작일은 보존한다. 종료일이 바뀌면 새 기간을 다시 확인할 때까지 시작일을 비워 둔다. 원문은 키 없이 요청하며 API 목록의 HTML 오류 페이지와 혼동하지 않도록 한다.
- 기존 이벤트 봉투의 `source`, `change_type`, `target`, `dates`, `old/new`, `context.source_url`을 재사용한다. `target`에는 의안/예고 ID·제목·위원회, `dates`에는 실제로 확인된 날짜만 담는다. `context`에는 원천 코드, 원천 상태, 날짜별 근거, 정규화 스키마 버전을 담는다. 공식 예고 게시일이 없으면 최초 감지 시각은 `detected_at`에만 기록한다.
- `items`에 출처별 정규화 스냅샷과 기준선 상태를 보관한다. 필요하면 기존 SQLite에 추가 테이블로 수집기별 기준선과 관측 순번을 저장한다. 정규화 내용이 같으면 이벤트를 만들지 않고, 변경이 있으면 `(source, item_id, 관측 순번, 내용 해시)`를 기존 ID 생성기에 사용한다. 동일 전환의 재시도는 같은 ID를 사용하며 A→B→A 정정은 두 변화가 모두 남는다. 상태·순번·이벤트 등록은 기존 `State.apply` 트랜잭션에 넣는다. DB에 등록되지 않은 파일은 sync 대상에서 배제한다.
- `bootstrap --only assembly_bill` / `assembly_notice`는 기준선만 만들고 기존 법령 기준점을 건드리지 않는다. 첫 성공 일일 실행에서 현재 키워드에 맞는 진행 예고를 초기 후보로 발행하고, 이후 발견한 변경을 증분 반영한다. 과거 종료 예고와 과거 의안 이력은 기준선에만 보존하며 초기 공개 후보에는 소급 입력하지 않는 안이다.
- 새 키 누락·기준선 미준비·장애는 국회 수집기 단위로 기록하고, 기존 법령 수집과 뒤 점검으로 진행한다. 재시도는 일시 네트워크 오류·서버 장애에 한정해 최대 2회, 지수 대기와 전체 마감 시간 안에서 순차 실행한다. 쿼터·인증·응답 구조 오류는 오류 코드만 기록한다. 쿼리 문자열, 원본 응답, HTTP 예외 메시지와 traceback에 키가 남는 경로를 테스트한다. `collector_failing`과 기존 AI 요약은 유지하고, 국회 이벤트는 공식 필드로 별도 요약 절을 만들어 추가 모델 호출 없이 기록한다.

## 4. 캘린더 DB·sync·공개

- v4는 국회 게시물·의안 스냅샷과 검토 이력을 추가한다. 공통 식별자 `(source, upstream_id)`, 최신 스냅샷, 승인된 스냅샷, 원천 수집 시각을 두고, 가져온 Monitor `event_id`를 유일하게 등록한다. 기존 법령·조문·공개 및 보류 뷰는 보존한다. 입법예고의 게시물 단위 UI와 공개 계약을 재사용하되 국회 항목에 합성 조문을 만들지 않는다.
- 일정 투영은 `(항목 ID, 날짜 역할)`을 유일키로 둔다. 예고는 확인된 `notice_start`와 `notice_end`만 날짜에 배치하고 같은 게시물·같은 날짜는 한 건으로 센다. 의안은 확인된 `bill_proposed`와 `bill_decided`를 별도 유형으로 표시하고, 심사 단계·결과는 상세에 둔다. 날짜가 정정되면 같은 역할의 일정을 이동하고, 날짜가 명시적으로 빠진 새 승인본에서는 해당 역할을 제외한다. 날짜가 없는 후보는 검토 기록에서 볼 수 있다.
- 기존 월별 집계와 일별 목록에서 예고와 법률안을 구분한다. `/api/law-calendar`에 별도 의안 건수를 추가하고, `/api/law-events`의 날짜·페이지 규약을 그대로 사용해 입법예고와 의안 카드에 제목·소관위원회·상태·날짜 역할·공식 링크를 제공한다. 기존 조문 상세와 국회 항목 상세는 타입으로 구분하고, 생성된 TS 계약·정렬·페이지네이션을 함께 검증한다.
- 국회 후보의 공개는 기존 방식에 맞춰 운영자가 정확한 스냅샷을 검토하고 승인·반려·철회하는 CLI로 처리한다. 예고 날짜·상태·원문 링크가 바뀌는 등 공개 내용의 변경은 재검토 대상으로 만든다. 검토가 끝나기 전에는 정정 전 일정이 현재 정보처럼 보이지 않도록 해당 공개 투영을 숨긴다. 신규 출처 검토에서도 감사 이력과 오래된 승인 요청 거부를 유지한다. 링크 허용 범위는 검증된 HTTPS 국회 원문 호스트·경로 및 식별자로 제한한다.
- sync는 현재와 같이 등록된 이벤트만 읽는다. 국회 출처마다 최신 실행 결과와 이벤트를 독립적으로 검증하고, 이벤트 등록과 투영 저장은 같은 캘린더 트랜잭션에 넣는다. 출처별 실패를 합산해 종료 상태로 반환하되 정상 법령·정상 국회 출처의 입력은 계속 진행한다. 실패 출처는 기존 행과 마지막 성공 시각을 보존한다. 법령, 의안, 예고의 신선도는 따로 갱신해 정상 0건과 미수집을 구분한다. 소스가 부분 실패했어도 이전에 확정된 이벤트는 다음 정상 수집에서 재처리한다.
- SQLite backup API로 운영 DB의 독립 사본을 먼저 만들고 기존 백업 이름은 재사용하지 않는다. 복사본에서 `quick_check`·`foreign_key_check`, v3 행 수·검토 감사·공개 API 비교와 v4 재실행을 확인한다. 승인된 배포 창에서 writer와 버전을 맞춘 뒤 명시적 마이그레이션 한 트랜잭션으로 v4를 적용한다. 새 배포 실패 시 기록한 v3 백업과 이전 코드로 복구하는 절차를 문서화하고 복사본에서 먼저 검증한다. sync 및 API는 DB를 자동 생성하거나 마이그레이션하지 않는다.

## 5. 변경 파일과 영향

| 경로 | 승인 후 변경 |
| --- | --- |
| `services/monitor/src/law_monitor/assembly.py`, `collectors/assembly.py` | 새 API 클라이언트, 예고 원문 기간 확인, 두 수집기 |
| `services/monitor/src/law_monitor/{app,cli,config,state,summarize}.py`, `services/monitor/config/targets.yaml`, `services/monitor/.env.example`, `services/monitor/README.md` | 활성화·키 설정, 개별 bootstrap과 실패 격리, 상태 반영, 국회 요약, 운영 안내 |
| `services/monitor/tests/test_mon_assembly.py`, `test_mon_assembly_network.py`, `tests/fixtures/assembly/`, 기존 `test_mon_runner.py`·`test_mon_summary.py` | 응답 대역, 선택 실행 실제 API 계약, 실행기 회귀 |
| `services/law-calendar/src/law_calendar/{db,sync,models,server,validation,manage,review}.py`, 새 `assembly_ingest.py` | v4, 독립 import, 공식 출처 검증, 검토, 공개 응답. 생성기 변경은 새 응답 유형에 필요한 경우로 한정 |
| `services/law-calendar/frontend/src/law-calendar/`의 `api-types.ts`, `types.ts`, `data.ts`, `LawCalendarPage.tsx`, `MonthCalendar.tsx`, `EventList.tsx`, `ChangeDialog.tsx` 및 관련 테스트·스타일 | 예고 날짜 구분, 의안 유형, 상세와 링크, 카운트와 페이지 처리 |
| `services/law-calendar/tests/`의 sync·workflow·API·UI 테스트, `services/law-calendar/README.md`, `docs/operations.md`, `docs/evals/` | 마이그레이션·백업·재실행·기존 기능 회귀, 검증·반영 기록 |

- 목록 조회 예산은 2026-11-05 표본의 제22대 총건수 기준 약 44회/일(의안 22, 진행 1, 종료 21)이다. 공식 상세 페이지 보강과 재시도는 별도 계수한다. 계획값으로 목록은 최대 64회, 원문 보강은 실행당 최대 40회, 두 수집기의 추가 소요는 합계 90초를 상한으로 검증한다. 한도를 넘으면 원문 보강을 다음 실행으로 넘기고, 목록이 불완전한 출처의 상태는 커밋하지 않는다. 실측으로 06:30 후속 점검의 시작 지연을 확인한 후 활성화한다.
- 국회 상태는 원천의 게시 시점과 일일 실행 간격을 따른다. 원천 정정 및 불완전 페이지는 재수집하며, 진행 중 목록에서 누락된 경우 공개 상태의 마지막 확인 시각을 표시한다. 공식 상세 페이지 구조 변화는 시작일 확인에만 영향을 준다.
- 기존 06:30 launchd 작업과 점검 순서, 법제처 수집, AI 요약, 조문 시행일 자동 등록과 보류는 회귀 검증 대상이다. 배포 및 운영 DB·운영 설정 변경은 구현 승인과 별도로 진행한다.

## 6. 구현·검증 단계

1. 승인 후 개발 브랜치와 기준 HEAD를 확정한다. 합성 응답으로 클라이언트와 수집기의 Red 테스트를 작성한다. 다중 페이지·`INFO-200`·쿼터·부분 실패·충돌·비밀값 누출·재실행·정정·원문 기간 불일치를 재현하고 구현한다.
2. 임시 v3 DB를 v4로 올리는 테스트부터 작성한다. DB 백업, 무손실 이전, 중복·정정·공개 검토, 등록되지 않은 파일 배제와 소스별 실패 복구를 검증한다. 기존 법령 시행일 자동 공개·보류·감사와 법령 신선도는 기존 API에서 대조한다.
3. 캘린더 프런트엔드를 연결한다. Playwright에서 공식 날짜가 있는 예고 시작/종료, 별도 발의/의결 항목, 날짜 없는 비노출, 동일 의안 정정과 승인/철회, 원문 링크, 데스크톱·모바일 및 키보드를 확인한다.
4. 기본 테스트 전층과 CI의 린트·타입·빌드·스키마 검사를 실행한다. 실제 외부 API 계약 테스트는 `network`로 분리해 임시 데이터와 제한된 읽기 호출로 수행한다. 실제 모델 E2E·모델 스모크·품질 평가는 별도 표기한다. 실행 순서, 결과 양식, 완료 판정은 [검증 가이드](../evals/2026-11-05-assembly-api-validation-guide.md)를 따른다.
5. 완료 기준은 키워드로 선택된 실제 예고 및 의안이 분리된 데이터로 들어오고, 승인 후 실제 근거 날짜에만 표시되며, 동일 자료 재실행과 부분 장애 복구에서 일정이 중복되지 않는 것이다. 전체 수집 측정, 대역 상태 전환과 검증 범위는 [구현 결과](../evals/2026-11-05-assembly-law-calendar.md)에 기록했다.
