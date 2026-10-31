[English](README.md) | 한국어

# ai.newmean:pii (Android)

텍스트 속 개인정보를 Android **기기 안에서** 찾아 가립니다. npm 패키지 [`@newmean-ai/pii`](../js)와 같은 다국어
모델(`newmean-pii-<size>`, 54개 언어)로 같은 구간을 찾으며, 라이브러리 안의 작은 C 코어([`native/core`](../native/core))가
실행합니다. 모델 파일이 기기에 있으면 실행 중 네트워크를 쓰지 않고, 아무것도 밖으로 보내지 않습니다.

## 설치

```kotlin
// 앱 모듈의 build.gradle.kts
dependencies {
    implementation("ai.newmean:pii:1.0.0")
}
```

Android 7.0 이상(minSdk 24), ABI `arm64-v8a`, `armeabi-v7a`, `x86_64`. 의존성은 kotlinx-coroutines-core 하나이고, 네이티브
라이브러리는 ABI마다 약 100 KB입니다. 권한이 필요 없으며, 모델 파일을 내려받을 때만 앱에 `INTERNET`을 추가합니다.

## 빠른 시작

```kotlin
import ai.newmean.pii.PiiRedactor

val redactor = PiiRedactor.create(context)   // suspend; medium, 앱 assets의 모델 파일
val (text, spans) = redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다")
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

Java나 코루틴 밖에서는:

```java
try (PiiRedactor redactor = PiiRedactor.createBlocking(context)) {  // 백그라운드 스레드에서
    String masked = redactor.redact(text).getText();
}
```

## 모델

| 크기 | 파일 | 비고 |
|---|---|---|
| `NANO` | 2.6 MB | 공유 층 하나를 4번 적용, 임베딩 rank 32 |
| `SMALL` | 7.1 MB | 4층, 임베딩 rank 64 |
| `MEDIUM` (기본) | 11.3 MB | 6층, 임베딩 rank 192 |
| `LARGE` | 15.5 MB | 6층, 임베딩 축소 없음(가지친 어휘) |

여기에 토크나이저 0.8 MB(모든 크기 공용)가 더해집니다. 기본 가중치 파일은 npm 패키지와 같이 large·medium은 `q4e`,
small·nano는 `q4`입니다. 모든 파일은 쓰기 전에 라이브러리에 고정된 크기와 SHA-256으로 검사합니다.

**앱에 포함(권장).** 한 크기의 두 파일을 Hugging Face(`https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/`)에서
받아 저장소와 같은 구조로 앱 assets에 넣습니다.

```
app/src/main/assets/newmean-pii/pii-medium/tokenizer.web.json
app/src/main/assets/newmean-pii/pii-medium/onnx/model_q4e.onnx
```

```bash
cd app/src/main/assets && mkdir -p newmean-pii/pii-medium/onnx && cd newmean-pii/pii-medium
curl -LO https://huggingface.co/newmean-ai/pii-medium/resolve/v1.0.0/tokenizer.web.json
curl -L -o onnx/model_q4e.onnx https://huggingface.co/newmean-ai/pii-medium/resolve/v1.0.0/onnx/model_q4e.onnx
```

`PiiRedactor.create(context)`가 여기서 읽습니다(다른 폴더는 `source = PiiModelSource.Assets(dir = "models")`, assets 바로 아래는 `""`).

**한 번 내려받기.** `source = PiiModelSource.Download()`이면 처음 쓸 때 Hugging Face에서
`cacheDir/newmean-pii/1.0.0/pii-<size>/`로 내려받고, 불러올 때마다 다시 검사합니다. 시스템이 캐시를 지우면 다시
내려받습니다. 다른 곳에 두려면 `dir`, 직접 호스팅한 파일을 쓰려면 `baseUrl`을 넘깁니다.

```kotlin
val redactor = PiiRedactor.create(
    context, size = PiiSize.NANO,
    source = PiiModelSource.Download(dir = File(context.filesDir, "newmean-pii"), onProgress = { file, loaded, total, cached -> }),
)
// 또는: PiiModels.download(dir, PiiSize.NANO) 후 PiiRedactor.create(model, tokenizer, PiiSize.NANO)
```

어떤 모델도 개인정보를 빠짐없이 찾지는 못합니다. 기술 보고서에 언어·데이터셋별 놓침과 과마스킹 비율이 있으니, 결과는
보증이 아니라 강력한 1차 처리로 다뤄 주세요. 성적: [저장소 README](https://github.com/newmean-ai/pii#results).

## 레이블

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, 그리고
간접 식별자용 레벨 2 레이블 둘: `PERSON_L2`(직함을 붙인 성, 별명, 이니셜)와 `URL_L2`(공개 프로필·페이지 URL).
레벨 2를 가리지 않으려면 `labels = PiiLabels.LEVEL1`을 넘깁니다.

## API

```kotlin
PiiRedactor.create(                       // suspend, Dispatchers.IO에서 불러옴; Java·스레드에서는 createBlocking(...)
    context,
    size = PiiSize.MEDIUM,                // NANO, SMALL, MEDIUM, LARGE
    variant = size.defaultVariant,        // Q4 또는 Q4E; 기본 large·medium Q4E, small·nano Q4
    sensitivity = 0.0,                    // > 0 더 많이 가림, < 0 덜 가림 (대략 -1..1)
    labels = PiiLabels.ALL,
    regex = false,                        // 몇 가지 패턴(이메일, URL, 카드·계좌, 전화, 주민등록번호)도 가림; 기본 false
                                          // (모델만): 추가로 찾는 것은 거의 없고 과마스킹은 두 배
    source = PiiModelSource.Assets(),     // 또는 PiiModelSource.Download(...)
)
PiiRedactor.create(modelFile, tokenizerFile, size, ...)   // 디스크의 파일; 바이트는 fromBytes(model, tokenizer, size, ...)

