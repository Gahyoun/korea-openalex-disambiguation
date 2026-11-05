# 공개 릴리스 단계 규칙 (2026-11-05)

KOAD의 작업 단위 판정(`work_decision`)이 1차 관문입니다. 여기의 규칙은 그다음에, 공개 릴리스가 실제로
내보낼 `(professor_uid, work_id)`에 적용됩니다. 모두 결정적이고 표준 라이브러리만 씁니다.

- 구현: [`src/koad/release_rules.py`](../src/koad/release_rules.py) · 테스트: [`tests/test_release_rules.py`](../tests/test_release_rules.py)
- 같은 규칙을 대시보드 저장소가 빌드와 공개 데이터 패치에 사용합니다
  (`professor-research-career-dashboard`: `backend/scripts/career_consistency.py`,
  `apply_verified_works.py`, `patch_release_*.py`). 두 구현은 같은 입력에 같은 결과를 내야 하며,
  바꿀 때는 함께 바꿉니다.

적용 순서는 아래 1 → 2 → 3이고, 사람이 내린 명시적 결정(3)이 자동 규칙(1·2)보다 우선합니다.

---

## 1. 경력 일관성 규칙 — KOAD를 통과한 동명이인 제거

**문제.** OpenAlex author ID가 동명이인의 논문을 흡수하면, ID 자체는 맞아 보여 KOAD가 그 논문들을
남깁니다. 실제 사례: 네트워크과학자의 ID에 식품과학 동명이인의 논문이 섞였고, 흔한 이름의 공저자 ID도
두 사람이 공유하고 있었습니다.

**정의.** 연구자 본인 authorship의 기관이 경력 기관(박사·박사후·교수) 중 하나와 일치하면 **기준 논문**
(anchor)입니다. 기관명은 `norm_institution()`으로 정규화합니다(악센트·괄호 안 캠퍼스·the/of/campus 무시).
기준 논문의 field·subfield·공저자 ID가 그 연구자의 기준선입니다.

기준 논문이 아닌 논문은

| 조건 | 판정 |
| --- | --- |
| OpenAlex domain이 전공의 home domain 안 | 유지 |
| 기준 논문과 공저자 2명 이상 공유 | 유지 (해외 방문·겸임 등) |
| 공저자 1명 공유 + field가 기준 논문에 없음 | 제외 `career_field_mismatch` |
| 공저자 0명 공유(공저자는 있음) + subfield가 기준 논문에 없음 | 제외 `career_subfield_mismatch` |
| 그 밖(단독 저자 등) | 유지 |

home domain: 물리·수학 = Physical Sciences, 화학 = Physical + Life Sciences, 생물 = Life + Health Sciences.
기준 논문이 3편 미만이면 판단하지 않습니다(`MIN_ANCHORS = 3`).

**보정 근거(공개 논문 255,856편 전수, 2026-11-05).**

| 규칙 | 제외 편수 | 대상 연구자 | 표본 판독 |
| --- | --- | --- | --- |
| "경력 기관과 하나도 안 맞으면 제외" | 17,030 | 2,098 | 방문연구·공동연구소·대형 국제협력 등 본인 논문 다수 포함 → 폐기 |
| 위 + subfield 밖일 때만 | 4,919 | 974 | 재료·박막 등 애매한 사례 다수 |
| home domain 밖으로 한정, 공저자 1명 공유 시 field 기준 (채택) | **1,038** | **260** | 의학·바이러스·경제·심리·식품 등 동명이인이 대부분 |

공저자 1명으로는 부족한 이유: 흔한 이름의 공저자 ID가 OpenAlex에서 합쳐져 동명이인 논문을 실제
공동연구자와 이어 주는 사례가 확인됐습니다.

**알려진 한계.** 박사기관·재직기관 자체에 동명이인이 있으면 기준 논문이 오염되어 규칙이 작동하지
않습니다(실제 사례: 한 수학자의 ID에 같은 박사기관·같은 재직 대학의 의학·생화학 동명이인 논문 수백 편).
이런 경우는 3의 명시적 결정으로 처리합니다. home domain 안의 같은 분야 동명이인도 이 규칙의 대상이
아니며 KOAD 1차 판정의 몫입니다.

