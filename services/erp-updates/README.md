# ERP 자료 피드 생성기

monitor가 받아 둔 법령 저장소 사본(`admrule-kr`, `legalize-kr`)에서 ERP 업무에 쓰는 고시·별표·서식·조문을 골라 검토합니다. 승인한 것만 공개 피드(`erp_calendar_public.json`)로 내보냅니다. 법령 캘린더의 ERP 자료 탭이 이 피드를 읽습니다. [기존 ERP 탭 계획](../../docs/archives/erp-calendar.md)

- 네트워크에 접속하지 않습니다. 저장소 사본은 `git --git-dir … cat-file`로 읽기만 합니다.
  - 처음 읽은 HEAD 커밋에 고정해서 읽습니다. 수집 중에 monitor가 저장소를 갱신해도 한 번의 수집 결과에 서로 다른 커밋이 섞이지 않습니다.
  - 설정한 자료가 하나도 일치하지 않으면(고시 0건, 별표·조문 없음) 그 자료의 오류로 보고합니다.
  - `GIT_NO_LAZY_FETCH=1`로 부분 클론(partial clone)이 원격에서 파일을 받아 오지 않게 합니다.
  - 로컬에 없는 파일은 그 자료의 오류로 보고하고, 나머지 자료는 계속 수집합니다.
- monitor 잠금을 잡지 않습니다. monitor의 코드·설정·상태 DB·이벤트에 쓰지 않습니다.
- 데이터는 이 서비스 전용 폴더(기본 `services/erp-updates/data/`, git 밖)의 `erp.db`와 `files/`에만 씁니다.
- 대상과 선택 규칙은 [`config/sources.yaml`](config/sources.yaml)에 있습니다. 종류마다 규칙이 하나이고, 특정 커밋·날짜·파일 번호를 적지 않습니다.

## 명령

저장소 루트에서 실행합니다(fish).

```fish
uv pip install --no-deps -e services/erp-updates
uv run erp-updates collect --repos <monitor_home>/data/repos   # 읽기 전용, 같은 내용이면 새 버전 0건
uv run erp-updates list
uv run erp-updates attach --file <받은 PDF> --url <머리말의 PDF링크>   # PDF 여부와 링크 목록을 확인
uv run erp-updates review approve <항목 id> --reviewer <이름>
uv run erp-updates export --out <피드 경로>                     # 임시 파일 → os.replace
```

`--data`로 데이터 폴더를, `--config`로 설정 파일을 바꿀 수 있습니다. `collect`는 자료 하나라도 읽지 못하면 종료 코드 1을 냅니다.

## 버전과 검토

- 내용(공개 필드)의 SHA-256이 달라지면 새 버전이 생기고 `pending`으로 시작합니다.
  - 해시는 4,000자 발췌가 아니라 전체 본문으로 계산합니다.
  - 첨부 링크(flSeq)는 법령이 개정될 때마다 모두 다시 발급되므로 버전 비교에서 뺍니다. 링크만 바뀌면 최신 버전의 링크만 새것으로 고칩니다. 등록해 둔 PDF는 옛 링크에 남고 피드에는 "원본 파일 미등록"으로 나갑니다. 판마다 PDF 바이트가 달라서, 새 링크의 파일은 다시 받아 `attach`해야 합니다.
  - 최신 버전이 승인되기 전에는 그 항목이 피드에서 빠집니다.
- 수집할 때마다 이력이 남습니다. 결과는 `collected`(새 버전) 또는 `unchanged`이고, 원천은 `저장소@커밋`입니다. 파일 등록(`attached`)과 승인·반려도 이력에 남습니다. 피드에는 승인된 항목의 이력만 들어갑니다.

## 날짜

- 고시: 머리말 `시행일자`를 `effectiveFrom`으로 씁니다.
- 별표·서식·조문: 사본은 지금 시행 중인 법령본입니다. 그 서식·조문 자체의 시행일은 알 수 없습니다. 그래서 법령 `시행일자`를 `asOf`(현행 법령본 기준일)로만 두고 `effectiveFrom`은 비워 둡니다.

## 피드 v1

| 필드 | 내용 |
| --- | --- |
| `schemaVersion`, `generatedAt` | 1, 생성 시각(KST) |
| `items[]` | `id`, `sourceKey`, `sourceTitle`, `category`, `kind`(admrule·form·article), `title`, `agency`, `lawName`, `article`, `issuedNo`, `issuedOn`, `effectiveFrom`, `asOf`, `excerpt`(최대 4,000자), `sourceUrl`, `attachments[]`(`title`, `pdfUrl`, 등록한 파일의 `sha256`·`bytes`), `contentSha256`, `collectedAt`, `reviewedAt` |
| `relatedLaws` | 공개된 별표·서식·조문 항목의 법령명 (캘린더가 관련 법령 일정을 고를 때 사용) |
| `history[]` | `itemId`, `sourceKey`, `title`, `at`, `action`, `origin` |

검토자 이름, 반려 사유, 로컬 경로는 피드에 넣지 않습니다.

## 테스트

```fish
uv run pytest services/erp-updates/tests
uv run pyright services/erp-updates/src
```

테스트는 실제 저장소와 같은 레이아웃의 합성 bare 저장소를 임시 폴더에 만들어 씁니다.
