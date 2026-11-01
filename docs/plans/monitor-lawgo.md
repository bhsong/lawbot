# 계획: 법령 변경 수집기 PR 2 (법제처 API: 개정이유·별표서식)

- 작성일: 2026-11-01
- 상위 계획: [monitor-collector.md](monitor-collector.md) 3절의 PR 2. 근거: [스파이크 기록](../evals/2026-11-01-monitor-spike.md)
- 목표: 원 계획서의 "이벤트 하나에 요약에 필요한 원문(diff, 개정문, 제·개정 이유, 공포일, 시행일)이 모두 들어 있다"를 법령·행정규칙·별표서식에서 채운다

## 1. 스파이크로 바뀐 설계

| 항목 | 원래 계획 (monitor-collector.md) | 바뀐 설계 | 근거 (스파이크 기록) |
| --- | --- | --- | --- |
| 서식 변경 판단 | 파일을 받아 PDF 추출 텍스트의 해시 비교 | **본문 API의 `별표내용` 텍스트 비교** (신·구판 MST 본문 2개) | 발견 2: 254개 중 다른 5개 = 바뀐 서식 목록과 같음 |
| 받을 파일 | 개정 법령의 첨부 전부 (약 250개) | **바뀌거나 새로 생긴 별표·서식의 PDF·HWP만** 원본 보관 | 발견 3: 파일 바이트는 판마다 다름, 비교에 쓰지 않음 |
| `pdfplumber`·`lxml` | 추가 | **넣지 않음** (JSON 응답, 텍스트가 API에 있음) | 발견 2·3 |
| 서식별 시행일 | 없음 | `기본정보.별표시행일자문자열`에서 서식마다 시행일을 읽음 (없으면 법령 시행일자) | 발견 1 |
| 수집기 구조 | `lawgo_forms`·`lawgo_admrul` 별도 수집기 | **legalize_git 수집기가 이벤트를 만들 때 법제처 API로 보강**. 별표서식 이벤트의 `source`는 `lawgo_forms`·`lawgo_admrul` | 신·구판 MST·행정규칙일련번호가 git 커밋에서 나오므로 커밋 단위로 같이 처리해야 개정과 서식 변경이 짝이 맞음 |
| 5번 (전자신고 파일 레이아웃) | 법제처 행정규칙 첨부 | **이번 PR 범위 밖**. 법제처에 없음 → 출처를 사용자와 다시 정함 (8절) | 발견 5 |

## 2. 동작

### 2.1 법령 이벤트 보강 (`legalize_law`)

legalize-kr 커밋에서 이벤트를 만들 때 신판 머리말의 `법령MST`로 `lawService.do?target=law&MST=…&type=JSON`을 부른다.

- `context.amendment_text` = `개정문.개정문내용` (줄을 이어 붙인 문자열)
- `context.amendment_reason` = `제개정이유.제개정이유내용`
- `context.lawgo` = `{mst_old, mst_new, 별표시행일자문자열, 조문시행일자문자열}`
- 법령이 `forms` 설정에 있으면 (머리말 `제목`이 `forms[].law`와 같음) 구판 MST 본문도 받아 2.3을 한다.
- 폐지·삭제 이벤트(신판 없음)는 구판 MST로 개정문·이유만 채운다.

### 2.2 행정규칙 이벤트 보강 (`legalize_admrule`)

신판 머리말의 `행정규칙일련번호`로 `lawService.do?target=admrul&ID=…&type=JSON`을 부른다.

- `amendment_text`·`amendment_reason`·`context.addenda`(부칙)를 채운다.
- `context.lawgo.files` = `첨부파일`(고시 원문·개정 이유서) 이름과 링크. 파일은 받지 않는다.
- 구판 상세도 받아 별지·별표에 2.3을 한다 (행정규칙은 모든 대상에 적용).

### 2.3 별표·서식 비교 → 이벤트

키는 `(별표구분, 별표번호, 별표가지번호)`이고, `별표내용`을 줄로 펴서 비교한다 (줄 끝 공백 제거).

