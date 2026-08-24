# KOAD: Korea–OpenAlex Author Disambiguation

한국 대학 교수 명부·학위·재직 이력과 OpenAlex 논문을 결합해 동명이인 논문을 판별하는 재현 가능한 Python 라이브러리입니다. 1차 단계는 비용 없이 결정적으로 실행되며, 애매한 사례만 선택적으로 Claude Code, Gemini CLI, Antigravity CLI에 보낼 수 있습니다.

## 핵심 원칙

- 박사 취득 전 5년부터 박사 취득년까지를 박사 논문 탐색 구간으로 사용합니다.
- 첫 seed는 **박사기관뿐 아니라 학과/전공까지 맞아야** 합니다. 같은 KAIST라도 물리학과와 기계공학과는 동일 근거가 아닙니다.
- seed 논문군과의 핵심 공저자, 당시 기관, 주제 거리, 논문 그래프 경로를 함께 봅니다.
- ORCID는 보조 증거입니다. OpenAlex 저자 프로필 자체가 합쳐졌을 수 있으므로 ORCID만 일치하는 논문은 자동 보존하지 않습니다.
- 네트워크과학↔이산수학, 생태동역학 모델링↔수리생물학처럼 연결 증거가 있는 학제 연구는 보존합니다.
- 연구년·겸임·복수 소속은 논문 동일성 연결이 있으면 정상 이력으로 보존합니다.
- 원본 DB를 직접 삭제하지 않습니다. 최종 판단은 감사 가능한 overlay DB로 만듭니다.

자세한 판정식은 [docs/METHODOLOGY.md](docs/METHODOLOGY.md), 입출력 구조는 [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)를 참고하세요.

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

외부 Python 패키지는 필요하지 않습니다. Python 3.11 이상과 SQLite가 있으면 됩니다.

## 1차 자동 판별

전체 교수:

```bash
koad classify \
  --db /path/to/professor_affiliation_timeline_through_2026.sqlite \
  --output outputs/run-2026
```

한 교수 또는 논문 근거가 있는 100명 표본:

```bash
koad classify --db /path/to/input.sqlite --output outputs/one \
  --professor-uid prof:physics:phy0075

koad classify --db /path/to/input.sqlite --output outputs/sample \
  --limit 100 --with-works-only
```

결과는 `true_papers.jsonl`, `namesake_papers.jsonl`, `review_queue.jsonl`, `professor_identity_summary.jsonl`, `identity_decisions.sqlite`로 나뉩니다. 설정과 입력 DB SHA-256은 `run_manifest.json`에 기록됩니다.

## 2차 선택 판별

설치/로그인 상태 확인:

```bash
koad providers
```

먼저 전달될 익명 payload와 실행 명령만 확인할 수 있습니다.

```bash
koad adjudicate \
  --decision-db outputs/run-2026/identity_decisions.sqlite \
  --provider claude \
  --output outputs/run-2026/claude.jsonl \
  --limit 10 --dry-run
```

실제 판정에서는 `--dry-run`을 제거합니다. `--provider`는 `claude`, `gemini`, `antigravity` 중 하나입니다. 이름·ORCID는 외부 CLI에 보내지 않고, 해시 처리된 교수 토큰·논문 메타데이터·판정 증거만 전달합니다.

CLI 옵션은 각 공식 문서를 기준으로 구현했습니다: [Claude Code CLI](https://code.claude.com/docs/en/cli-usage), [Gemini CLI headless mode](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md), [Antigravity headless mode](https://antigravity.google/docs/cli/headless/).

## 최종 overlay

```bash
koad build-overlay \
  --decision-db outputs/run-2026/identity_decisions.sqlite \
  --adjudication outputs/run-2026/claude.jsonl \
  --adjudication outputs/run-2026/antigravity.jsonl \
  --output outputs/run-2026/final_identity_overlay.sqlite
```

1차 `high` 자동 판정은 그대로 적용합니다. 1차 review는 단일 CLI의 `high` 판정 또는 복수 CLI의 다수결이 있을 때만 해소합니다. CLI끼리 충돌하면 review로 남습니다.

## 테스트

```bash
python -m unittest discover -s tests -v
```

## 공개 저장소 주의사항

`outputs/`는 `.gitignore`에 포함되어 있습니다. 실명, ORCID, 원본 DB, API 키, 판정 산출물은 커밋하지 마세요. 라이브러리 코드와 합성 테스트만 공개 저장소에 올리는 구성을 권장합니다.