---

## 2. 추가 author ID에서 누락 논문 회수 — 기관 × 시기

검토 workbook에 기록된 추가·보류 author ID의 논문을 받아, 다음을 **모두** 만족할 때만 그 연구자의
논문으로 인정합니다(`accept_candidate()`).

1. authorship 기관이 경력 기관이고 출판연도가 그 경력 구간 ±1년 안
2. field가 `allowed_fields()`에 속함: 기준 논문의 15% 이상(2편 이상)을 차지하는 field + 전공 field
3. 저자 표기가 기존 공개 논문의 표기와 일치(`name_matches()`): 전체 이름 일치, 또는 이니셜만 쓴
   표기의 이니셜 일치. 'Sangmin Lee'는 'Sang-Kwon Lee'와 일치하지 않습니다
4. 학술지 유형(article·review·letter·editorial)

**보정 근거.** 206명·406개 ID·후보 29,130편 중 기관×시기 일치 740편 → field 조건으로 561편(대형
의대가 있는 대학에서 같은 시기 의학·생명 동명이인 179편 제외) → 이름 조건으로 516편(한 ID에 서로 다른
두 사람 등 74편 제외) → 공개 선정 규칙 적용 후 487편 추가.

**경력 구간 보강.** 논문이 추가된 연구자에 대해, 박사학위 이후·첫 교수 구간 이전에 경력에 없는 같은
기관 소속 공개 논문이 2편 이상이면 박사후 구간(`medium`, "박사 후 첫 교수 임용 전 논문 소속; 공식
직함 미확인")을 추가합니다. 기존 구간과 겹치거나 교수 구간에 닿으면 추가하지 않습니다.

---

## 3. 명시적 결정 — 사람이 확인한 것

사람의 확인은 자동 규칙보다 우선하며, 모든 변경은 이전 값·근거와 함께 감사 테이블
(`manual_work_verification_audit`)에 남습니다. 원본 DB는 바꾸지 않고 사본에만 적용합니다.

```json
[{"professor_uid": "prof:…",
  "source": {"kind": "google_scholar_profile | physics_openalex_mapping_review | career_institution_period_match | explicit_keep_decision",
             "checked": "YYYY-MM-DD", "…": "…"},
  "works": [{"work_id": "W…", "author_id": "A…" }],
  "rejected_works": [{"work_id": "W…", "note": "…"}]}]
```

- `works` (확인된 본인 논문): 원본에 없으면 그 authorship의 author ID로 가져와 `keep` + `true_paper`.
  author ID가 없는 authorship은 자동으로 가져오지 않고 보고만 합니다.
- `rejected_works` (확인된 동명이인 논문): `namesake_paper`.
- 명시적 유지 결정(explicit keep): ID가 심하게 섞인 연구자에 대해 "OpenAlex field = X 또는 학술지명이
  정규식 Y에 맞는 논문만 본인 것"으로 정하고, 나머지를 `rejected_works`로 기록합니다.

## 재현

대시보드 저장소에서(키는 환경변수로만 읽고 출력하지 않습니다):

```bash
# 비공개 DB 사본에 명시적 결정 반영 → 정식 재빌드
OPENALEX_API_KEY=… python3 backend/scripts/apply_verified_works.py \
  --source-db SRC/web.sqlite --decisions-db SRC/identity_decisions.sqlite --verified decisions.json --out-dir OUT
KOAD_DECISIONS_DB=OUT/identity_decisions.sqlite backend/scripts/update_release.sh 2026 OUT/web.sqlite
```

규칙 1은 빌드의 `STRICT_IDENTITY_PREDICATE`가 `career_consistency.prepare()`를 통해 자동 적용합니다.
비공개 DB 없이 공개 릴리스만 고칠 때는 `patch_release_namesakes_bulk.py`(규칙 1),
`patch_release_verified_works.py`(규칙 2·3), `patch_release_career.py --auto`(경력 구간),
`patch_release_explicit_keep.py`(명시적 유지)를 같은 입력으로 실행합니다.