| 경우 | `change_type` | 내용 |
| --- | --- | --- |
| 둘 다 있고 텍스트 다름 | `attachment_changed` | 텍스트 unified diff, 신판 PDF·HWP 받음 |
| 신판에만 있음 | `attachment_added` | 신판 텍스트, PDF·HWP 받음 |
| 구판에만 있음 | `attachment_removed` | 구판 텍스트 |
| 텍스트 같음 | 이벤트 없음 | 링크·파일명만 바뀐 249개 |

- `source`: 법령은 `lawgo_forms`(category 2), 행정규칙은 `lawgo_admrul`(그 행정규칙의 category)
- `forms[].kinds`(`별표`·`서식`)에 있는 구분만. 행정규칙은 전부(`별지`·`별표`·`서식`)
- `target` = `{law_name, item_name: 별표제목, item_id: "서식 0021-00", kind}`
- `dates` = `{promulgation: 공포일자, effective: 서식별 시행일 (별표시행일자문자열) 또는 법령 시행일자}`
- `old`/`new` = `{version_key: 텍스트 sha256, text: data/files/<sha256>.txt, file: data/files/<sha256>.pdf, hwp: …}` (파일은 신판만)
- `context` = 부모 법령 이벤트의 `event_id`, `amendment_text`·`amendment_reason`(같은 개정의 것), `source_url`(PDF 링크)
- `event_id = sha1(source | 법령ID 또는 행정규칙ID | 키 | 신판 텍스트 해시)`. 같은 서식이 같은 내용으로 다시 나와도 한 번만 저장된다.

### 2.4 HTTP (`utils/http.py`)

- `httpx` + `tenacity`: 타임아웃 30초, 최대 3회 재시도(지수 백오프, 연결 오류·5xx만), 요청 사이 0.5초 이상
- 한 실행 안에서 같은 MST·일련번호 응답은 한 번만 받는다 (replay에서 이어진 커밋이 같은 판을 공유함). 소득세법 시행규칙 본문은 약 6.7MB
- OC는 환경변수 `LAW_OC`, 없으면 `services/monitor/.env`에서 읽는다. 로그·이벤트·오류 메시지에 OC를 남기지 않는다 (URL은 `OC=***`로 가림)
- `flDownload.do`는 OC 없이 받는다. 응답이 PDF·HWP가 아니면(HTML 오류 페이지 등) 실패로 본다

### 2.5 실패 처리

- 법제처 호출이 실패하면 그 수집기(`legalize_law` 또는 `legalize_admrule`)가 실패로 끝난다. 상태(커서)가 안 바뀌므로 **다음 실행에서 같은 커밋을 다시 처리**한다. 원문이 빠진 이벤트를 저장하지 않는다 (원 계획서 5.2 "2단계가 다시 API를 부르지 않게").
- `LAW_OC`가 없으면 `collect`·`replay`가 실행 전에 거부한다 (종료 코드 2). `bootstrap`은 API를 부르지 않으므로 OC 없이 된다.
- 법제처가 3일 연속 실패하면 PR 1의 `collector_failing` 이벤트가 나간다.

## 3. 파일

```
services/monitor/src/law_monitor/
  lawgo.py                # LawGoClient: law_body(mst), admrule_detail(serial), download(link) → 캐시·재시도·OC 가림
  attachments.py          # 별표단위 정규화, 별표시행일자문자열 파싱, 신·구 비교 → 별표서식 이벤트
  secrets.py              # LAW_OC 읽기 (환경변수 → services/monitor/.env)
  utils/http.py           # httpx 클라이언트 + tenacity 재시도 + 요청 간격
  collectors/legalize_git.py   # 이벤트 생성 뒤 보강 훅 호출 (Enricher)
  app.py · cli.py         # Enricher 연결, OC 없음 거부
services/monitor/tests/
  fixtures/lawgo/         # 스파이크 응답을 줄인 견본 (별표 몇 개, 별표내용 짧게, OC 없음)
  test_mon_lawgo.py       # httpx.MockTransport로 응답 고정
```

`services/monitor/` 밖 변경 (구현 전에 이 목록으로 승인받음):

