# 수집 설정 관리

법령·ERP 수집원 등록과 변경 이력을 관리합니다. 초기 YAML 위에 별도 SQLite 설정을 적용합니다. 검증과 dry-run은 저장소나 외부 사이트를 호출하지 않습니다. 고용노동부 공식 RSS는 아래 수동 명령으로 수집합니다. `law-monitor daily`와 챗봇 조문 자동 갱신은 기존 작업으로 실행됩니다.

## 개발 실행

레포 루트에서 실행합니다. `collector_db`에는 개발 전용으로 새 경로를 지정합니다.

```fish
uv sync --project services/source-hub --locked
set hub uv run --project services/source-hub --frozen source-hub
set collector_db "$PWD/services/source-hub/data/collector.db"
$hub --database $collector_db validate
$hub --database $collector_db dry-run
$hub --database $collector_db set group erp '{"enabled": false}' --version 0 --reason '개발 설정 확인'
$hub --database $collector_db audit
```

`list`와 `dry-run`은 같은 현재 설정을 조회합니다. `set`은 `group`, `agency`, `source`, `law`, `law-part` 단위를 받습니다. 설정 버전이 바뀌면 최신 버전으로 다시 조회합니다. 그룹·기관·출처와 개별 법령 설정이 모두 켜진 범위가 실효 활성입니다. 새 게시판은 관리자 화면 또는 `add`로 등록합니다. 새로 등록하거나 수집 URL·모드·어댑터·파서 버전을 바꾼 수집원은 다시 검증해야 합니다. 초기 등록부에서 실제 URL이 확인되지 않은 항목에는 수집 경로 확인 필요가 표시됩니다.

관리자 화면은 루프백에만 바인딩합니다. `SOURCE_HUB_ADMIN_USER`와 `SOURCE_HUB_ADMIN_PASSWORD`를 비공개 프로세스 환경에 설정하고 아래 명령을 실행합니다. `collector_port`는 개발용 빈 포트입니다.

```fish
$hub --database $collector_db serve --port $collector_port
```

관리자 인증은 이 서비스 전용입니다. 다른 서비스의 SQLite는 설정 DB로 열 수 없습니다. 관리자 화면의 수동 수집·재시도, 스케줄 연동과 일일 알림은 [통합 수집기 계획](../../docs/plans/source-hub-erp-v1.1.md)을 따릅니다.

## 고용노동부 RSS 수동 수집