redactor.detect(text)                     // List<PiiSpan>, 정렬, 겹침 없음
redactor.redact(text, RedactMode.TAG)     // PiiRedaction(text, spans); TAG → [EMAIL], MASK → ***** (maskChar = "•"),
                                          // BLANK → 삭제; 직접 바꾸려면 redact(text) { span, original -> "..." }
redactor.sensitivity = 0.3                // 모델을 다시 불러오지 않고 임계값 변경
redactor.close()                          // 모델 해제 (AutoCloseable: use { })
PiiRedactor.applyRedaction(text, spans, RedactMode.TAG)
PiiRegex.spans(text)                      // 패턴 층만
```

`PiiSpan`에는 `start`·`end`(텍스트의 UTF-16 인덱스, `String.substring`이 받는 값이자 npm 패키지가 돌려주는 값), `label`,
`source`(`MODEL`, `REGEX`, `MODEL_AND_REGEX`)가 있습니다. 구간은 코드 포인트 경계에 놓이지만 문자소 안(결합 부호 앞)에서
끝날 수 있습니다. `MASK`는 npm 패키지처럼 코드 포인트마다 가림 문자 하나를 씁니다.

**스레드.** `PiiRedactor`는 스레드 안전합니다. 여러 스레드나 코루틴이 한 인스턴스의 `detect`·`redact`를 동시에 불러도
됩니다(호출마다 작업 메모리가 따로). 둘 다 CPU를 쓰는 블로킹 호출이므로 메인 스레드가 아니라 `Dispatchers.Default`나
백그라운드 스레드에서 부릅니다. `close()`는 실행 중인 호출을 기다립니다. 코어 하나 기준 속도: 짧은 텍스트 약 10 ms(nano)~15
ms(medium), 130토큰 문단 40~60 ms(Apple M5에서 잰 C 코어 수치; Android 폰에서는 아직 재지 않았고 더 느림).

**R8 / ProGuard.** 추가할 것이 없습니다. AAR에 JNI 클래스 `ai.newmean.pii.internal.NativeBridge`를 지키는 consumer 규칙이
들어 있습니다.

## 개발

```bash
cd android
./gradlew :pii:assembleRelease          # 세 ABI가 든 AAR (NDK 29.0.14206865, CMake 3.22.1)
./gradlew :pii:testDebugUnitTest        # JVM 테스트; JNI 라이브러리를 호스트(macOS, Linux)용으로도 빌드
./gradlew :pii:publishReleasePublicationToLocalRepository   # pii/build/repo에 AAR, POM, sources, javadoc
```

JNI 브리지(`pii/src/main/cpp/pii_jni.c`)와 `CMakeLists.txt`는 `native/core`를 제자리에서 컴파일합니다(`add_subdirectory`,
복사 없음). `armeabi-v7a`는 코어의 스칼라 커널을 씁니다. 32비트 NEON은 비정규수를 0으로 내려 마지막 비트가 달라질 수 있기
때문입니다. 단위 테스트용으로 Gradle 작업 `buildHostJni`가 같은 `CMakeLists.txt`를 `cmake`와 시스템 C 컴파일러로 호스트용
빌드하므로, 에뮬레이터 없이 JVM에서 실제 모델이 돕니다(`-Ppii.hostJni=false`면 건너뛰고 네이티브 테스트도 건너뜀).
텍스트는 표준 UTF-8 바이트(외톨이 서로게이트는 3바이트 형태)로 JNI를 건너며, `GetStringUTFChars`는 쓰지 않습니다.

일치 테스트는 비공개 픽스처와 모델 사본을 제자리에서 읽고(`PII_FIXTURES`, 기본 `js/test/fixtures`; `PII_MODELS`, 기본
`js/test/models`, Hugging Face 구조의 `pii-<size>` 폴더) 없으면 건너뜁니다. `PII_SIZES=nano,medium`으로 크기를 줄이고,
`PII_DOWNLOAD=1`이면 Hugging Face에서 nano(3.4 MB)를 실제로 내려받는 테스트가 더해집니다.

| 테스트 | 확인 내용 |
|---|---|
| tokenizer | `tokenize.jsonl`(3,273행)에서 Hugging Face tokenizers와 id·오프셋 비교 |
| prediction | `js/test/predict.test.ts`의 모든 실행에서 `detect()` 구간을 Python 평가기와 비교(실행마다 412행, 99% 이상) |
| npm | npm 패키지 `detect()`와 구간·레이블·출처가 같음(`regex`, `labels`, `sensitivity` 포함): `scripts/npm_spans.mjs`로 만든 `PII_NPM_SPANS` |
| regex | `regex.jsonl`(3,273행)에서 `PiiRegex`를 Python 기준과 비교; JavaScript `\s`와 유니코드 숫자 사례 |
| redaction, offsets | 세 모드와 직접 치환; 이모지, ZWJ 시퀀스, 국기, 결합 부호, 외톨이 서로게이트, NUL |
| options, threads, files | 레이블, 민감도, regex 병합; 한 인스턴스에 스레드 4개; `js/src/models.ts`·코어와 고정값 비교; 손상 파일, 내려받기와 재사용 |
| API | 이 README의 호출을 Kotlin(코루틴)과 Java에서 |

```bash
node scripts/npm_spans.mjs pii/build/npm.jsonl nano q4 0 1 all ../js/test/fixtures/predict-nano-q4.jsonl   # js/dist 필요
PII_NPM_SPANS=$PWD/pii/build/npm.jsonl ./gradlew :pii:testDebugUnitTest --tests '*NpmParityTest*'
```

Maven Central 배포(`ai.newmean` 네임스페이스, 서명, 업로드)는 관리자가 하며 이 빌드에는 설정하지 않았습니다.

## 라이선스

라이브러리 코드와 모델 가중치 모두 [Apache-2.0](LICENSE)이고, 제3자 고지는 [NOTICE](NOTICE)에 있습니다(두 파일 모두 AAR 안 `META-INF/ai.newmean.pii/`에도 들어 있습니다). 제품에 쓰신다면
문서나 정보·라이선스 화면에 "Powered by newmean.ai"를 적어 주시면 감사하겠습니다(라이선스 조건이 아닌 요청).
만든 곳: [newmean.ai](https://newmean.ai).
