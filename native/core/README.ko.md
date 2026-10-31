[English](README.md) | 한국어

# newmean-pii 네이티브 코어

모든 네이티브 패키지(Python, Android, iOS, Flutter)가 함께 컴파일하는 C 라이브러리입니다. 공개된
`newmean-pii-<size>` 모델 파일을 읽어 토큰화하고 모델을 실행해 가릴 구간(span)을 돌려줍니다. 결과는 npm 패키지
[`@newmean-ai/pii`](../../js)의 `detect()`(`regex: false`)와 같습니다(토큰 id와 오프셋이 같고 로짓이 비트 단위로 같음).
순수 C11이며 의존성, 네트워크 통신, 전역 상태가 없습니다.

## 구성

| 경로 | 원본 | 내용 |
|---|---|---|
| `pii.h` | | 공개 API (패키지가 포함하는 유일한 헤더) |
| `src/engine.c`, `src/simd.h` | `js/engine/pii.c` | 추론: 같은 커널, 같은 부동소수점 연산 순서 |
| `src/onnx.c` | `js/src/engines/onnx-weights.ts` | 최소 protobuf 리더. `model_<variant>.onnx`를 엔진 배치로 변환 (검사 항목 동일) |
| `src/tokenizer.c`, `src/unicode_tables.h` | `js/src/tokenizer.ts` | `tokenizer.web.json`을 읽는 Unigram 토크나이저 (Precompiled 정규화, WhitespaceSplit, Metaspace, Viterbi) |
| `src/api.c` | `js/src/decode.ts`, `js/src/redactor.ts`의 `combine` | 윈도, 토큰별 최고 태그, BIO 구간, 병합, 라벨 필터 |
| `src/util.c` | | JSON·base64 리더, 오프셋 표를 만드는 UTF-8 디코딩, 정렬 메모리 |
| `src/sha256.c`, `src/models.c` | `js/src/models.ts` | SHA-256과 고정된 파일 크기·해시 (생성 파일) |
| `scripts/` | | `gen_models.py`(`js/src/models.ts`에서 고정값 생성), `gen_unicode.py`(문자소 표) |
| `tests/` | | `pii_cli.c`(테스트 CLI), `run_tests.py`(일치 테스트), `js_detect.mjs`(비교용 npm 엔진 실행) |

## API

```c
#include "pii.h"

// 파일: https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/{onnx/model_<variant>.onnx, tokenizer.web.json}
if (pii_verify_file(onnx, onnx_len, "medium", "q4e") || pii_verify_file(tok, tok_len, "medium", "tokenizer")) { /* 거부 */ }
char err[256];
pii_model *m = pii_model_load(onnx, onnx_len, tok, tok_len, err, sizeof err);  // 이후 입력 버퍼는 해제해도 됨

pii_options o;
pii_options_init(&o);         // 민감도 0, 모든 라벨
o.sensitivity = 0.3;          // > 0 더 많이 가림, < 0 덜 가림 (npm `sensitivity`)
o.labels = PII_LABELS_LEVEL1; // npm LEVEL1_LABELS, 또는 PII_LABEL_BIT(PII_EMAIL) | ...

pii_span *spans;
size_t n;
if (pii_detect(m, text, strlen(text), &o, &spans, &n) == PII_OK) {
  for (size_t i = 0; i < n; i++)  // 정렬됨, 겹치지 않음
    printf("%s [%zu, %zu)\n", spans[i].label_name, spans[i].byte_start, spans[i].byte_end);
  pii_spans_free(spans);
}
pii_model_free(m);
```

- **구간**은 `[start, end)`를 UTF-8 바이트, 코드 포인트(Python `str`), UTF-16 단위(JavaScript, Java/Kotlin, Dart,
  `NSString`) 세 가지로 모두 담으므로 래퍼가 변환할 필요가 없습니다. 잘못된 UTF-8 바이트는 하나당 코드 포인트 하나로 셉니다.
  오프셋은 항상 코드 포인트 경계에 놓입니다. 토크나이저의 정규화가 바꾸는 문자소(예: `e` + U+0301)는 쪼개지지 않지만,
  모델이 ZWJ 연쇄 같은 다른 문자소 안에서 구간을 끝낼 수는 있습니다. 그래서 자를 때는 코드 포인트나 UTF-16 단위를 쓰고
  Swift `Character` 인덱스는 쓰지 않습니다. 입력은 표준 UTF-8로 넘깁니다. JVM에서는
  `String.getBytes(UTF_8)`을 쓰고 JNI `GetStringUTFChars`는 쓰지 않습니다(수정 UTF-8은 이모지를 서로게이트로, NUL을 두 바이트로
  나눠 토큰과 오프셋이 달라짐). Python에서는 `str.encode("utf-8", "surrogatepass")`를 씁니다.
