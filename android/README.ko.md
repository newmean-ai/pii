[English](README.md) | 한국어

# ai.newmean:pii (Android)

텍스트 속 개인정보를 Android **기기 안에서** 찾아 가립니다. npm 패키지 [`@newmean-ai/pii`](../js)와 같은 다국어
모델(`newmean-pii-<size>`, 54개 언어)로 같은 구간을 찾으며, 라이브러리 안의 작은 C 코어([`native/core`](../native/core))가
실행합니다. 모델 파일이 기기에 있으면 실행 중 네트워크를 쓰지 않고, 아무것도 밖으로 보내지 않습니다.

## 설치

```kotlin
// 앱 모듈의 build.gradle.kts
dependencies {
    implementation("ai.newmean:pii:1.4.1")
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

**앱에 포함(권장).** 한 크기의 두 파일을 Hugging Face(`https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.1.0/`)에서
받아 저장소와 같은 구조로 앱 assets에 넣습니다.

```
app/src/main/assets/newmean-pii/pii-medium/tokenizer.web.json
app/src/main/assets/newmean-pii/pii-medium/onnx/model_q4e.onnx
```

```bash
cd app/src/main/assets && mkdir -p newmean-pii/pii-medium/onnx && cd newmean-pii/pii-medium
curl -LO https://huggingface.co/newmean-ai/pii-medium/resolve/v1.1.0/tokenizer.web.json
curl -L -o onnx/model_q4e.onnx https://huggingface.co/newmean-ai/pii-medium/resolve/v1.1.0/onnx/model_q4e.onnx
```

`PiiRedactor.create(context)`가 여기서 읽습니다(다른 폴더는 `source = PiiModelSource.Assets(dir = "models")`, assets 바로 아래는 `""`).

**한 번 내려받기.** `source = PiiModelSource.Download()`이면 처음 쓸 때 Hugging Face에서
`cacheDir/newmean-pii/1.1.0/pii-<size>/`로 내려받고, 불러올 때마다 다시 검사합니다. 시스템이 캐시를 지우면 다시
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
    keepEmoji = false,                    // true: 모델이 이모지도 읽음(1.0 동작); 기본: 이모지는 읽지도 가리지도 않음
    wholeWords = true,                    // true: 낱말 중간에서 끝나는 구간을 낱말 전체로 넓힘
    strictShortNames = false,             // true: 8토큰 이하의 짧은 글은 근거가 더 있어야 이름으로 봄
    allow = emptyList<PiiAllowEntry>(),   // 데이터에서 개인정보가 아닌 값(아래 '목록' 참고); 1.4.0부터
    block = emptyList<PiiBlockEntry>(),   // 데이터에서 개인정보인 값(아래 '목록' 참고); 1.4.0부터
    listsId = null,                       // 목록에 붙이는 이름, info().lists.id에 나옴; 1.4.0부터
)
PiiRedactor.create(modelFile, tokenizerFile, size, ...)   // 디스크의 파일; 바이트는 fromBytes(model, tokenizer, size, ...)

redactor.detect(text)                     // List<PiiSpan>, 정렬, 겹침 없음
redactor.redact(text, RedactMode.TAG)     // PiiRedaction(text, spans); TAG → [EMAIL], MASK → ***** (maskChar = "•"),
                                          // BLANK → 삭제; 직접 바꾸려면 redact(text) { span, original -> "..." }
redactor.detectTable(text)                // 표 모드(아래 '표' 참고)
redactor.redactTable(text)                // PiiRedaction(text, spans)
redactor.sensitivity = 0.3                // 모델을 다시 불러오지 않고 임계값 변경
redactor.info()                           // packageVersion, modelVersion, size, variant, 모델·토크나이저 고정값, 모델 구조, 목록
redactor.close()                          // 모델과 목록 해제 (AutoCloseable: use { })
PiiRedactor.applyRedaction(text, spans, RedactMode.TAG)
PiiRegex.spans(text)                      // 패턴 층만
```

