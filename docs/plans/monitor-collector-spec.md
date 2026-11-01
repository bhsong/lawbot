# 1단계 구현 계획서: 법령 변경 수집기 (Collector)

> 대상 독자: 이 계획을 구현할 코딩 에이전트
> 작성일: 2026-11-01
> 상위 문서: "ERP 법령 변경 모니터링 – 추적 대상과 데이터 소스" (한눈에 보기 1~7번)

---

## 0. 한 줄 요약

매일 1회 실행되어 추적 대상 법령·행정규칙·별표서식·입법예고를 수집하고, **지난 실행과 달라진 것만** "변경 이벤트(JSON)"로 저장하는 Python 프로그램을 만든다. 요약(2단계)과 전송(3단계)은 이 이벤트 파일만 읽으면 되도록 한다.

## 1. 범위

### 1.1 이번 단계에서 하는 것

| 한눈에 보기 번호 | 대상 | 소스 | 수집 방식 |
|---|---|---|---|
| 1 | 법률·시행령·시행규칙 본문 | legalize-kr (git) | git pull 후 대상 폴더의 새 커밋 diff |
| 2 | 별표·서식 (간이세액표, 지급명세서, 부가세 신고서 등) | 법제처 Open API (OC) | 목록 조회 → 개정일·파일 해시 비교 → 변경 시 파일 다운로드 |
| 3 | 행정규칙 (국세청·고용노동부·보건복지부 고시 등) | admrule-kr (git) | git pull 후 대상 파일의 새 커밋 diff |
| 4 | 입법예고·행정예고·국회 법안 | 국민참여입법센터 API, 공공데이터포털 국회 API | 신규 게시물 중 키워드 매칭 |
| 5 | 전자신고 파일 레이아웃 | 국세청 고시 첨부 (법제처 API 행정규칙) | 3번과 같은 방식 + 첨부파일 해시 비교 |
| 7 | 4대보험·최저임금 | legalize-kr + admrule-kr | 1·3번에 대상만 추가 |

### 1.2 이번 단계에서 하지 않는 것

- AI 요약 (2단계)
- 텔레그램·노션 전송 (3단계)
- 6번 정기 리마인드 (데이터 수집이 아니라 일정 알림이므로 3단계에서 처리)
- 서식 PDF의 시각적(이미지) 비교. 1단계에서는 파일 보관과 텍스트 추출까지만 한다.

## 2. 실행 환경과 기술 선택

| 항목 | 결정 |
|---|---|
| 실행 장소 | 집의 맥미니 (macOS, Apple Silicon) |
| 언어 | Python 3.14 (`uv`로 설치·고정) |
| 패키지·환경 관리 | `uv` (가상환경, 의존성, 실행 모두 `uv`로 통일. `pip`·`venv` 직접 사용 금지) |
| 린트·포맷 | `ruff` (lint + format) |
| 테스트 | `pytest` |
| 주요 라이브러리 | `httpx`(HTTP), `lxml`(XML), `pyyaml`(설정), `GitPython` 또는 `subprocess`로 git 호출, `pdfplumber`(PDF 텍스트·표 추출), `tenacity`(재시도) |
| 상태 저장 | SQLite 1개 파일 (`data/state.db`) |
| 비밀값 | `.env` (`LAW_OC=...`, `DATA_GO_KR_KEY=...`), git에 올리지 않음 |
| 스케줄 | macOS `launchd` (매일 06:30 KST). cron 대신 launchd를 쓴다 (잠자기 후 놓친 실행을 보충할 수 있음) |
| 로그 | `logs/collector-YYYYMMDD.log`, 실행 요약을 마지막 줄에 기록 |

### 2.1 개발 환경 (uv / ruff / pytest)

**초기 설정**

```bash
uv init --package law-monitor --python 3.14
cd law-monitor
uv python pin 3.14                       # .python-version 생성
uv add httpx lxml pyyaml gitpython pdfplumber tenacity python-dotenv
uv add --dev ruff pytest pytest-cov
```

