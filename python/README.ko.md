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

## API

```python
create_redactor(               # 또는 Redactor(...), 인자 동일
    size="medium",             # "nano" | "small" | "medium" | "large"
    variant=None,              # "q4" | "q4e"; 기본은 large/medium q4e, small/nano q4
    *,
    sensitivity=0.0,           # > 0 더 많이 가림, < 0 덜 가림 (대략 -1..1)
    labels=None,               # 가릴 레이블; 기본 전체(LABELS); 모르는 이름은 ValueError
    regex=False,               # 몇 가지 패턴(이메일, URL, 카드/계좌, 전화, 주민등록번호)도 가림;
                               # 기본 꺼짐: 추가 탐지는 거의 없고 과잉 마스킹이 두 배
    keep_emoji=False,          # True: 모델이 이모지도 읽음(1.0 동작); 기본: 이모지는 읽지도 가리지도 않음
    whole_words=True,          # 기본 True: 낱말 중간에서 끝나는 구간을 낱말 전체로 넓힘
    strict_short_names=False,  # True: 8토큰 이하의 짧은 글은 근거가 더 있어야 이름으로 봄
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
redactor.set_sensitivity(value)        # 다시 불러오지 않음
redactor.tokenize(text)                # -> [Token(id, start, end)]
redactor.info()                        # package_version, model_version, size, variant, files(path, bytes, sha256), 모델 구조, build
redactor.close()                       # 또는: with create_redactor() as r: ...

apply_redaction(text, spans, mode="tag", mask_char="*", replace=None)
regex_spans(text)                      # 패턴 레이어만
fetch_model(size, variant=None, cache_dir=None)  # 미리 내려받고 검증; 폴더를 돌려줌
```

- **오프셋**은 Python `str` 인덱스(코드 포인트)입니다. 이모지가 있어도 `text[span.start:span.end]`가 가릴 텍스트입니다.
  npm 패키지는 같은 구간을 UTF-16 인덱스로 냅니다. 구간은 문자소 클러스터 단위로 넓혀지므로 이모지, 국기, 결합 문자열은
  통째로 가려지거나 통째로 남습니다. `source`는 `"model"`, `"regex"`, `"model+regex"`, `"table"` 중 하나입니다.
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

- **속도**: Apple M5 Pro 한 스레드에서 짧은 텍스트 하나에 nano 약 10 ms, medium 약 15 ms, 1,000토큰 텍스트는 0.4초(nano)
  ~0.55초(medium). 긴 텍스트는 256토큰 창으로 겹쳐 나눠 처리합니다.
- 오류: 잘못된 옵션은 `ValueError`, 파일이 없거나 내려받지 못하거나 고정값과 다르면 `ModelFileError`.

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

간단한 명령줄도 있습니다: `python -m newmean_pii redact --size nano --mode mask < input.txt`.

## 개발

```bash
cd python
uv build                      # sdist(native/core 복사 포함)와 그 sdist에서 빌드한 휠
pip install dist/*.whl pytest
pytest -q                     # 픽스처 패리티 테스트는 픽스처와 모델 사본이 없으면 건너뜀
```

C 확장은 `native/core`(모든 네이티브 패키지가 공유하는 코어)를 그 Makefile과 같은 플래그로 컴파일하므로, 로짓이 npm
패키지의 기본 WebAssembly 엔진과 비트 단위로 같습니다. 테스트: Python 기준 구현과의 정규식 패리티, 마스킹 모드,
이모지 텍스트의 오프셋, 스레드, 로컬 HTTP 서버와 `file://`를 쓰는 다운로드(네트워크 불필요), 그리고 비공개 픽스처와
모델 사본이 있으면(`PII_FIXTURES`, 기본 `js/test/fixtures`; `PII_MODELS`, 기본 `js/test/models`) Hugging Face와의
토크나이저 패리티, npm 테스트의 모든 실행에 대한 Python 평가기와의 구간 패리티(`PII_SIZES=nano,medium`으로 제한).
`NEWMEAN_PII_NETWORK_TESTS=1`이면 Hugging Face 다운로드 테스트 하나가 더 돕니다. 휠: `cibuildwheel`(설정은
`pyproject.toml`, 워크플로는 `.github/workflows/python-wheels.yml`).

## 라이선스

패키지 코드와 모델 가중치 모두 [Apache-2.0](LICENSE)이며, 제3자 고지는 [NOTICE](NOTICE)에 있습니다. 제품에 쓰신다면 문서나
정보·라이선스 화면에 "Powered by newmean.ai"를 표기해 주시면 감사하겠습니다(라이선스 조건이 아닌 요청). 만든 곳:
[newmean.ai](https://newmean.ai).