`PiiSpan`에는 `start`·`end`(텍스트의 UTF-16 인덱스, `String.substring`이 받는 값이자 npm 패키지가 돌려주는 값), `label`,
`source`(`MODEL`, `REGEX`, `MODEL_AND_REGEX`, `TABLE`, `BLOCK`)가 있습니다. 구간은 문자소 클러스터 단위로 넓혀지므로 이모지, 국기,
결합 문자열은 통째로 가려지거나 통째로 남습니다. `MASK`는 npm 패키지처럼 코드 포인트마다 가림 문자 하나를 씁니다.
기본으로 이모지는 모델이 읽는 텍스트에서 빠지므로 모델이 이모지를 개인정보로 읽지 않습니다(오프셋은 입력 기준 그대로, 구간 안의 이모지는 구간을 나눔).
`keepEmoji = true`면 모델이 텍스트를 그대로 읽습니다(1.0 동작). 기본으로(`wholeWords`, 1.3.0부터) 낱말 중간에서 시작하거나 끝나는 구간을 낱말 전체로 넓힙니다("R23" → `[ID_NUMBER]23`이 아니라 `[ID_NUMBER]`). 띄어쓰기 없이 쓰는 문자(중국어, 일본어, 태국어, 크메르어 등)는 구간을 넓히지 않으며, 한글과 숫자·라틴 문자가 만나는 곳에서 낱말이 끝납니다(「010-1234-5678입니다」 → `[PHONE]입니다`), 한글 구간 뒤에 붙은 조사는 그대로 둡니다(「홍길동님께」 → `[PERSON]님께`). 구간이 끝나는 곳만 바꿀 뿐 무엇을 가릴지는 바꾸지 않습니다. 모델이 이름으로 잘못 읽은 짧은 값(「경기도」 등)은 `[PERSON]기도`가 아니라 `[PERSON]`이 되며, 이런 값을 그대로 두려면 `strictShortNames`를 씁니다. `wholeWords = false`면 1.2와 같은 구간을 받습니다. `strictShortNames = true`면 모델이 8토큰 이하로 읽는 글(설문 응답, 표 칸)은 근거가 더 있어야 사람 이름으로 봅니다. 「경기」, 「남성」, 「네이버」는 그대로 두고 「김민지」, "Maria Lopez"는 여전히 찾습니다. large에서 개인정보 없는 짧은 설문 항목을 가린 비율은 7.9%에서 4.3%로 줄고, 단독 이름을 찾은 비율은 98.9%에서 96.5%로 줄어듭니다. 긴 글은 그대로입니다. `redactor.version`은
모델 버전(1.1.0)이고 패키지 버전은 `info().packageVersion`입니다.

**스레드.** `PiiRedactor`는 스레드 안전합니다. 여러 스레드나 코루틴이 한 인스턴스의 `detect`·`redact`를 동시에 불러도
됩니다(호출마다 작업 메모리가 따로). 둘 다 CPU를 쓰는 블로킹 호출이므로 메인 스레드가 아니라 `Dispatchers.Default`나
백그라운드 스레드에서 부릅니다. `close()`는 실행 중인 호출을 기다립니다. 코어 하나 기준 속도: 짧은 텍스트 약 10 ms(nano)~15
ms(medium), 130토큰 문단 40~60 ms(Apple M5에서 잰 C 코어 수치; Android 폰에서는 아직 재지 않았고 더 느림).

**R8 / ProGuard.** 추가할 것이 없습니다. AAR에 JNI 클래스 `ai.newmean.pii.internal.NativeBridge`를 지키는 consumer 규칙이
들어 있습니다.

## 목록

1.4.0부터 데이터에 대해 이미 아는 것을 알려 줄 수 있습니다.

