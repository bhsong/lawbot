# 수집 설정 관리

법령·ERP 수집원 등록과 변경 이력을 관리합니다. 초기 YAML 위에 별도 SQLite 설정을 적용합니다. 검증과 dry-run은 저장소나 외부 사이트를 호출하지 않습니다. 설정이 연결되는 통합 수집기는 후속 단계에서 제공하며, 현재 `law-monitor daily`와 챗봇 조문 자동 갱신은 기존 작업으로 실행됩니다.

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

관리자 인증은 이 서비스 전용입니다. 다른 서비스의 SQLite는 설정 DB로 열 수 없습니다. 소스 검증·스케줄 연동 이후의 실제 수집 이력, 수동 수집·재시도, 일일 알림은 [통합 수집기 계획](../../docs/plans/source-hub-erp-v1.1.md)을 따릅니다.
