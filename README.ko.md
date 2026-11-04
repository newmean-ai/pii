[English](README.md) | 한국어

# @newmean-ai/pii

**브라우저 안에서 하는 개인정보 마스킹.** 텍스트 속 개인정보(이름, 이메일, 전화번호, 주소, 신분증 번호 등)를 **브라우저 안에서만** 찾아 가리므로, 원문이 사용자의 기기를 떠나지 않습니다.

- 54개 언어, 11개 레이블과 간접 식별자를 위한 수준 2 레이블 2개(IP 주소는 `ID_NUMBER`로 학습); 패턴 계층(이메일, URL,
  카드·계좌, 전화, 주민등록번호)은 선택이며 기본은 꺼짐
- 모델 크기 네 가지: **nano 2.6 MB**, **small 7.1 MB**, **medium 11.3 MB**(기본), **large 15.5 MB**
- WebAssembly로 실행: SDK 자체 내장 엔진(기본, 약 13 KB) 또는 onnxruntime-web
- 상업적 사용을 포함해 어떤 용도로도 무료
- 같은 모델을 Python, iOS, Android, Flutter에서도 씀([설치](#설치))

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

## 설치

| 플랫폼 | 패키지 | 설치 |
|---|---|---|
| 브라우저, Node.js | [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii) | `npm install @newmean-ai/pii` |
| Python 3.9+ | [`newmean-pii`](https://pypi.org/project/newmean-pii/) | `pip install newmean-pii` |
| iOS 15+, macOS 12+ | `NewmeanPII` (Swift Package Manager) | `https://github.com/newmean-ai/pii` 추가, `1.4.1`부터 |
| Android 7.0+ | [`ai.newmean:pii`](https://central.sonatype.com/artifact/ai.newmean/pii) | `implementation("ai.newmean:pii:1.4.1")` |
| Flutter, Dart | [`newmean_pii`](https://pub.dev/packages/newmean_pii) | `flutter pub add newmean_pii` |

Python, iOS, Android, Flutter 패키지는 같은 모델을 기기에서 C 엔진 하나([`native/core`](native/core))로 실행하며, 토큰 id·오프셋·
로짓이 npm 패키지와 같습니다. 모델 파일은 Hugging Face에서 한 번 내려받아 고정된 SHA-256으로 검사하거나, 앱에 함께 넣습니다.

```python
from newmean_pii import create_redactor

create_redactor(size="medium").redact("제 메일은 kim@example.com입니다").text  # '제 메일은 [EMAIL]입니다'
```

```swift
import NewmeanPII

let (text, _) = try await PIIRedactor(size: .medium).redact("제 메일은 kim@example.com입니다")
```

```kotlin
import ai.newmean.pii.PiiRedactor

val (text, spans) = PiiRedactor.create(context).redact("제 메일은 kim@example.com입니다")  // create는 suspend, 모델은 앱 assets에서
```

```dart
import 'package:newmean_pii/newmean_pii.dart';

final r = await (await NewmeanPii.create(size: 'medium')).redact('제 메일은 kim@example.com입니다');
```

안내: [Python](python/README.ko.md) · [Swift](swift/README.ko.md) · [Android](android/README.ko.md) · [Flutter](flutter/README.ko.md)

### 표(CSV, TSV)

```ts
const { text } = await redactor.redactTable(csv);  // 구분자 자동 감지: ",", "\t", ";", 마크다운 파이프 표
const spans = await redactor.detectTable(tsv, { delimiter: "\t", header: true, vote: true });
const named = await redactor.redactTable(csv, { context: "header" });  // 칸마다 열 제목을 붙여 한 번 더 읽음
```

```python
create_redactor().redact_table(open("survey.csv", encoding="utf-8").read(), context="header").text
```

표 모드는 표 전체를 한 텍스트로 읽어 칸끼리 문맥이 되게 하고, 모든 구간을 칸 값 안으로 자릅니다. 구분자, 따옴표, 줄바꿈은
가리지 않으므로 결과도 올바른 표입니다. 짧은 값으로 된 열에서 모델이 칸의 80% 이상을 이름, 이메일, 전화번호, 사용자 이름으로
읽으면 그 열 전체를 가립니다(source `"table"`). 머리글 행과 긴 글 열은 이 투표에 들어가지 않습니다. `context: "header"`를 주면
값이 대부분 서로 다른 열의 칸을 열 제목과 함께(`"<머리글>: <값>"`) 한 번 더 따로 읽어, 이름·성이 나뉜 열의 이름을 찾습니다.
54개 언어의 설문·양식 내보내기 556개에서 large는 개인정보 글자를 6.2% 대신 1.4%만 놓치고, 개인정보가 없는 칸은 9.6% 대신
12.3%를 가립니다(크기별 수치는 [결과](#결과)). 그런 칸마다 모델을 한 번 더 부릅니다(표 모드의 약 2배 시간). 모든 패키지에
표 모드가 있습니다(Swift, Kotlin, Dart: `detectTable`, `redactTable`; C: `pii_detect_table`).

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
| **newmean-pii-large** | 15.5 MB | **1.6** | 9.6 | 4.9 | **6.1** | 21.4 |
| **newmean-pii-medium** | 11.3 MB | 1.9 | 11.5 | 6.3 | 7.7 | 21.5 |
| **newmean-pii-small** | 7.1 MB | 2.4 | 10.0 | 5.6 | 8.4 | 22.3 |
| **newmean-pii-nano** | 2.6 MB | 2.9 | 9.5 | 5.0 | 9.9 | 21.4 |
| gliner-pii-edge | 176 MB | 19.6 | 11.9 | 5.7 | 17.3 | **15.7** |
| OpenAI privacy-filter | 2.8 GB | 34.6 | 34.8 | 14.3 | 25.7 | 32.2 |
| Horizon pii-redactor-small | 563 MB | 41.0 | 25.7 | 11.1 | 7.8 | 31.6 |
| KoELECTRA Korean PII NER | — | 43.1 | **8.8** | 24.1* | 45.9 | 37.6 |
| GLiNER2-PII | 1.2 GB | 44.9 | 34.2 | 23.2 | 46.3 | 32.9 |
| Rampart | 15 MB | 56.5 | 36.1 | **3.5*** | 51.6 | 40.5 |
| Mozilla smart-pii-redactor | 15 MB | 56.9 | 46.0 | 25.9 | 48.8 | 32.9 |
| Rampart-edge | 1.5 MB | 74.7 | 89.0 | 46.1 | 55.5 | 48.0 |
| 정규식 기준선 | — | 75.3 | 62.0 | 61.1 | 44.6 | 86.7 |

읽는 법: 우리 모델은 newmean-pii-dataset (공개 테스트)와 같은 설계의 데이터로 학습했고, 다른 탐지기는 이 데이터를 처음 보며 일부는 다른 레이블링
정책(예: 조직명은 가리지 않음)을 따릅니다. 그래서 그 열은 각 탐지기가 이 마스킹 정책에 얼마나 맞는지를 보여 주고, 더 공정한 비교는
외부 데이터셋 네 열입니다. \* OpenPII 데이터로 학습했거나(Rampart) 그와 겹치는 데이터로 학습했습니다(KoELECTRA). GLiNER2-PII는
Gretel에서 라이브러리의 긴 글 모드로 돌렸습니다. 휴대폰(네 대 평균)에서 11.3 MB 모델은 40토큰 메시지에 약 26 ms가 걸립니다.
SDK 1.2(배포된 Python 패키지)를 거쳐도 이 표의 우리 모델 수치는 모두 0.04점 안에서 같습니다. SDK가 바꾸는 것은 이모지나 키캡이
든 텍스트뿐입니다.

1.2에서 달라진 점. 1.2는 v1.1 모델에 SDK 1.2.0을 더한 배포입니다. v1.1은 따로 공개하지 않았으므로 그 모델 변경이 여기에
들어 있습니다. *모델:* 짧은 글, 표의 칸, 설문 내보내기에서 홀로 쓴 일상어를 이름으로 읽는 일이 크게 줄었습니다. 54개 언어의 짧은
항목 20,691개(열 제목, 응답 보기, 낱말, 따로 쓴 이름과 값)에서 개인정보가 없는 항목을 가리는 비율은 30.6%에서 7.9%로 줄었고(large;
medium 30.6%에서 7.8%, small 36.8%에서 9.4%, nano 39.3%에서 11.1%), 설문 CSV 228개를 칸마다 검사하면 large는 개인정보가 없는 칸의
24.9% 대신 6.6%를 가립니다. 대가: KDPII에서 large는 9.6%(v1.0: 7.6%), medium은 11.5%(9.8%), small은 10.0%(9.5%)를 놓치고, nano는
9.5%로 그대로입니다. *SDK:* 이모지를 모델이 읽는 텍스트에서 빼므로 모델이 이모지를 이름으로 읽지 않습니다(`keepEmoji: true`로 끌 수
있고, 이모지나 키캡이 없는 텍스트는 1.1과 똑같은 구간을 냅니다). 3️⃣ 같은 키캡은 바탕 문자로 읽으므로 키캡으로 쓴 번호도 찾습니다.
모든 구간을 문자소 클러스터 단위로 넓혀 이모지, 국기, 결합 문자열을 통째로 가리거나 통째로 남깁니다(npm 패키지는
`Intl.Segmenter`가 있어야 합니다). 여기에 표 모드와 `context: "header"` 옵션(위)이 더해졌습니다. 짧은 글, 이모지, 표 세트는 모두
비공개 합성 데이터입니다.

이모지, 모델만 대 모델 + SDK 1.2: 이모지 1~3개를 개인정보 밖에 넣은 텍스트 4,501개 중 이모지가 가려진 텍스트의 비율입니다.
이모지가 반으로 잘리는 일은 없습니다. 남은 것은 대부분 이름 옆의 키캡으로, 키캡을 숫자로 읽기 때문에 이름과 함께 가려집니다.
이 텍스트에서 놓친 개인정보 글자도 줄었습니다(large 0.87%에서 0.31%, medium 1.03%에서 0.61%). 1.0판은 이 텍스트의
7.42%(large), 9.46%(medium), 16.69%(small), 21.88%(nano)에서 이모지를 가리고, 1.9~7.6%에서 이모지를 반으로 자릅니다.

| 모델 | 모델만 | 모델 + SDK 1.2 |
|---|---|---|
| newmean-pii-large | 23.95% | 0.87% |
| newmean-pii-medium | 14.89% | 0.78% |
| newmean-pii-small | 20.57% | 0.89% |
| newmean-pii-nano | 26.33% | 1.00% |

설문·양식 내보내기, 놓침 % / 개인정보가 없는 데이터 칸을 가린 비율 %: Claude Opus 5.5가 쓴 파일 556개(칸 121,272개, 54개
언어)이며, 우리 학습 데이터는 이 모델로 만들지 않았습니다. SDK 옵션을 정한 뒤에 만들었고 한 번만 채점했습니다.

| 모델 | 파일 전체, 모델만 | 표 모드(기본) | 표 모드 + `context: "header"` | 칸마다 `detect` |
|---|---|---|---|---|
| newmean-pii-large | 6.56 / 9.58 | 6.17 / 9.59 | **1.44** / 12.30 | 5.00 / 12.21 |
| newmean-pii-medium | 8.29 / 9.30 | 7.93 / 9.32 | **1.93** / 11.66 | 5.75 / 12.03 |
| newmean-pii-small | 7.00 / 15.87 | 6.65 / 15.90 | **1.72** / 19.03 | 6.21 / 14.21 |
| newmean-pii-nano | 6.47 / 23.62 | 6.13 / 23.66 | **1.58** / 27.34 | 6.31 / 17.74 |

`context: "header"`는 모든 크기에서 놓침을 약 4분의 1로 줄이고, 개인정보가 없는 칸은 2.3~3.7점 더 가립니다. 표 모드만으로는 파일
전체를 읽을 때와 놓침이 거의 같지만, 표를 올바르게 지키고 연락처 열을 통째로 가립니다. small과 nano는 어느 방식이든 개인정보가
없는 칸을 많이 가립니다(16~27%). 1.0판(v1.0 모델, 표 모드 없음)은 이 파일을 통째로 읽고 더 놓칩니다: large 7.52 / 11.30,
medium 9.50 / 9.37, small 9.80 / 13.43, nano 8.04 / 24.36. 옵션을 정할 때 쓴 세트 두 개와 전체 수치: [기술 보고서 6.2절](docs/report/REPORT.ko.md).

1.3에서 더한 것. SDK 1.3.0이며 모델은 v1.1 그대로입니다. 옵션 두 개를 더했습니다. `wholeWords`는 기본으로 켜져 있고(`false`면 1.2와
같은 구간, 위 수치는 이 옵션 없이 잰 것), `strictShortNames`는 기본으로 꺼져 있습니다.
`strictShortNames`: 모델이 8토큰 이하로 읽는 글(설문 응답, 표 칸)은 근거가 더 있어야 이름으로 보므로, 단독으로 쓴 「경기」, 「남성」,
「네이버」는 가리지 않습니다. 개인정보 없는 짧은 항목을 가린 비율은 7.9%에서 4.3%로 줄고(large; medium 7.8%→4.5%, small
9.4%→4.7%, nano 11.1%→5.9%), 단독 이름을 찾은 비율은 98.9%에서 96.5%로 줄어듭니다(medium 97.5%→95.4%, small 97.0%→93.5%, nano
96.7%→94.4%). 긴 글은 그대로입니다(공개 테스트와 KDPII 차이 0.07점 이하). `wholeWords`: 낱말 중간에서 끝나는 구간을 낱말 전체로
넓힙니다("R23" → `[ID_NUMBER]23`이 아니라 `[ID_NUMBER]`, 한글과 숫자·라틴 문자가 만나는 곳에서 낱말이 끝나므로
「010-1234-5678입니다」 → `[PHONE]입니다`, 한글 구간 뒤 조사는 그대로, 「홍길동님께」 → `[PERSON]님께`). 모든 세트에서 놓침이 0.1~1.4점 줄고(large: 공개 테스트 1.63%→1.54%,
KDPII 9.61%→9.08%), 조금 더 가립니다(large 과잉 가림: 공개 테스트 0.47%→0.53%, KDPII 1.70%→2.09%). 모든 패키지에 패키지·모델 버전과 검사한 모델 파일을 주는 `info`도 더했습니다.

1.4에서 더한 것. SDK 1.4.0이며 모델은 v1.1 그대로입니다. 모든 패키지에 사용자 목록을 더했습니다. `block`: 개인정보라고 아는 값
(강사 이름 등)은 낱말 경계에서 시작하고 끝나는 곳마다 가립니다. 조사 앞에서도 맞고(「박지훈님께」 → `[PERSON]님께`) 모델이
놓친 곳도 가립니다(출처 `block`). `allow`: 구간 글 전체가 개인정보가 아니라고 아는 값(설문의 지역, 상표 등)이면 가리지
않습니다. ASCII 영문자만 대소문자를 무시하고 그 밖은 정규화하지 않습니다(NFC 글을 넘기세요). 목록은 모델 파일을 읽기 전에
검사하고, 오류는 목록, 항목 번호, 사유만 알려 주며 글은 담지 않습니다. `info`에 항목 수와 `listsId`가 들어 있습니다. KDPII dev 분할(large)에서 세트의
이름을 모두 넣은 block 목록(상한)은 PERSON 글자 놓침을 24.6%에서 3.1%로 줄이고 과잉 가림을 0.4점 늘립니다. 일반 allow 목록
(136개)은 예상대로 거의 바꾸지 않습니다(allow는 자기 열의 값을 넣는 용도). 자세한
내용: [js/README.ko.md](js/README.ko.md#목록). 패키지 없이 C 코어를 직접 쓰는 경우: C API는 소스 호환만 약속하므로
릴리스마다 그 릴리스의 `pii.h`로 다시 빌드해야 하며, 다른 릴리스의 옵션 구조체는 거절됩니다([native/core](native/core/README.ko.md#api)).

1.4.1에서 고친 것. SDK 1.4.1이며 모델은 v1.1 그대로입니다. 버그와 문서만 고쳤고, 올바른 입력에는 1.4.0과 같은 구간을 냅니다.
`sensitivity`가 NaN이나 무한대면 이제 모든 패키지와 C 코어에서 오류입니다(Android는 전에도 거절). 전에는 NaN이면 모델이 아무
말 없이 꺼졌고, 무한대면 전부 가리거나 하나도 가리지 않았습니다. npm 패키지는 ES 모듈을 require할 수 있는 Node(20.19+,
22.12+)에서 `require()`로도 불러올 수 있고, `info.verified`는 두 모델 파일의 SHA-256을 실제로 검사했을 때만 true입니다(전에는
`verify` 옵션 값을 그대로 옮겼습니다). Flutter 패키지는 1.4.0 변경 기록에 적힌 대로, 불러올 때 파일을 읽기 전에 네이티브 코어를
검사합니다. 작은 수정: npm `detectTable`은 모델을 돌리기 전에 구분자를 검사하고, Flutter는 내려받을 때 User-Agent에 패키지
버전을 적으며, README 몇 곳을 바로잡았습니다(내장 엔진 크기, 목록 예시, 라벨이 아닌 이름).

## 저장소 구성

| 폴더 | 내용 |
|---|---|
| [`js/`](js) | SDK `@newmean-ai/pii`와 테스트 페이지, 공개 데모(`playground/`), 휴대폰 벤치마크(`experiments/`) |
| [`native/core/`](native/core) | Python, iOS, Android, Flutter 패키지가 함께 쓰는 C 엔진 |
| [`python/`](python) | Python 패키지 `newmean-pii` |
| [`swift/`](swift), `Package.swift` | Swift 패키지 `NewmeanPII`(iOS, macOS) |
| [`android/`](android) | Android 라이브러리 `ai.newmean:pii` |
| [`flutter/`](flutter) | Flutter·Dart 패키지 `newmean_pii` |
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
