[English](README.md) | 한국어

# NewmeanPII (Swift)

텍스트 속 개인정보를 iOS와 macOS **기기 안에서** 찾아 가립니다. npm 패키지 [`@newmean-ai/pii`](../js)와 같은 다국어
모델(`newmean-pii-<size>`, 54개 언어)로 같은 구간을 찾으며, 앱에 함께 컴파일되는 작은 C 코어([`native/core`](../native/core))가
실행합니다. 모델 파일이 기기에 있으면 실행 중 네트워크를 쓰지 않고, 아무것도 밖으로 보내지 않습니다.

## 설치

Swift Package Manager: Xcode에서 File › Add Package Dependencies… › `https://github.com/newmean-ai/pii`, 제품
`NewmeanPII`. 또는 `Package.swift`에:

```swift
.package(url: "https://github.com/newmean-ai/pii", from: "1.0.0"),
// target: .product(name: "NewmeanPII", package: "pii")
```

iOS 15 이상, macOS 12 이상. 의존성이 없고, 앱 코드 크기는 수백 KB 늘어납니다.

## 빠른 시작

```swift
import NewmeanPII

let redactor = try await PIIRedactor(size: .medium)
let (text, spans) = try await redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다")
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

## 모델

| 크기 | 파일 | 비고 |
|---|---|---|
| `.nano` | 2.6 MB | 공유 층 하나를 4번 적용, 임베딩 rank 32 |
| `.small` | 7.1 MB | 4층, 임베딩 rank 64 |
| `.medium` (기본) | 11.3 MB | 6층, 임베딩 rank 192 |
| `.large` | 15.5 MB | 6층, 임베딩 축소 없음(가지친 어휘) |

여기에 토크나이저 0.8 MB(모든 크기 공용)가 더해집니다. 기본 가중치 파일은 npm 패키지와 같이 large·medium은 `q4e`,
small·nano는 `q4`입니다. 모든 파일은 쓰기 전에 패키지에 고정된 크기와 SHA-256으로 확인합니다.

**앱에 포함(권장).** Hugging Face(`https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/`)에서 한 크기의 파일 두 개,
`tokenizer.web.json`과 `onnx/model_<variant>.onnx`를 받아 앱 타깃에 넣고(폴더 참조로 넣으면 `onnx/` 폴더가 유지되고, 파일만
넣어 나란히 놓여도 됩니다) 그 폴더를 넘깁니다.

```swift
let redactor = try await PIIRedactor(size: .medium, modelDirectory: Bundle.main.resourceURL!)
```

**한 번 내려받기.** `modelDirectory`를 주지 않으면 처음 쓸 때 Hugging Face에서
`Caches/newmean-pii/v1.0.0/pii-<size>/`로 내려받고, 불러올 때마다 다시 확인합니다. 시스템이 Caches를 비우면 다시
내려받습니다. 미리 받거나 다른 폴더를 쓰거나 파일을 직접 제공하려면:

```swift
let store = PIIModelStore(directory: appSupportURL.appendingPathComponent("newmean-pii"))
let dir = try await store.download(.medium, from: URL(string: "https://example.com/pii-medium/"))  // 기본: Hugging Face
let redactor = try await PIIRedactor(size: .medium, modelDirectory: dir)
```

모든 개인정보를 찾는 모델은 없습니다. 기술 보고서에 언어·데이터셋별 놓침과 과잉 가림 비율이 있으니, 결과는 보장이 아니라
강력한 1차 처리로 다뤄 주세요. 결과: [저장소 README](https://github.com/newmean-ai/pii#results).

## 라벨

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, 그리고
간접 식별자인 2단계 라벨 두 개: `PERSON_L2`(직함이 붙은 성, 별명, 이니셜)와 `URL_L2`(공개 프로필·페이지 URL).
2단계를 보이게 두려면 `labels: PIILabel.level1`을 넘깁니다.

## API

```swift
try await PIIRedactor(
  size: PIISize = .medium,          // .nano, .small, .medium, .large
  variant: PIIVariant? = nil,       // .q4 또는 .q4e; 기본 large/medium은 q4e, small/nano는 q4
  sensitivity: Double = 0,          // > 0 더 많이 가림, < 0 덜 가림 (약 -1...1)
  labels: Set<PIILabel> = PIILabel.all,
  regex: Bool = false,              // 몇 가지 패턴(이메일, URL, 카드·계좌, 전화, 주민등록번호)도 가림;
                                    // 기본 false(모델만): 추가로 찾는 것은 거의 없고 과잉 가림이 두 배
  modelDirectory: URL? = nil)       // 모델 파일 폴더; nil이면 Caches로 한 번 내려받음

try await redactor.detect(text)                  // [PIISpan], 정렬, 겹침 없음
try await redactor.redact(text, mode: .tag)      // (text, spans); .tag → [EMAIL], .mask → *****, .mask(with: "•"),
                                                 // .blank → 삭제, .custom { span, original in ... }
