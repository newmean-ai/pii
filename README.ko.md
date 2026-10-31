[English](README.md) | 한국어

# @newmean-ai/pii

**브라우저 안에서 하는 개인정보 마스킹.** 텍스트 속 개인정보(이름, 이메일, 전화번호, 주소, 신분증 번호 등)를 **브라우저 안에서만** 찾아 가리므로, 원문이 사용자의 기기를 떠나지 않습니다.

- 54개 언어, 11개 레이블과 간접 식별자를 위한 수준 2 레이블 2개(IP 주소는 `ID_NUMBER`로 학습); 패턴 계층(이메일, URL,
  카드·계좌, 전화, 주민등록번호)은 선택이며 기본은 꺼짐
- 모델 크기 네 가지: **nano 2.6 MB**, **small 7.1 MB**, **medium 11.3 MB**(기본), **large 15.5 MB**
- WebAssembly로 실행: SDK 자체 내장 엔진(기본, 약 20 KB) 또는 onnxruntime-web
- 상업적 사용을 포함해 어떤 용도로도 무료
- 같은 모델로 Python, 모바일(Android, iOS, Flutter) 패키지를 준비하고 있습니다

```bash
npm install @newmean-ai/pii
```

```ts
import { createRedactor } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium" });
const { text, spans } = await redactor.redact("My email is kim@example.com");
// text: "My email is [EMAIL]"
```

전체 API는 [js/README.ko.md](js/README.ko.md)를 보세요.

## 링크

- Hugging Face 모델: [newmean-pii-nano](https://huggingface.co/newmean-ai/pii-nano) ·
  [newmean-pii-small](https://huggingface.co/newmean-ai/pii-small) · [newmean-pii-medium](https://huggingface.co/newmean-ai/pii-medium) ·
  [newmean-pii-large](https://huggingface.co/newmean-ai/pii-large)
- newmean-pii-dataset (공개 테스트): [newmean-ai/pii-dataset](https://huggingface.co/datasets/newmean-ai/pii-dataset)
- 데모: [pii.newmean.ai](https://pii.newmean.ai)
- 리더보드: [newmean-ai/pii-leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)
- 기술 보고서: [docs/report/REPORT.ko.md](docs/report/REPORT.ko.md)

## 결과

가려지지 않고 남은 PII 문자의 비율(놓침 %, 낮을수록 좋음)이며, 모델만 쓴 결과입니다. 우리 newmean-pii-dataset (공개 테스트)와, 우리 어떤 모델도 학습에
쓰지 않은 외부 데이터셋 네 개에서 쟀습니다. 과잉 마스킹, 파라미터 수, 속도를 담은 전체 표: [기술 보고서 6절](docs/report/REPORT.ko.md);
newmean-pii-dataset (공개·비공개 테스트)의 주간 점수: [리더보드](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

| 모델 | 다운로드 | 공개 테스트(54개 언어) | KDPII(한국어) | OpenPII(30개 언어) | Nemotron-PII(영어) | Gretel 금융 |
|---|---|---|---|---|---|---|
| **newmean-pii-large** | 15.5 MB | **1.6** | **7.6** | 4.6 | **7.4** | 20.3 |
| **newmean-pii-medium** | 11.3 MB | 2.1 | 9.8 | 6.1 | 7.9 | 22.5 |
| **newmean-pii-small** | 7.1 MB | 2.7 | 9.5 | 5.6 | 10.6 | 24.1 |
| **newmean-pii-nano** | 2.6 MB | 3.3 | 9.5 | 4.9 | 10.6 | 22.8 |
| gliner-pii-edge | 176 MB | 19.6 | 11.9 | 5.7 | 17.3 | **15.7** |
| OpenAI privacy-filter | 2.8 GB | 34.6 | 34.8 | 14.3 | 25.7 | 32.2 |
| Horizon pii-redactor-small | 563 MB | 41.0 | 25.7 | 11.1 | 7.8 | 31.6 |
| KoELECTRA Korean PII NER | — | 43.1 | 8.8 | 24.1* | 45.9 | 37.6 |
| GLiNER2-PII | 1.2 GB | 44.9 | 34.2 | 23.2 | 46.3 | 32.9 |
| Rampart | 15 MB | 56.5 | 36.1 | **3.5*** | 51.6 | 40.5 |
| Mozilla smart-pii-redactor | 15 MB | 56.9 | 46.0 | 25.9 | 48.8 | 32.9 |
| Rampart-edge | 1.5 MB | 74.7 | 89.0 | 46.1 | 55.5 | 48.0 |
| 정규식 기준선 | — | 75.3 | 62.0 | 61.1 | 44.6 | 86.7 |

읽는 법: 우리 모델은 newmean-pii-dataset (공개 테스트)와 같은 설계의 데이터로 학습했고, 다른 탐지기는 이 데이터를 처음 보며 일부는 다른 레이블링
정책(예: 조직명은 가리지 않음)을 따릅니다. 그래서 그 열은 각 탐지기가 이 마스킹 정책에 얼마나 맞는지를 보여 주고, 더 공정한 비교는
외부 데이터셋 네 열입니다. \* OpenPII 데이터로 학습했거나(Rampart) 그와 겹치는 데이터로 학습했습니다(KoELECTRA). GLiNER2-PII는
Gretel에서 라이브러리의 긴 글 모드로 돌렸습니다. 휴대폰(네 대 평균)에서 11.3 MB 모델은 40토큰 메시지에 약 26 ms가 걸립니다.

## 저장소 구성

| 폴더 | 내용 |
|---|---|
| [`js/`](js) | SDK `@newmean-ai/pii`와 테스트 페이지, 공개 데모(`playground/`), 휴대폰 벤치마크(`experiments/`) |
| [`docs/report/`](docs/report) | 기술 보고서와 부록 두 개(한국어판 포함) |
| [`leaderboard/`](leaderboard) | Hugging Face의 리더보드 Space 코드 |
| [`site/`](site) | pii.newmean.ai에서 데모와 휴대폰 벤치마크를 제공하는 Worker |
| [`tools/data-request/`](tools/data-request) | 학습 데이터 요청 방법(연구용 무료, 상업용 문의) |
| [`training/`](training) | 학습, 내보내기, 평가 코드(데이터 생성기는 포함하지 않음) |
| [`release/`](release) | newmean-pii-dataset (공개 테스트) 카드 |

## 라이선스

- SDK 코드와 모델 가중치: [Apache-2.0](LICENSE).
- 제품에 쓰신다면 문서나 소개·라이선스 화면에 "Powered by newmean.ai"를 표시해 주시면 감사하겠습니다(라이선스 조건이 아니라 요청입니다).
- newmean-pii-dataset (공개 테스트): CC BY-NC 4.0(연구와 평가; 상업적 사용은 문의).
- 학습·검증 데이터: [데이터 요청 양식](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform)으로 요청 시 제공(연구용은 무료; 상업적 사용은 문의; 신청은 지금 받고 데이터는 2026년 12월부터 공유). 조건은 승인 메일이나 계약서로 드립니다([tools/data-request](tools/data-request)).
- 제3자 저작권 표시: [NOTICE](NOTICE).

[newmean.ai](https://newmean.ai)가 만들었습니다.