- **오류**: 함수는 `PII_OK`나 `PII_ERR_*` 코드를 돌려줍니다(`pii_strerror`가 메시지를 줌). `pii_model_load`는 이유를 `err`에
  씁니다. 잘리거나 손상된 모델·토크나이저 파일은 메시지와 함께 실패하며 멈추지 않습니다. 돌려받은 배열은 호출한 쪽이
  소유하며 `pii_spans_free` / `pii_tokens_free`로 해제합니다(Windows에서는 `free`가 다른 C 런타임 것일 수 있음). 글은
  `INT32_MAX` 바이트까지이며, 작업 메모리는 입력 1바이트당 약 20바이트에 윈도 하나분이 더해집니다.
- **옵션**은 npm 패키지와 같습니다. `sensitivity`(O 로짓에 `-sensitivity`를 더함), `labels`(비트 집합, 기본은 13개 전부).
  `extra`는 래퍼의 패턴 계층이 찾은 구간을 받아 npm `combine`과 똑같이 병합합니다(모델 라벨 우선, 같은 계열의 모델
  라벨이 덮지 않는 곳에서만 패턴 라벨 사용). 그래서 패턴 계층은 각 언어에 둘 수 있습니다.
- **고정값과 기본값**: `pii_model_file(size, file)`은 공개 파일별 경로, 크기, SHA-256을, `pii_default_variant(size)`는 npm
  기본값(large·medium은 `q4e`, small·nano는 `q4`)을 돌려줍니다. 기본 크기는 `PII_DEFAULT_SIZE`(`medium`)입니다.
- **스레드**: `pii_model`은 로드 뒤 바뀌지 않습니다. 여러 스레드가 한 모델로 동시에 `pii_detect` / `pii_tokenize`를
  불러도 됩니다. 호출마다 작업 메모리를 따로 잡습니다(256토큰 윈도 하나에 약 4.5 MB, 짧은 글은 더 적음).
- `pii_tokenize`는 토큰 id와 오프셋(`<s>`, `</s>` 제외)을, `pii_model_get_info`는 모델 형태를, `pii_build_info`는 커널
  빌드 종류를 돌려줍니다.

각 언어에 남긴 부분(작고 언어마다 관용이 다름): 정규식 계층(`js/src/regex.ts`, npm처럼 기본 꺼짐)과 `applyRedaction`의
마스킹 방식(`tag` → `[계열]`, `PERSON_L2` → `PERSON`, `URL_L2` → `URL`; `mask` → 코드 포인트마다 마스크 문자 하나; `blank`).

## 빌드

```bash
make                 # build/libpii.a, build/libpii.{dylib,so}, build/pii_cli
make FMA=1           # 융합 곱셈-덧셈 커널 (아래 참고)
make asan            # AddressSanitizer + UndefinedBehaviorSanitizer로 build/asan/pii_cli

cmake -S native/core -B build && cmake --build build      # 타깃 newmean::pii (정적), newmean::pii_shared
#   -DPII_FMA=ON  -DPII_BUILD_SHARED=OFF  -DPII_BUILD_TESTS=ON  -DPII_SANITIZE=ON
```

C11, `-O3 -Wall -Wextra -Wshadow`. SIMD 계층(`src/simd.h`)은 GCC/Clang 벡터 확장을 써서 arm64에서는 NEON, x86-64에서는
SSE로 컴파일됩니다. 그 밖의 컴파일러(MSVC)는 같은 IEEE 연산을 하는 스칼라 경로를 씁니다(`-DPII_SCALAR`로 강제 가능).
`-ffp-contract=off`(MSVC는 `/fp:precise`)는 빌드의 일부입니다. WebAssembly 엔진처럼 곱셈과 덧셈을 따로 계산하게 합니다.
리틀 엔디언 대상만 지원합니다(모델 파일이 리틀 엔디언).