- `uv.lock`은 git에 커밋한다 (맥미니와 개발 PC의 의존성을 같게 유지).
- 실행은 항상 `uv run`으로 한다: `uv run law-monitor collect`, `uv run pytest`, `uv run ruff check`.
- 의존성이 Python 3.14를 지원하지 않는 경우(특히 `lxml`, `pdfplumber`의 휠): 0단계 스파이크에서 먼저 `uv sync`로 확인하고, 막히면 대안 라이브러리를 찾되 Python 버전은 내리지 않는다. 대안이 없을 때만 사용자에게 보고한다.

**`pyproject.toml` 설정 (발췌)**

```toml
[project]
name = "law-monitor"
requires-python = ">=3.14"

[project.scripts]
law-monitor = "law_monitor.cli:main"

[tool.ruff]
line-length = 100
target-version = "py314"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
  "network: 실제 외부 API·저장소에 접속하는 테스트 (기본 실행에서 제외)",
]
```

**규칙**

- 모든 코드 변경 후 `uv run ruff format`, `uv run ruff check --fix`, `uv run pytest`가 통과해야 한다.
- 기본 테스트는 네트워크 없이 `tests/fixtures/`의 저장된 응답과 임시 git 저장소만 사용한다.
- 실제 API를 부르는 테스트는 `@pytest.mark.network`를 붙이고 `uv run pytest -m network`로 따로 실행한다.
- (선택) `pre-commit`으로 커밋 전 `ruff check`·`ruff format --check` 자동 실행.

## 3. 디렉터리 구조

```
law-monitor/
├── config/
│   └── targets.yaml          # 추적 대상 (4장)
├── src/law_monitor/
│   ├── __init__.py
│   ├── cli.py                # 진입점: collect / bootstrap / replay
│   ├── config.py             # targets.yaml 로드·검증
│   ├── state.py              # SQLite 상태 저장소
│   ├── events.py             # 변경 이벤트 스키마·저장
│   ├── collectors/
│   │   ├── base.py           # Collector 인터페이스
│   │   ├── legalize_git.py   # 1, 3, 5(일부), 7번 (legalize-kr, admrule-kr 공용)
│   │   ├── lawgo_forms.py    # 2번 별표·서식 (법제처 API)
│   │   ├── lawgo_admrul.py   # 3, 5번 보조 (법제처 API 행정규칙 + 첨부)
│   │   ├── lawmaking.py      # 4번 국민참여입법센터 입법예고·행정예고
│   │   └── assembly.py       # 4번 국회 입법예고·법안
│   └── utils/
│       ├── http.py           # 공통 HTTP 클라이언트 (재시도, 지연, 타임아웃)
│       ├── hashing.py
│       └── pdf_text.py       # PDF → 텍스트/표 추출
├── data/
│   ├── repos/                # legalize-kr, admrule-kr 클론
│   ├── files/                # 다운로드한 서식·첨부 원본 (해시별 보관)
│   ├── events/               # 변경 이벤트 JSON (2단계 입력)
│   └── state.db
├── tests/
│   └── fixtures/             # 0단계 스파이크에서 저장한 실제 API 응답 샘플
├── logs/
├── .env.example
├── .python-version           # 3.14 (uv python pin)
├── pyproject.toml            # 의존성 + ruff + pytest 설정
├── uv.lock
└── README.md
```

## 4. 추적 대상 설정 (`config/targets.yaml`)

엑셀의 한눈에 보기 표를 그대로 설정으로 옮긴다. 대상 추가·삭제는 코드 수정 없이 이 파일만 고치면 되어야 한다.