```kotlin
import java.text.Normalizer

val redactor = PiiRedactor.create(
    context,
    allow = listOf(PiiAllowEntry("제주"), PiiAllowEntry("배민"), PiiAllowEntry("경기", PiiLabels.PERSON)),  // 레이블이 없으면 모든 레이블
    block = listOf(PiiBlockEntry("박지훈", PiiLabels.PERSON), PiiBlockEntry("새솔고등학교", PiiLabels.ORG)),
    listsId = "survey-2026-11 v3",
)
redactor.redact(Normalizer.normalize("제주, 박지훈", Normalizer.Form.NFC)).text  // "제주, [PERSON]"
// 목록이 없으면 medium 모델은 "[PERSON], [PERSON]"
```

- `block`: 낱말 경계에서 시작하고 끝나는 항목은 모두 그 항목의 레이블로 가립니다(출처 `BLOCK`). 모델이 찾지 못한 곳도,
  `labels`로 끈 레이블도 가립니다. 한글 항목은 조사 앞에서도 맞습니다(「박지훈님께」 → 「[PERSON]님께」). "Kim"은
  "Kimberly" 안에서 맞지 않고, "Kim's"나 "Kim-Lee"에서도 맞지 않습니다(`wholeWords`처럼 아포스트로피와 하이픈이 낱말을
  잇습니다). 띄어쓰기 없이 쓰는 문자는 어디서든 맞습니다. block이 맞은 영역은 block 레이블을 쓰고 allow로 빠지지 않습니다.
- `allow`: 구간 글 전체가 항목과 같으면 가리지 않습니다. `label`이 있으면 그 레이블 계열의 구간만 허용합니다(PERSON은
  PERSON_L2, URL은 URL_L2 포함). 구간 전체를 비교하므로 모델이 구간을 어디서 끝내느냐에 달려 있습니다. 「제주도」 구간은
  "제주"로 허용되지 않으니, 두 형태가 다 나오면 둘 다 넣으세요.
- ASCII 영문자만 대소문자를 무시하고(`KIM` = `kim`) 그 밖은 정규화하지 않습니다. 글과 항목을 NFC로 넘기세요. macOS에서
  내보낸 파일이나 PDF에서 복사한 글은 NFD 한글일 수 있고, 그러면 맞지 않습니다
  (`java.text.Normalizer.normalize(s, Normalizer.Form.NFC)`).
- 정확한 형태만 맞습니다. 「박 지훈」과 「박지훈」은 다른 항목입니다. 「김」 같은 한 글자 항목은 김에 조사가 붙은
  낱말(「김은」, 「김도」)도 모두 가립니다.
- 표 모드에서 block 항목은 칸마다 맞추고 열 투표에 참여하지 않습니다. 허용된 구간은 열 투표 전에 빠집니다.
- 목록마다 항목 10,000개까지, 항목마다 1~64글자, 줄바꿈과 앞뒤 공백은 안 됩니다. 잘못된 목록은 `create`, `createBlocking`,
  `fromBytes` 모두 모델 파일을 읽기 전에 `IllegalArgumentException`인 `PiiListException`을 던집니다(`list`: "allow",
  "block", `listsId`는 "meta"; `index`, `listsId`는 null; `reason`은 `id`가 `bad_shape`, `too_long`, `bad_text`, `empty`,
  `edge_space`, `unknown_label`, `too_many`, `label_conflict`, `in_both`, `bad_lists_id` 중 하나인 `PiiListReason`). 예외에는
  항목 글이 들어가지 않습니다.
- `info().lists`(`PiiListsInfo`: `allow`, `block`, `id`)에는 감사 기록용으로 중복을 합친 뒤의 항목 수와 `listsId`가 들어
  있습니다. 항목 글에서 만든 값은 없습니다. 목록은 `close()`가 모델과 함께 해제합니다.

## 표

```kotlin
redactor.detectTable(text: String, options: TableOptions = TableOptions()): List<PiiSpan>
redactor.redactTable(text: String, options: TableOptions = TableOptions(), mode: RedactMode = RedactMode.TAG,
                     maskChar: String = "*"): PiiRedaction
redactor.redactTable(text: String, options: TableOptions, replace: (span: PiiSpan, original: String) -> String): PiiRedaction
TableOptions(delimiter = TableDelimiter.AUTO, header = true, vote = true,   // AUTO, COMMA, TAB, SEMICOLON, MARKDOWN
             context = TableContext.NONE)                                  // NONE or HEADER
```