**FMA 빌드.** 기본 빌드는 곱셈과 덧셈을 따로 하므로 어떤 CPU에서도 로짓이 비트 단위로 같고, npm 패키지의 기본(SIMD128)
WebAssembly 빌드와도 같습니다. `PII_FMA`는 행렬 곱셈-덧셈을 융합합니다. npm 패키지의 relaxed-SIMD 빌드(Chrome, Edge,
Firefox에서 쓰임)와 같은 방식이며, Apple M5에서 약 25% 빠르고 NEON(arm64)이나 FMA3(x86-64, `-mfma`)가 필요합니다. 로짓은
기본 빌드와 마지막 비트에서 다르지만, 확인한 모든 픽스처 행에서 구간은 같았습니다.

## 테스트

```bash
make && make asan && python3 tests/run_tests.py [--only pins,selftest,tokenizer,offsets,predict,js,threads,asan,fuzz] [--bench]
```

Python 표준 라이브러리만 씁니다. 픽스처와 모델 사본은 비공개(검증용 텍스트)이므로 복사하지 않고 제자리에서 읽습니다:
`PII_FIXTURES`(기본 `js/test/fixtures`), `PII_MODELS`(기본 `js/test/models`). 없으면 모든 테스트를 건너뜁니다.
`PII_SIZES=nano,medium`으로 크기를 줄일 수 있습니다. `fuzz`는 이름을 지정할 때만 돕니다(`PII_FUZZ=N`으로 규모 지정).

| 테스트 | 확인 내용 | 목표 |
|---|---|---|
| pins | 모델 파일이 고정된 크기·SHA-256과 일치 | 전부 |
| selftest | `js/test/combine.test.ts`의 `combine` 사례; 인자, 라벨 필터, UTF-16 단위 `extra` 구간, 잘못된 UTF-8, 긴 글; 잘리거나 변조된 모델·토크나이저 파일이 오류로 끝남 | 전부 |
| tokenizer | `tokenize.jsonl`(3,273행)에서 npm 패키지의 `Tokenizer`(node + `js/dist`), Hugging Face `tokenizers`와 id·오프셋 비교 | npm: 100%; Hugging Face: id 100%, 오프셋은 많아야 3행까지 다를 수 있음(npm 테스트와 같음) |
| offsets | 까다로운 글에서 UTF-8·코드 포인트·UTF-16 오프셋을 Python과 비교: 이모지와 ZWJ 연쇄, 국기, 결합 문자, CJK, RTL과 양방향 제어 문자, 폭 없는 문자, CRLF, NUL, 짝 없는 서로게이트, 잘못된 UTF-8, 빈 글과 공백뿐인 글, 특수 토큰 문자열, 10,000토큰 이상; 구간이 정렬되고 글 안에 있음 | 전부 |
| predict | `js/test/predict.test.ts`의 모든 실행(각 412행)에서 Python 평가기와 구간 비교 | npm 테스트처럼 99% 이상 |
| js | npm 패키지 자체 WebAssembly 엔진(`node`, `js/dist`, `tests/js_detect.mjs`)과 구간 비교; `PII_JS=all`이면 모든 실행 | 100% |
| threads | 4개 스레드가 한 모델 공유(nano, medium; 짧은 글과 여러 윈도 글)해도 단일 스레드 결과와 같음 | 동일 |
| asan | ASan + UBSan에서 selftest, 토크나이저 600행, offsets, nano 60행, 3개 스레드 | 오류 없음 |
| fuzz | `pii_cli fuzz`: 잘리거나 비트가 바뀐 모델 파일 N개, 변조한 토크나이저 파일 N / 2개, 무작위 바이트열 2N개(빌드돼 있으면 ASan + UBSan) | 로드되거나 메시지와 함께 실패; 구간은 입력 안 |

Apple M5 Pro(macOS, Apple clang 21, 기본 빌드) 결과, 2026-10-31:

| 테스트 | 결과 |
|---|---|
| pins | 12/12 파일 (4개 크기 × q4, q4e, tokenizer) |
| selftest | 통과 (기본 빌드, ASan + UBSan) |
| tokenizer | ids 3,273/3,273, offsets 3,273/3,273 |
| predict large q4e, large q4, medium q4e, medium q4, small q4, small q4e, nano q4e, medium q4e bias −0.5 | 각 412/412 (100%) |
| predict nano q4, nano q4 bias 1 | 각 410/412 (99.51%) |
| js (nano q4, nano q4 bias 1, medium q4e, medium q4e bias −0.5) | 각 412/412; FMA 빌드와 relaxed-SIMD 빌드 비교도 각 412/412 |
| threads | nano, medium, 203개 글 × 4개 스레드: 차이 0; 153개 글 × 8개 스레드(medium q4e, 민감도 0.5의 nano q4): 차이 0 |
| asan | 보고 없음 |
| offsets | 까다로운 글 24개(최대 13,996토큰): UTF-8·코드 포인트·UTF-16 오프셋이 모두 Python과 같음 |
| fuzz (ASan + UBSan) | nano q4·q4e: 변조한 모델 파일 800개와 토크나이저 파일 550개가 모두 로드되거나 메시지와 함께 실패; 무작위 바이트열 1,800개에서 구간과 토큰이 입력 안; 보고 없음. 메모리 누수 없음(`leaks --atExit`, 변조 파일 900개 추가) |
| logits | 윈도 13개 × nano q4, medium q4e, large q4(각 로짓 30,294개): 기본 빌드는 wasm SIMD128 빌드와, `FMA=1`은 wasm relaxed-SIMD 빌드(Node, arm64)와 비트 단위로 같음 |
| 다른 빌드 | GCC 16(NEON, `-DPII_SCALAR`)이 nano q4 412행 모두에서 Apple clang과 바이트 단위로 같은 출력; x86-64(`-target x86_64-apple-macos12`, `-mfma -DPII_FMA` 유무)는 경고 없이 컴파일되나 실행하지 않음 |

Python 평가기와 다른 행은 npm 패키지에서도 다릅니다. 평가기는 라벨이 다른 두 구간을 겹친 채 내놓고(예:
`20-21:ADDRESS 20-22:ORG`), `detect()`는 두 패키지 모두 겹친 구간을 한 영역으로 병합(`combine`)하기 때문입니다.

속도, 단일 스레드, 기본 빌드(`--bench`; 괄호 안은 `make FMA=1`):

| 모델 | 짧은 글 (64토큰 이하, 평균 36) | 픽스처 글 (평균 131토큰) | 1,062토큰 글 |
|---|---|---|---|
| nano q4 | 9.9 ms (7.8 ms) | 39.6 ms (29.6 ms) | 364 ms (269 ms) |
| small q4 | 9.9 ms | 39.7 ms | 366 ms |
| medium q4e (기본) | 14.9 ms (11.9 ms) | 59.7 ms (45.0 ms) | 550 ms (412 ms) |
| large q4e | 14.8 ms | 59.3 ms | 547 ms |

행렬 커널이 코어 하나에서 곱셈·덧셈 분리 시 최대 성능의 약 78%로 돌기 때문에, 기본 빌드는 같은 행에서 Node의 npm wasm
엔진보다 약 15%만 빠릅니다(nano 412행: 16.4초 대 19.2초).

## `js/engine/pii.c`와의 관계

`src/engine.c`는 같은 엔진에 기계적인 변경 세 가지만 한 것입니다. wasm SIMD128 인트린식을 `src/simd.h`의 `v_*` 함수로
바꿨고(인트린식 하나에 함수 하나, 레인별 IEEE 연산 동일), 범프 할당자를 `malloc`으로, 전역 모델 상태를 바뀌지 않는
`pii_engine`과 호출별 작업 메모리(`pii_ws`)로 바꿨습니다. 커널, `.piiw` 배치, 4비트 패널 형식, exp/erf 근사, 모든 합의
순서는 그대로이며 `js/engine/pii.c`는 건드리지 않았습니다. 엔진을 고칠 때는 두 파일을 함께 고치고 `js` 테스트로 확인합니다.

npm 패키지가 Hugging Face나 코드 포인트 수와 다르게 하는 곳에서는 토크나이저와 `combine`도 npm을 따릅니다.

- 정규화가 문자소를 바꿀 때(예: `e` + U+0301 → `é`) 새 문자를 모두 문자소 전체에 맞춥니다(`tokenizer.ts`와 같음).
  Hugging Face는 각 문자를 원문 문자 하나에 맞춥니다(`NormalizedString::transform`). 그래서 토큰 오프셋은 3,273행 모두
  npm 패키지와 같고, Hugging Face와는 3,272행이 같습니다(`e\u0301cole`: `[0, 1]`이 아니라 `[0, 2]`). 구간이 글자와,
  정규화가 그 글자에 합친 결합 문자 사이에서 끝나는 일은 없습니다.
- 겹친 모델 구간을 한 영역으로 합칠 때 가장 긴 구간의 라벨을 쓰며, 길이는 `redactor.ts`처럼 UTF-16 단위로 셉니다(이모지는 2).

## 라이선스

Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)).