```yaml
# category 값은 한눈에 보기 표의 번호와 같다.

laws:   # 1, 7번: legalize-kr 폴더명 (띄어쓰기 제거). 폴더 안의 법률·시행령·시행규칙을 모두 추적
  - { name: 소득세법,                    category: 1 }
  - { name: 조세특례제한법,              category: 1 }
  - { name: 국세기본법,                  category: 1 }
  - { name: 지방세법,                    category: 1 }
  - { name: 부가가치세법,                category: 1 }
  - { name: 근로자퇴직급여보장법,        category: 1 }
  - { name: 근로기준법,                  category: 1 }
  - { name: 국민연금법,                  category: 7 }
  - { name: 국민건강보험법,              category: 7 }
  - { name: 노인장기요양보험법,          category: 7 }
  - { name: 고용보험법,                  category: 7 }
  - { name: 고용보험및산업재해보상보험의보험료징수등에관한법률, category: 7 }
  - { name: 최저임금법,                  category: 7 }

forms:  # 2번: 법제처 API 별표·서식. 아래 법령에 딸린 별표·서식 전체를 추적
  - { law: 소득세법 시행령,        kinds: [별표] }          # 근로소득 간이세액표 등
  - { law: 소득세법 시행규칙,      kinds: [별표, 서식] }    # 원천징수영수증·지급명세서·간이지급명세서·공제신고서 등
  - { law: 부가가치세법 시행규칙,  kinds: [별표, 서식] }    # 부가세 신고서·부속서류
  - { law: 조세특례제한법 시행규칙, kinds: [서식] }

admrules:  # 3, 5, 7번: 행정규칙. 이름 정확 일치 목록 + 키워드 규칙을 함께 쓴다
  exact: []          # 0단계 스파이크 후 실제 고시명으로 채운다 (7장 참고)
  keyword_rules:
    - { agency: 국세청,     keywords: [원천징수, 연말정산, 지급명세서, 간이지급명세서, 전산매체, 전자신고, 부가가치세], category: 3 }
    - { agency: 국세청,     keywords: [전산매체, 제출요령, 레이아웃], category: 5 }
    - { agency: 고용노동부, keywords: [퇴직연금, 최저임금, 산재보험료율, 사업종류별], category: 7 }
    - { agency: 보건복지부, keywords: [기준소득월액, 보험료 상한, 보험료 하한, 보수월액], category: 7 }

prelegislation:  # 4번: 입법예고·행정예고·국회 법안 제목 키워드
  keywords: [소득세법, 조세특례제한법, 국세기본법, 지방세법, 부가가치세법,
             근로자퇴직급여, 근로기준법, 국민연금, 국민건강보험, 장기요양보험,
             고용보험, 보험료징수, 최저임금, 원천징수, 연말정산, 지급명세서]
```

## 5. 핵심 설계

### 5.1 Collector 인터페이스

모든 수집기는 같은 모양을 따른다. 한 수집기가 실패해도 나머지는 계속 실행되어야 한다.

```python
class Collector(Protocol):
    name: str  # "legalize_law", "lawgo_forms" ...

    def collect(self, ctx: RunContext) -> list[ChangeEvent]: ...
```

- `RunContext`: 설정, 상태 저장소, HTTP 클라이언트, 실행 모드(`bootstrap`/`normal`), 실행 ID
- 수집기는 상태를 읽고 비교한 뒤, **이벤트를 반환하고 상태 갱신은 실행 마지막에 한꺼번에** 한다. 중간에 실패하면 상태가 바뀌지 않아 다음 실행에서 다시 잡힌다.

### 5.2 변경 이벤트 스키마 (2단계의 입력)

`data/events/YYYY-MM-DD/<event_id>.json` 으로 저장한다. 같은 변경이 두 번 저장되지 않도록 `event_id`는 내용 기반으로 만든다 (예: `sha1(source + target_id + new_version_key)`).

```json
{
  "event_id": "c3f1...",
  "detected_at": "2026-11-02T06:31:12+09:00",
  "category": 2,
  "source": "lawgo_forms",
  "change_type": "attachment_changed",
  "target": {
    "law_name": "소득세법 시행규칙",
    "item_name": "별지 제24호서식(1) 근로소득 원천징수영수증",
    "item_id": "법제처 별표서식 ID"
  },
  "dates": {
    "promulgation": "2027-03-14",
    "effective": "2027-03-14"
  },
  "old": { "version_key": "...", "file": "data/files/ab12....pdf", "text": "data/files/ab12....txt" },
  "new": { "version_key": "...", "file": "data/files/cd34....pdf", "text": "data/files/cd34....txt" },
  "diff": { "kind": "unified_text", "path": "data/events/2026-11-02/c3f1....diff" },
  "context": {
    "amendment_text": "개정문 원문 (있으면)",
    "amendment_reason": "제·개정 이유 원문 (있으면)",
    "source_url": "https://www.law.go.kr/..."
  }
}
```

