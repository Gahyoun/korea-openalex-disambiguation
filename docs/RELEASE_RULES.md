# 공개 릴리스 단계 규칙 (2026-11-05)

KOAD의 작업 단위 판정(`work_decision`)이 1차 관문입니다. 여기의 규칙은 그다음에, 공개 릴리스가 실제로
내보낼 `(professor_uid, work_id)`에 적용됩니다. 모두 결정적이고 표준 라이브러리만 씁니다.

- 구현: [`src/koad/release_rules.py`](../src/koad/release_rules.py) · 테스트: [`tests/test_release_rules.py`](../tests/test_release_rules.py)
- 같은 규칙을 대시보드 저장소가 빌드와 공개 데이터 패치에 사용합니다
  (`professor-research-career-dashboard`: `backend/scripts/career_consistency.py`,
  `apply_verified_works.py`, `patch_release_*.py`). 두 구현은 같은 입력에 같은 결과를 내야 하며,
  바꿀 때는 함께 바꿉니다.

적용 순서는 아래 1(1b 포함) → 2 → 3이고, 사람이 내린 명시적 결정(3)이 자동 규칙(1·2)보다 우선합니다.

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

### 1b. 경력 충돌 규칙 — 이력과 명백히 다른 확인 (`career_conflict()`, 2026-11-11)

**문제.** 경력상 연구자가 특정 기관에 있었던 시기에 다른 대학 소속으로 나온 논문은 동명이인의 것입니다.
예를 들어 성균관대 박사과정 중에 충북대·부산대 소속으로 나온 식품·토양 논문입니다.

**판정.** 아래를 **모두** 만족하면 제외합니다(`career_conflict`).

- 경력 기준 논문이 3편 이상 있습니다.
- 본인 authorship의 기관이 모두 경력 기관 밖이고, 모두 대학(OpenAlex type `education`)이나 병원(`healthcare`)입니다.
  - 연구소(IBS, Inserm, Max Planck 등)는 학생·교원이 함께 소속되므로 충돌 근거가 되지 않습니다.
  - 기준 논문 기관과 OpenAlex 계보(lineage)가 같은 기관은 같은 곳으로 봅니다.
  - 핵심 이름(일반어 제외)이 서로 포함되는 기관도 같은 곳으로 봅니다(Stanford Medicine ~ Stanford University).
- 출판연도가 박사 또는 교수 구간 안입니다. 단, 다음 연도는 이전·다음 기관이 실릴 수 있어 제외합니다.
  - 모든 구간의 첫 두 해(출판 지연)와 마지막 해
  - 교수 구간 종료 전년도(이직)
- 박사 구간이면 소분야도 기준 논문에 없어야 합니다. 박사 시작은 추정값이고 박사 기관명은 약어가 많으며,
  석사 연구가 늦게 출판되기 때문입니다.
- 기준 논문과 공저자 공유가 0명이거나, 1명이면서 소분야가 기준 논문에 없어야 합니다. 2명 이상이면 실제
  공동연구나 방문으로 봅니다.

**보정 근거(공개 논문 257,996편).**

| 규칙 | 제외 편수 | 대상 연구자 | 표본 판독 |
| --- | --- | --- | --- |
| 경계 ±1년, 기관 유형·계보 조건 없음 | 3,128 | 586 | 연구소 공동 소속, Stanford Medicine/University 같은 이름 차이 포함 → 폐기 |
| 대학·병원만, 계보·핵심 이름 일치 제외 | 1,964 | 294 | 대부분 동명이인. 경계 ±1년이 박사 종료 전년도 사례를 놓침 |
| 경계를 첫 두 해·마지막 해로 좁힘 | +152 | +79 | 출판 지연·박사 기관명 약어 오판이 나타남 → 박사 구간에 소분야 불일치 조건 추가 |
| **채택** | **1,849** | **265** | 예: 물리학자에게 섞인 유기태양전지 화학자 논문, 생물학자들에게 섞인 수의학 논문 |

공개 릴리스에는 `patch_release_namesakes_bulk.py --rule conflict --inst-meta institutions.json`으로 적용했습니다.
뺀 논문은 대시보드 저장소의 `data/private_encrypted/career_conflict_20261111.json`에 비공개 DB용
`rejected_works` 명세로 남겨 두었습니다.

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
- 상호보충 확인 요구(`require_corroboration`, `corroborated()`): author ID에 동명이인이 섞인 것이 확인된
  연구자는 논문마다 두 확인이 **함께** 있어야 남습니다.
  - 신원: authorship 기관이 경력 기관이고 그 구간 ±1년 안, 또는 경력 기준 논문과 공저자 ID 공유
  - 주제: 경력 기준 논문에 있는 subfield, 또는 전공 home domain

  한쪽만으로는 부족합니다. 흔한 이름의 공저자 ID도 합쳐지고, 동명이인이 인접 분야에 있을 수 있기 때문입니다.
  실제 사례: 통계물리학자의 ID에 식품과학 동명이인의 심포지엄 발표·국내 학술지 논문 6편이 섞여 있었습니다.
  이 중 2편은 공저자 ID를 하나 공유했고, 4편은 소속도 공저자도 없어 규칙 1의 '단독 저자 보호'로 남아
  있었습니다. 이 결정으로 6편만 빠지고 본인 논문 25편은 모두 남았습니다.
  공개 릴리스에는 `patch_release_namesakes.py --rule corroboration`으로 적용합니다.

## 4. 저수량 연구자 전수점검 — 매핑 실패·ID 분할 회수

공개 논문 30편 미만 연구자를 대상으로 합니다. 대시보드 저장소 `backend/scripts/low_count_audit/`에
구현이 있고, 절차와 규칙은 그 README에 있습니다. 규칙 2를 일반화했으며 차이는 다음과 같습니다.

- **후보 출처:** 추가 author ID 대신 Crossref 저자명 검색을 씁니다. 이름은 한글 이름의 엄격한 로마자 변형으로
  맞춥니다(성씨 변형표와 음절별 변형, 이니셜만인 표기는 불가).
- **현재 재직 기관의 시기:** 마지막 비교수 구간 이후 전체를 인정합니다. 교수 명부가 최근 연도만 관측해
  재직 시작을 잘라 버리기 때문입니다(실제 사례: 2023년만 관측된 서울대 교수).
- **일관성:** 공저자 또는 ID로 연결된 묶음 중 공개 논문이나 박사·박사후 구간에 닿는 묶음만 자동 반영합니다.
  그런 묶음이 없으면 전체의 80% 이상인 단일 묶음만 반영합니다.
- **의학 쪽 제외:** 병원·의대 소속 authorship과 의학·간호·보건·치의학·약학 분야는 자동 반영하지 않습니다.
- **분량 이상치:** 비교수 구간에 연 6편이 넘게 추가되면 동명 PI로 보고 전부 검토로 보냅니다.

**박사후 구간 근거의 한계.** 공동연구 소속이 이후 연도까지 이어지면 자동 구간이 교수 시기까지 늘어날 수
있습니다(실제 사례: Cornell 2005–2020). 이런 경우 근거 논문을 박사후 시기로 한정한 명시적 구간을 씁니다.

**결과(2026-11-05).**

| 항목 | 값 |
| --- | --- |
| 대상 | 1,510명 |
| 1단계 분류 | 매핑 없음 478명, ID 논문 수가 공개분의 2배 이상 707명, 비슷 325명 |
| 자동 반영 | 642명 4,354편 (공개 릴리스 3,454편) |
| 검토 | 313명 794편 |
| 표본 정밀도 | 약 85–90% |

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
