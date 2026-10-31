[English](README.md) | 한국어

# training/

공개한 모델과 공개 평가 결과를 만든 스크립트입니다: 학습, 증류, 압축, 내보내기, 채점. 이 폴더에서 실행합니다(`cd training`).
`data/`, `models/`, `reports/` 같은 경로는 이 폴더 기준입니다.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

GLiNER 기반 탐지기는 환경을 따로 만들어야 합니다. [requirements.txt](requirements.txt) 끝의 주석을 보세요.

## 스크립트

| 스크립트 | 하는 일 | 주요 입력 |
|---|---|---|
| `scripts/train_ner.py` | 교사·학생 모델의 토큰 분류(BIO) 미세조정. 교사 소프트 라벨로 지식 증류, EMA 가중치, 에폭별 스냅샷 선택 가능 | 학습·검증 JSONL(`--train`, `--dev`), 기반 모델(`--base`), 소프트 라벨(`--soft`), 학생 등급(`--arch`) |
| `scripts/teacher_soft.py` | 교사의 소프트 라벨을 문자 구간 단위로 저장(토크나이저와 무관한 증류 목표) | `train_ner.py`의 `final/` 폴더(`--model`), 학습 JSONL(`--data`) |
| `scripts/prune_xlmr.py` | mMiniLMv2의 XLM-R 어휘를 54개 언어가 쓰는 조각으로 줄이고 사전학습 임베딩은 그대로 유지 | 학습 JSONL(`--train`), 어휘 크기(`--max`) |
| `scripts/student_arch.py` | 압축 학생 등급(인수분해 임베딩, 층 제거, 한 층을 여러 번 반복 적용). `train_ner.py`, `evaluate.py`, `export_onnx.py`가 불러 씀 | — |
| `scripts/textnorm.py` | 유니코드 정규화기가 없는 토크나이저용 문자 단위 NFKC와 원문 위치 대응표 | — |
| `scripts/select_ckpt.py` | 검증 세트에서 언어·라벨 매크로 누락+과마스킹 비용, 다음으로 정확 경계 F1로 체크포인트 선택 | 저장된 예측 `reports/preds/<tag>/<set>.jsonl`(`--set`, `--tags`) |
| `scripts/export_onnx.py` | 학습한 모델을 ONNX로 내보내고 양자화(MatMul 가중치 4비트, `--emb4`면 임베딩도 4비트) | `train_ner.py`의 `final/` 폴더(`--model`) |
| `scripts/export_web.py` | SDK가 내려받는 모델 폴더(`models/web/pii-<tier>`: ONNX, 토크나이저, 설정)와 SHA-256 매니페스트 생성 | `models/onnx/s-<tier>-v1`의 `export_onnx.py` 출력 |
| `scripts/export_wasm_weights.py` | 웹용 ONNX 파일을 SDK 내장 WebAssembly 엔진(`js/engine/pii.c`)이 읽는 `.piiw` 가중치 파일로 변환. 형식은 docstring에 정의 | `models/web/pii-<tier>`(`--src`) |
| `scripts/model_cards.py` | Hugging Face 모델 카드를 쓰고 LICENSE와 NOTICE를 옆에 복사 | `models/web`(`--web`) |
| `scripts/evaluate.py` | 모델이나 공개 탐지기 채점: 문자 누락률(leak), 과마스킹, 구간 F1과 정확 경계 F1을 라벨·언어·개인정보 등급별로 | `--model`(`regex`, 로컬 또는 Hub 모델, `onnx:<dir>[:<file>]`, `ext:<detector>`), `data/eval/<name>.jsonl`에서 읽는 세트 이름(`--sets`) |
| `scripts/compare_models.py` | 한 세트에서 두 탐지기를 짝지은 부트스트랩으로 비교 | `evaluate.py` 두 번의 실행에서 저장된 예측(`--a`, `--b`, `--set`) |
| `scripts/public_test_eval.sh` | 공개 모델과 공개 탐지기를 공개 테스트 데이터셋에서 실행(보고서의 비교) | `data/eval/pii_multilingual_test.jsonl`, `FETCH=1`로 내려받는 모델 |
| `scripts/ext_data.py` | 외부 평가 세트 네 개를 원본 다운로드에서 다시 만들고 매니페스트와 대조. 각 데이터셋의 학습 분할 변환도 함 | `data/raw`의 원본 다운로드(`--raw`), `eval_sets/manifest.json` |
| `scripts/external_sets.py` | 외부 데이터셋 라벨을 우리 체계로 옮기는 매핑(`ext_data.py`가 씀) | — |
| `scripts/dedupe_splits.py` | 분할 간 완전 중복·근접 중복 제거(마스킹한 텍스트의 MinHash), 개인정보 값 겹침, 다양성 리포트 | JSONL 분할(`--split name=path`, `--order`) |

## 데이터

- **공개 테스트 데이터셋.** Hugging Face Hub의
  [newmean-ai/pii-multilingual-test](https://huggingface.co/datasets/newmean-ai/pii-multilingual-test). `data/test.jsonl`을
  `data/eval/pii_multilingual_test.jsonl`로 저장하면 `evaluate.py`와 `public_test_eval.sh`에서 쓸 수 있습니다.
- **학습·검증 데이터.** 이 저장소에는 없습니다.
  [데이터 요청 양식](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform)으로
  요청하면 제공합니다(연구용은 무료, 상업적 사용은 문의).
- **데이터 생성 코드는 공개하지 않습니다.** 프롬프트, 라벨링 가이드라인, 생성·검수 파이프라인은 비공개입니다. 방법은 기술 보고서
  부록 M([APPENDIX-methods.ko.md](../docs/report/APPENDIX-methods.ko.md))에 모두 적었습니다.

## 외부 평가 세트

외부 세트 네 개(`openpii_val`, `kdpii_test`, `nemotron_test`, `gretel_fin_test`)는 재배포하지 않습니다. 원본 다운로드에서
다시 만듭니다.

1. 원본을 `data/raw/`에 내려받습니다(라이선스와 데이터셋 리비전은 [eval_sets/manifest.json](eval_sets/manifest.json)의
   `sources`):

   | 출처 | `data/raw/` 아래 파일 |
   |---|---|
   | `ai4privacy/pii-masking-openpii-1.5m` (CC BY 4.0) | `openpii/data/train.jsonl`, `openpii/data/validation.jsonl` |
   | KDPII, Zenodo 레코드 16759166, v2 (CC BY 4.0) | `kdpii.json` |
   | `nvidia/Nemotron-PII` (CC BY 4.0) | `nemotron/train.parquet`, `nemotron/test.parquet` |
   | `gretelai/synthetic_pii_finance_multilingual` (Apache-2.0) | `gretel_fin/train-00000-of-00001.parquet`, `gretel_fin/test-00000-of-00001.parquet` |

2. `python scripts/ext_data.py eval`을 실행합니다. 매니페스트에 적힌 행을 골라 라벨을 우리 체계로 옮기고(`external_sets.py`)
   `data/eval/<set>.jsonl`을 쓰며, 파일의 SHA-256이 매니페스트와 다르면 오류로 끝납니다.

표본 추출과 라벨 매핑은 기술 보고서 부록 M15에 있습니다.
