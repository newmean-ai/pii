[English](README.md) | 한국어

# newmean-pii (Rust)

54개 언어의 개인정보를 기기 안에서 찾고, 가리고, 추출합니다. 모델은 작고(2.6~15.5 MB) 프로그램과 같은 프로세스에서 CPU로
돕니다. 별도 서비스도 API 키도 없습니다. 이 crate는 [Python, Swift, Android, Flutter 패키지](https://github.com/newmean-ai/pii)와
같은 C 코어를 컴파일하고 같은 구간을 돌려줍니다. 의존성이 없습니다(빌드할 때 C 컴파일러가 필요합니다).

```toml
[dependencies]
newmean-pii = "1.7"
```

```rust
use newmean_pii::{Label, Mode, Redactor};

let redactor = Redactor::builder().model_dir("models/pii-medium").build()?;

let text = "안녕하세요 이준호입니다 제 이메일은 joon@example.com입니다";
redactor.redact(text, Mode::Tag)?.text;
// "안녕하세요 [PERSON]입니다 제 이메일은 [EMAIL]입니다"

let found = redactor.extract(text)?;
found.first(Label::Email);   // Some("joon@example.com")
found.to_json();             // {"person":["이준호"],"email":["joon@example.com"]}
```

어떤 모델도 전부 찾지는 못합니다. 시스템 밖으로 나가는 개인정보를 줄여 줄 뿐, 하나도 빠져나가지 않는다고 보장하지
않습니다. 측정한 미탐지율은 [저장소 README](https://github.com/newmean-ai/pii/blob/main/README.ko.md)에 있습니다.

## 모델 파일

이 crate는 내려받기를 하지 않습니다. 모델 하나의 파일 두 개를 한 번 받아 같은 구조로 두세요.

```sh
base=https://huggingface.co/newmean-ai/pii-medium/resolve/v1.1.0
mkdir -p models/pii-medium/onnx
curl -L -o models/pii-medium/tokenizer.web.json $base/tokenizer.web.json
curl -L -o models/pii-medium/onnx/model_q4e.onnx $base/onnx/model_q4e.onnx
```

`model_files(size, variant)`가 파일마다 URL, 크기, SHA-256을 알려 줍니다. 모든 파일은 불러오기 전에 공개된 크기와
SHA-256과 대조하므로, 손상되거나 바뀐 파일은 다른 모델로 돌지 않고 오류가 됩니다.

| 크기 | 가중치 | 파일 |
|---|---|---|
| `Size::Nano` | 2.6 MB | `onnx/model_q4.onnx` |
| `Size::Small` | 7.1 MB | `onnx/model_q4.onnx` |
| `Size::Medium` (기본) | 11.3 MB | `onnx/model_q4e.onnx` |
| `Size::Large` | 15.5 MB | `onnx/model_q4e.onnx` |

여기에 0.8 MB 토크나이저가 더해집니다. 큰 모델일수록 덜 놓칩니다. 파일을 주는 다른 방법은 다음과 같습니다.

- `model_dir` 없이 `Builder::build()`를 부르면 Python 패키지의 캐시(`$NEWMEAN_PII_CACHE`, `$XDG_CACHE_HOME/newmean-pii`
  또는 `~/.cache/newmean-pii`)를 읽습니다. `python -m newmean_pii download --size medium`이 채워 줍니다.
- `Builder::build_from_bytes(onnx, tokenizer)`는 바이트를 받습니다. `include_bytes!`나 자체 저장소에 씁니다.

## 레이블

`Person`, `Email`, `Phone`, `Address`, `IdNumber`, `Financial`, `Org`, `Username`, `Url`, `DateOfBirth`, `Secret`, 그리고
간접 식별자를 위한 2단계 레이블 두 개가 있습니다: `PersonL2`(호칭이 붙은 성, 별명, 이니셜)와 `UrlL2`(공개 프로필이나
페이지 URL). `Label::as_str()`은 다른 패키지가 쓰는 이름(`"PERSON"`, `"ID_NUMBER"`)을 줍니다.

모델은 이 레이블만 압니다. `labels`로 이 가운데에서 고르고, 다른 종류의 값은 block 목록에 넣습니다.

## API

```rust
let redactor = Redactor::builder()
    .size(Size::Medium)                     // Nano | Small | Medium | Large
    .variant(Variant::Q4e)                  // 기본: Large/Medium은 Q4e, Small/Nano는 Q4
    .sensitivity(0.0)                       // 0보다 크면 더 많이, 작으면 더 적게 가림 (대략 -1..1)
    .labels([Label::Person, Label::Phone])  // 기본: 전체 레이블; Label::LEVEL1은 2단계를 가리지 않음
    .keep_emoji(false)                      // true: 모델이 이모지도 읽음
    .whole_words(true)                      // 단어 중간에서 끝나는 구간을 단어 전체로 넓힘
    .strict_short_names(false)              // true: 짧은 글은 더 강한 근거가 있어야 이름으로 읽음
    .allow("제주")                           // 내 데이터에서 개인정보가 아닌 값 (모든 레이블)
    .allow_label("경기", Label::Person)      // ... 한 레이블 계열에만
    .block("새솔고등학교", Label::Org)         // 내 데이터에서 개인정보인 값, 나오는 곳마다 가림
    .model_dir("models/pii-medium")         // 또는 .cache_dir(...), build_from_bytes
    .build()?;

redactor.detect(text)?;                     // Vec<Span>, 정렬됨, 겹치지 않음
redactor.redact(text, Mode::Tag)?;          // Redacted { text, spans }; Tag -> [EMAIL], Mask -> ****, MaskWith('#'), Blank
redactor.redact_with(text, |span, original| format!("<{}>", span.label.key()))?;
redactor.extract(text)?;                    // Extracted: 레이블별로 묶은 값
redactor.detect_table(csv, &TableOptions::default())?;
redactor.redact_table(csv, Mode::Tag, &TableOptions::default())?;
redactor.tokenize(text)?;                   // Vec<Token>
redactor.set_sensitivity(0.5)?;             // &mut 필요; 다시 불러오지 않음
redactor.info();                            // 버전, 모델 파일, 커널 빌드, 목록 크기

apply_redaction(text, &spans, Mode::Tag);   // 직접 거른 구간만 가리기
Extracted::new(text, spans);                // 직접 거른 구간에서 추출하기
```

- **위치**: `span.start..span.end`는 UTF-8 바이트라서 `&text[span.start..span.end]`(또는 `span.text(text)`)가 그 값입니다.
  구간마다 `char_start..char_end`(코드 포인트, Python 기준)와 `utf16_start..utf16_end`(JavaScript, Java, Swift의
  `NSString` 기준)도 들어 있습니다. 구간은 문자소 묶음을 쪼개지 않습니다.
- **출처**: `Source::Model`, `Source::Table`(표 모드에서 열 전체로 퍼뜨린 구간), `Source::Block`.
- **스레드**: `Redactor`는 `Send + Sync`입니다. 모델은 바뀌지 않고 호출마다 작업 메모리를 따로 쓰므로, 여러 스레드의
  호출이 코어 하나씩을 써서 동시에 돕니다. `Arc`나 `OnceLock`에 넣어 공유하세요.
- **빌드**: C 코어는 프로그램을 디버그로 빌드해도 항상 `-O3`로 컴파일됩니다.
- **오류**: `Error::InvalidOption`, `Error::ModelFile`(파일 없음, 크기나 SHA-256 불일치), `Error::List`(어느 목록의 몇
  번째 항목이 왜 틀렸는지; 항목의 글자는 담지 않음), `Error::Core`. 옵션과 목록은 모델 파일을 읽기 전에 검사합니다.
- **목록**은 다른 패키지와 같은 규칙을 따릅니다: ASCII 글자만 대소문자를 구분하지 않고 그 밖에는 정규화하지 않으며(NFC
  글을 넘기세요), 항목은 1~64 코드 포인트, 목록마다 최대 10,000개입니다. 자세한 내용:
  [Python README, 목록](https://github.com/newmean-ai/pii/blob/main/python/README.ko.md).
- **표**: `TableOptions { delimiter, header, vote, context }`는 다른 패키지와 같습니다:
  [Python README, 표](https://github.com/newmean-ai/pii/blob/main/python/README.ko.md).

## 값 되돌리기 (vault)

1.7.2부터. vault는 번호가 붙은 자리표시와 원래 값의 대응표를 이 기기 안에만 둡니다. 그래서 모델 답변에서 원래 값을 되돌릴 수
있습니다. 대응표는 어디로도 보내지 않습니다.

```rust
let mut vault = Vault::new(); // one per user or conversation
let masked = redactor.redact_vault("Call Kim Minsu on 010-1234-5678", &mut vault)?.text; // "Call [PERSON_1] on [PHONE_1]"
assert_eq!(vault.restore("I called [PERSON_1]."), "I called Kim Minsu.");
```

한 vault 안에서 같은 값에는 늘 같은 자리표시가 붙고, vault에 있는 값은 같은 글이든 뒤의 글이든 다시 나오면 모델이 그 자리에서
놓치더라도 다시 가립니다(출처 `vault`). 모델이 찾은 값은 구간이 값 둘레에서 함께 잡은 공백, 문장 부호,
괄호, 따옴표를 빼고 배우며, 생년월일은 ISO 타임스탬프의 시각을 빼고 배웁니다. 입력에 원래 있던 자리표시 같은 글은 "［PERSON_1]"로 바꿔 보내고 되돌리지 않습니다.
restorer는 스트리밍 답변을 되돌리고, `bindings`는 모델 호출 한 번의 값을 스냅숏으로 잡으며, `entries`로 요청 사이에 vault를 이어
씁니다(원래 값이 들어 있음). **vault를 여러 사용자가 함께 쓰면 안 됩니다.**

아는 값을 찾는 법은 여섯 패키지가 같습니다(공유 사례: `python/tests/vault_cases.json`).

- 3자 이상입니다. 띄어 쓰지 않는 문자 체계(한자, 가나, 태국 문자, 라오, 크메르, 미얀마, 타이 탐, 발리, 자와) 글자가 하나라도
  있거나 두 글자가 모두 한글 음절이면 2자도 찾습니다. 그보다 짧은 값은 모델이 찾은 것이면 답에서 되돌리되 다른 곳에서 찾지
  않고, 애플리케이션이 넣은 것이면 `hide_value`가 문자열 전체로 만날 때 찾습니다.
- 단어 경계에서 찾습니다. 값 뒤의 모음 기호 같은 결합 기호는 값에 속합니다(`สมัคร`에서 `สม`을 찾지 않음). 한글 값 뒤에는
  조사가 붙어도 되고, 바로 앞의 한글은 경계가 아닙니다. 한글 두 음절인 값 뒤에는 같은 단어 안에 조사, 높임의 「님」·「씨」,
  서술격 조사만 올 수 있습니다(`지원님께`는 이름, `지원금`은 다른 단어). 이름에 붙은 글자(아랍어 접두어, 격어미)는 다른
  단어입니다.
- 이메일 주소, 사용자 이름, 두 단어 이상인 값은 ASCII 대소문자를 가리지 않고, URL의 scheme과 호스트도 그렇습니다. 모든 값은
  대문자로 쓴 것도 찾습니다. 대문자는 유니코드 전체 대문자 변환이고(`Weiß`에 대한 `WEISS`), 터키어·아제르바이잔어(`EMİNE`),
  그리스어(강세와 숨표 없이 `Νίκος`에 대한 `ΝΙΚΟΣ`), 독일어(`Weiß`에 대한 `WEIẞ`)가 쓰는 대문자도 찾습니다. 한 단어인 값을
  다른 대소문자로 쓰면 다른 단어입니다(`Will`과 `will`).
- 찾은 값은 바로 뒤의 결합 부호, 이체자 선택자, 결합자까지 포함하므로 그 구간은 문자소 클러스터 중간에서 끝나지 않습니다.
- 자모를 풀어 쓴 한글(NFD)은 음절로, 전각 ASCII와 U+3000은 ASCII로 읽습니다. 이렇게 찾은 값은 아는 값의 자리표시를 씁니다.
- 숫자 사이의 점은 숫자를 잇습니다. 숫자로 시작하거나 끝나는 값은 점이 그 값을 다른 숫자와 이어 주는 곳에서 찾지 않습니다
  (4524.7의 4524, `Unit 4.12 Main St`의 `12 Main St`도). 지수는 숫자만 잇습니다. 1e+4524에서 4524는 찾지 않지만, `4E+` 뒤의
  주소는 찾습니다.
- 모델이 표시한 값은 가장자리에 붙은, 값이 아닌 공백, 구두점, 괄호, 따옴표를 빼고 배웁니다(`Kim Minsu.`, `"Kim Minsu"`,
  `(Kim Minsu`는 `Kim Minsu`로 배우고, 뺀 글자는 글에 남습니다). 남는 값이 너무 짧아 다시 찾을 수 없으면 공백만 뺍니다.
  짝이 값 안에 있는 괄호는 남기고(`(02) 1234-5678`), 값 앞의 `.`과 ORG 끝의 `.`(`Tiffany & Co.`)도 남기며, SECRET은
  공백만 뺍니다.

그 밖에는 쓰인 그대로 찾습니다. 다르게 쓴 공백, 다른 표기의 숫자, 날짜, 이스케이프, URL 인코딩은 npm·Python 패키지만 읽으며,
두 패키지에는 답변과 허용한 도구 호출을 되돌리는 미들웨어도 있습니다([js/README.ko.md](../js/README.ko.md#아는-값을-다시-찾는-법)).
자리표시는 `[<계열>_<번호>]`(계열 32자까지, 번호 1~12자리)입니다. 번호를 다 쓴 계열은 `[PERSON__1]`로 이어지고, 빈 값에는
자리표시를 붙이지 않습니다. 단어 글자는 crate에 든 유니코드 16.0 표를 씁니다.

## 추출

`extract`는 가린 글 대신 찾은 값을 데이터로 돌려줍니다.

```rust
let found = redactor.extract("Kim Minsu and Lee Jiwon, kim@example.com")?;
found.get(Label::Person);   // ["Kim Minsu", "Lee Jiwon"]
found.first(Label::Email);  // Some("kim@example.com")
found.to_map();             // BTreeMap {"email": [...], "person": [...]}
found.to_json();            // {"person":["Kim Minsu","Lee Jiwon"],"email":["kim@example.com"]}
for e in found.entities() { /* e.label, e.text, e.span, 글에 나온 순서 */ }
```

- 값은 레이블 계열별로 묶고(`PersonL2`는 `person`, `UrlL2`는 `url` 아래), 중복을 빼고, 글에 나온 순서로 둡니다. 키는
  레이블 이름의 소문자입니다(`person`, `email`, `phone`, `address`, `id_number`, `financial`, `org`, `username`, `url`,
  `date_of_birth`, `secret`). 필요한 필드 이름으로는 직접 바꿔 쓰세요.
- 한 글에 같은 레이블의 값이 여러 개 있을 수 있어서 모든 묶음은 목록입니다.
- 모델은 값이 무엇인지 알려 줄 뿐 누구의 것인지는 알려 주지 않습니다. 고객의 이름과 상담원의 이름이 둘 다 `person`입니다.
- 값마다의 신뢰도 점수는 없습니다. `sensitivity`가 전체의 기준을 옮깁니다.

## 이 crate에 없는 것

다른 패키지의 패턴 계층(`regex: true`, 그쪽에서도 기본은 꺼짐)과 모델 파일 내려받기.

## 개발

```sh
cargo test                  # 모델이 필요한 테스트는 PII_MODELS나 캐시를 읽고, 없으면 아무것도 하지 않고 통과합니다
cargo run --release --example extract -- models/pii-medium "Call Maria Lopez on +1 415 555 0142"
tool/vendor_core.sh && cargo publish --dry-run --allow-dirty && tool/vendor_core.sh --clean
```

저장소에서는 `build.rs`가 `../native/core`를 컴파일하고, 공개된 crate에는 그 사본이 `native-core/`에 들어 있습니다.

## 라이선스

Apache-2.0 (코드와 모델). [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 보세요.
