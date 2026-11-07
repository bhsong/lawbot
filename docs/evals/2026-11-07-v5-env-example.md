# v5 기본 설정 예시 확인 (2026-11-07)

`.env.dev.example`과 `.env.example`이 모두 v5 조문과 v5b 임베딩을 선택하도록 맞췄다. 기본 예시는 등록된 v5b 모델 폴더 `models/v5/emb_v5b_fp16`을 사용한다. 두 조문 CSV의 매니페스트, 법률·하위법령 인코딩 한도 4,352·5,120, 간결형·전체형 프롬프트와 모델 서버 등록 ID를 확인했다. 기존 리랭커와 Pro 모델 파일 경로는 그대로 사용한다. [계획](../archives/v5-env-example.md)

- Red에서 `test_env_example_is_complete_without_secrets`가 v4 선택으로 실패했고, 예시를 수정한 뒤 통과했다. 기본 예시의 등록 모델을 쓰는 대역 브라우저 경로를 포함해 `python -m pytest -q`에서 923개가 통과했다(기본 선택에서 제외 13개).
- Ruff, Pyright 두 프로젝트, Python 문법, 캘린더 API 스키마와 두 프런트엔드 타입 검사·Vitest 각 19개·빌드 통과. 실제 모델 E2E·모델 스모크·품질 평가는 실행하지 않았다.
