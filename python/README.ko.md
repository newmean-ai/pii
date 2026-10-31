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

여기에 0.8 MB 토크나이저가 더해집니다. 처음 쓸 때 Hugging Face(`newmean-ai/pii-<size>`, 태그 `v1.0.0`)에서
`~/.cache/newmean-pii`로 내려받고, 이 패키지에 고정된 SHA-256 값과 대조합니다. 이후 불러올 때마다 다시 대조합니다.

모든 개인정보를 찾는 모델은 없습니다. 언어·데이터셋별 놓침률과 과잉 마스킹률은 기술 보고서에 있습니다. 결과는 보장이
아니라 강력한 1차 처리로 다뤄 주세요. newmean-pii-dataset(공개 테스트, 54개 언어)에서 개인정보 글자 중 놓치는 비율은
1.6%(large)~3.3%(nano)이며, 전체 비교는 [저장소 README](https://github.com/newmean-ai/pii#results)에 있습니다.

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
    model_dir=None,            # tokenizer.web.json과 onnx/model_<variant>.onnx가 있는 폴더; 다운로드하지 않음
    cache_dir=None,            # 기본: $NEWMEAN_PII_CACHE, $XDG_CACHE_HOME/newmean-pii 또는 ~/.cache/newmean-pii
    model_url=None,            # newmean-ai/pii-<size> 미러의 기준 URL (https://, http://, file://)
    on_progress=None,          # callback(file, loaded_bytes, total_bytes)
) -> Redactor

redactor.detect(text)                  # -> [Span(start, end, label, source)], 정렬, 겹침 없음
redactor.redact(text, mode="tag")      # -> RedactResult(text, spans); "tag" -> [EMAIL], "mask" -> *****, "blank" -> 삭제
redactor.redact(text, mask_char="#", replace=None)  # replace(span, original) -> str 가 mode보다 우선
redactor.set_sensitivity(value)        # 다시 불러오지 않음
redactor.tokenize(text)                # -> [Token(id, start, end)]
redactor.close()                       # 또는: with create_redactor() as r: ...

apply_redaction(text, spans, mode="tag", mask_char="*", replace=None)
regex_spans(text)                      # 패턴 레이어만
fetch_model(size, variant=None, cache_dir=None)  # 미리 내려받고 검증; 폴더를 돌려줌
```

- **오프셋**은 Python `str` 인덱스(코드 포인트)입니다. 이모지가 있어도 `text[span.start:span.end]`가 가릴 텍스트입니다.
  npm 패키지는 같은 구간을 UTF-16 인덱스로 냅니다. `source`는 `"model"`, `"regex"`, `"model+regex"` 중 하나입니다.
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
