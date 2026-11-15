# 정확 일치 캐시 런타임에서 P07 ZIP 의존 제거

- 작성: 2026-11-15
- 브랜치: `refactor/exact-cache-bundle-to-db`
- 사용자 결정(2026-11-15): 운영에서 ZIP 파일을 기준으로 데이터를 적재·확인·사용하지 않는다. ZIP은 최초 적재 명령의 입력으로만 쓴다.

## 현재 상태

`LAWBOT_EXACT_CACHE_BUNDLE`의 P07 ZIP을 런타임 세 곳이 매번 열고 SHA-256을 검증한다.

| 사용처 | ZIP에서 얻는 값 |
|---|---|
| 06:30 조문 갱신 이월(`cache_release_store.apply_release`) | 보호 문항 해시, 원본 답변 |
| 어드민 검수 화면(`cache_review_store`) | 보호 문항 해시, 원본 답변 |
| `cache_admin set-mode`(on/shadow) | 보호 지문 대조 |

운영 DB 사본으로 확인한 결과(2026-11-15, 읽기 전용):

- ZIP의 보호 해시 3,629개는 모두 코퍼스 DB의 val/test 분할에서 나오는 보호 목록에 이미 들어 있다. ZIP에만 있는 해시는 0개다.
- 보호 지문은 DB 분할과 상수(`P07_SHA256`, 정규화 버전)로만 계산한다. ZIP 내용과 무관하다.
- 운영 캐시 답변 120개는 모두 `cache_render_inputs`에 원본 답변이 있다. ZIP 원본 대체 경로를 쓰는 답변은 0개다.

따라서 운영 DB에 새로 적재할 데이터는 없다. 런타임의 ZIP 읽기만 제거하면 된다.

## 변경

1. `load_protection(db, extra)`는 ZIP 없이 DB 분할과 추가 보호 CSV로만 보호 목록을 만든다. 지문 계산식은 그대로 둔다. 그래야 저장된 `protection_hash`가 그대로 유효하다.
2. 이월과 검수 화면은 DB만 읽는다. 원본 답변은 `cache_render_inputs`에서만 읽는다. 원본이 없으면 기존처럼 `render_unavailable` 검토 대기로 둔다. `bundle_missing` 가드는 `protection_unavailable`로 바꾼다. 코퍼스 DB에 QA 분할이 없어 보호 목록을 만들 수 없으면 답변을 검토 대기로 둔다.
3. `cache_admin set-mode`는 `--bundle` 없이 활성 릴리스와 보호 지문만 대조한다.
4. `inspect`·`seed`·`approve`·`replace`는 P07 ZIP을 입력으로 받는 적재·검수 도구로 남긴다. ZIP의 보호 해시가 DB 보호 목록에 모두 들어 있지 않으면 실패한다.
5. `LAWBOT_EXACT_CACHE_BUNDLE` 설정, 시작 경고와 배포 점검 경고를 없앤다. `.env.example`과 `docs/operations.md`를 고친다.

## 완료 기준

- ZIP 파일이 없어도 이월·검수 화면·승인·모드 변경이 이전과 같은 판정을 낸다.
- 런타임 경로에서 `ZipFile`을 열지 않는다(테스트로 막아 확인).
- 저장된 보호 지문이 바뀌지 않는다.
- 기본 pytest 전체, ruff, pyright, 프론트엔드 빌드가 통과한다.

## 운영 반영(병합 후 사용자 승인 범위)

> #153으로 구현 완료. 남은 운영 반영 단계는 [검토된 비슷한 질문 추천 §5](../plans/reviewed-question-suggestions.md)로 옮겼다.

1. 운영 반영 대상과 함께 들어가는 main 변경(#148~#151)을 확인한다.
2. 반영한 뒤 운영 DB 사본으로 이월을 미리 계산해 결과가 이전 코드와 같은지 확인한다.
3. 운영 `.env`에서 `LAWBOT_EXACT_CACHE_BUNDLE`을 지운다.
4. 다음 06:30 조문 갱신의 캐시 판정을 확인한다.
5. ZIP 파일을 운영 트리 밖으로 옮기는 일은 별도로 승인받는다.