`change_type` 값: `law_amended`, `law_new`, `law_repealed`, `admrule_amended`, `admrule_new`, `attachment_changed`, `attachment_added`, `attachment_removed`, `prelegislation_new`.

**2단계를 위한 원칙:** 이벤트 하나만 읽어도 요약에 필요한 원문(diff, 개정문, 제·개정 이유)이 다 들어 있어야 한다. 2단계가 다시 API를 부르지 않게 한다.

### 5.3 상태 저장소 (SQLite)

| 테이블 | 용도 | 주요 컬럼 |
|---|---|---|
| `git_cursor` | 저장소별 마지막 확인 커밋 | `repo`, `last_commit` |
| `items` | API로 추적하는 개별 항목의 마지막 상태 | `source`, `item_id`, `version_key`, `content_hash`, `file_path`, `seen_at` |
| `seen_posts` | 이미 본 입법예고·법안 | `source`, `post_id`, `first_seen_at` |
| `events` | 저장한 이벤트 목록 (중복 방지·2단계 처리 여부) | `event_id`, `created_at`, `processed_at` |
| `runs` | 실행 기록 | `run_id`, `started_at`, `finished_at`, `status`, `summary_json` |

### 5.4 수집기별 상세

#### A. `legalize_git` (1, 3, 5, 7번)

1. `data/repos/legalize-kr`, `data/repos/admrule-kr`이 없으면 clone, 있으면 `git fetch` + `git pull --ff-only`.
2. `git_cursor.last_commit` 부터 `HEAD` 까지 범위에서, 대상 경로(`kr/<법령명>/` 등)를 건드린 커밋만 뽑는다.
   - **주의: 날짜 필터(`--since`)를 쓰지 않는다.** legalize-kr은 커밋 날짜를 공포일로 맞춰 두어서, 오늘 추가된 커밋의 날짜가 과거일 수 있다. 반드시 커밋 해시 범위(`A..B`)로 비교한다.
3. 대상 파일마다 `git diff last_commit..HEAD -- <file>`을 저장하고, 파일 앞부분의 YAML 메타데이터에서 공포일·시행일을 읽어 이벤트를 만든다.
4. 행정규칙은 `keyword_rules`로 거른다: 파일 메타데이터의 소관부처 + 제목 키워드가 맞으면 대상.
5. 강제 푸시 등으로 `last_commit`이 사라진 경우: 경고 로그를 남기고, 대상 파일의 현재 내용 해시를 `items` 테이블 값과 비교하는 방식으로 대체한다.

#### B. `lawgo_forms` (2번)

1. 법제처 Open API로 `forms`에 지정한 법령의 별표·서식 목록을 조회한다.
2. 항목마다 `version_key` = (개정일, 개정 구분, 파일 링크) 조합을 만들어 `items`와 비교한다.
3. 달라졌거나 새로 생긴 항목만 파일을 내려받는다. **PDF 링크가 있으면 PDF를 우선**하고, HWP는 함께 보관만 한다.
4. 파일은 `data/files/<sha256>.<ext>` 로 저장하고 (같은 파일은 한 번만 저장), `pdfplumber`로 텍스트를 추출해 `<sha256>.txt` 로 둔다. 간이세액표처럼 표가 핵심인 별표는 표를 CSV로도 추출한다 (`<sha256>.tables.csv`).
5. 이전 텍스트와 새 텍스트의 unified diff를 이벤트에 붙인다.
6. 같은 법령의 본문 조회 API로 **개정문과 제·개정 이유**를 가져와 `context`에 넣는다. 어떤 서식이 왜 바뀌었는지가 여기에 가장 정확하게 적혀 있다.

#### C. `lawgo_admrul` (3, 5번 보조)

- admrule-kr에 없는 정보가 있을 때만 사용한다: 특히 **고시의 첨부파일**(전산매체 제출요령의 레이아웃 파일 등).
- 대상 고시의 상세를 조회해 첨부파일 목록을 B와 같은 방식(해시 비교 → 다운로드 → 텍스트 추출 → diff)으로 처리한다.

#### D. `lawmaking` (4번, 국민참여입법센터)

