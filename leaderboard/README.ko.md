# newmean-pii 리더보드

감지기가 개인정보를 얼마나 놓치고, 개인정보가 아닌 것을 얼마나 가리는가? 모든 모델은 같은 설계의 두 테스트셋에서
채점한다. [newmean-pii-dataset (공개 테스트)](https://huggingface.co/datasets/newmean-ai/pii-dataset)(합성 텍스트
17,058개, 54개 언어)과 공개하지 않는 비공개 쌍둥이 테스트셋이다. [English](README.md)

이 폴더는 Hugging Face Space(`newmean-ai/pii-leaderboard`, 정적 페이지 `index.html` + `lb.js`, 서버·비밀값 없음)이자 매주 도는
채점 Job의 코드다.

## 보여 주는 것

- **newmean-pii-dataset (공개 테스트)**: 전체, 라벨별, 언어별, PII 등급별.
- **newmean-pii-dataset (비공개 테스트)**: 전체 수치만. 행 단위 출력, 라벨별·언어별 수치는 만들지 않는다.

지표(가리기 관점, `training/scripts/evaluate.py`·기술 보고서와 같은 코드):

| 지표 | 뜻 |
|---|---|
| 놓침 % (leak) | 어떤 예측 구간에도 덮이지 않은 PII 글자 비율. 낮을수록 좋고 기본 순위 |
| 과잉 가림 % | 가려진 비PII 글자 비율. 낮을수록 좋음 |
| exact F1 | 경계와 라벨이 정답과 같은 예측 구간 |
| span F1 | 라벨이 같고 50% 이상 겹침 |
| 개체 놓침 % | 글자의 절반 미만만 가려진 PII 값의 비율 |

모델이 2등급 라벨 `PERSON_L2` / `URL_L2`를 내지 않으면 정답 라벨을 그 모델의 세분도로 합쳐서(`PERSON_L2` → `PERSON`,
`URL_L2` → `URL`) 채점한다. 놓침과 과잉 가림은 라벨과 무관하다. 모델만 평가하고 정규식 층은 붙이지 않는다.

## 모델 제출

1. Space의 **Submit** 탭에서 Hugging Face로 로그인한다(`write-discussions` 범위).
2. 공개 Hub 모델 id와 리비전을 넣고 어댑터를 고른다.
   - `transformers`: 토큰 분류 모델(`…ForTokenClassification`, safetensors 가중치, 사용자 코드 없음, 3 GB 이하)을
     transformers pipeline(`aggregation_strategy="simple"`)으로 돌린다. 모델의 각 라벨(B-/I- 접두사 제외)을 우리 라벨
     `PERSON`, `PERSON_L2`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `URL_L2`,
     `DATE_OF_BIRTH`, `SECRET` 또는 `OTHER`(가리지만 맞는 라벨 없음), `ignore`(가리지 않음)로 옮기는 **라벨 맵**을 넣는다.
     `auto`는 `evaluate.py`의 이름 기반 일반 매핑을 쓴다. IP 주소는 우리 라벨 체계에서 `ID_NUMBER`다.
   - `newmean-onnx`: newmean 모델 `newmean-ai/pii-<size>`. SDK가 기본으로 받는 가중치 파일로 채점한다(`variant`:
     large·medium은 `q4e`, small·nano는 `q4`).
   - 그 밖의 형식(GLiNER, 다른 ONNX 구성 등)은 관리자가 어댑터로 추가한다. Space에 discussion을 열어 달라.
3. **Check**는 공개 Hub API로 브라우저에서 가벼운 검사(공개·비게이트, safetensors, 크기, 사용자 코드 없음, 토큰 분류 구조,
   라벨 맵 범위)를 한다. **Submit**은 `newmean-ai/pii-leaderboard-requests`에 요청을 JSON 블록으로 담은 discussion을 연다.
   같은 형식으로 직접 열어도 된다. 주간 Job이 다시 검사하고, 계정당 최근 7일 2건과 대기열 상한을 적용해 채점한 뒤
   discussion에 점수나 거절 사유를 답하고 닫는다.

**newmean-pii-dataset (공개 테스트)도 주 1회 채점하는 이유.** 공개셋은 공개되어 있으므로 `scripts/score_public.py`(같은 지표)로 누구나 바로
자기 점수를 낼 수 있다. 리더보드가 몇 분 안에 채점해도 더해지는 정보가 없다. 주 1회 Job 하나로 돌리면 Space에 유료 Job을
띄울 수 있는 토큰을 둘 필요가 없고, Job 준비 시간을 대기열 전체에 나눠 쓰며, 비용이 예측 가능하고(한 번 실행, 시간 상한),
비공개셋 탐색이 느려진다.

## 채점 방식

