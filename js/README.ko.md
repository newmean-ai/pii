[English](README.md) | 한국어

# @newmean-ai/pii

텍스트 속 개인정보를 **브라우저 안에서** 찾아 가립니다. 텍스트가 어디로 보내지기 전에 처리합니다. 54개 언어를 다루는 작은 토큰 분류
모델을 WebAssembly로 실행합니다.

```bash
npm install @newmean-ai/pii
```

```ts
import { createRedactor } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium" });
const { text, spans } = await redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다");
// text: "제 메일은 [EMAIL], 전화는 [PHONE]입니다"
```

## 모델

| 크기 | 파일 | 비고 |
|---|---|---|
| `nano` | 2.6 MB | 공유 층 하나를 4번 적용, 임베딩 rank 32 |
| `small` | 7.1 MB | 4층, 임베딩 rank 64 |
| `medium` (기본) | 11.3 MB | 6층, 임베딩 rank 192 |
| `large` | 15.5 MB | 6층, 임베딩 축소 없음(가지친 어휘) |

여기에 토크나이저 0.8 MB(모든 크기 공용)가 더해집니다. 파일은 Hugging Face
(`newmean-ai/pii-<size>`)에서 한 번 내려받아 Cache API에 보관하고, 이 패키지에 고정된 SHA-256 값과 맞는지 확인합니다. 직접 제공하려면
모델 폴더를 복사하고 `modelUrl`을 지정하세요.

## 엔진

두 엔진이 같은 모델 파일을 실행하고 같은 구간을 냅니다. `engine`으로 고르세요.

| `engine` | 실행기 내려받기 | 비고 |
|---|---|---|
| `"builtin"` (기본) | 약 20 KB(brotli, JS와 WebAssembly 합계) | 이 모델 전용 내장 엔진. 시작이 빠르고, 측정한 휴대폰에서 짧은 텍스트는 약 1.9배, 긴 텍스트는 약 24% 빠름(휴대폰 네 대 평균). 브라우저가 relaxed SIMD를 지원하면(Chrome, Edge, Firefox) 융합 곱셈-덧셈 사용 |
| `"onnxruntime"` | onnxruntime-web, 약 2.4 MB(brotli) | 범용 ONNX 실행기, 교차 출처 격리 페이지에서 멀티스레드 |

엔진은 각각 따로 나뉜 청크로, 처음 쓸 때 불러옵니다. 앱은 고른 엔진만 내려받습니다.

어떤 모델도 개인정보를 모두 찾지는 못합니다. 기술 보고서에 언어별·데이터셋별 놓침률과 과잉 마스킹률이 있습니다. 결과는 보장이
아니라 강력한 1차 처리로 다뤄 주세요.

## 결과

newmean-pii-dataset (공개 테스트, 54개 언어)에서 모델은 개인정보 글자의 1.6%(large)~3.3%(nano)를 놓칩니다. 비교한 공개 탐지기 8개 중 가장 가까운 것은
19.6%를 놓칩니다. 외부 데이터셋 4종 점수, 전체 비교, 휴대폰 속도는 [저장소 README](https://github.com/newmean-ai/pii#results)와
기술 보고서에, 주간 점수는 [리더보드](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)에 있습니다.

## 라벨

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, 그리고 간접
식별자를 위한 수준 2 라벨 두 개: `PERSON_L2`(성+직함, 별명, 이니셜)와 `URL_L2`(공개 프로필·페이지 URL). 수준 2를 가리지 않으려면
`labels: LEVEL1_LABELS`를 넘기세요.

## API

[README.md](README.md#api)를 보세요. 위치(offset)는 `String.prototype.slice`와 같은 UTF-16 인덱스입니다. 추론은 호출한 스레드에서
실행되므로, 긴 텍스트는 Web Worker 안에서 redactor를 만드는 것을 권합니다.

## 개발

```bash
npm install
npm run typecheck   # tsc --noEmit
npm test            # vitest; 패리티 테스트는 fixture가 없으면 건너뜀
npm run build       # dist/
npm run demo        # 테스트 페이지(vite), demo/models/ 필요
```

내장 엔진의 WebAssembly는 base64로 저장소에 들어 있습니다(`src/engines/wasm-*.ts`). `engine/pii.c`를 바꿨다면 `engine/build.sh`
(wasm32 타깃을 지원하는 LLVM clang)를 실행한 뒤 `node scripts/embed-wasm.mjs`를 실행하세요.

데모 페이지와 패리티 테스트는 Hugging Face 모델 저장소 네 개(`newmean-ai/pii-<size>`)의 로컬 사본을 읽습니다.

```
demo/models/pii-<size>/tokenizer.web.json
demo/models/pii-<size>/onnx/model_q4.onnx, model_q4e.onnx
test/models/pii-<size>/...    (같은 구조; 또는 PII_MODELS에 이 구조의 폴더 지정)
```

패리티 테스트는 `test/fixtures/`의 기준 출력으로 SDK를 Python 평가기와 비교합니다. fixture는 검증 텍스트로 만들어 공개하지
않으므로, 없으면 이 테스트들은 건너뜁니다.

## 라이선스

[Apache-2.0](LICENSE)(SDK 코드와 모델 가중치), 제3자 고지는 [NOTICE](NOTICE). 제품에 쓰신다면 문서나 정보·라이선스 화면에 "Powered by newmean.ai"를 표시해 주시면 감사하겠습니다(라이선스 조건이 아닌 요청입니다). [newmean.ai](https://newmean.ai)
