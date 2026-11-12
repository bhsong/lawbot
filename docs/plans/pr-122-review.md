# PR #122 재리뷰 반영

기준은 PR head `768ed44`와 대상 `main`의 `3b42ee9`다. 개발 브랜치 `fix/code-review-findings`를 이어서 사용한다. 기존 `docs/plans`에는 미완료 작업이 남아 있다. [재리뷰 코멘트](https://github.com/bhsong/lawbot/pull/122#issuecomment-6811487090)

## 이번 PR의 변경

1. 첫 답변이 30초를 넘겨도 이미 받은 두 번째 질문이 앞 질문 완료 후 생성되는 회귀를 가상 시계로 먼저 재현한다. 웹의 별도 시간 상한은 제거하고, 처리 슬롯당 대기 1건과 가득 찬 경우의 503 및 연결 종료 시 슬롯 회수는 유지한다.
2. 인용 표시의 두 오류를 합성 문장으로 재현한다. 인용 파서와 관련 신규 테스트는 #122에서 제외해 승인 캐시의 표시와 `DISPLAY_VERSION`을 보존한다. 인용·캐시 변경, 수집 ID 계약, 모델 서버 변경과 Git 시간 제한은 후속 계획에 기록한다. [후속 계획](code-review-followups.md)
3. Vite 8.3.4는 공식 보안 권고의 영향 버전이고 8.3.7에서 수정됐다. 두 프런트엔드 갱신을 유지하고 PR 본문에 권고와 개발 서버 조건을 적는다. [Vite 보안 권고](https://github.com/vitejs/vite/security/advisories/GHSA-p4f5-76q4-ffq7)
4. 마지막 코드 변경 뒤 기본 pytest 전체, 양쪽 프런트엔드 검사·빌드, 정적 검사와 스키마 검사를 실행한다. 전체 Green 뒤 같은 PR을 갱신하고 CI를 확인한다. 공유 모델 추론은 0회다.

## 타법 부칙 재생성 비교

승인 후 문서에 기록된 운영 원천 미러를 읽어 레포 밖 임시 폴더에 복사한다. 같은 폴더에 운영 DB에서 `v5-live-2026-11-11_3`의 조문 CSV·매니페스트만 내보낸다. DB 경로는 운영 설정에서 확인한다. Git의 지연 다운로드를 막고, 원천 객체가 부족하면 비교를 중단한다. 운영 트리·DB·서비스와 공유 모델에는 쓰기 요청을 보내지 않는다.

개발 Python으로 아래 명령을 사용한다. `corpus_db`는 확인한 운영 DB 경로다. `repo_copy`와 `release_csv`는 위 임시 입력 경로, `report`는 임시 보고 경로, `asof`는 승인 시 고정할 기준일이다. `main`의 생성기와 PR의 생성기를 같은 원천 HEAD와 기준일에 각각 실행해 현재 릴리스 대비 변화와 수정 자체의 차이를 구분한다. 기준 코드의 도구 소스는 `git archive`로 임시 위치에 추출해 `PYTHONPATH`로 불러온다.

```sh
LAWBOT_ENV_FILE='' .venv/bin/lawbot-db export-release --db "$corpus_db" \
  --release v5-live-2026-11-11_3 --out "$release_csv"
GIT_ALLOW_PROTOCOL='' GIT_NO_LAZY_FETCH=1 LAWBOT_ENV_FILE='' LAWBOT_PROFILE=v5 \
  .venv/bin/lawbot-articles-check --repo "$repo_copy" \
  --asof "$asof" --data-dir "$release_csv" --report-dir "$report"
```

종료 코드 0과 1은 각각 동일과 차이 발견이다. 종료 코드 2는 비교 실패로 기록한다. 현재 릴리스 대비 답변 근거가 바뀌는 조문 수·목록, 유효기간 판정 보류 수(리뷰 기준 31줄) 및 main과 PR 사이의 차이를 기록한다. 생성 중 임베딩·생성 호출, 자동 갱신, 재시작은 없다. 이전 원천 점검 사례를 감안해 최대 20분을 배정하고 임시 결과는 기록을 옮긴 뒤 정리한다. 이 증거는 리뷰가 요청한 병합 전 조건이다. [운영 절차](../operations.md#법령-데이터-버전-매니페스트) · [DB 릴리스 내보내기](../operations.md#db-코퍼스와-csv-내보내기)