1. 입법예고 목록과 행정예고 목록을 최근순으로 조회한다 (첫 페이지부터, 이미 본 게시물이 나오면 중단).
2. 제목에 `prelegislation.keywords` 중 하나라도 들어 있으면 상세를 조회해 이벤트(`prelegislation_new`)를 만든다. 의견제출 기한도 함께 담는다.
3. 매칭 여부와 관계없이 본 게시물은 `seen_posts`에 기록한다.

#### E. `assembly` (4번, 국회)

- 공공데이터포털 국회사무처 "진행중 입법예고" API를 D와 같은 방식으로 처리한다.
- 법안 처리 결과(통과 여부) 추적은 이번 단계에서는 선택 사항이다. 시간이 남으면 추가한다.

### 5.5 실행 모드

| 명령 | 동작 |
|---|---|
| `law-monitor bootstrap` | 첫 실행. 현재 상태를 기준점으로 저장만 하고 이벤트는 만들지 않는다. (안 그러면 첫날 수천 건이 쏟아진다) |
| `law-monitor collect` | 평소 실행. 기준점 이후 변경만 이벤트로 만든다. |
| `law-monitor replay --repo legalize-kr --from <commit> --to <commit>` | 과거 구간을 다시 재생해 이벤트를 만든다. 2단계 개발용 테스트 데이터를 만들 때 쓴다. |
| `law-monitor collect --only lawgo_forms` | 특정 수집기만 실행 (디버깅용) |

### 5.6 안정성 규칙

- HTTP: 타임아웃 30초, 최대 3회 재시도(지수 백오프), 요청 사이 0.5초 이상 간격.
- 한 수집기의 예외는 잡아서 `runs.summary_json`에 기록하고 다음 수집기로 넘어간다.
- 실행 결과는 마지막에 한 줄 요약으로 로그에 남긴다. 예: `collectors=5 ok=4 failed=1(lawmaking) events=3`
- 수집기가 연속 3회 실패하면 그 사실도 이벤트(`change_type: collector_failing`)로 남긴다. 3단계에서 "수집이 멈췄다"는 알림을 보낼 수 있게 하기 위해서다.
- 같은 날 여러 번 실행해도 이벤트가 중복 생성되지 않아야 한다 (`event_id` 내용 기반 + `events` 테이블).

## 6. 구현 순서

각 단계가 끝날 때마다 테스트가 통과해야 다음으로 넘어간다.

| 순서 | 작업 | 완료 기준 |
|---|---|---|
| 0 | **스파이크: API·저장소 실제 확인** (7장 목록) | 각 API의 실제 요청 URL·응답을 `tests/fixtures/`에 저장하고, 확인 결과를 `docs/spike-notes.md`에 정리 |
| 1 | `uv` 프로젝트 생성(Python 3.14), ruff·pytest 설정, 설정 로더, SQLite 상태 저장소, 이벤트 저장 | `uv run ruff check`·`uv run pytest` 통과, 설정 검증·이벤트 중복 방지 테스트 통과 |
| 2 | `legalize_git` (법령) | 임시 git 저장소로 "커밋 추가 → 이벤트 1건, 재실행 → 0건" 테스트 통과. 실제 legalize-kr로 `replay` 실행 성공 |
| 3 | `legalize_git` (행정규칙, 키워드 필터) | 키워드 필터 단위 테스트 통과 |
| 4 | `lawgo_forms` | fixture 기반으로 "개정일 변경 → 다운로드 → 텍스트 diff → 이벤트" 테스트 통과 |
| 5 | `lawgo_admrul` (고시 첨부) | 전산매체 제출요령 고시 1건으로 첨부 다운로드 확인 |
| 6 | `lawmaking`, `assembly` | fixture 기반 키워드 매칭·페이지 중단 테스트 통과 |
| 7 | CLI, 실행 모드, 로그, launchd 설정 파일 | 맥미니에서 `bootstrap` → 다음 날 `collect` 정상 동작 |

## 7. 구현 전에 확인할 것 (0단계 스파이크)

아래는 계획 작성 시점에 확인하지 못한 사항이다. **추측으로 구현하지 말고 실제 호출로 확인한 뒤 진행한다.**

