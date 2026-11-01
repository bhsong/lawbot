# 기록: 법령 변경 수집기 PR 2 실제 법제처 확인

- 날짜: 2026-11-01
- 코드: `feat/monitor-lawgo` (PR #39). 계획: [docs/archives/monitor-lawgo.md](../archives/monitor-lawgo.md) 6절
- 장소: 맥미니, 개발 레포. `LAW_MONITOR_HOME`은 임시 폴더 (PR 1 확인 때 받은 저장소 클론을 다시 씀)
- 법제처: `services/monitor/.env`의 `LAW_OC`. 모델·운영 서비스와는 관계없음

## 결과

| # | 항목 | 기대값 | 실제값 |
| --- | --- | --- | --- |
| 1 | `replay --repo legalize-kr --from 4220f9dc --to 51fea869` | 법령 19건 모두 `amendment_reason`. 소득세법 시행규칙 `lawgo_forms` 5건 (21·23·100의2 시행 05-22, 24·24의5 시행 07-01), 서식 21 diff에 `CA6` | **OK**. 이벤트 70건 = 법령 19 + 별표서식 51. 법령 19건 모두 개정이유 있음. 소득세법 시행규칙 5건, 날짜가 기대와 같음. 서식 21 diff에 `+ … CA6` 줄. 71초 |
| 2 | 1번의 다운로드 | 바뀐 서식만 PDF·HWP | **OK**. PDF 51 + HWP 51 (별표서식 이벤트 수와 같음), 파일 46MB. 링크만 바뀐 서식은 받지 않음 |
| 3 | `replay --repo admrule-kr --from 70a4db47c5c^ --to 70a4db47c5c` | `amendment_reason`, 첨부 목록 | **부분**. 첨부 목록 `2027년 적용 최저임금안 고시.pdf`, 일련번호 2100000262710 → 2100000282670은 채워짐. `amendment_reason`·부칙은 비어 있음: **법제처 응답에 `개정문`·`제개정이유`가 null이고 본문(`조문내용`)도 빈 값**(고시 내용이 원문 PDF에만 있음). 수집기 문제가 아니라 데이터에 없음 |
| 4 | OC 없이 `collect` (`.env`를 잠시 옮기고 `LAW_OC` 해제) | 종료 코드 2, 메시지에 OC 없음 | **OK**. `법제처 인증키 LAW_OC가 없습니다 (legalize_law, legalize_admrule)`, 종료 코드 2 |
| 5 | `uv run pytest -m network services/monitor` | 1 passed | **OK** (처음에는 테스트가 응답 바깥 키 `법령`을 빼먹어 KeyError → 테스트 고침) |

## 교차 확인

- **바뀐 서식 목록과 이벤트가 일치**: 부모 법령 이벤트의 `context.lawgo.attachment_effective`(법제처 `별표시행일자문자열`)에 적힌 서식과 실제로 나온 별표서식 이벤트를 비교했다.
  - 소득세법 시행규칙: 5 = 5, 같음.
  - 조세특례제한법 시행규칙: 이벤트 46, 법제처 목록 53. 남는 7개는 모두 `별표`(별표4·6·6의2·6의3·7·7의2·7의3)인데, 설정이 `kinds: [서식]`이라 의도대로 빠진 것이다. 서식만 보면 46 = 46.
- **OC가 남지 않음**: 실행 데이터 폴더 전체(`data/`, `logs/`)에서 OC 값을 찾으면 0개.
- 조세특례제한법 시행규칙(2026-03-20 개정)은 서식 33개가 바뀌고 13개가 새로 생겼다 (`attachment_added`). 한 개정에서 별표서식 이벤트가 수십 건 나올 수 있으므로 2단계에서 부모 이벤트로 묶어 요약하는 편이 좋다 (`context.parent_event_id`).

## 알게 된 점

- 일부 고시(최저임금 고시처럼 원문 PDF만 있는 것)는 법제처 API에 개정문·제개정이유·본문이 없다. 이 경우 이벤트의 원문은 admrule-kr 본문 diff와 `context.lawgo.files`의 원문 PDF 링크뿐이다. PDF 텍스트 추출은 PR 2 범위 밖(계획 5절)이라, 필요하면 2단계 전에 따로 정한다.
