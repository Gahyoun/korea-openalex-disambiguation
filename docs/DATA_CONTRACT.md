# 데이터 계약

## 입력 SQLite

현재 adapter는 다음 테이블을 읽습니다.

- `professors`: `professor_uid`, `name`, `subject`, `openalex_id`, `phd_year`
- `education`: `professor_uid`, `degree_level`, `institution_unit_id`, `confidence`, `education_id`
- `career_positions_v2`: `professor_uid`, `institution_unit_id`
- `work_authorship_evidence`: 논문 메타데이터, prior identity decision, anchor flags, coauthor IDs
- `raw_affiliation_units`: work별 정규화 기관·학과 단위

읽기 전용·immutable 모드로 연결하므로 1차 판정은 입력 DB를 변경하지 않습니다.

## 1차 JSONL 공통 키

- 식별: `professor_uid`, `professor_token`, `work_id`
- 서지: `publication_year`, `publication_date`, `title`, `topic_name`, `field_name`, `domain_name`
- 결과: `decision`, `confidence`, `recommended_action`, `score`
- 기준점: `debut_year`, `is_seed`
- 증거: `core_coauthor_connected`, `institution_connected`, `department_compatible`, `topic_similarity`, `graph_path_connected`, `orcid_exact`, `orcid_only`
- 감사: `prior_decision`, `reasons`

`decision`은 `true_paper|namesake_paper`, `recommended_action`은 `keep|drop|review`입니다.

## 교수 요약

`professor_identity_summary.jsonl`에는 seed 출처, 박사 추정 시작연도, 진짜 데뷔연도, seed work IDs, confidence별 논문 수가 저장됩니다. 입력 명부에는 있으나 논문 증거가 없는 교수도 `seed_source=no_works`로 남습니다.

## 최종 overlay

`final_identity_overlay.sqlite`의 `final_identity_decision`은 stage 1과 stage 2를 나란히 저장합니다. 원본 `work_authorship_evidence`와 결합할 때는 `professor_uid` 대신 공개 가능한 `professor_token` 매핑이 필요하므로, 내부 적용 파이프라인에서는 stage-1 DB의 `(professor_uid, professor_token)`을 사용해 조인합니다.