1. **법제처 Open API**
   - 별표·서식 목록 조회의 정확한 엔드포인트와 파라미터 (`lawSearch.do`의 별표·서식 target 값), 응답에 개정일과 PDF·HWP 링크가 있는지.
   - 법령 본문 조회 응답에 개정문·제·개정 이유 필드가 있는지.
   - 행정규칙 상세 조회에서 첨부파일 목록과 다운로드 링크를 얻을 수 있는지.
   - 받아둔 OC 값이 맥미니(집 IP)에서 호출되는지. IP·도메인 등록 제한이 있으면 신청 정보를 갱신해야 한다.
2. **legalize-kr / admrule-kr**
   - 폴더·파일 이름 규칙 (예: `kr/소득세법/` 안의 파일명), YAML 메타데이터 필드명 (공포일자, 시행일자, 소관부처 등).
   - **공포됐지만 아직 시행 전인 법령**이 저장소에 언제 들어오는지 (공포 시점인지 시행 시점인지). 시행 시점이라면 ERP 대응이 늦어지므로, 법제처 API의 시행예정 법령 조회로 보완해야 한다.
   - 별표(간이세액표 등)가 텍스트로 들어 있는지, 이미지 링크인지.
   - 저장소 갱신 주기.
3. **국민참여입법센터 API**: 목록 조회 엔드포인트, 기존 OC로 호출 가능한지, 응답의 게시물 ID·제목·의견제출 기한 필드.
4. **국회 API**: 공공데이터포털 서비스키 발급 필요 여부, 응답 형식.
5. **행정규칙 실제 이름 확정**: 아래 고시들의 정확한 명칭을 찾아 `admrules.exact`에 채운다.
   - 국민연금 기준소득월액 상한액과 하한액 고시
   - 건강보험료 상한·하한 고시
   - 최저임금 고시
   - 사업종류별 산재보험료율 고시
   - 지급명세서 등 전산매체 제출요령 고시
   - 부가가치세 전자신고 관련 고시 (존재 여부부터 확인)

## 8. 완료 기준 (1단계 전체)

- [ ] `config/targets.yaml`만 고쳐서 추적 대상을 추가·삭제할 수 있다.
- [ ] 맥미니에서 매일 06:30에 자동 실행되고, 잠자기로 놓친 실행은 깨어난 뒤 실행된다.
- [ ] 첫 실행(`bootstrap`)은 이벤트를 만들지 않는다.
- [ ] 법령·행정규칙·별표서식·입법예고의 변경이 각각 이벤트 JSON으로 저장된다.
- [ ] 이벤트 하나에 요약에 필요한 원문(diff, 개정문, 제·개정 이유, 공포일, 시행일)이 모두 들어 있다.
- [ ] 같은 변경으로 이벤트가 두 번 생기지 않는다.
- [ ] 한 소스가 실패해도 나머지는 수집되고, 실패가 로그와 이벤트에 남는다.
- [ ] `replay`로 과거 개정 구간의 이벤트를 만들어 2단계 개발에 쓸 수 있다.
- [ ] `uv sync`만으로 맥미니에 Python 3.14 환경과 의존성이 재현된다.
- [ ] `uv run ruff check`, `uv run ruff format --check`, `uv run pytest`가 모두 통과한다 (네트워크 없이).
- [ ] launchd는 `uv run law-monitor collect`를 실행한다 (`uv`의 절대 경로 사용).
- [ ] README에 설치(`uv sync`), `.env` 설정, launchd 등록, 대상 추가, 테스트·린트 실행 방법이 적혀 있다.

## 9. 2·3단계와의 연결

- 2단계(로컬 AI 요약)는 `events` 테이블에서 `processed_at`이 비어 있는 이벤트를 읽어 처리하고 표시만 하면 된다. 1단계는 2단계 코드를 몰라도 된다.
- 요약에 쓸 모델의 컨텍스트가 제한적이므로(맥미니 32GB, 27B 모델), diff가 매우 큰 이벤트(예: 전부개정)는 1단계에서 **조문 단위로 쪼갠 diff 목록**도 함께 저장해 두면 2단계가 편하다. 구현 여유가 있으면 `diff.chunks` 필드로 추가한다.