표 모드는 CSV, TSV, 세미콜론 구분, 마크다운 파이프 표를 한 텍스트로 읽고 모든 구간을 칸 값 안으로 자릅니다. 구분자, 따옴표,
줄바꿈은 가리지 않으므로 결과도 올바른 표입니다. `AUTO`는 구분자를 자동 감지하고, 따옴표 필드는 RFC 4180을 따릅니다. `vote`가
켜져 있으면, 짧은 값으로 된 열에서 모델이 비어 있지 않은 칸의 80% 이상(4칸 이상; 비어 있지 않은 칸이 5개 미만인 열은 투표하지
않음)을 이름, 이메일, 전화번호, 사용자 이름으로 찾을 때 그 열 전체를 가립니다. 이 구간의 source는 `TABLE`입니다. 긴 글 열(값의
중앙값이 40자 또는 4단어 초과)은 투표하지 않고, 자리 표시 값(`N/A`, `-`, `none`, `null`, `없음`)은 건너뛰며, 이메일 열은 `@`,
전화 열은 숫자 6개 이상이 있는 값만 가립니다. `header`가 켜져 있으면 첫 행은 모델이 찾은 개인정보만 가리고 투표에는 넣지
않습니다. `context = TableContext.HEADER`이고 머리 행이 있으면, 비어 있지 않은 값의 80% 이상이 서로 다른 열의 비어 있지 않은
데이터 칸을 "<머리글>: <값>"으로 한 번 더 따로 읽어 그 구간을 더합니다. 이름·연락처·ID 열을 덜 놓치는 대신 개인정보가 없는
칸을 더 가리고, 그런 칸마다 모델을 한 번 더 부릅니다. 표가 아닌 텍스트는 `detect`와 똑같은 구간을 냅니다. 직접 만든
`replace`는 구분자, 따옴표, 줄바꿈을 넣으면 안 됩니다. `detect`처럼 블로킹 호출입니다.

```kotlin
val csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free"
redactor.redactTable(csv).text
// "name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free"
redactor.redactTable(tsv, TableOptions(delimiter = TableDelimiter.TAB, vote = false), RedactMode.MASK)
```

설문·양식 내보내기에서 표 모드는 이름·성이 나뉜 열의 이름을 많이 놓칩니다. `context = TableContext.HEADER`를 쓰면
놓침이 약 4분의 1로 줄고, 개인정보가 없는 칸은 2.3~3.7점 더 가립니다. 재현율이 먼저라면 이 옵션을 쓰세요. 측정값:
[저장소 README](https://github.com/newmean-ai/pii/blob/main/README.ko.md#표csv-tsv).

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
| tokenizer | `tokenize.jsonl`(3,273행)에서 Hugging Face tokenizers와 id·오프셋 비교(id는 전부, 오프셋은 npm 테스트처럼 많아야 3행까지 다를 수 있음) |
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

Maven Central 배포는 `android-v<version>` 태그로 합니다(`.github/workflows/android-publish.yml`). 워크플로가
`SIGNING_KEY`·`SIGNING_PASSWORD`가 있을 때 모든 파일에 서명하고(로컬 빌드는 서명하지 않음), 번들을 Central Portal에 올려
검증을 기다립니다. 공개는 관리자가 Portal에서 합니다.

## 라이선스

라이브러리 코드와 모델 가중치 모두 [Apache-2.0](LICENSE)이고, 제3자 고지는 [NOTICE](NOTICE)에 있습니다(두 파일 모두 AAR 안 `META-INF/ai.newmean.pii/`에도 들어 있습니다). 제품에 쓰신다면
문서나 정보·라이선스 화면에 "Powered by newmean.ai"를 적어 주시면 감사하겠습니다(라이선스 조건이 아닌 요청).
만든 곳: [newmean.ai](https://newmean.ai).
