[English](https://github.com/newmean-ai/pii/blob/main/python/README.md) | 한국어

# newmean-pii

텍스트를 저장·로그 기록·LLM 전송하기 전에 **내 컴퓨터 안에서** 개인정보를 찾아 가립니다. 작은 다국어 토큰 분류
모델(54개 언어)이 C 확장 모듈에서 돌고, 첫 다운로드 뒤에는 네트워크를 쓰지 않으며 의존 패키지도 없습니다. npm 패키지
[`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii)의 Python 판으로, 같은 모델에서 같은 구간을 냅니다.

```bash
pip install newmean-pii
```

```python
from newmean_pii import create_redactor

redactor = create_redactor(size="medium")
redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다").text
# '제 메일은 [EMAIL], 전화는 [PHONE]입니다'
```

Python 3.9 이상. Linux(x86-64, arm64), macOS(arm64, x86-64), Windows(x86-64) 휠을 제공하고, 그 밖의 플랫폼은 C11
컴파일러로 sdist에서 빌드합니다.

## 모델

| size | 파일 | 비고 |
|---|---|---|
| `nano` | 2.6 MB | 공유 레이어 하나를 4번 적용, rank-32 임베딩 |
| `small` | 7.1 MB | 4 레이어, rank-64 임베딩 |
| `medium` (기본) | 11.3 MB | 6 레이어, rank-192 임베딩 |
| `large` | 15.5 MB | 6 레이어, 전체(어휘 가지치기) 임베딩 |

여기에 0.8 MB 토크나이저가 더해집니다. 처음 쓸 때 Hugging Face(`newmean-ai/pii-<size>`, 태그 `v1.1.0`)에서
`~/.cache/newmean-pii`로 내려받고, 이 패키지에 고정된 SHA-256 값과 대조합니다. 이후 불러올 때마다 다시 대조합니다.

모든 개인정보를 찾는 모델은 없습니다. 언어·데이터셋별 놓침률과 과잉 마스킹률은 기술 보고서에 있습니다. 결과는 보장이
아니라 강력한 1차 처리로 다뤄 주세요. newmean-pii-dataset(공개 테스트, 54개 언어)에서 개인정보 글자 중 놓치는 비율은
1.6%(large)~2.9%(nano)이며, 전체 비교는 [저장소 README](https://github.com/newmean-ai/pii#results)에 있습니다.

## 레이블

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, 그리고
간접 식별자용 2단계 레이블 두 개: `PERSON_L2`(호칭이 붙은 성, 별명, 이니셜)와 `URL_L2`(공개 프로필·페이지 URL).
2단계를 가리지 않으려면 `labels=LEVEL1_LABELS`를 넘기세요.

사람은 가리고 회사·가게 이름은 남기려면 `ORG`를 빼세요. 아는 가게인데 모델이 사람으로 읽는 이름(주인 이름을 딴 가게)은 허용
목록에 넣습니다([목록](#목록)):

```python
from newmean_pii import LABELS, create_redactor

redactor = create_redactor(size="medium", labels=[l for l in LABELS if l != "ORG"], allow=[("Wendy's", "PERSON")])
redactor.redact("Customer Maria Lopez disputes a charge from Jo Malone London and one from Tiffany & Co.").text
# 'Customer [PERSON] disputes a charge from Jo Malone London and one from Tiffany & Co.'
```

## API

```python
create_redactor(               # 또는 Redactor(...), 인자 동일
    size="medium",             # "nano" | "small" | "medium" | "large"
    variant=None,              # "q4" | "q4e"; 기본은 large/medium q4e, small/nano q4
    *,
    sensitivity=0.0,           # > 0 더 많이 가림, < 0 덜 가림 (대략 -1..1); NaN이나 무한대는 ValueError(1.4.1부터)
    labels=None,               # 가릴 레이블; 기본 전체(LABELS); 모르는 이름은 ValueError
    regex=False,               # 몇 가지 패턴(이메일, URL, 카드/계좌, 전화, 주민등록번호)도 가림;
                               # 기본 꺼짐: 추가 탐지는 거의 없고 과잉 마스킹이 두 배
    keep_emoji=False,          # True: 모델이 이모지도 읽음(1.0 동작); 기본: 이모지는 읽지도 가리지도 않음
    whole_words=True,          # 기본 True: 낱말 중간에서 끝나는 구간을 낱말 전체로 넓힘
    strict_short_names=False,  # True: 8토큰 이하의 짧은 글은 근거가 더 있어야 이름으로 봄
    allow=None,                # 데이터에서 개인정보가 아닌 값: str 또는 (str, label) (아래 '목록' 참고); 1.4.0부터
    block=None,                # 데이터에서 개인정보인 값: (str, label) (아래 '목록' 참고); 1.4.0부터
    lists_id=None,             # 목록에 붙이는 이름, info()["lists"]["id"]에 나옴; 1.4.0부터
    model_dir=None,            # tokenizer.web.json과 onnx/model_<variant>.onnx가 있는 폴더; 다운로드하지 않음
    cache_dir=None,            # 기본: $NEWMEAN_PII_CACHE, $XDG_CACHE_HOME/newmean-pii 또는 ~/.cache/newmean-pii
    model_url=None,            # newmean-ai/pii-<size> 미러의 기준 URL (https://, http://, file://)
    on_progress=None,          # callback(file, loaded_bytes, total_bytes)
) -> Redactor

redactor.detect(text)                  # -> [Span(start, end, label, source)], 정렬, 겹침 없음
redactor.redact(text, mode="tag")      # -> RedactResult(text, spans); "tag" -> [EMAIL], "mask" -> *****, "blank" -> 삭제
redactor.redact(text, mask_char="#", replace=None)  # replace(span, original) -> str 가 mode보다 우선
redactor.detect_table(text, delimiter="auto", header=True, vote=True, context="none")  # 표 모드(아래 '표' 참고)
redactor.redact_table(text, mode="tag", *, delimiter="auto", header=True, vote=True, context="none", mask_char="*", replace=None)
redactor.set_sensitivity(value)        # 다시 불러오지 않음; NaN이나 무한대는 마찬가지로 ValueError
redactor.tokenize(text)                # -> [Token(id, start, end)]
redactor.info()                        # package_version, model_version, size, variant, files(path, bytes, sha256), 모델 구조, build,
                                       # lists(allow, block, id)
redactor.close()                       # 또는: with create_redactor() as r: ...

apply_redaction(text, spans, mode="tag", mask_char="*", replace=None)
regex_spans(text)                      # 패턴 레이어만
fetch_model(size, variant=None, cache_dir=None)  # 미리 내려받고 검증; 폴더를 돌려줌. 거기에 파일을 쓰지 못하면
                                                 # ModelFileError(오프라인 사용 참고)
```

- **오프셋**은 Python `str` 인덱스(코드 포인트)입니다. 이모지가 있어도 `text[span.start:span.end]`가 가릴 텍스트입니다.
  npm 패키지는 같은 구간을 UTF-16 인덱스로 냅니다. 구간은 문자소 클러스터 단위로 넓혀지므로 이모지, 국기, 결합 문자열은
  통째로 가려지거나 통째로 남습니다. `source`는 `"model"`, `"regex"`, `"model+regex"`, `"table"`, `"block"` 중 하나입니다.
- **이모지**: 기본으로 이모지는 모델이 읽는 텍스트에서 빠지므로 모델이 이모지를 개인정보로 읽지 않습니다. 오프셋은 그대로 입력 기준이고, 구간 안의
  이모지는 구간을 둘로 나눕니다. `keep_emoji=True`면 모델이 텍스트를 그대로 읽습니다(1.0 동작).
- **낱말 단위**: 기본으로(1.3.0부터) 낱말 중간에서 시작하거나 끝나는 구간을 낱말 전체로 넓힙니다("R23" → `[ID_NUMBER]23`이 아니라 `[ID_NUMBER]`). 띄어쓰기 없이 쓰는 문자(중국어, 일본어, 태국어, 크메르어 등)는 구간을 넓히지 않으며, 한글과 숫자·라틴 문자가 만나는 곳에서 낱말이 끝납니다(「010-1234-5678입니다」 → `[PHONE]입니다`), 한글 구간 뒤에 붙은 조사는 그대로 둡니다(「홍길동님께」 → `[PERSON]님께`). 구간이 끝나는 곳만 바꿀 뿐 무엇을 가릴지는 바꾸지 않습니다. 모델이 이름으로 잘못 읽은 짧은 값(「경기도」 등)은 `[PERSON]기도`가 아니라 `[PERSON]`이 되며, 이런 값을 그대로 두려면 `strict_short_names=True`를 씁니다. `whole_words=False`면 1.2와 같은 구간을 받습니다.
- **짧은 값**: `strict_short_names=True`면 모델이 8토큰 이하로 읽는 글(설문 응답, 표 칸)은 근거가 더 있어야 사람 이름으로 봅니다. 「경기」, 「남성」, 「네이버」는 그대로 두고 「김민지」, "Maria Lopez"는 여전히 찾습니다. large에서 개인정보 없는 짧은 설문 항목을 가린 비율은 7.9%에서 4.3%로 줄고, 단독 이름을 찾은 비율은 98.9%에서 96.5%로 줄어듭니다. 긴 글은 그대로입니다.
- **버전**: `redactor.version`은 모델 버전(1.1.0)입니다. 감사 기록에 남길 패키지 버전은 `newmean_pii.__version__`이나
  `info()["package_version"]`입니다.
- **`tag` 모드**는 레이블 계열을 씁니다. `PERSON_L2`는 `[PERSON]`, `URL_L2`는 `[URL]`이 됩니다. `mask`는 코드 포인트마다
  `mask_char` 하나를 씁니다.
- **스레드**: redactor 하나를 함께 써도 됩니다. 모델은 불변이고 실행 중에는 GIL을 풀기 때문에 스레드 풀로 병렬
  처리됩니다(호출 하나당 코어 하나).

  ```python
  from concurrent.futures import ThreadPoolExecutor
  with ThreadPoolExecutor(4) as pool:
      results = list(pool.map(redactor.redact, texts))
  ```

- **속도**: Apple M5 Pro 한 스레드에서 짧은 텍스트 하나에 nano 약 6.5 ms, medium 약 10 ms, 1,000토큰 텍스트는 0.22초(nano)
  ~0.35초(medium)(arm64, 1.5.0부터). 긴 텍스트는 256토큰 창으로 겹쳐 나눠 처리합니다.
  x86-64는 AMD EPYC 9V74 한 스레드(GitHub Actions `ubuntu-24.04` 러너, 2026-11-25; 평균 40토큰인 짧은 글 227개와 1,010토큰
  글 하나)에서 `sse`로 짧은 텍스트 하나에 nano 약 23 ms, medium 약 35 ms, 1,000토큰은 0.63초(nano)~0.94초(medium),
  `avx2`로 약 13 ms와 19 ms, 0.27초~0.41초입니다(1.7.2 커널의 manylinux 휠, `sse`는 `NEWMEAN_PII_KERNEL=sse`로 고정).
- **커널**: `info()["build"]`는 그 프로세스가 쓰는 행렬 커널의 이름입니다. `+fma`가 없는 커널은 어느 CPU에서나 로짓이
  비트 단위로 같고 npm 패키지의 기본 WebAssembly 빌드와도 같습니다. `+fma` 커널은 곱셈과 덧셈을 융합해 더 빠르고, 로짓이
  마지막 비트에서 달라 드물게 구간이 달라질 수 있습니다. arm64는 `neon+fma`, Windows 휠(MSVC)은 `scalar`를 씁니다.
  x86-64의 Linux·macOS 휠은 1.7.2부터(그전에는 `sse`) 첫 detect나 `build_info` 호출 때 프로세스마다 한 번 커널을
  고릅니다. CPU와 운영체제가 AVX2를 지원하면 `avx2`(한 번에 float 8개), 아니면 `sse`입니다. 그 호출 전에
  `NEWMEAN_PII_KERNEL=avx2+fma`를 두면 AVX2와 FMA3가 있는 CPU에서 융합 커널을 씁니다(로짓이 `neon+fma`와 같음).
  `NEWMEAN_PII_KERNEL=sse`는 4폭 커널을 씁니다. 모르는 이름이나 CPU에 없는 커널이면 기본을 씁니다.
  AVX2와 FMA3가 있는 x86-64 CPU(Intel Haswell, AMD Zen 이후)용으로 융합 곱셈-덧셈을 켜고 소스에서 빌드하려면(이렇게 만든
  빌드는 그런 CPU에서만 돕니다):

  ```bash
  CFLAGS="-march=x86-64-v3 -DPII_FMA" pip install --no-binary newmean-pii newmean-pii
  ```

  그 빌드의 `info()["build"]`는 `+fma`로 끝납니다(`avx2+fma`, `NEWMEAN_PII_KERNEL=sse+fma`면 `sse+fma`).
- 오류: 잘못된 옵션은 `ValueError`(잘못된 목록은 `ValueError`의 하위 클래스인 `ListError`), 파일이 없거나 내려받지 못하거나
  고정값과 다르면 `ModelFileError`.

## 목록

1.4.0부터 데이터에 대해 이미 아는 것을 알려 줄 수 있습니다.

```python
import unicodedata

redactor = create_redactor(
    allow=["제주", "배민", ("경기", "PERSON")],  # 문자열은 모든 레이블을 허용
    block=[("박지훈", "PERSON"), ("새솔고등학교", "ORG")],
    lists_id="survey-2026-11 v3",
)
redactor.redact(unicodedata.normalize("NFC", "제주, 박지훈")).text  # '제주, [PERSON]'
# 목록이 없으면 medium 모델은 '[PERSON], [PERSON]'
```

- `block`: 낱말 경계에서 시작하고 끝나는 항목은 모두 그 항목의 레이블로 가립니다(출처 `"block"`). 모델이 찾지 못한 곳도,
  `labels`로 끈 레이블도 가립니다. 한글 항목은 조사 앞에서도 맞습니다(「박지훈님께」 → 「[PERSON]님께」). "Kim"은
  "Kimberly" 안에서 맞지 않고, "Kim's"나 "Kim-Lee"에서도 맞지 않습니다(`whole_words`처럼 아포스트로피와 하이픈이 낱말을
  잇습니다). 띄어쓰기 없이 쓰는 문자는 어디서든 맞습니다. block이 맞은 영역은 block 레이블을 쓰고 allow로 빠지지 않습니다.
- `allow`: 구간 글 전체가 항목과 같으면 가리지 않습니다. 레이블이 있으면 그 레이블 계열의 구간만 허용합니다(PERSON은
  PERSON_L2, URL은 URL_L2 포함). 구간 전체를 비교하므로 모델이 구간을 어디서 끝내느냐에 달려 있습니다. 「제주도」 구간은
  "제주"로 허용되지 않으니, 두 형태가 다 나오면 둘 다 넣으세요.
- 항목: `allow`는 `str`이나 `(str, label)`(label이 `None`이면 모든 레이블), `block`은 `(str, label)`을 받습니다. 두 목록 모두
  `str`과 `bytes`가 아닌 이터러블이면 됩니다. 2-튜플을 목록 자체로 넘기면 항목 두 개로 읽습니다. `block=("Kim", "PERSON")`은
  `bad_shape` 오류를 내고, `allow=("경기", "PERSON")`은 오류 없이 "경기"와 "PERSON"을 모든 레이블에 허용합니다. 레이블을
  붙인 항목 하나는 `block=[("Kim", "PERSON")]`, `allow=[("경기", "PERSON")]`처럼 목록에 담아 쓰세요.
- ASCII 영문자만 대소문자를 무시하고(`KIM` = `kim`) 그 밖은 정규화하지 않습니다. 글과 항목을 NFC로 넘기세요. macOS에서
  내보낸 파일이나 PDF에서 복사한 글은 NFD 한글일 수 있고, 그러면 맞지 않습니다(`unicodedata.normalize("NFC", text)`). 이
  규칙은 목록의 것이고, vault는 아는 값을 더 많은 표기에서 찾습니다([통합](#통합)의 "아는 값을 다시 찾는 법").
- 정확한 형태만 맞습니다. 「박 지훈」과 「박지훈」은 다른 항목입니다. 「김」 같은 한 글자 항목은 김에 조사가 붙은
  낱말(「김은」, 「김도」)도 모두 가립니다.
- 표 모드에서 block 항목은 칸마다 맞추고 열 투표에 참여하지 않습니다. 허용된 구간은 열 투표 전에 빠집니다.
- 목록마다 항목 10,000개까지, 항목마다 1~64글자, 줄바꿈과 앞뒤 공백은 안 됩니다. 잘못된 목록은 모델 파일을 불러오기 전에
  `newmean_pii.ListError`(`ValueError`의 하위 클래스)를 냅니다(`.list`: "allow", "block", `lists_id`는 "meta"; `.index`,
  `lists_id`와 목록 자체의 오류는 `None`; `.reason`: `bad_shape`, `too_long`, `bad_text`, `empty`, `edge_space`,
  `unknown_label`, `too_many`, `label_conflict`, `in_both`, `bad_lists_id`). 오류에는 항목 글이 들어가지 않습니다.
- `info()["lists"]`에는 감사 기록용으로 중복을 합친 뒤의 항목 수와 `lists_id`가 들어 있습니다. 항목 글에서 만든 값은
  없습니다. 목록은 redactor와 함께 해제됩니다.

## 표

```python
redactor.detect_table(text, delimiter="auto", header=True, vote=True, context="none") -> List[Span]
redactor.redact_table(text, mode="tag", *, delimiter="auto", header=True, vote=True, context="none", mask_char="*", replace=None) -> RedactResult
```

표 모드는 CSV, TSV, 세미콜론 구분, 마크다운 파이프 표를 한 텍스트로 읽고 모든 구간을 칸 값 안으로 자릅니다. 구분자, 따옴표,
줄바꿈은 가리지 않으므로 결과도 올바른 표입니다. `delimiter`는 `"auto"`(자동 감지), `","`, `"\t"`, `";"`, `"markdown"`
(`TABLE_DELIMITERS`)이고, 따옴표 필드는 RFC 4180을 따릅니다. `vote`가 켜져 있으면, 짧은 값으로 된 열에서 모델이 비어 있지 않은
칸의 80% 이상(4칸 이상; 비어 있지 않은 칸이 5개 미만인 열은 투표하지 않음)을 이름, 이메일, 전화번호, 사용자 이름으로 찾을 때
그 열 전체를 가립니다. 이 구간의 source는 `"table"`입니다. 긴 글 열(값의 중앙값이 40자 또는 4단어 초과)은 투표하지 않고, 자리
표시 값(`N/A`, `-`, `none`, `null`, `없음`)은 건너뛰며, 이메일 열은 `@`, 전화 열은 숫자 6개 이상이 있는 값만 가립니다. `header`가
켜져 있으면 첫 행은 모델이 찾은 개인정보만 가리고 투표에는 넣지 않습니다. `context="header"`(`TABLE_CONTEXTS`; 기본 `"none"`)이고
머리 행이 있으면, 비어 있지 않은 값의 80% 이상이 서로 다른 열의 비어 있지 않은 데이터 칸을 `"<머리글>: <값>"`으로 한 번 더 따로
읽어 그 구간을 더합니다. 이름·연락처·ID 열을 덜 놓치는 대신 개인정보가 없는 칸을 더 가리고, 그런 칸마다 모델을 한 번 더
부릅니다. 표가 아닌 텍스트는 `detect(text)`와 똑같은 결과를 냅니다. 직접 만든 `replace`는 구분자, 따옴표, 줄바꿈을 넣으면 안 됩니다.

```python
csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free"
redactor.redact_table(csv).text
# 'name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free'
redactor.redact_table(tsv, "mask", delimiter="\t", vote=False)
```

설문·양식 내보내기에서 표 모드는 이름·성이 나뉜 열의 이름을 많이 놓칩니다. `context="header"`를 쓰면 놓침이 약 4분의 1로
줄고, 개인정보가 없는 칸은 2.3~3.7점 더 가립니다. 재현율이 먼저라면 이 옵션을 쓰세요. 측정값:
[저장소 README](https://github.com/newmean-ai/pii/blob/main/README.ko.md#표csv-tsv).

## 통합

LangChain, LiteLLM, Microsoft Presidio, Langfuse용 어댑터입니다. 프롬프트가 외부 LLM에 가기 전이나 기록이
저장되기 전에 개인정보를 가립니다. 어댑터는 모두 모델을 내 프로세스 안에서 돌리며, 이 라이브러리들은 이 패키지의
의존성이 아닙니다(`pip install "newmean-pii[langchain]"`, `"newmean-pii[litellm]"`, `"newmean-pii[presidio]"`로
함께 설치).

**어디에서 도는가.** `redact`와 미들웨어는 앱 프로세스 안에서, 글이 밖으로 나가기 전에 가립니다. 값을 되돌리는 vault도 여기서만
쓸 수 있습니다. LiteLLM guardrail은 프록시 안에서 가립니다. 프록시를 거쳐 모델을 부르는 앱 모두를 한곳에서 막지만, 글은
프록시까지는 그대로 가고 값을 되돌리지 않습니다. 클라우드 공급자의 자체 개인정보 필터(Amazon Bedrock Guardrails 등)는 글이
공급자에 닿은 뒤에 돕니다. 이 패키지 옆에 켜 두어도 되고, 그러면 그 필터는 자리표시를 봅니다. 로그와 트레이스에 개인정보를
남기지 않으려면 돌아오는 글도 가리세요(guardrail의 `mode: "post_call"`, Langfuse의 `pii_mask`. `pii_mask`는 Langfuse가
넘기는 부분만 가립니다).

**LangChain**(1.4.0 이상): `PIIMiddleware`용 탐지기로, 모델을 한 번 불러 모든 레이블을 알려 줍니다.

```python
from langchain.agents import create_agent
from newmean_pii.langchain import pii_middleware

agent = create_agent("openai:gpt-6.1", middleware=[pii_middleware(size="medium")])
# "김민수에게 010-1234-5678로 전화해 줘"는 모델에 "[REDACTED_PERSON]에게 [REDACTED_PHONE]로 전화해 줘"로 갑니다
```

`pii_middleware(redactor=None, *, strategy="redact", apply_to_input=True, apply_to_output=False,
apply_to_tool_results=False, labels=None, name="pii", **create_redactor_options)`는 `PIIMiddleware`를 돌려줍니다.
`strategy`는 LangChain의 값(`"redact"`, `"mask"`, `"hash"`, `"block"`)입니다. `pii_detector(redactor=None, *,
labels=None, **options)`는 탐지기만 돌려줍니다. `(text) -> [{"type", "value", "start", "end"}]` 꼴이고 `type`은 소문자
레이블이며, 직접 만든 `PIIMiddleware(...)`나 같은 꼴의 함수를 받는 코드에 쓸 수 있습니다. redactor를 주지 않으면 모델은
첫 호출 때 읽습니다.

**에이전트: 에이전트가 읽는 것도 가리기.** 개인정보는 뒤의 턴에서도 모델에 들어옵니다. CRM 조회, 검색 결과, PDF가 사용자가
쓰지 않은 이름과 연락처를 가져옵니다. 도구 결과도 가리세요: `pii_middleware(redactor, apply_to_tool_results=True)`, 또는
vault와 함께 쓰면(아래) 모든 호출에서 사용자 메시지, 앞선 답변, 도구 결과를 가립니다. 검색(retrieval) 체인에서는
`mask_documents(docs, redactor)`가 LangChain `Document`의 가린 사본을 돌려줍니다(metadata는 그대로). 예시:
[`examples/agent_tool_results.py`](examples/agent_tool_results.py)(CRM 도구와 티켓 검색이 있는 에이전트),
[`examples/mask_pdf.py`](examples/mask_pdf.py)(모델은 글을 읽으므로 pypdf로 PDF의 글을 먼저 뽑음).

**값 되돌리기**(1.7.2부터): `Vault`는 번호가 붙은 자리표시와 원래 값의 대응표를 이 프로세스 안에만 둡니다. 그래서 모델의
답변과 도구 호출 인자에서 가린 값을 되돌릴 수 있습니다. 대응표는 어디로도 보내지 않습니다.

```python
from newmean_pii import Vault

vault = Vault()  # 사용자나 대화마다 하나, 여러 사용자가 함께 쓰지 않음
redactor.redact("Call Kim Minsu on 010-1234-5678", vault=vault).text  # 'Call [PERSON_1] on [PHONE_1]'
vault.restore("I called [PERSON_1].")                                  # 'I called Kim Minsu.'

agent = create_agent("openai:gpt-6.1", tools=[...], middleware=[pii_middleware(redactor, vault=vault, restore_tools=["lookup_customer"])])
```

**자리표시는 쓰인 그대로일 때만 되돌립니다.** `[PERSON_1]`은 되돌리고, `[Person_1]`, `PERSON_1`, 번역된 꼴은 모델이 쓴 그대로
답변에 남습니다(다른 값이 들어가는 일은 없습니다). 시스템 프롬프트에 한 줄을 넣어 모델에게 그대로 옮겨 쓰게 하세요:

```text
Text in square brackets with a label and a number, such as [PERSON_n], stands for a value hidden from you. Copy it exactly as written, brackets included, wherever you refer to it.
```


한 vault 안에서 같은 값에는 늘 같은 자리표시가 붙고, vault에 있는 값은 같은 글이든 뒤의 글이든 다시 나오면 모델이 그 자리에서
놓치더라도 다시 가립니다(출처 `"vault"`. 찾는 법은 아래). `restorer()`는 스트림을, `restore_value`·`hide_value`는 JSON 값의
문자열을 다루고(키와 숫자는 그대로), `entries()`와 `Vault(entries)`로 요청 사이에 vault를 이어 씁니다. 미들웨어와 함께 쓸 때는
`to_json()`과 `Vault(state)`를 쓰세요. 되돌린 답변을 모델이 어떻게 썼는지도 함께 담습니다(둘 다 원래 값이 들어 있음). 글을
가리는 도중(`redact` 중이거나 미들웨어가 모델 호출을 준비하는 중)에 vault를 비우면, 그 호출은 비워진 vault에 값을 쓰는 대신
`RuntimeError`를 냅니다. 비운 뒤에 도착한 답변은 그 호출 때의 값으로 되돌려집니다. 여러 스레드가 vault 하나를 함께 써도 됩니다.
가리기, `bindings`, `restore`, `restore_value`, `hide_value`는 각각 잠긴 한 단계이고, restorer는 만들어질 때의 값을 끝까지 씁니다.
vault를 여러 사용자가 함께 쓰면 안 됩니다. 한 사용자의 답변에 든 자리표시가 다른 사용자의 값을 되살릴 수 있습니다.

`vault`를 주면 `pii_middleware`는 LangChain의 `PIIMiddleware` 대신 모델 호출을 감싸는 미들웨어를 돌려줍니다. 이때는
`strategy="redact"`, `apply_to_input=True`, `apply_to_output=False`만 받고(다른 값은 `ValueError`), `apply_to_tool_results`가
무엇이든 도구 결과를 가리며, `labels`는 모델이 찾은 구간 가운데 무엇을 남길지만 정합니다(모든 레이블의 아는 값은 그래도 숨깁니다).

- 모델은 사용자 메시지, 앞선 답변과 그 도구 호출 인자, 도구 결과를 가린 사본으로 받습니다. 답변 글은 그 호출의 자리표시만, 가릴
  때의 값으로 되돌려진 채 돌아옵니다. 앱이 값을 되돌려 받은 앞선 답변(`restore_reasoning=True`면 추론 글, `restore_tools`면 도구
  인자도)을 다시 넘기면 미들웨어는 모델이 쓴 그대로 보내며, 그 안에서 되돌렸던 자리표시도 그 호출이 넣은 것으로 칩니다.
  `to_json()`으로 되살린 뒤에도 같습니다. 앱이 되돌리지 않은 채 받은 자리표시는 세지 않습니다.
- 메시지에 원래 있던 자리표시 같은 글은 "［PERSON_1]"(전각 괄호)로 바꿔 보내고 되돌리지 않습니다. 개발자가 직접 쓴 글
  (`SystemMessage`, `system_prompt`, 그리고 아래 메시지 밖의 필드인 도구 설명, 스키마, 응답 형식, `model_settings`)에서는 쓰인
  그대로 둡니다. 프롬프트가 자리표시를 설명할 수 있게 하려는 것이고, 그러면 모델은 그것과 값의 자리표시를 가려 보지 못하니
  예시는 자리표시가 될 수 없는 꼴(`[PERSON_n]`)로 쓰세요. 퍼센트·HTML 꼴(`%5BPERSON_1%5D`, `&#91;PERSON_1]`)은 쓰인 그대로 보내고, 모델이
  퍼센트 인코딩한 자리표시는 되돌리지 않습니다.
- 도구 호출 인자는 `restore_tools`에 지정한 도구만 되돌리고(이름 하나, 이름 목록, 이름을 받는 함수) 다음 호출에는 모델이 쓴
  인자 그대로 보냅니다. 다른 도구는 자리표시를 받습니다. 모델이 읽는 글에 숨은 지시가 있으면 모델이 도구 호출에 자리표시를 넣을
  수 있기 때문입니다. 되돌리지 않는 것: content 블록 안의 도구 입력, `invalid_tool_calls`, `additional_kwargs`(도구는
  `tool_calls`로 돕니다).
- 앞선 답변과 도구 호출은 모델이 쓴 그대로 다시 보내고(미들웨어가 하나하나 기록해 두며 `to_json()`에 함께 담깁니다) 모델이 한
  번 더 읽으므로, 모델이 직접 써 넣은 값도 가려집니다. 답변의 기록이 없으면(`entries()`만으로 되살린 vault) 아는 값을 그 글의
  어디에서든, 더 긴 단어 안에서도 가립니다. 필요한 것보다 더 가릴 수는 있어도 덜 가리지는 않습니다.
- 사용자 메시지와 도구 결과는 늘 받은 그대로 읽습니다. content 블록 안의 도구 입력과 결과(Anthropic `tool_use`·`tool_result`,
  OpenAI `function_call`)도 가립니다. JSON 글은 값마다, JSON 데이터 행은 모든 문자열 값을 가립니다. LangChain과 공급자의 블록
  종류만 블록으로 봅니다. 블록에서는 글, 도구 입력, 도구 결과를 모델이 읽고, 나머지 필드에서는 vault가 이미 아는 값만 숨깁니다
  (아래의 보내기 직전 확인). 도구가 돌려주거나 사용자가 보낸 것에서는 그 종류의 내용이 있는 객체만 블록입니다(`text` 블록은
  `text`, `file`·`image`는 `url`이나 base64 데이터나 파일 id, `document`는 `source`, `json` 블록은 `json`). 도구가 돌려준
  것에서는 블록의 `id`와 `name`도 살핍니다(거기서는 어떤 블록도 id로 맞추지 않습니다). 사용자 메시지에서 블록의 `id`는
  LangChain이 블록에 붙인 식별자이므로 쓰인 그대로 둡니다. 도구가 자기 `"type"`을 붙여 돌려준 행(`{"type": "contact", "name":
  ...}`)과, 블록의 종류를 달았지만
  그 내용이 없는 행(저장소 API의 `{"type": "file", "id", "name", "owned_by"}`)은 데이터입니다. 모델이 읽고, 모든 필드와 키를
  셉니다.
- JSON 문서인 메시지나 도구 결과(`\u00e9` 같은 이스케이프가 든 `json.dumps` 출력)는 값마다 읽되 문서 전체를 모델의 맥락으로
  주며, 올바른 JSON으로 남습니다. 모델이 앞서 쓴 JSON 답변과 도구 입력도 그렇고, 키가 같은 자리표시가 되어도 필드를 모두
  남깁니다. 찾은 숫자(JSON 숫자로 쓴 카드 번호나 전화번호)는 자리표시 문자열로 바뀝니다. 숫자는 어떻게 쓰였든 같은 숫자로
  보고(`8.21023456789e11`과 `821023456789`), 되돌릴 때는 풀어 쓴 십진 꼴로 돌아옵니다(`1.25e1`은 `12.5`, 지수는 64까지). 값은 키
  옆에 놓고 읽으므로(`name: Kim Minsu`) 모델이 그 값이 무엇인지 압니다. 키 자체는 그대로 두되, vault가 이미 아는 값은 키에서도
  숨깁니다(이메일 주소를 키로 쓴 맵, 전화번호를 `int` 키로 쓴 맵). 두 키가 같은 자리표시가 되면 필드를 둘 다
  남기고(`[PERSON_1]`, `[PERSON_1] #2`), 가리기 전에 같던 키는 같게 남습니다.
- **모든 데이터 자리는 한 번 만든 순수 JSON으로 보냅니다(1.7.2부터).** 각 메시지의 content, 도구 호출 인자,
  `additional_kwargs`, `model_settings`, 요청에 들어가는 모델 자신의 필드(아래)를 먼저 공급자 클라이언트가 쓰는 JSON으로
  만들고, 그 JSON을 가려서 보냅니다. 어댑터는
  Python 객체 대신 그 JSON을 받습니다. pydantic 모델은 OpenAI·Anthropic 클라이언트가 만드는 것처럼 한 번 만든
  덤프입니다(`model_dump(mode="json", by_alias=True, exclude_unset=True)`: 직렬화기, 계산 필드, 별칭, 추가 키를 포함하고, 기본값
  그대로인 필드는 쓰지 않습니다). 그래서 두 번째 덤프가 달라지는 모델도 가리지 않은 것을 보낼 수 없습니다. dataclass는 그
  필드, 튜플·set·frozenset은 list, date·datetime·time은 ISO 꼴, `Enum`은 그 값, `Decimal`은 숫자(float로 담을 수 없으면 숫자를 쓴
  문자열), `UUID`와 경로는 그 글, bytes는 UTF-8 글(bytes는 파일 블록이나 블록의 source에 든 데이터일 때만 bytes로 둡니다),
  `NaN`과 무한대는 `null`, 그 밖의 객체는 pydantic의 JSON 모드가 주는 꼴(`timedelta`)입니다. 키는 `json.dumps`가 쓰는 대로
  씁니다(`4155550123`은 `"4155550123"`, `True`는 `"true"`, `None`은 `"null"`, `str`·`int` Enum은 그 값). JSON으로 같게 쓰이는 두
  키는 필드를 둘 다 남깁니다(`"4155550123"`과 `"4155550123 #2"`). 그 JSON의 문자열과 키는 다른 JSON 데이터처럼 가립니다. JSON이
  쓸 수 없는 것은 아무것도 보내기 전에 호출을 실패시킵니다(아래): 자기 자신을 가리키는 객체, 예외를 내는 직렬화기(읽을 때
  예외를 내는 `model_dump`도, `AttributeError` 포함. pydantic 모델은 그 클래스로 알아보므로 이런 모델을 덤프가 없는 모델로
  여기지 않습니다), 읽을 수 없는 dataclass 필드(값이 없거나 예외를 내는 속성. 빼고 보내는 필드는 없습니다), 다른 타입의
  키(튜플, `frozenset`, `bytes`, `Decimal`, 일반 `Enum` 키), 파일 블록 밖의 UTF-8이 아닌 bytes, JSON 꼴이 없는 객체입니다.
  `json.dumps`가 도구의 반환값을 쓰지 못하면(set, bytes, date) LangChain은 미들웨어가 보기 전에 `str()`로 쓰며, 그 repr
  이스케이프도 읽습니다(아래).
- 그 밖의 글(JSON이 섞인 로그 한 줄, 코드 블록, 여러 줄의 JSON, 중간에 잘린 문서)은 통째로 읽습니다. 모델에게는 유니코드
  이스케이프를 푼 꼴로 줍니다(`Jos\u00e9`는 `José`). vault가 이미 아는 값은 쓰인 그대로의 글(`C:\Users\u1234`의
  `u1234`)과 그 글의 다른 읽기에서도 찾습니다(아래).
- 한 호출의 어느 메시지에서 찾은 값은 그 앞의 메시지와 시스템 프롬프트를 포함해 모든 메시지에서 가립니다.
- 호출을 보내기 직전에 메시지의 모든 글 필드를 모델 없이 한 번 더 살펴, 어떤 종류의 블록에 있든 vault가 아는 값을 숨깁니다.
  LangChain과 공급자 자신의 블록에서만 건드리지 않는 것: 식별자와 이름, 파일·이미지 참조(`url`, `image_url`, `file_id`. 그런
  URL 안의 아는 값은 쓰인 그대로 나갑니다), base64 데이터, 서명된 추론 글. 이 패키지가 모르는 블록 종류에서는 나머지 필드도
  모두 살핍니다. 블록 안의 데이터에서는 모든 문자열과 키를 보며, `data` 아래 객체나 목록도 그렇습니다. 통째로 아는 값인 `data`
  문자열은 숨깁니다. data URL이나 base64처럼 보이는 64자 이상의 문자열은 그대로 두되, 쓰인 그대로든 그 읽기 중 하나에서든 6자
  이상의 아는 값이 단어 경계에 있으면 가립니다. `additional_kwargs`는 데이터이며, 그 안의 `tool_calls`와 `function_call`만
  예외입니다. 글로 바로 넣은 문서는 밖에서 온 다른 글처럼 읽고, 다른 종류의 메시지(`ChatMessage`, `FunctionMessage`)도 밖에서 온
  글처럼 가립니다.
- `HumanMessage`나 `ChatMessage`의 `name`에 든 아는 값은 괄호 없는 자리표시(`PERSON_1`, 공급자가 그 자리에 받는 꼴)로
  보내며 되돌리지 않습니다. 공급자는 그 자리에 글자, 숫자, `_`, `-`만 받으므로, 두 단어 이상인 아는 값은 이어 쓴 꼴로도
  찾습니다(`Kim Minsu`에 대해 `Kim_Minsu`, `kim-minsu`, `KimMinsu`). 한 단어인 값은 쓰인 그대로 찾습니다.
- 추론 글은 `restore_reasoning=True`일 때만 되돌립니다. 모델이 쓴 추론 글은 쓴 그대로 다시 보냅니다(공급자가 서명하기
  때문입니다). vault가 나중에 배운 값이 들어 있어도 그렇습니다. 그 값은 모델이 썼으므로 공급자가 이미 가진 값입니다.
  JSON 답변(구조화 출력)은 문자열 값 안에서 되돌리고, 에이전트의 `structured_response`도 되돌립니다(pydantic 모델,
  dataclass, dict는 타입을 그대로 둡니다. pydantic 모델은 검증에 쓰는 키, 곧 별칭으로 다시 만듭니다).
- **가리기를 끝낼 수 없는 호출은 오류를 내고, 그 호출의 어떤 것도 보내지 않습니다.** 탐지기가 오류를 낼 때, 가리는 도중에
  vault를 비울 때, JSON으로 쓸 수 없는 입력(자기 자신을 가리키는 객체, 예외를 내는 직렬화기, JSON이 쓸 수 없는 키, JSON 꼴이
  없는 객체)이나 아주 깊이 중첩된 데이터가 있을 때, 도구를 정의로 쓸 수 없을 때입니다.
- 스스로 모델을 부르는 다른 미들웨어(`SummarizationMiddleware` 등)는 에이전트의 가리지 않은 상태를 읽습니다. 이 미들웨어는
  자신을 거치는 호출만 가립니다.
- **맨 뒤에 두세요.** 이 미들웨어는 받은 요청을 가리므로, 그 뒤에 둔 미들웨어가 넣는 것(`@dynamic_prompt`, `@wrap_model_call`로
  더한 검색 맥락)은 가리지 않은 채 모델에 갑니다. `middleware=[...]`의 끝에 두세요.

`mask_documents(docs, redactor, vault=vault)`는 각 `page_content`를 미들웨어가 도구 결과를 읽듯 읽습니다(metadata는 그대로).
vault와 함께면 `mode="tag"`만 받고, 다른 mode는 `ValueError`를 냅니다.

**메시지 밖의 필드.** vault를 주면 다음에서도 아는 값을 모델 없이 숨깁니다. 도구(설명, 인자 스키마의 설명·제목·예시.
숨긴 도구는 이름을 그대로 두고, 에이전트는 자기 도구를 그대로 돌립니다), 응답 형식(`ProviderStrategy`의 JSON 스키마,
`ToolStrategy`의 구조화 출력 도구, `AutoStrategy`, `"title"`이 없는 스키마도. 에이전트가 모델에 묶는 자리, 곧 공급자가
받는 바인딩에서 숨깁니다), `model_settings`. 모델 설정은 데이터입니다. 그 안의 모든 문자열과 키(`user`, 저장된
프롬프트의 `variables`, `metadata`)와 아는 값인 숫자를 숨깁니다. 예외는 정확한 경로이며, 각각 한 공급자가 식별자, 자격
증명, 숫자 설정, JSON 스키마를 정한 자리입니다(1.7.2부터, 출시 관문 8 항목 5의 Python 짝: `previous_response_id` 같은
이름을 공급자가 정한 필드 안이면 어느 깊이에서든 그대로 두었습니다). 그 경로의 값은 쓰인 그대로 두고(그 경로 위의 키도),
같은 이름도 다른 자리(한 단계 아래, 다른 필드 아래, `{"acme": {...}}`처럼 어느 공급자도 정하지 않은 설정 아래,
`extra_body` 안, `order`라는 템플릿 변수)에서는 데이터이며 그 모든 키와 값을 셉니다. "`*`"는 객체의 아무 키, "`[]`"는
목록의 아무 항목이며 값이 목록일 때만 해당합니다. `"[]"`라고 쓴 키를 가진 객체는 데이터입니다
(`{"mcp_servers": {"[]": {"name": ...}}}`). 아래 전부를 그대로 두는 경로는 없습니다.

- 식별자와 자격 증명: `model`, `models[]`, `provider.order[]`, `provider.only[]`, `provider.ignore[]`(게이트웨이의
  라우팅. OpenAI 클라이언트가 요청 본문에 합치는 `extra_body` 아래도), `previous_response_id`, `conversation`(또는
  `conversation.id`), `prompt.id`, `prompt.version`, `container`(또는 `container.id`와 `container.skills[].type`,
  `.skill_id`, `.version`), `mcp_servers[].type`, `.name`, `.url`, `.authorization_token`,
  `.tool_configuration.allowed_tools[]`, `cachedContent`, `cached_content`, `tools[].type`, `tools[].name`,
  `tools[].function.name`, `functions[].name`, `response_format.type`, `response_format.json_schema.name`,
  `text.format.type`, `text.format.name`, `tool_choice`(이름이나 종류, 또는 그 `type`, `mode`, `name`,
  `function.name`, `server_label`과 `tools[].type`, `.name`, `.function.name`, `.server_label`),
  `function_call`(또는 `function_call.name`), 내장 도구의 `tools[].server_label`, `.server_url`, `.connector_id`,
  `.authorization`, `.headers.*`, `.allowed_tools[]`, `.allowed_tools.tool_names[]`, `.require_approval`(또는 그
  `always.tool_names[]`와 `never.tool_names[]`), `.vector_store_ids[]`, `.container`, `.container.file_ids[]`,
  `.filters.allowed_domains[]`, `.filters.blocked_domains[]`, `.allowed_domains[]`, `.blocked_domains[]`,
  `logit_bias.*`, `logitBias.*`, `headers.*`, `extra_headers.*`. 시스템 프롬프트, instructions, input에서는 종류인
  `system[].type`, `system[].cache_control.type`, `input[].type`, `input[].content[].type`, `input[].summary[].type`과
  암호화해 돌려보내는 추론인 `input[].encrypted_content`(`instructions` 아래도 같음).
- 숫자 그대로 두는 숫자: `maxTokens`, `max_tokens`, `maxOutputTokens`, `max_output_tokens`, `maxCompletionTokens`,
  `max_completion_tokens`, `max_tokens_to_sample`, `temperature`, `topP`, `top_p`, `topK`, `top_k`, `minP`, `min_p`,
  `seed`, `frequencyPenalty`, `frequency_penalty`, `presencePenalty`, `presence_penalty`, `repetitionPenalty`,
  `repetition_penalty`, `n`, `candidateCount`, `candidate_count`, `logprobs`, `topLogprobs`, `top_logprobs`,
  `budgetTokens`, `budget_tokens`, `thinkingBudget`, `thinking_budget`, `maxToolCalls`, `max_tool_calls`, `maxRetries`,
  `max_retries`, `timeout`, `dimensions`, `numCtx`, `num_ctx`, `numPredict`, `num_predict`가 설정의 맨 위와
  `generationConfig`, `generation_config`, `inferenceConfig`, `inference_config`, `additionalModelRequestFields`,
  `additional_model_request_fields`, `options` 바로 아래에 있을 때. 그리고 `thinking.budget_tokens`,
  `thinking.budgetTokens`, `thinkingConfig.thinkingBudget`, `thinking_config.thinking_budget`,
  `generationConfig.thinkingConfig.thinkingBudget`, `generation_config.thinking_config.thinking_budget`,
  `additionalModelRequestFields`와 `additional_model_request_fields` 아래의 `thinking.budget_tokens`. 거기의 문자열은
  읽습니다.
- JSON 스키마(도구의 스키마처럼 읽음. 설명·제목·예시는 숨기고 속성 이름과 `enum`·`const`·`default`·`pattern`은 그대로):
  `response_format.json_schema.schema`, `text.format.schema`, `tools[].parameters`, `tools[].input_schema`,
  `tools[].function.parameters`, `functions[].parameters`, `responseSchema`, `response_schema`, `responseJsonSchema`,
  `response_json_schema`, `output_format.schema`, `outputFormat.schema`, `output_config.format.schema`,
  `outputConfig.format.schema`, `generationConfig.responseSchema`, `generationConfig.responseJsonSchema`,
  `generation_config.response_schema`, `generation_config.response_json_schema`.

설정도 메시지처럼 먼저 JSON으로 만듭니다. 공급자가 정한 필드에서 JSON 꼴이 없는 객체(스키마의 클래스, `httpx.Timeout`)는
그대로 두고, 어느 공급자도 정하지 않은 설정, `extra_body`, 시스템 프롬프트·instructions·input, 그리고 어디에 있든 앱의
맵(`metadata`, `labels`, `tags`, `variables`)에서는 호출을 실패시킵니다. 도구 이름과 스키마 속성 이름은 쓰인 그대로
둡니다. 이 필드에서 새 값을 찾지는 않으니 개인정보를 넣지 마세요. 호출의 메시지에 자리표시가 있으면 응답 형식의 스키마는 `format`과 `pattern` 없이 모델에
갑니다(`datetime.date` 필드나 pattern은 `[DATE_OF_BIRTH_1]`을 담을 수 없음). 에이전트는 이번 호출의 값을 되돌린 답변을
읽고 검사합니다. 이를 위해 복사할 수 없는 모델(pydantic 채팅 모델이 아닌 것)은 응답 형식에 아는 값이 있으면 호출을
실패시킵니다. 요청에 들어가는 모델 자신의 필드도 키가 무엇이든 데이터입니다(1.7.2부터). `model_kwargs`(`user`,
`safety_identifier`, `prompt_cache_key`, 저장된 프롬프트의 `variables`, 그리고 여기에 준 `system`, `messages`,
`instructions`, `tools`. `langchain-anthropic`은 메시지로 만든 것 대신 이것을 보냅니다), `extra_body`(OpenAI의
`metadata`는 여기에 넣습니다. `model_kwargs`에 준 `metadata`는 LangChain이 실행의 metadata로 두고 보내지 않습니다. OpenAI와
Anthropic 클라이언트는 `extra_body`를 본문에 합치며, 그 안의 `messages`나 `system`도 그렇습니다), Anthropic의
`user_profile_id` 같은 모델의 고유 필드를 공급자 패키지가 요청을 만드는 자리(`_get_request_payload`, 없으면
`_default_params`, 그것도 없으면 `model_kwargs`와 `extra_body` 자체)에서 읽고, 모델 설정과 같은 규칙과 식별자 예외를
같은 경로에서 적용합니다. 여기의 시스템 프롬프트, instructions, input은 텍스트입니다. 문자열과 키를 세고, 위 경로의
종류와 암호화된 추론만 그대로 두며, JSON 꼴이 없는 객체가 있으면 호출을 실패시킵니다. 각 필드는 모델 설정처럼 한 번 JSON으로
만들고 그 한 번 만든 것을 가립니다. 필드에 아는 값이 있거나, 자기 코드로 JSON을 쓰는 객체(두 번째 dump에서 다른 JSON을 줄 수
있는 serializer가 있는 pydantic 모델, dataclass)가 있으면 에이전트는 그 호출에 쓸 모델 복사본(`model_copy`, 모델의
클라이언트를 함께 씀)을 받습니다. 복사본이 가린 JSON을 들고 있으므로 클라이언트는 가린 것을 씁니다. 원래 모델은 바꾸지
않습니다. 다른 필드는 모델이 가진 그대로 둡니다(클라이언트는 날짜나 tuple을 매번 같게 쓰거나, 미들웨어가 없을 때처럼
거부합니다). 복사본의 요청은 호출 전에 확인합니다. 숨겨 보낼 수 없는 필드가 있으면 아무것도 보내기 전에 그 필드 이름을
밝히는 오류로 호출을 실패시키며(JSON 스키마에 아는 값이 있는 pydantic 클래스로 준 응답 형식, 복사본이 출처를 찾을 수 없는
필드에 그런 객체가 있을 때도), pydantic 채팅 모델이 아닌 모델과 `init_chat_model`이 호출마다 만드는 모델(설정 가능한 필드)이
보내는 것(그 설정)에 아는 값이나 그런 객체가 있을 때도 그렇습니다.

**아는 값을 다시 찾는 법.** 여섯 패키지 모두에서, vault를 준 `redact`, `hide`, `hide_value`, 미들웨어가 같게 찾습니다(공유
사례: `python/tests/vault_cases.json`).

- 3자 이상입니다. 띄어 쓰지 않는 문자 체계(확장 J까지의 한자, U+1B000 이후도 포함한 가나, 태국 문자, 라오, 크메르, 미얀마,
  타이 탐, 발리, 자와) 글자가 하나라도 있거나 두 글자가 모두 한글 음절이면(`이준`) 2자도 찾습니다. 그보다 짧은 값은 애플리케이션이
  넣은 것이면 JSON 값, 키, 도구 인자 전체일 때, `hide_value`가
  문자열 전체로 만날 때, 기록이 없는 모델 글에서 가립니다. 모델이 찾은 것이면 찾은 자리와 기록이 없는 모델
  글에서 가리고 답에서 되돌립니다.
- 단어 경계에서 찾습니다. 더 긴 단어 안에서는 찾지 않습니다(글자, 숫자, 결합 기호, `_`가 단어를 이룹니다). 띄어 쓰지 않는 문자
  체계의 글자가 어느 쪽에 있든 경계입니다(`注文ZX-88213-Kは`). 다만 값 뒤의 모음 기호 같은 결합 기호는 앞 글자에 속하므로
  경계가 아닙니다(`สมัคร`에서 `สม`을 찾지 않음). 한글 값 뒤에는 조사가 붙어도 되고(`김민수님께`), 바로 앞의 한글은
  경계가 아니며(`이김민수`), 한글과 라틴 글자·숫자 사이는 경계입니다. 한글 두 음절인 값 뒤에는 같은 단어 안에 조사, 높임의
  「님」·「씨」, 서술격 조사만 올 수 있습니다(`지원님께`는 이름, `지원금`은 다른 단어). 이름에 붙은 글자는 다른 단어입니다.
  아랍어·히브리어 접두어(`ومحمد`), 격어미나 접미사(터키어, 핀란드어, 독일어 `-s`).
- 대소문자: 이메일 주소, 사용자 이름, 두 단어 이상인 값은 ASCII 대소문자를 가리지 않고, URL의 scheme과 호스트도 그렇습니다(경로는
  쓰인 그대로). 모든 값은 대문자로 쓴 것도 찾습니다. 대문자는 유니코드 전체 대문자 변환이고(`José`에 대한 `JOSÉ`, `Weiß`에 대한
  `WEISS`), 터키어·아제르바이잔어(`Emine`에 대한 `EMİNE`), 그리스어(강세와 숨표 없이 `Νίκος`에 대한 `ΝΙΚΟΣ`),
  독일어(`Weiß`에 대한 `WEIẞ`)가 쓰는 대문자도 찾습니다. 한 단어인 값을 다른 대소문자로 쓰면 다른 단어입니다(`Will`과 `will`).
- 찾은 값은 바로 뒤의 결합 부호, 이체자 선택자, 결합자까지 포함하므로 그 구간은 문자소 클러스터 중간에서 끝나지 않습니다.
- NFC와 NFD는 같습니다(어느 쪽으로 쓴 `José`든, 자모를 풀어 쓴 한글이든. 네이티브 네 패키지는 한글만 모읍니다). 전각 ASCII
  (`ｋｉｍ＠example.com`)와 전각 공백 U+3000은 ASCII로 읽습니다. 수학용 글자, 모양이 비슷한 글자, 다른 문자 체계의 숫자는 다른
  값입니다.
- 숫자 사이의 점은 숫자를 잇습니다. 숫자로 시작하거나 끝나는 값은 점이 그 값을 다른 숫자와 이어 주는 곳에서 찾지 않습니다
  (`4524.7`이나 `1.4524`의 `4524`, `Unit 4.12 Main St`의 `12 Main St`도). 지수는 숫자만 잇습니다. `1e+4524`에서 `4524`는 찾지
  않지만, `4E+` 뒤의 주소, 전화번호, 우편번호는 찾습니다(`Unit+4E+12+Main+St`). 미들웨어에서는 그 부호나 점을 퍼센트·유니코드
  이스케이프로 쓴 경우(`1e%2B4524`, `4524%2E7`)도 같습니다.
- 다른 표기로 찾은 값은 아는 값의 자리표시를 씁니다. 값 하나에 자리표시 하나입니다. 아는 값 둘이 글의 같은 자리에 잡히면
  vault가 더 오래 가진 값을 씁니다.
- 모델이 표시한 값은 가장자리에 붙은, 값이 아닌 공백, 구두점, 괄호, 따옴표를 빼고 배웁니다(`Kim Minsu.`, `"Kim Minsu"`,
  `(Kim Minsu`는 `Kim Minsu`로 배우고, 뺀 글자는 글에 남습니다). 남는 값이 너무 짧아 다시 찾을 수 없으면 공백만 뺍니다.
  짝이 값 안에 있는 괄호는 남기고(`(02) 1234-5678`), 값 앞의 `.`과 ORG 끝의 `.`(`Tiffany & Co.`)도 남기며, SECRET은
  공백만 뺍니다.

미들웨어와 `mask_documents`는 다음도 찾습니다.

- 다르게 쓴 공백. 공백은 어떤 공백 문자와도 맞습니다(탭, NBSP, U+3000, 여러 칸). 줄바꿈은 쉼표와 공백, 또는 들여 쓴 줄과도
  맞습니다. 쉼표와 공백은 줄바꿈, 또는 공백 하나와도 맞습니다(`12 Main St Seoul`). 다른 구두점(공백 없는 `,`, `;`)은 다른
  값이고, 값의 앞뒤 공백, 값 안의 폭 없는 공백·결합자·소프트 하이픈, 공백이 없던 값에 넣은 공백(`김 민수`)도 그렇습니다.
  `\v`와 `\f`는 공백이지 줄바꿈이 아닙니다.
- 다른 표기의 숫자. 자릿수마다 견줍니다(지수는 64까지). `821023456789`에 대한 `8.21023456789e11`, 끝의 0, `E`. 앞에 0이 붙으면
  (`04524`) 숫자가 아닌 글자열이라 쓰인 그대로만 찾습니다. 자리 묶음(`4,524`, `4 524`, `4_524`)과 구두점이 다른 전화번호
  (`010-1234-5678`에 대한 `01012345678`)는 다른 값입니다. 부호는 자리표시 밖에 둡니다. 아는 `4524`에 대해 `-4524`와
  `-4.524e3`은 `-[ID_NUMBER_1]`이 되고, JSON 숫자 `-4524`는 문자열 `"-[ID_NUMBER_1]"`이 됩니다.
- ISO 시각의 앞부분(`isoformat()`이 쓰는 `1990-01-02T00:00:00`)과 날짜 repr(도구가 `date`를 돌려줄 때 LangChain이 쓰는
  `datetime.date(1990, 1, 2)`)에 든 아는 ISO 날짜(`1990-01-02`). 다른 순서와 구분자(`01/02/1990`, `2 Jan 1990`)는 다른
  값입니다.
- 글의 다른 읽기. JSON 이스케이프(줄바꿈을 `\n`으로 쓴 것, `CORP\kim`을 쓴 `CORP\\kim`, `01\/02\/1990`, JSON 문자열 안의
  JSON까지), UTF-8로 읽는 퍼센트 표기(`kim%40example.com`), 공백으로 읽은 `+`와 그대로인 `+`(`name=Kim+Minsu`), Python repr
  이스케이프(`Kim\xa0Minsu`, UTF-8로 읽는 `b'Jos\xc3\xa9'`, `\U00020bb7`). 이들은 네 단계 깊이까지 서로 겹칠 수 있습니다(폼 필드
  안의 JSON, JSON 안의 폼 필드). 글의 다른 곳에 이스케이프가 얼마나 있든 네 단계 안의 읽기를 모두 찾고, vault가 더 배우는 것이
  없을 때까지 다시 찾습니다.
- 쓰인 그대로 읽는 것. 이런 꼴의 값은 모델이 찾은 곳에서만 가려집니다. HTML 엔티티와 태그(`&#233;`, `<br>`), base64,
  quoted-printable과 MIME 인코딩 단어, 겹쳐 쓴 따옴표(SQL, CSV), Markdown과 셸의 역슬래시, `%uXXXX`, punycode 호스트 이름
  (`xn--`), 다른 문자 집합(EUC-KR)과 UTF-8이 아닌 바이트, 다섯 번째 단계의 인코딩.

**한계.**

- 미들웨어 하나에 vault 하나: 한 번 만들어 여러 대화(여러 `thread_id`)에 쓰는 컴파일된 에이전트는 vault를 함께 씁니다. 대화마다
  만드세요. vault는 LangGraph 체크포인터에 들지 않습니다. `to_json()`을 대화 옆에 직접 저장하세요.
- vault에는 크기 제한이 없습니다. 미들웨어는 가장 최근에 쓴 답변과 도구 호출 4,096개의 기록(그보다 오래된 것은 느슨하게 찾아
  더 가릴 뿐 덜 가리지 않습니다)과 최근 글 256개에 대한 모델의 구간을 기억합니다. 모델 호출 중 흘려보내는 토큰에는 자리표시가
  보이고, 에이전트가 보관하는 메시지에는 값이 들어 있습니다.
- 구간은 vault 세대와 탐지기 설정마다 따로 두고(결과는 그것을 낸 설정 아래에만 둡니다), vault는 호출마다 다시 적용합니다. 그래서
  나중에 배운 값이 전에 가린 글도 덮습니다. 한 글에서 배운 값은 첫 호출부터 그 글의 어디에서든 가리고, 모델이 아는 값이나 더 큰
  합친 구간 안에서 찾은 것도 배웁니다. 모델이 찾은 값은 구간이 값 둘레에서 함께 잡은 것을 빼고
  배웁니다. 공백, 문장의 마침표와 쉼표, 괄호와 따옴표(`I'm Kim Minsu.`에서 `Kim Minsu`), Python bytes 리터럴의 `b'`, 생년월일 ISO
  타임스탬프의 시각입니다.
- vault와 함께라면 redactor에 `detect()`가 있어야 합니다. 없으면 분명한 메시지로 실패합니다. 자기 `detect`를 가진 `Redactor`
  하위 클래스는 그대로 씁니다.
- 허용·차단 목록은 vault와 함께도 동작합니다. 모델과 목록은 쓰인 그대로의 글을 읽고, 되돌린 답변을 다시 보낼 때도 둘이 다시
  읽습니다.
- 미들웨어가 돌려주는 것(도구 인자, 내보낸 상태)은 사본입니다. 그것을 바꿔도 vault는 바뀌지 않습니다.
- vault를 준 `apply_redaction`과 `redact_table`은 받은 구간에 번호만 붙이고, 아는 값을 찾지는 않습니다.
- 공급자에 보내지 않으므로 가리지 않는 것: `response_metadata`, `usage_metadata`, `ToolMessage`의 `artifact`.
- 자리표시는 `[<계열>_<번호>]`입니다. 계열은 32자까지(허용되지 않는 글자마다 `_`), 번호는 ASCII 숫자 1~12자리이고, 더 긴 비슷한
  꼴은 평범한 글입니다. PERSON_L2는 PERSON의 번호를 함께 씁니다. 모델이 지어낸 번호는 쓰는 번호와 백만 이내일 때 건너뛰며
  `to_json()`에 남습니다. 번호(999,999,999,999까지)를 다 쓴 계열은 `[PERSON__1]`로 이어지므로, vault가 내보낸 상태는 언제나 다시
  읽힙니다. 빈 값에는 자리표시를 붙이지 않습니다.
- vault의 단어 글자는 Python의 유니코드 데이터를 씁니다. Python 3.14는 16.0, 패키지가 받는 가장 오래된 Python 3.9는 13.0입니다.
  오래된 Python은 값 옆의 새 글자를 다르게 볼 수 있습니다.
- 이번 배포에서 돌린 것: langchain 1.5.2(langchain-core 1.8.4, langchain-openai 1.8.5, langchain-anthropic 1.7.12)와
  1.4.0(langchain-core 1.6.0, langchain-openai 1.6.0, langchain-anthropic 1.7.0)의 `create_agent`, `invoke`와 `ainvoke`.
  패키지 시험, 구조화 출력 확인, 공유 코퍼스, OpenAI·Anthropic 어댑터의 요청 본문을 둘 다에서 돌렸습니다. extra는
  langchain 1.4.0 이상을 받습니다(1.6은 1.0 이상). 그보다 이른 릴리스는 지원하지 않습니다. 패키지 시험이 1.0.0에서 돌지
  않고, 1.0과 1.4 사이의 릴리스에서는 이번 배포를 확인하지 않았습니다.
- **시간.** vault의 일은 글 길이 × 아는 값 수에 비례해 늡니다. 아는 값이 수천 개인 큰 데이터는 더 느립니다. 아는 값
  2,000개로 JSON 행 2,000개(181 KB)에 호출마다 1.8초, 행 4,000개(362 KB)에 3.5~3.8초, 산문 1 MB에 6.9~7.0초, 겹친
  인코딩(퍼센트·폼 인코딩, 유니코드 이스케이프, JSON 안의 JSON) 512 KB에 13.8~14.2초가 걸렸습니다(vault 일만, 모델 없음.
  Apple M5 Pro와 Python 3.14에서 두 차례, 각각 세 번 부른 중앙값). 모든 모양에서 아는 값 2,000개와 1 MB를 호출당 2초 안에
  하는 더 빠른 찾기는 1.7.3에서 합니다.

**LiteLLM 프록시**: 따로 띄울 서비스가 없는 guardrail입니다. `config.yaml`에:

```yaml
guardrails:
  - guardrail_name: "newmean-pii"
    litellm_params:
      guardrail: newmean_pii.litellm.NewmeanPIIGuardrail
      mode: "pre_call"      # 요청을 보내기 전에 가림; "post_call"은 응답을 가림
      size: "medium"        # 선택; variant, labels, sensitivity, redact_mode, model_dir, allow, block 등도 가능
```

요청에서 `"guardrails": ["newmean-pii"]`로 켜거나 `default_on: true`로 둡니다. 글은 `redact`와 같게 가려집니다
(`[PERSON]`, `[PHONE]`; 다른 방식은 `redact_mode: "mask"` 또는 `"blank"`). 가린 값은 응답에서 되돌리지 않으며, 위의
놓침 비율이 그대로 적용됩니다. 제공자가 보는 개인정보를 줄일 뿐, 하나도 새지 않는다고 보장하지는 않습니다.

**Microsoft Presidio**: `presidio-analyzer`용 recognizer입니다. 단독으로도, Presidio의 기본 recognizer와 함께도 씁니다.

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from newmean_pii.presidio import NewmeanPIIRecognizer

analyzer = AnalyzerEngine()
analyzer.registry.add_recognizer(NewmeanPIIRecognizer(size="medium"))
text = "Call Kim Minsu on 010-1234-5678"
AnonymizerEngine().anonymize(text=text, analyzer_results=analyzer.analyze(text, language="en")).text
# 'Call <PERSON> on <PHONE_NUMBER>'
```

레이블은 Presidio에 같은 뜻의 이름이 있으면 그 이름으로 알려 줍니다(`EMAIL` → `EMAIL_ADDRESS`, `PHONE` → `PHONE_NUMBER`,
`ADDRESS` → `LOCATION`, `ORG` → `ORGANIZATION`; 전체 표는 `ENTITY_MAPPING`, `entity_mapping={...}`으로 바꿈). 모든 결과의
`score`는 같습니다(기본 0.85). 모델은 점수가 아니라 구간을 돌려주므로, 기준은 `sensitivity`로 옮기세요. Presidio의
recognizer는 언어 하나만 맡습니다. `recognizers(["en", "ko", "ja"], size="medium")`은 모델 하나를 함께 쓰는 언어별
recognizer를 돌려줍니다. 분석하는 언어마다 Presidio 자체의 NLP 엔진은 따로 필요합니다.

**Langfuse**: `mask` 함수입니다. Langfuse는 관측마다 입력, 출력, 메타데이터를 이 함수에 넘기고, 이것들은 모델이 찾은
개인정보를 뺀 채로 저장됩니다.

```python
from langfuse import Langfuse
from newmean_pii.langfuse import pii_mask

langfuse = Langfuse(mask=pii_mask(size="medium"))
```

`pii_mask(redactor=None, *, mode="tag", keys=True, ids=True, **create_redactor_options)`는 관측 하나의 입력, 출력,
메타데이터 안에 있는 모든 문자열을 깊이 제한 없이 가립니다: dict(모든 매핑)의 키와 값, list·tuple·set·deque·dict
뷰의 항목, bytes(UTF-8로 읽음), Enum 멤버의 값, dataclass의 필드, 그 밖의 객체에서는 그 클래스가 정의한
`model_dump`(pydantic v2)나 `dict`(pydantic v1) 메서드가 돌려주는 것. 값의 종류는 그 값의 실제 클래스(`type(value)`)로
정하고 `__class__`가 말하는 클래스로 정하지 않으며, 이 메서드와 필드는 값의 코드를 실행하지 않고 클래스와 그 부모
클래스에서 찾습니다. `__getattr__`로만 넘겨주는 객체(프록시)는 아래의 "그 밖의 값"입니다. 가린 키가 이미 있는 키와
같아지면 ` #2`, ` #3`, ...을 붙여 값이 사라지지 않습니다. 트레이스의 id와 필드 이름인 키는 쓰인 그대로 둡니다. 모델은
이들 대부분을 개인정보로 읽기 때문입니다.

- **공급자와 프레임워크가 쓰는 id**는 어디에 있든(값, 리스트 항목, 키) 둡니다: OpenAI의 chat completion, 도구 호출,
  Responses, fingerprint id(`chatcmpl-…`, `call_…`, `resp_…`, `msg_…`, `fc_…`, `fp_…`), Anthropic의 메시지, 도구 사용,
  요청 id(`msg_01…`, `toolu_01…`, `req_…`), Bedrock의 도구 사용 id(`tooluse_…`), LangChain의 run id(`lc_run--<uuid>-0`).
- **UUID**(16진수 `8-4-4-4-12`, 대소문자 무관)와 LangGraph 체크포인트 이름공간(`model:<uuid>`)은 트레이스가 id라고 이름
  붙인 자리에서 둡니다: id 키(`id`, `run_id`, `parent_run_id`, `session_id`, `thread_id`, `trace_id`, `message_id`,
  `tool_call_id`, `call_id`, `request_id`, `response_id`, `checkpoint_ns`와 모듈에 적은 나머지, `_ID_KEYS`)의 값, 또는
  그 자리에 있는 list·tuple·set·deque·dict 뷰의 항목(리스트 안의 리스트도). 그 자리의 bytes, Enum 멤버, 객체의 dump는
  담은 값으로 칩니다. 키는 그런 자리가 아닙니다: 키로 쓴 UUID는 id 키 아래에서도
  모델이 읽습니다(`{"run_id": {"<uuid>": "ok"}}`). 다른 자리의 UUID(다른 키 아래, id 키 아래 dict 안, 홀로 있는 것)도
  모델이 읽습니다. UUID는 사람의 식별자(광고 ID, 결제 키, `user_id` 아래의 고객 id)일 수 있기 때문입니다.
- **필드 이름인 키**: 64자 이하의 `str` 키(하위 클래스 포함)로, 두 단어 이상인 ASCII 글자의 snake_case나 camelCase(`user_id`,
  `user_name`, `user_email`, `USER_ID`, `userName`. camelCase에서 첫 단어 뒤의 각 단어는 대문자 하나와 소문자들이라
  `userID`는 읽습니다)이거나 id 키인 것. 그 밖의 키는 다른 글처럼 읽습니다(이메일 주소나 이름을 키로 쓴 맵). 한 단어,
  숫자, 점, 하이픈은 사용자 이름도 쓰는 모양이고(`minsu92`, `jukka.nieminen`), 더 긴 키는 안에 이름을 담을 수 있기
  때문입니다(`userJohnSmith…`). `str`이 아닌 키(bytes, 숫자, tuple, `UserString`)는 값처럼 읽습니다.

id 키는 64자 이하이고, 대소문자와 `_`, `-`, `.`, 공백을 빼고 끝의 `ids`를 `id`로 읽었을 때 `_ID_KEYS`에 있는
키입니다(`runId`, `run-ids`, `Run ID`, `run.id`). id는 1,024자까지 둡니다. id와 필드 이름은 그 안에서 아무것도 찾지
못할 때만 그대로 둡니다: 패턴 계층이 읽고(UUID나 16진수 id의 무작위 16진수는 읽지 않습니다. 거기서는 전화·카드 번호를
우연히 찾기 때문입니다), `block` 목록이 그것과 그 안의 UUID 하나하나를 읽습니다.
찾으면 모델이 다른 글처럼 읽습니다. 개발용 세트에서(1.7.2용으로 측정) 가려진
필드 이름은 1,030개 중 75개에 1개꼴에서 지명이기도 한 네 개(`Georgia`, `Paris` 등, nano는 `antall_forsøk`도)로 줄었고,
키로 쓴 개인정보 값은 여전히 96.3%를 가리며(모든 키를 읽을 때 96.4%), 위 모양마다 무작위 id 200개는 모두 남습니다.
`ids=False`면 id를 글로 읽고, `keys=False`면 모든 키를 그대로 둡니다. 이 패키지의 `Redactor`를 쓰면 가장 최근에 읽은
키 4,096개의 결과를 탐지기 설정별로 기억해, 다음 관측의 같은 키는 다시 읽지 않습니다. 다른 redactor는 키를 매번 읽습니다.

이런 타입의 Python 객체는 그대로 둡니다: 숫자, 불리언, 날짜와 시각과 기간, `uuid.UUID`, `None`, Langfuse의 미디어
객체(Langfuse가 올리는 파일의 참조, 아래 참고). 긴 숫자는 그것만 보아서는 전화번호인지 시각인지 알 수 없으니, 가려야 하는
숫자는 글자로 저장하세요. 문자열로 쓴 숫자, 날짜, 시각과 다른 모양의 id는 글이라 모델이 읽습니다. 시각은 일부 가립니다.
그 밖의 값과 UTF-8이 아닌 bytes는 `[UNMASKED: <type>]`으로 바꿔 읽지 못한 것은 보내지 않습니다. 함수가 돌려주는 값은
Python 자체 타입(dict, list, tuple, set, frozenset, str, bytes, bytearray와 위에서 그대로 두는 타입. `keys=False`면 키는
그대로)으로만 만들고 내 클래스의 객체는 돌려주지 않습니다. Langfuse는 객체를 그 클래스의 코드(`str()`, `isoformat()`, 인스턴스가 가진 속성)로
쓰기 때문입니다: named tuple은 tuple이, `str`과 bytes의 하위 클래스는 `str`과 bytes가, 그대로 두는 타입의 하위 클래스는
그 타입 자체가 됩니다(`uuid.UUID` 하위 클래스는 같은 번호의 `uuid.UUID`, `datetime` 하위 클래스는
날짜, 시각, `tzinfo`가 같은 `datetime`). 하위 클래스의 코드는 실행하지 않습니다. Langfuse의 미디어 객체는 이름만 같은 클래스가 아니라
Langfuse 자신의 클래스입니다. 가리기가 실패하면(모델을
불러오지 못할 때, 예를 들어 캐시에 아무것도 없는 오프라인; 탐지기가 예외를 낼 때; 컨테이너나 객체의 필드를 읽지 못할 때,
예를 들어 예외를 내는 pydantic serializer, 읽을 때 예외(`AttributeError`도)를 내는 `model_dump` 속성, 값이 없는 dataclass
필드, 항목을 읽지 못하는 매핑이나 리스트. 읽기에 실패한 것을 없는 메서드나 필드로 여기지 않습니다) 나머지 부분까지 값
전체가 `[MASK FAILED]`가 되고, 오류의 타입을 `newmean_pii.langfuse` 로거에 ERROR로 남깁니다. 함수는 예외를 내지 않습니다. 옵션은 `pii_mask`를 부를 때 확인하고(크기 이름을 잘못 쓰면 거기서 예외),
모델은 첫 호출 때 불러옵니다. 모든 관측마다 내 프로세스에서 돌므로, 기록 양을 생각해 모델 크기를 고르세요.

트레이스의 나머지는 Langfuse가 `mask`에 넘기지 않고 그대로 보냅니다: 이름, 모델 파라미터, 상태 메시지, 예외 이벤트와
스택 트레이스(이메일 주소가 들어간 오류 메시지는 그대로 갑니다), 트레이스 속성(사용자·세션 id, 태그,
`propagate_attributes`로 넣은 메타데이터. Langfuse의 LangChain 콜백 핸들러는 실행하는 run의 `metadata`를 여기에 넣습니다),
Langfuse가 내보내는 다른 OpenTelemetry 계측의 span(OpenInference, OpenLLMetry
등). 파일은 `mask`가 돌기 전에 올립니다: 관측 안의 base64 data URI, base64 파일 블록(Anthropic, Vertex, Gemini),
`LangfuseMedia` 객체는 그대로 Langfuse에 올라가고, 트레이스에는 그 참조가 남습니다. 파일을 빼려면 기록하는 데이터에서
빼거나 `LANGFUSE_MEDIA_UPLOAD_ENABLED=false`로 두세요. 그러면 아무것도 올리지 않고 base64 글이 `mask`에 닿는데, 모델은
그것을 쓰인 그대로 읽습니다(base64를 풀지 않습니다).

## 오프라인 사용

한 번 내려받은 뒤 네트워크 없이 실행합니다.

```bash
python -m newmean_pii download --size medium            # 캐시에 저장; 폴더를 출력
NEWMEAN_PII_OFFLINE=1 python app.py                     # 다운로드하지 않음; 캐시에 없으면 실패
```

또는 Hugging Face 저장소의 파일(`tokenizer.web.json`, `onnx/model_<variant>.onnx`)을 직접 복사해 폴더를 넘깁니다.

```python
redactor = create_redactor(size="medium", model_dir="/opt/models/pii-medium")
```

이 경우에도 고정된 SHA-256과 대조합니다. 컨테이너에서는 이미지 빌드 때 `NEWMEAN_PII_CACHE`를 이미지 안 폴더로 두고
`python -m newmean_pii download`를 실행하세요.

`fetch_model()`과 `python -m newmean_pii download`는 파일을 캐시에 쓰지 못하면(읽기 전용이거나 가득 찬 디스크, 파일을
가리키는 `--cache-dir`) 실패합니다. `fetch_model()`은 `ModelFileError`를 내고, 명령은 stdout에 아무것도 쓰지 않고 stderr에
그 파일을 적은 뒤 상태 1로 끝납니다. 그래서 빌드 단계가 나중에 오프라인에서 열 수 없는 폴더를 알려 주는 일이 없습니다.
바로 쓰려고 불러올 때(`create_redactor`)는 캐시에 쓰지 못해도 내려받아 검증한 바이트로 그대로 동작하고, 다 갖춘 캐시는
읽기만 합니다. 1.7.2부터이며, 이전 버전은 폴더를 출력하고 상태 0으로 끝났습니다.

간단한 명령줄도 있습니다: `python -m newmean_pii redact --size nano --mode mask < input.txt`.

## 개발

```bash
cd python
uv build                      # sdist(native/core 복사 포함)와 그 sdist에서 빌드한 휠
pip install dist/*.whl pytest
pytest -q                     # 픽스처 패리티 테스트는 픽스처와 모델 사본이 없으면 건너뜀
```

C 확장은 `native/core`(모든 네이티브 패키지가 공유하는 코어)를 그 Makefile과 같은 플래그로 컴파일하므로, 로짓이 npm
패키지의 WebAssembly 엔진과 비트 단위로 같습니다(arm64에서는 1.5.0부터 커널이 곱셈과 덧셈을 융합하므로 relaxed-SIMD
빌드와, 그 밖에서는 기본 빌드와 같음). 테스트: Python 기준 구현과의 정규식 패리티, 마스킹 모드,
이모지 텍스트의 오프셋, 스레드, 로컬 HTTP 서버와 `file://`를 쓰는 다운로드(네트워크 불필요), 그리고 비공개 픽스처와
모델 사본이 있으면(`PII_FIXTURES`, 기본 `js/test/fixtures`; `PII_MODELS`, 기본 `js/test/models`) Hugging Face와의
토크나이저 패리티, npm 테스트의 모든 실행에 대한 Python 평가기와의 구간 패리티(`PII_SIZES=nano,medium`으로 제한).
`NEWMEAN_PII_NETWORK_TESTS=1`이면 Hugging Face 다운로드 테스트 하나가 더 돕니다. 휠: `cibuildwheel`(설정은
`pyproject.toml`, 워크플로는 `.github/workflows/python-wheels.yml`).

## 라이선스

패키지 코드와 모델 가중치 모두 [Apache-2.0](LICENSE)이며, 제3자 고지는 [NOTICE](NOTICE)에 있습니다. 제품에 쓰신다면 문서나
정보·라이선스 화면에 "Powered by newmean.ai"를 표기해 주시면 감사하겠습니다(라이선스 조건이 아닌 요청). 만든 곳:
[newmean.ai](https://newmean.ai).
