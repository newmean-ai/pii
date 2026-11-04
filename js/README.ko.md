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
| `"builtin"` (기본) | 약 13 KB(brotli, JS와 WebAssembly 합계) | 이 모델 전용 내장 엔진. 시작이 빠르고, 측정한 휴대폰에서 짧은 텍스트는 약 1.9배, 긴 텍스트는 약 24% 빠름(휴대폰 네 대 평균). 브라우저가 relaxed SIMD를 지원하면(Chrome, Edge, Firefox) 융합 곱셈-덧셈 사용 |
| `"onnxruntime"` | onnxruntime-web, 약 2.4 MB(brotli) | 범용 ONNX 실행기, 교차 출처 격리 페이지에서 멀티스레드 |

엔진은 각각 따로 나뉜 청크로, 처음 쓸 때 불러옵니다. 앱은 고른 엔진만 내려받습니다.

어떤 모델도 개인정보를 모두 찾지는 못합니다. 기술 보고서에 언어별·데이터셋별 놓침률과 과잉 마스킹률이 있습니다. 결과는 보장이
아니라 강력한 1차 처리로 다뤄 주세요.

## 결과

newmean-pii-dataset (공개 테스트, 54개 언어)에서 모델은 개인정보 글자의 1.6%(large)~2.9%(nano)를 놓칩니다. 비교한 공개 탐지기 8개 중 가장 가까운 것은
19.6%를 놓칩니다. 외부 데이터셋 4종 점수, 전체 비교, 휴대폰 속도는 [저장소 README](https://github.com/newmean-ai/pii#results)와
기술 보고서에, 주간 점수는 [리더보드](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)에 있습니다.

## 라벨

`PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `ID_NUMBER`, `FINANCIAL`, `ORG`, `USERNAME`, `URL`, `DATE_OF_BIRTH`, `SECRET`, 그리고 간접
식별자를 위한 수준 2 라벨 두 개: `PERSON_L2`(성+직함, 별명, 이니셜)와 `URL_L2`(공개 프로필·페이지 URL). 수준 2를 가리지 않으려면
`labels: LEVEL1_LABELS`를 넘기세요. `labels`에 라벨이 아닌 이름이 있으면 이 패키지는 그 이름을 무시합니다(Python과 Kotlin
패키지는 오류를 냅니다).

## API

[README.md](README.md#api)를 보세요. 위치(offset)는 `String.prototype.slice`와 같은 UTF-16 인덱스입니다. 구간은 문자소 클러스터
단위로 넓혀지므로 이모지, 국기, 결합 문자열은 통째로 가려지거나 통째로 남습니다(`Intl.Segmenter`가 필요하며, 없으면 구간은 코드
포인트 경계에 머뭅니다). 문자소 나누기는 실행 환경의 것을 씁니다. C 코어는 Unicode 17.0을 쓰므로, 다른 Unicode 데이터를 쓰는 브라우저나 Node는 일부 문자열을 다르게 나눌 수 있고, 그러면 구간 끝뿐 아니라 합치기, 라벨, allow 판정도 달라질 수 있습니다. 낱말 글자(`wholeWords`, block 경계)는 어느 환경에서나 고정된 Unicode 17.0 표를 씁니다. 기본으로 이모지는 모델이 읽는 텍스트에서 빠지므로 모델이 이모지를 개인정보로 읽지 않습니다. 오프셋은 그대로 입력 기준이고, 구간
안의 이모지는 구간을 둘로 나눕니다. `keepEmoji: true`면 모델이 텍스트를 그대로 읽습니다(1.0 동작).
기본으로(`wholeWords`, 1.3.0부터) 낱말 중간에서 시작하거나 끝나는 구간을 낱말 전체로 넓힙니다. "R23"은 `[ID_NUMBER]23`이 아니라 `[ID_NUMBER]`가
됩니다. 낱말은 문자, 숫자, 결합 부호가 이어진 것이며 아포스트로피와 하이픈이 둘을
잇습니다. 띄어쓰기 없이 쓰는 문자(중국어, 일본어, 태국어, 크메르어 등)는 구간을 넓히지 않으며, 한글과 숫자·라틴 문자가 만나는 곳에서 낱말이 끝납니다(「010-1234-5678입니다」 → `[PHONE]입니다`), 한글 구간 뒤에 붙은 조사는 그대로 둡니다(「홍길동님께」 → `[PERSON]님께`). 구간이 끝나는 곳만 바꿀 뿐 무엇을 가릴지는 바꾸지 않습니다. 모델이 이름으로 잘못 읽은 짧은 값(「경기도」 등)은 `[PERSON]기도`가 아니라 `[PERSON]`이 되며, 이런 값을 그대로 두려면 `strictShortNames`를 씁니다. 조금 더 가릴 뿐 덜 가리지는 않으며,
표 모드에서는 칸 안에서만 넓힙니다. `wholeWords: false`면 1.2와 같은 구간을 받습니다. `strictShortNames: true`면 모델이 8토큰 이하로 읽는 글(설문 응답, 표 칸)은 근거가 더 있어야 사람 이름으로 봅니다. 「경기」, 「남성」, 「네이버」는 그대로 두고 「김민지」, "Maria Lopez"는 여전히 찾습니다. large에서 개인정보 없는 짧은 설문 항목을 가린 비율은 7.9%에서 4.3%로 줄고, 단독 이름을 찾은 비율은 98.9%에서 96.5%로 줄어듭니다. 긴 글은 그대로입니다. `redactor.version`은 모델 버전(1.1.0)이고, 패키지 버전은 `VERSION` 또는
`redactor.info.packageVersion`입니다. `info`에는 감사 기록용으로 패키지·모델 버전, 크기, 종류, 실행기, 두 모델 파일의 크기와
SHA-256이 들어 있습니다. `info.verified`는 불러온 두 파일의 SHA-256을 실제로 계산해 맞았을 때만 true입니다. `verify: false`면(검사 없이
사용) false이고, 일반 HTTP처럼 보안 컨텍스트가 아니어서 `crypto.subtle`을 쓸 수 없는 페이지에서도(크기만 확인) false입니다.
`fetch` 옵션으로 모델 파일을 받을 함수를 넘길 수 있습니다(기본
`globalThis.fetch`, Node나 테스트에서 사용). 추론은 호출한 스레드에서 실행되므로, 긴 텍스트는 Web Worker 안에서 redactor를
만드는 것을 권합니다.

## 목록

1.4.0부터 데이터에 대해 이미 아는 것을 알려 줄 수 있습니다.

```ts
const redactor = await createRedactor({
  allow: ["제주", "배민", { text: "경기", label: "PERSON" }],  // 문자열은 모든 라벨을 허용
  block: [{ text: "박지훈", label: "PERSON" }, { text: "새솔고등학교", label: "ORG" }],
  listsId: "survey-2026-11 v3",
});
const { text } = await redactor.redact("제주, 박지훈".normalize("NFC"));
// text: "제주, [PERSON]" (목록이 없으면 medium 모델은 "[PERSON], [PERSON]")
```

- `block`: 낱말 경계에서 시작하고 끝나는 항목은 모두 그 항목의 라벨로 가립니다(출처 `"block"`). 모델이 찾지 못한 곳도,
  `labels`로 끈 라벨도 가립니다. 한글 항목은 조사 앞에서도 맞습니다(「박지훈님께」 → 「[PERSON]님께」). "Kim"은
  "Kimberly" 안에서 맞지 않고, "Kim's"나 "Kim-Lee"에서도 맞지 않습니다(`wholeWords`처럼 아포스트로피와 하이픈이 낱말을
  잇습니다). 띄어쓰기 없이 쓰는 문자는 어디서든 맞습니다. block이 맞은 영역은 block 라벨을 쓰고 allow로 빠지지 않습니다.
- `allow`: 구간 글 전체가 항목과 같으면 가리지 않습니다. `label`이 있으면 그 라벨 계열의 구간만 허용합니다(PERSON은
  PERSON_L2, URL은 URL_L2 포함). 구간 전체를 비교하므로 모델이 구간을 어디서 끝내느냐에 달려 있습니다. 「제주도」 구간은
  "제주"로 허용되지 않으니, 두 형태가 다 나오면 둘 다 넣으세요.
- ASCII 영문자만 대소문자를 무시하고(`KIM` = `kim`) 그 밖은 정규화하지 않습니다. 글과 항목을 NFC로 넘기세요. macOS에서
  내보낸 파일이나 PDF에서 복사한 글은 NFD 한글일 수 있고, 그러면 맞지 않습니다(`text.normalize("NFC")`).
- 정확한 형태만 맞습니다. 「박 지훈」과 「박지훈」은 다른 항목입니다. 「김」 같은 한 글자 항목은 김에 조사가 붙은
  낱말(「김은」, 「김도」)도 모두 가립니다.
- 표 모드에서 block 항목은 칸마다 맞추고 열 투표에 참여하지 않습니다. 허용된 구간은 열 투표 전에 빠집니다.
- 목록마다 항목 10,000개까지, 항목마다 1~64글자, 줄바꿈과 앞뒤 공백은 안 됩니다. 잘못된 목록은 모델 파일을 받기 전에
  `ListError`를 던집니다(`list`: "allow", "block", `listsId`는 "meta"; `index`; `reason`: `bad_shape`, `too_long`,
  `bad_text`, `empty`, `edge_space`, `unknown_label`, `too_many`, `label_conflict`, `in_both`, `bad_lists_id`). 오류에는
  항목 글이 들어가지 않습니다.
- `info.lists`에는 감사 기록용으로 중복을 합친 뒤의 항목 수와 `listsId`가 들어 있습니다. 항목 글에서 만든 값은 없습니다.

## 표

```ts
redactor.detectTable(text: string, table?: {
  delimiter?: "auto" | "," | "\t" | ";" | "markdown";  // 기본 "auto"
  header?: boolean;            // 첫 행이 머리글 행; 기본 true
  vote?: boolean;              // 열 투표; 기본 true
  context?: "none" | "header"; // "header": 값이 서로 다른 열의 칸을 "<머리글>: <값>"으로도 읽음; 기본 "none"
}): Promise<Span[]>
redactor.redactTable(text: string, table?: TableOptions, options?: RedactOptions): Promise<{ text: string; spans: Span[] }>
```

표 모드는 CSV, TSV, 세미콜론 구분, 마크다운 파이프 표를 한 텍스트로 읽고(구분자를 주지 않으면 자동 감지, 따옴표 필드는 RFC 4180),
모든 구간을 칸 값 안으로 자릅니다. 구분자, 따옴표, 줄바꿈은 가리지 않으므로 결과도 올바른 표입니다. `vote`가 켜져 있으면, 짧은 값으로
된 열에서 모델이 비어 있지 않은 칸의 80% 이상(4칸 이상; 비어 있지 않은 칸이 5개 미만인 열은 투표하지 않음)을 이름, 이메일,
전화번호, 사용자 이름으로 찾을 때 그 열 전체를 가립니다. 이 구간의 source는 `"table"`입니다. 긴 글 열(값의 중앙값이 40자 또는
4단어 초과)은 투표하지 않고, 자리 표시 값(`N/A`, `-`, `none`, `null`, `없음`)은 건너뛰며, 이메일 열은 `@`, 전화 열은 숫자 6개
이상이 있는 값만 가립니다. 머리글 행은 모델이 찾은 개인정보만 가리고 투표에는 넣지 않습니다. `context: "header"`이고 머리글 행이
있으면, 비어 있지 않은 값의 80% 이상이 서로 다른 열의 비어 있지 않은 데이터 칸을 `"<머리글>: <값>"`으로 한 번 더 따로 읽어
그 구간을 더합니다. 이름·연락처·ID 열을 덜 놓치는 대신 개인정보가 없는 칸을 더 가리고, 그런 칸마다 모델을 한 번 더 부릅니다.
표가 아닌 텍스트는 `detect`와 똑같은 구간을 냅니다. 직접 만든 `replace`는 구분자, 따옴표, 줄바꿈을 넣으면 안 됩니다.

```ts
const csv = "name,email,plan\nKim Minsu,kim@example.com,Pro\nLee Jiwon,lee@example.com,Free";
const { text } = await redactor.redactTable(csv);
// "name,email,plan\n[PERSON],[EMAIL],Pro\n[PERSON],[EMAIL],Free"
await redactor.redactTable(tsv, { delimiter: "\t", vote: false }, { mode: "mask" });
```

설문·양식 내보내기에서 표 모드는 이름·성이 나뉜 열의 이름을 많이 놓칩니다. `context: "header"`를 쓰면 놓침이 약 4분의 1로
줄고, 개인정보가 없는 칸은 2.3~3.7점 더 가립니다. 재현율이 먼저라면 이 옵션을 쓰세요. 측정값:
[저장소 README](https://github.com/newmean-ai/pii/blob/main/README.ko.md#표csv-tsv).

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