`moel-rss-notice`, `moel-rss-policy`, `moel-rss-lawinfo` 세 채널을 지원합니다. 기본 상태는 OFF입니다. 실행마다 `robots.txt`를 확인하고, 등록된 공식 피드만 요청합니다. 제목·게시일·공식 링크를 내부 DB에 저장합니다. [공식 RSS 안내](https://www.moel.go.kr/site/rss/rssList.do) · [저작권 정책](https://www.moel.go.kr/site/copyright/copyrightList.do)

새 개발용 설정 DB의 예시입니다. 기존 DB에서는 `list`로 조회한 현재 `config_version`을 사용합니다.

```fish
set materials_db "$PWD/services/source-hub/data/materials.db"
$hub --database $collector_db set source moel-rss-notice '{"enabled": true}' --version 0 --reason '공식 RSS 수동 검증'
$hub --database $collector_db collect moel-rss-notice --materials $materials_db
$hub --database $collector_db documents moel-rss-notice --materials $materials_db
$hub --database $collector_db runs moel-rss-notice
```

첫 성공 응답의 `baseline_count`는 시작 시 이미 있던 게시물입니다. 후속 실행에서는 신규와 수정 건수를 표시합니다. 소스·기관·그룹 중 하나라도 OFF이면 `disabled`, 수집 간격이 지나지 않았으면 `not_due`이며 두 경우 모두 외부 요청은 0건입니다. 접근 제한은 `blocked`, 피드 구조 오류는 `error`로 남습니다. 수집 이력과 문서는 OFF 이후에도 조회할 수 있습니다.

### 공식 게시판 목록 대조

게시판 목록도 비교할 때 `--reconcile`을 추가합니다. 소스의 수집 간격과 별도의 목록 대조 간격이 지난 실행에서 최대 2페이지를 읽습니다. 기본 목록 대조 간격은 7일입니다.

```fish
$hub --database $collector_db collect moel-rss-notice --materials $materials_db --reconcile
```

RSS와 목록에서 같은 `bbs_seq`는 한 게시물로 저장합니다. 첫 대조에서 확인한 목록 전용 글은 기준선에 포함합니다. 목록에서 확인한 제목과 게시일은 다음 대조까지 유지합니다. `board_only_count`는 이번 RSS 응답에서 볼 수 없었던 목록 게시물 수입니다. 게시판 읽기나 검증이 실패하면 `partial`과 상세 오류를 출력하고 RSS 자료를 저장합니다. 게시물 상세와 첨부는 요청하지 않습니다.

## 재정경제부 수동 수집

`mofe-rss-press`, `mofe-rss-explanation`, `mofe-rss-notice`, `mofe-rss-policy`는 `--reconcile`로 공식 게시판 최대 2쪽을 비교합니다. `mofe-rss-economy`는 RSS를 읽습니다. `mofe-laws`, `mofe-notice`, `mofe-tax-reform`은 각 공식 목록 첫 쪽을 읽습니다. 각 출처는 기본 OFF입니다. 제목·게시일·공식 링크를 내부 DB에 저장하고 날짜가 없는 항목은 빈 값으로 둡니다. [공식 RSS 안내](https://mofe.go.kr/mn/siteguide/rssService.do?menuNo=2060000) · [저작권 정책](https://mofe.go.kr/mn/siteguide/drmp.do?menuNo=2030000)

## 국세청 수동 수집

`nts-notice`, `nts-rules`, `nts-tax-references`, `nts-withholding`, `nts-tools`는 공식 목록 첫 쪽을 읽습니다. `nts-tax-02`, `nts-tax-amendments`, `nts-developer-notices`는 연말정산 종합 안내에서 해당 자료 제목과 안내 페이지 링크를 기록합니다. 게시일이 제공되지 않으면 비워 둡니다. 모든 출처는 기본 OFF이고, 저장 범위는 내부 메타데이터입니다. [국세청 저작권 정책](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=6791&cntntsId=8169) · [연말정산 종합 안내](https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=2304&cntntsId=238938)

## 홈택스 자료실

홈택스 자료는 ERP 탭의 [홈택스 공식 홈](https://www.hometax.go.kr/)에서 **전체메뉴 → 기타 → 고객센터 → 자료실**로 이동해 확인합니다. 홈택스 수집원은 등록부에서 비활성 상태입니다. [국세청 공식 안내](https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350699)

## 기관별 최신 소식 검토와 내보내기

관리자 화면에서 검토하려면 자료 DB와 공개 파일을 함께 지정합니다. `news_feed`는 캘린더의 `LAWBOT_CALENDAR_NEWS_FEED`에 연결한 공개 파일 경로입니다. 두 서비스가 이 파일에 접근할 수 있게 구성합니다.

```fish
$hub --database $collector_db serve --port $collector_port --materials $materials_db --news-feed $news_feed
```

**공개 검토**에서 수집원과 검토 상태를 고르고 이전 승인판과 현재 수집판의 제목·게시일·공식 링크를 확인합니다. 공개 승인, 새 버전 승인, 철회에는 사유가 필요합니다. 이어서 **공개 스냅샷 갱신**을 누릅니다. 화면은 내보낸 파일의 일치 여부, 마지막 갱신 시각, 포함된 기관 게시물 수를 표시합니다. 운영 중인 캘린더에서 조회되는지는 그 서비스의 ERP 드로어에서 확인합니다.

### CLI에서 검토하기

`documents`에서 게시물 ID인 `upstream_id`와 현재 판 번호인 `revision_count`를 확인합니다. 공식 출처·공개 이용 조건·개인정보를 검토한 판을 승인하고 공개 파일을 내보냅니다. 공개 파일 경로는 설정 DB와 자료 DB에서 분리합니다.

```fish
$hub --database $collector_db documents moel-rss-notice --materials $materials_db
$hub --database $collector_db review-news moel-rss-notice $post_id $revision approve \
    --materials $materials_db --reason '공식 원문·공개 조건·개인정보 확인'
$hub --database $collector_db export-news --materials $materials_db --out $news_feed
```

새 판은 다시 검토합니다. 철회할 때는 `withdraw`를 기록한 뒤 같은 공개 파일에 다시 내보냅니다. 수집원 설정을 끄거나 바꿨을 때도 다시 내보내야 조회에 반영됩니다. 파일에는 제목·공식 링크·게시일과 게시판 상태를 제공합니다. 캘린더 서비스는 해당 파일을 읽습니다. [캘린더 설정](../law-calendar/README.md#기관별-최신-소식)
