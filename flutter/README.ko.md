[English](README.md) | 한국어

# newmean_pii

Flutter·Dart 앱에서 텍스트 속 개인정보를 **기기 안에서** 찾아 가립니다. 작은 다국어 토큰 분류 모델(54개 언어)이
`dart:ffi`로 C 엔진에서 돌고, 어디로도 아무것도 보내지 않습니다. 모델·옵션·스팬은 npm 패키지
[`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii)와 같습니다.

```yaml
dependencies:
  newmean_pii: ^1.0.0
```

```dart
import 'package:newmean_pii/newmean_pii.dart';

final pii = await NewmeanPii.create(size: 'medium');
final r = await pii.redact('제 메일은 kim@example.com, 전화는 010-1234-5678입니다');
// r.text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

Android(arm64-v8a, armeabi-v7a, x86_64), iOS(기기·시뮬레이터), macOS, Linux, Windows. Flutter 3.38 또는 Dart 3.10
이상이 필요합니다. 패키지의 빌드 훅이 각 대상의 C 컴파일러(Xcode, Android NDK, clang·gcc, MSVC)로 엔진을 컴파일하므로
미리 빌드한 바이너리도, 플러그인 설정도 없습니다. 웹에서는 npm 패키지를 쓰세요.

## 모델

| size | 파일 | 비고 |
|---|---|---|
| `nano` | 2.6 MB | 공유 레이어 1개를 4번 적용, rank-32 임베딩 |
| `small` | 7.1 MB | 4 레이어, rank-64 임베딩 |
| `medium` (기본) | 11.3 MB | 6 레이어, rank-192 임베딩 |
| `large` | 15.5 MB | 6 레이어, 전체(어휘 축소) 임베딩 |

여기에 모든 크기가 함께 쓰는 0.8 MB 토크나이저가 더해집니다. 어디서 읽든 모든 파일은 쓰기 전에 패키지에 고정된 크기와
SHA-256으로 검사합니다.

- **한 번 내려받기**(기본): Hugging Face(`newmean-ai/pii-<size>`, 태그 `v1.0.0`)에서 `cacheDir`로 받습니다. 기본 위치는
  iOS·macOS `~/Library/Caches/newmean-pii`, Linux `~/.cache/newmean-pii`, Windows `%LOCALAPPDATA%\newmean-pii`,
  Android는 앱의 임시 디렉터리입니다. 다른 곳에 두려면 `cacheDir`(예: `path_provider`의
  `getApplicationSupportDirectory()`)를 넘기세요. `modelUrl`은 직접 호스팅하는 모델 폴더를 가리킵니다. Android 앱은
  내려받기에 `INTERNET` 권한이 필요합니다.
- **앱에 포함**: Flutter 에셋으로 넣고 `readFile`로 읽습니다.

  ```yaml
  flutter:
    assets:
      - assets/pii-nano/tokenizer.web.json
      - assets/pii-nano/onnx/model_q4.onnx
  ```

  ```dart
  import 'package:flutter/services.dart' show rootBundle;

  final pii = await NewmeanPii.create(
    size: 'nano',
    readFile: (path) async => (await rootBundle.load('assets/pii-nano/$path')).buffer.asUint8List(),
  );
  ```

- **로컬 폴더**(Hugging Face와 같은 구조, `modelDir`) 또는 이미 가진 바이트(`NewmeanPii.fromBytes`).

어떤 모델도 개인정보를 빠짐없이 찾지는 못합니다. 언어·데이터셋별 놓침·과잉 마스킹 비율은 기술 보고서에 있습니다.
결과는 강력한 1차 처리로 쓰고, 보장으로 여기지 마세요.

## 레이블

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, 그리고
간접 식별자를 위한 2단계 레이블 두 개: `PERSON_L2`(직함을 붙인 성, 별명, 이니셜)와 `URL_L2`(공개 프로필·페이지 URL).
2단계를 가리지 않으려면 `labels: level1Labels`를 넘기세요.

## API

```dart
NewmeanPii.create({
  String size = 'medium',      // nano, small, medium, large
  String? variant,             // 'q4' 또는 'q4e'; 기본 large/medium은 q4e, small/nano는 q4
  double sensitivity = 0,      // > 0이면 더 가리고, < 0이면 덜 가림 (대략 −1..1)
  Iterable<String>? labels,    // 가릴 레이블; 기본 전체 (piiLabels)
  bool regex = false,          // 몇 가지 패턴(이메일, URL, 카드·계좌, 전화, 주민등록번호)도 가림;
                               // 기본 false(모델만): 더 찾는 것은 거의 없고 과잉 마스킹은 두 배
  String? modelDir,            // 로컬 폴더: tokenizer.web.json, onnx/model_<variant>.onnx
  Future<Uint8List> Function(String path)? readFile,  // 예: Flutter 에셋
  Uri? modelUrl,               // 직접 호스팅하는 모델 폴더("/"로 끝남); 기본 Hugging Face
  String? cacheDir,            // 내려받은 파일을 둘 곳
  bool cache = true,           // 내려받은 파일 보관
  bool verify = true,          // 모든 파일의 크기와 SHA-256 검사
  void Function(PiiProgress)? onProgress,
}) → Future<NewmeanPii>
NewmeanPii.fromBytes(onnx: ..., tokenizer: ..., size: ..., variant: ...) → Future<NewmeanPii>

pii.detect(text)              // → Future<List<PiiSpan>> {start, end, label, source}, 정렬·겹침 없음
pii.redact(text, mode: RedactMode.tag)  // tag → [EMAIL], mask → *****, blank → 삭제; 또는 replace: (span, original) => ...
                                        // → Future<PiiRedaction> {text, spans}
pii.detectSync(text), pii.redactSync(text)  // 같은 일을 호출한 isolate에서
pii.sensitivity = 0.3          // 또는 setSensitivity(0.3); 다시 로드하지 않음
pii.dispose()

regexSpans(text), applyRedaction(text, spans, mode: ...)  // 패턴 레이어와 마스킹 방식만 따로
modelFile(size, file)          // 'q4', 'q4e', 'tokenizer'의 공개 경로·크기·SHA-256
```

- **오프셋**은 UTF-16 코드 유닛, 곧 Dart `String`의 인덱스입니다: `text.substring(span.start, span.end)`. 이모지, 국기 등
  BMP 밖 문자를 절대 쪼개지 않습니다. `mask`는 코드 포인트마다 마스크 문자 하나를 씁니다.
- **Isolate**: `detect`와 `redact`는 모델을 백그라운드 isolate(`Isolate.run`)에서 돌리므로 UI가 멈추지 않습니다. 추가 비용은
  호출당 약 0.5 ms입니다. 로드한 모델 하나를 모든 호출이 함께 쓰고, 호출이 겹쳐도 됩니다. `detect`를 여러 번
  동시에 부르면 여러 코어에서 병렬로 돕니다. 직접 만든 백그라운드 isolate 안에서는 `detectSync`로 한 단계를 줄일 수
  있습니다.
- **메모리**: 로드한 모델은 대략 파일 크기에 토크나이저를 더한 만큼 차지합니다. `dispose()`는 실행 중인 호출이 끝난 뒤
  해제합니다(부르지 않으면 객체가 가비지 컬렉션될 때 해제).

## 결과

이 패키지를 거친 스팬은 같은 모델 파일에서 Python 평가기와 같습니다(실행마다 검증 텍스트 412개, npm 패키지의 일치
테스트): large·medium·small(q4, q4e), nano q4e, sensitivity 0.5의 medium q4e는 412/412, sensitivity 0과 −1의 nano q4는
410/412. 다른 두 행에서는 평가기가 레이블이 다른 겹친 스팬 두 개를 내고, `detect`는 npm 패키지처럼 이를 한 영역으로
합칩니다. 이모지, 국기, 결합 문자, CRLF, NUL, 짝 없는 서로게이트를 섞은 합성 텍스트 151개(nano·medium, `regex` 켜고
끈 경우)에서 스팬, source, 세 가지 가림 모드 결과가 604건 모두 npm 패키지와 같았습니다.

Apple M5 Pro(macOS)에서 한 번에 한 호출, 한 문장 텍스트 기준 `detect` 속도: nano 약 11 ms, medium 약 16 ms(백그라운드
isolate가 약 0.5 ms를 더함). 폴더에서 로드(SHA-256 검사 포함): nano 40 ms, medium 110 ms, large 145 ms. 휴대폰은 더
느립니다. 웹 패키지의 휴대폰 측정은 기술 보고서를 보세요.

54개 언어 정확도와 다른 탐지기 비교: [저장소 README](https://github.com/newmean-ai/pii#results)와 기술 보고서, 주간
점수는 [리더보드](https://huggingface.co/spaces/newmean-ai/pii-leaderboard).

## 동작 방식

이 패키지는 저장소의 `native/core`를 컴파일합니다. Python·Android·iOS·Flutter 패키지가 함께 쓰는 C 라이브러리
하나입니다. 공개된 ONNX 파일을 직접 읽고(ONNX Runtime 없음), npm 패키지와 똑같이 토큰화하며(토큰 id와 오프셋이 같음), 로짓은 npm
패키지의 WebAssembly 엔진과 비트 단위로 같습니다. 패턴 레이어(`regexSpans`, npm 패키지 `regex.ts`의 포팅)와 마스킹
방식은 Dart로 되어 있고, 패턴 스팬은 npm 패키지의 `combine`과 같은 규칙으로 C 코어에서 모델 스팬과 합쳐집니다. 곱셈과
덧셈을 합치지(fuse) 않게 빌드하므로 모든 CPU에서 결과가 같고, 같은 이유로 32비트 ARM은 엔진의 스칼라 경로를 씁니다.

## 개발

```bash
cd flutter
dart pub get
dart analyze
dart test                       # 호스트용 C 코어를 빌드한 뒤 테스트 실행
cd example && flutter build apk --debug && flutter build ios --simulator --no-codesign
```

| 테스트 | 확인 내용 |
|---|---|
| `predict_test.dart` | Dart API를 거친 스팬 vs Python 평가기, npm 일치 테스트의 모든 실행(행의 99% 이상) |
| `regex_test.dart` | `regex.jsonl`(3,273행)에서 `regexSpans` vs Python 기준; 이모지 오프셋; 유니코드 숫자와 경계 |
| `api_test.dart` | 이모지, BMP 밖 문자, 결합 문자, RTL, NUL, 짝 없는 서로게이트 주변의 UTF-16 오프셋; 마스킹 방식; 레이블; sensitivity; regex 병합; 동시·동기 호출; 긴 텍스트; 고정값; dispose |
| `loader_test.dart` | 로컬 서버에서 내려받기, SHA-256 거부, 캐시 재사용과 복구; `PII_DOWNLOAD=1`이면 Hugging Face에서 nano를 내려받음 |
| `redaction_test.dart` | npm 패키지의 `applyRedaction` 사례 |

픽스처와 모델 사본은 비공개(검증 텍스트)이며 복사하지 않고 제자리에서 읽습니다: `PII_FIXTURES`(기본
`../js/test/fixtures`), `PII_MODELS`(기본 `../js/test/models`, 폴더 `pii-<size>/`). 이것이 필요한 테스트는 없으면
건너뜁니다. `PII_SIZES=nano,medium`은 일치 테스트 크기를 제한하고, `PII_CONCURRENCY`(기본 4)는 동시에 돌리는 호출 수를
정합니다.

**배포.** 빌드 훅은 `../native/core`가 있으면(저장소) 그것을, 없으면(pub.dev에서 받은 패키지) `third_party/native_core`를
컴파일합니다. `dart pub publish` 전에 `dart run tool/vendor_core.dart`로 이 사본을 만듭니다(git은 무시하고 `.pubignore`로
배포에는 포함). `--clean`으로 지웁니다.

## 라이선스

[Apache-2.0](LICENSE)(패키지 코드와 모델 가중치), 제3자 고지는 [NOTICE](NOTICE). 제품에 쓰신다면 문서나 정보·라이선스 화면에 "Powered by newmean.ai"를 표시해 주시면 감사하겠습니다(라이선스 조건이 아닌 요청입니다). [newmean.ai](https://newmean.ai)