`jobs/run_queue.py`가 `newmean-ai` 네임스페이스의 예약 Hugging Face Job으로 돈다. 열린 제출 discussion을 읽어 하나씩 다시
검사하고(거절은 답글을 달고 닫음), 자체 장부를 `requests/<id>.json`에 둔다. 그다음 대기 요청마다(오래된 순) 모델 파일을 받고,
어댑터 전용 uv 환경(`jobs/requirements-<env>.txt`, 보고서 L4 실행과 같은 라이브러리 버전)의 자식 프로세스
(`jobs/score_model.py`)에서 Hub 토큰 없이, Hub 오프라인 상태로 채점한다. `results/<model>@<revision>__<adapter>.json`을 쓰고
discussion에 답한 뒤, 페이지가 읽는 두 파일 `leaderboard.json`(결과 데이터셋)과 `queue.json`(요청 데이터셋)을 올린다.
비공개 점수가 아직 pending인 결과(저장된 보고서로 시드한 행)는 같은 실행에서 비공개셋으로 채점한다. 제출 모델의 자체 코드는 절대 실행하지 않는다
(`trust_remote_code=False`, safetensors만).

| 저장소 | 내용 |
|---|---|
| Space `newmean-ai/pii-leaderboard`(정적) | 이 폴더: `index.html`, `lb.js`, Job 코드 `pii_leaderboard/`, `jobs/` |
| 데이터셋 `newmean-ai/pii-leaderboard-requests` | 제출 discussion, 장부 `requests/<id>.json`, `queue.json` |
| 데이터셋 `newmean-ai/pii-leaderboard-results` | `results/<key>.json`(공개셋: 전체·라벨·언어·등급, 비공개셋: 전체), `leaderboard.json` |
| 데이터셋 `newmean-ai/pii-dataset` | newmean-pii-dataset (공개 테스트) |
| 데이터셋 `newmean-ai/pii-multilingual-test-private` | newmean-pii-dataset (비공개 테스트). 채점 Job 토큰만 읽을 수 있음 |

Space에는 비밀값이 없다. Job에는 이름으로 하나: `HF_TOKEN`(테스트셋·Space 읽기, 요청·결과 데이터셋과 그 discussion 쓰기).

### 채점 Job

Job 명령이 Space에서 이 코드를 받아 실행하므로 로컬 파일을 올리지 않는다(로컬 스크립트 파일을 주면 `hf jobs uv run`이
`jobs-artifacts` 버킷을 만든다).

```bash
hf jobs run --namespace newmean-ai --flavor l4x1 --timeout 1h -s HF_TOKEN -e ORT_THREADS=8 -e PII_LB_HARDWARE=l4x1 \
  ghcr.io/astral-sh/uv:python3.12-bookworm bash -c \
  'uvx --from huggingface_hub hf download newmean-ai/pii-leaderboard --repo-type space --include "pii_leaderboard/*" --include "jobs/*" \
     --local-dir /code && PII_LB_CODE=/code uv run /code/jobs/run_queue.py'
```

값 없는 `-s HF_TOKEN`은 로컬 로그인 토큰을 Job 비밀값으로 넘긴다(명령과 로그에 나타나지 않음). 예약 Job(매주)은 같은 명령을
전용 fine-grained 토큰(위 저장소 다섯 개의 읽기·쓰기만)으로 돌린다. `ORT_THREADS`는 하드웨어의 vCPU 수로 둔다(Job 안의 CPU 수는 호스트
값이 보임). 관리자는 `scripts/admin_request.py`로 요청을 바로 넣는다(우리가 추가한 어댑터, 재채점, 시험 실행).

## 시드 결과

첫 행들은 기술 보고서의 Colab L4 실행(2026-10-30) 공개셋 결과다. newmean 모델 4종(티어별 SDK 기본 가중치 파일: large·medium은 `model_q4e.onnx`, small·nano는
`model_q4.onnx`), 공개 감지기 8종,
정규식 기준선(`scripts/seed_results.py`). Job의 첫 실행(2026-10-31)이 시드 13행 모두를 newmean-pii-dataset (비공개 테스트)로 채점했다.
새 제출은 다음 주간 실행이 채점할 때까지 "pending"으로 보인다.

## 개발

```bash
# 테스트: 지표가 evaluate.py와 같음(정의 AST 동일, 시드 감지기 13종 모두 저장된 수치 재현), 브라우저와 Job 검사의
# 일치(node), discussion 접수와 제한, 로컬 저장소에서 Job 전 과정
uv run --no-project --python 3.13 --with pytest --with onnxruntime==1.31.1 --with transformers==5.21.0 --with numpy \
  --with huggingface_hub python -m pytest tests -q
# 로컬 저장소로 페이지 미리보기(http://localhost:8000, ./preview/를 읽고 아무것도 올리지 않음)
python scripts/seed_results.py --reports <reports 디렉터리> --out /tmp/lb && python scripts/preview.py /tmp/lb
```

브라우저 테스트는 node가 필요하다. newmean-pii-dataset (공개 테스트)와 newmean 모델은 Hub에서 받는다(로컬 사본은 `tests/conftest.py` 참고).
수치 동일성 테스트는 관리자가 보관한 L4 저장 예측이 있어야 하며(공개하지 않음), 없으면 건너뛴다. 같은 이유로 결과 시드
(`scripts/seed_results.py`)는 관리자만 하는 단계다.
`pii_leaderboard/scoring.py`는 `training/scripts/evaluate.py`에서 그대로 가져온 것이므로 지표는 그쪽에서 고치고 다시 가져온다.

코드: Apache-2.0. 테스트 데이터: CC BY-NC 4.0. 만든 곳 [newmean.ai](https://newmean.ai).