| 파일 | 바꿀 내용 |
| --- | --- |
| `uv.lock` | `law-monitor` 의존성에 `httpx`·`tenacity` 추가 (httpx는 이미 챗봇이 씀, tenacity만 새로) |
| `docs/evals/<날짜>-monitor-lawgo.md` | 6절 실제 확인 결과 |

## 4. 테스트 (구현보다 먼저)

| 테스트 | 확인 |
| --- | --- |
| 별표시행일자문자열 파싱 | `20260522:서식21,서식23,서식100의220260701:서식24,서식24의5` → 서식마다 시행일 (구분자 없이 붙은 날짜 처리) |
| 별표 비교 | 텍스트만 같고 링크 다름 → 0건. 텍스트 다름 → `attachment_changed` + diff. 추가·삭제. `kinds` 필터 |
| 법령 이벤트 보강 | `amendment_text`·`amendment_reason`·`lawgo` 채워짐. forms 대상이면 별표서식 이벤트가 같이 나오고 `parent_event_id`가 맞음 |
| 행정규칙 보강 | 제개정이유·부칙·첨부파일 목록, 별지 변경 이벤트 (`lawgo_admrul`, 행정규칙 category) |
| 파일 받기 | 바뀐 서식만 PDF·HWP를 받음 (요청 횟수 확인), `data/files/<sha256>.pdf`, 같은 파일은 한 번만 |
| 캐시 | 이어진 개정 2개에서 가운데 판 본문은 한 번만 요청 |
| 재시도 | 5xx 2번 뒤 성공 → 성공, 3번 실패 → 수집기 실패 + 커서 그대로 → 다음 실행에서 같은 이벤트 |
| 오류 응답 | 다운로드가 HTML이면 실패 |
| OC 가림 | 실패 메시지·로그·이벤트 JSON에 OC 값이 없음 |
| OC 없음 | `collect` 거부(종료 코드 2), `bootstrap`은 됨 |
| 기존 테스트 | PR 1 테스트는 가짜 법제처 클라이언트를 넣어 그대로 통과 |
| `@pytest.mark.network` 1개 | 실제 `lawSearch.do` 1회 (기본 실행 제외, `-m network`) |

## 5. 하지 않는 것

- PDF 텍스트 추출 (`pdfplumber`), 서식의 시각 비교
- 고시 첨부(원문 hwpx·개정 이유서)의 다운로드·텍스트 추출 (목록과 링크만)
- 입법예고·국회 (PR 3)
- 5번 전자신고 파일 레이아웃 (출처 미정)

## 6. 실제 확인 (맥미니, 실행 전 승인)

| # | 항목 | 기대값 |
| --- | --- | --- |
| 1 | `replay --repo legalize-kr --from 4220f9dc --to 51fea869` | 법령 이벤트 19건 모두 `amendment_reason` 있음. 소득세법 시행규칙(`51fea869`)에 `lawgo_forms` 5건 (서식 21·23·100의2는 시행 2026-05-22, 서식 24·24의5는 2026-07-01), 서식 21 diff에 `CA6` 줄 |
| 2 | 1번의 다운로드 | 바뀐 서식만 PDF·HWP. 링크만 바뀐 249개는 받지 않음 |
| 3 | `replay --repo admrule-kr` 최저임금 커밋 | `amendment_reason` 있음, 첨부(원문) 목록 |
| 4 | OC 없이 `collect` | 종료 코드 2, 메시지에 OC 없음 |

## 7. 비용

- 법령 개정 1건당 본문 1~2회(각 수 MB), 바뀐 서식마다 파일 2개. 평소 하루 0~수 건이다.
- replay 19커밋이면 본문 약 25회(약 150MB 내려받기), 3~5분.

## 8. 사용자와 정할 것

- **5번 출처**: "지급명세서 등 전산매체 제출요령"은 법제처 행정규칙이 아니다 (스파이크 발견 5). 국세청 홈택스·누리집 자료실의 게시물을 따라갈지, 3단계 정기 리마인드(매년 제출 시기 알림)로 돌릴지 정해야 한다. 이번 PR에서는 다루지 않는다.