redactor.sensitivity = 0.3                       // 모델을 다시 불러오지 않고 임계값 변경
PIIRedactor.applyRedaction(to: text, spans: spans, mode: .tag)
PIIRegex.spans(in: text)                         // 패턴 층만 따로
```

`PIISpan`에는 `range`(찾은 텍스트의 `Range<String.Index>`), `utf16Range`(NSString·NSRange 오프셋, npm 패키지의 `start`·`end`),
`scalarRange`(유니코드 스칼라, Python 오프셋), `label`, `source`(`.model`, `.regex`, `.modelAndRegex`)가 있습니다. 구간은
유니코드 스칼라 경계에 놓이며, 이 경계가 Character 경계와 늘 같지는 않습니다(이모지 ZWJ 연쇄는 스칼라 여러 개로 된 Character 하나). 자를 때는 `text[span.range]`(Swift가
Character 경계로 맞춤)가 아니라 `text.unicodeScalars[span.range]`나 `utf16Range`를 쓰세요. `.mask`는 npm 패키지처럼 유니코드
스칼라마다 가림 문자 하나를 씁니다.

**스레드.** `PIIRedactor`는 `Sendable`이고 잠글 필요가 없습니다. 여러 작업이 한 인스턴스의 `detect`를 동시에 불러도 됩니다.
async 메서드는 모델을 분리된 작업(detached task)에서 실행하고 호출한 액터에서 돌리지 않으므로, 메인 액터에서 불러도 UI가
멈추지 않습니다. async가 아닌 코드(그리고 Swift가 `main.swift`의 최상위 코드)에서 쓰는 동기 `detect`·`redact` 오버로드는
호출한 스레드를 막습니다. 한 코어 기준 속도는 Apple M5 Pro에서 짧은 텍스트 약 10 ms(nano)~15 ms(medium), 130토큰 문단
40~60 ms입니다(C 코어 수치, 휴대폰은 더 느림).

**Debug 빌드는 느립니다.** Xcode는 Debug 구성에서 패키지의 C 코드를 최적화 없이 컴파일하므로 모델이 약 18배 느려집니다(Apple
M5 Pro, medium, 짧은 텍스트 약 19 ms → 350 ms). 속도는 Release 빌드에서 재세요. Release는 코어를 `-Os`로 컴파일하며
`-O3`과 속도가 같습니다.

## 개발

```bash
swift build
swift test -c release           # release: -O0에서는 C 코어가 느림
xcodebuild -scheme NewmeanPII -destination 'generic/platform=iOS' build
xcodebuild -scheme NewmeanPII -destination 'generic/platform=iOS Simulator' build
```

저장소 URL을 패키지로 추가할 수 있도록 `Package.swift`는 저장소 루트에 있습니다. C 타깃 `CNewmeanPII`
(`swift/Sources/CNewmeanPII`)는 한 줄짜리 심 파일(`api.c`가 `native/core/src/api.c`를 include하는 식)로 `native/core/src`를
컴파일하고, 모듈 맵으로 `pii.h`만 노출합니다. 모든 코어 파일에 심이 있는지는 테스트가 확인합니다. Swift 소스는
`swift/Sources/NewmeanPII`, 테스트는 `swift/Tests/NewmeanPIITests`에 있습니다.

동등성 테스트는 비공개 픽스처와 모델 사본을 제자리에서 읽고(`PII_FIXTURES`, 기본 `js/test/fixtures`; `PII_MODELS`, 기본
`js/test/models`, Hugging Face 구조의 `pii-<size>` 폴더) 없으면 건너뜁니다. `PII_SIZES=nano,medium`으로 크기를 줄이고,
`PII_DOWNLOAD=1`이면 Hugging Face에서 nano(3.4 MB)를 실제로 내려받는 테스트를 더합니다.

| 테스트 | 확인 내용 |
|---|---|
| 토크나이저 | `tokenize()`의 id·오프셋 vs Hugging Face tokenizers, `tokenize.jsonl`(3,273행; id는 전부, 오프셋은 npm 테스트처럼 많아야 3행까지 다를 수 있음) |
| 예측 | `detect()` 구간 vs Python 평가기, `js/test/predict.test.ts`의 모든 실행(각 412행, 99% 이상) |
| 정규식 | `PIIRegex` vs Python 기준선, `regex.jsonl`(3,273행); JavaScript `\s` 사례 |
| 가림·오프셋 | `js/test/combine.test.ts`의 모드; 이모지, ZWJ 시퀀스, 국기, 결합 문자; 브리지된 NSString |
| 옵션·스레드·파일 | 라벨, 민감도, 정규식 병합; 동시 호출 4 × 48; 손상·누락된 모델 파일 |
| 패키지 | 모든 `native/core/src/*.c`의 심; 기본(FMA 아님) 커널 빌드; 핀 12개가 `js/src/models.ts`와 같음 |

## 라이선스

패키지 코드와 모델 가중치 모두 [Apache-2.0](LICENSE)이며, 제3자 고지는 [NOTICE](NOTICE)에 있습니다. 제품에 쓰신다면 문서나
정보·라이선스 화면에 "Powered by newmean.ai"를 적어 주시면 감사하겠습니다(라이선스 조건이 아닌 요청입니다).
[newmean.ai](https://newmean.ai)가 만들었습니다.
