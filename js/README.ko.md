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

사람은 가리고 회사·가게 이름은 남기려면 `ORG`를 빼세요. 아는 가게인데 모델이 사람으로 읽는 이름(주인 이름을 딴 가게)은 허용
목록에 넣습니다([목록](#목록)):

```ts
import { createRedactor, LABELS } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium", labels: LABELS.filter((l) => l !== "ORG"), allow: [{ text: "Wendy's", label: "PERSON" }] });
(await redactor.redact("Customer Maria Lopez disputes a charge from Jo Malone London and one from Tiffany & Co.")).text;
// "Customer [PERSON] disputes a charge from Jo Malone London and one from Tiffany & Co."
```

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
  내보낸 파일이나 PDF에서 복사한 글은 NFD 한글일 수 있고, 그러면 맞지 않습니다(`text.normalize("NFC")`). 이 규칙은
  목록의 것이고, vault는 아는 값을 더 많은 표기에서 찾습니다([아래](#아는-값을-다시-찾는-법)).
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

## Vercel AI SDK

프롬프트가 외부 모델에 가기 전에 가립니다. `@newmean-ai/pii/ai-sdk`는 language model middleware이며, `ai` 패키지는
이 패키지의 의존성이 아닙니다.

```ts
import { generateText, wrapLanguageModel } from "ai";
import { openai } from "@ai-sdk/openai";
import { piiMiddleware } from "@newmean-ai/pii/ai-sdk";

const model = wrapLanguageModel({ model: openai("gpt-6.1"), middleware: piiMiddleware() });
await generateText({ model, prompt: "김민수에게 010-1234-5678로 전화해 줘" });
// 제공자는 "[PERSON]에게 [PHONE]로 전화해 줘"를 받습니다
```

```ts
piiMiddleware({
  redactor,                    // 이미 만든 Redactor(또는 그 promise); 기본: 첫 호출 때 만듦
  create: { size: "medium" },  // redactor를 주지 않을 때 createRedactor에 넘길 옵션
  roles: ["user"],             // 가릴 역할: "user"(기본), "system", "assistant", "tool"(글로 된 도구 결과만)
  redact: { mode: "tag" },     // redact()와 같음: "tag", "mask", "blank" 또는 replace; vault와 함께면 { mode: "tag" }만
  specificationVersion: "v3",  // 기본 "v3"(AI SDK 6, 7); AI SDK 5는 "v2"
});
```

호출의 프롬프트만 바꾸므로(vault를 주면 호출의 일부 필드도: [아래](#메시지-밖의-필드)) `generateText`, `streamText`,
에이전트에서 똑같이 동작하고, 최근 글 256개를 기억해 대화에서
바뀌지 않은 앞부분은 한 번만 가립니다. 가린 값은 vault를 줄 때만 응답에서 되돌리며([아래](#값-되돌리기-vault)), 어떤 모델도 전부 찾지는 못합니다. 제공자가
보는 개인정보를 줄일 뿐, 하나도 새지 않는다고 보장하지는 않습니다.

## LangChain.js

`@newmean-ai/pii/langchain`은 `langchain` 1.5.1 이상용 에이전트 미들웨어입니다(`langchain`과 그에 딸린
`@langchain/core` 1.2.1 이상은 이 진입점에만 필요한 선택 peer 의존성).

```ts
import { createAgent } from "langchain";
import { piiMiddleware } from "@newmean-ai/pii/langchain";

const agent = createAgent({ model: "openai:gpt-6.1", middleware: [piiMiddleware()] });
// "김민수에게 010-1234-5678로 전화해 줘"는 모델에 "[PERSON]에게 [PHONE]로 전화해 줘"로 갑니다
```

`piiMiddleware({ redactor, create, roles, redact })`의 옵션은 위 AI SDK 미들웨어와 같고, `roles`는 `"user"`(기본),
`"tool"`, `"assistant"`입니다. 모델 호출을 감싸므로 모델은 가린 사본을 받고, 에이전트의 상태에는 쓴 그대로의 메시지가
남습니다. (LangChain의 `piiMiddleware`는 동기 탐지기만 받는데 이 패키지의 모델 호출은 비동기라서, 별도 미들웨어로
만들었습니다.)

## 에이전트: 에이전트가 읽는 것도 가리기

개인정보는 뒤의 턴에서도 모델에 들어옵니다. CRM 조회, 검색 결과, PDF가 사용자가 쓰지 않은 이름과 연락처를 가져옵니다.
사용자 메시지뿐 아니라 도구 결과도 모든 모델 호출마다 가리세요.

```ts
piiMiddleware({ redactor, roles: ["user", "tool"] });            // AI SDK, LangChain.js: 도구 결과도 가림
piiMiddleware({ redactor, vault, restoreTools: ["lookupCustomer"] }); // vault와 함께(아래): 기본으로 user, assistant, tool
```

AI SDK에서는 글, JSON, content 형식의 도구 결과를 가립니다(1.7.2부터). CRM 도구와 티켓 검색이 있는 전체 에이전트 예시:
[`examples/ai-sdk-agent.mjs`](examples/ai-sdk-agent.mjs). 모델은 파일을 읽지 않으니 먼저 글을 뽑고(PDF는 pdf.js 같은 추출기)
다른 글처럼 가리면 됩니다.

**어디에서 도는가.** 미들웨어와 `redact`는 앱(브라우저, 서버) 안에서, 글이 밖으로 나가기 전에 가립니다. 값을 되돌리는
vault도 여기서만 쓸 수 있습니다. 실행 중에 WebAssembly를 컴파일하지 못하게 하는 런타임(Cloudflare Worker 등)에서는 모델을
불러오지 못합니다. 게이트웨이에서는 Python 패키지의 LiteLLM guardrail이 프록시 뒤의 모든 앱을 한곳에서 가리지만,
값을 되돌리지 않습니다. 클라우드 공급자의 자체 개인정보 필터(Amazon Bedrock Guardrails 등)는 글이 공급자에 닿은 뒤에 돕니다.
이 패키지 옆에 켜 두어도 되고, 그러면 그 필터는 자리표시를 봅니다.

## 값 되돌리기 (vault)

1.7.2부터. vault는 번호가 붙은 자리표시와 원래 값의 대응표를 이 프로세스 안에만 둡니다. 그래서 모델의 답변과 도구 호출 인자에서
가린 값을 되돌릴 수 있습니다. 대응표는 어디로도 보내지 않습니다.

```ts
import { createRedactor, createVault } from "@newmean-ai/pii";

const vault = createVault();                                   // 사용자나 대화마다 하나
const { text } = await redactor.redact("Call Kim Minsu on 010-1234-5678", { vault });
// "Call [PERSON_1] on [PHONE_1]"
vault.restore("I called [PERSON_1].");                         // "I called Kim Minsu."
```

**자리표시는 쓰인 그대로일 때만 되돌립니다.** `[PERSON_1]`은 되돌리고, `[Person_1]`, `PERSON_1`, 번역된 꼴은 모델이 쓴 그대로
답변에 남습니다(다른 값이 들어가는 일은 없습니다). 시스템 프롬프트에 한 줄을 넣어 모델에게 그대로 옮겨 쓰게 하세요:

```text
Text in square brackets with a label and a number, such as [PERSON_n], stands for a value hidden from you. Copy it exactly as written, brackets included, wherever you refer to it.
```


- 한 vault 안에서 같은 값에는 늘 같은 자리표시가 붙습니다. 그래서 가린 대화가 턴마다 같게 유지되고 프롬프트 캐시도 계속 맞습니다.
- vault에 있는 값은 같은 글이든 뒤의 글이든 다시 나오면, 모델이 그 자리에서 놓치더라도 다시 가립니다(출처 `"vault"`인 구간).
  어떻게 찾는지는 [아래](#아는-값을-다시-찾는-법)에 있습니다.
- `vault.restorer()`는 스트림을 되돌립니다(조각 사이에 걸친 자리표시는 완성될 때까지 잡아 둡니다). `restoreValue`와
  `hideValue`는 JSON 값의 문자열에(키와 숫자는 그대로), `vault.restore(json, { json: true })`는 JSON 글에 씁니다.
- `vault.entries()`와 `createVault(entries)`로 요청 사이에 vault를 이어 쓸 수 있습니다. 미들웨어와 함께 쓸 때는
  `vault.toJSON()`과 `createVault(state)`를 쓰세요. 되돌린 답변을 모델이 어떻게 썼는지도 함께 담습니다. 둘 다 원래 값이
  들어 있으니 원문을 둘 수 있는 곳에만 저장하세요.
- `vault.clear()`는 vault를 비웁니다. 글을 가리는 도중(`redact` 중이거나 미들웨어가 모델 호출을 준비하는 중)에 비우면, 그
  호출은 비워진 vault에 값을 쓰는 대신 오류로 끝납니다. 비운 뒤에 도착한 답변은 그 호출 때의 값으로 되돌려집니다.
- **vault를 여러 사용자가 함께 쓰면 안 됩니다.** 한 사용자의 모델 답변에 든 자리표시가 다른 사용자의 값을 되살릴 수 있습니다.

두 미들웨어 모두 `vault`를 받습니다. 그러면 모델은 번호 붙은 자리표시를 받고, 답변 글은 값이 되돌려진 채 돌아옵니다(AI SDK는
스트리밍도). 기본 역할은 `user`, `assistant`, `tool`이 됩니다. 이제 앞선 답변과 도구 결과에 실제 값이 들어 있기 때문이며,
vault 덕분에 같은 자리표시로 다시 가려집니다. vault와 함께라면 `redact`는 `{ mode: "tag" }`만 됩니다. 다른 가리기 설정을 주면
미들웨어를 만들 때 오류가 납니다.

- **이번 호출의 자리표시만, 가릴 때의 값으로 되돌립니다.** 미들웨어가 그 모델 호출에 넣은 것만입니다. 앱이 값을 되돌려 받은 앞선
  답변(`restoreReasoning`이면 추론 글, `restoreTools`면 도구 인자도)을 다시 넘기면 미들웨어는 모델이 쓴 그대로 보내며, 그 안에서
  되돌렸던 자리표시도 이번 호출이 넣은 것으로 칩니다. `toJSON()`으로 되살린 뒤에도 같습니다. 메시지에 원래 있던 자리표시 같은
  글(사용자가 쓰거나 로그에 있던 "[PERSON_1]")은 "［PERSON_1]"(전각 괄호)로 바꿔 보내고 되돌리지 않습니다. 모델이 앞선 도구
  호출이나 답변에 써 넣었는데 앱이 되돌리지 않은 채 받은 자리표시도 세지 않습니다. 개발자가 직접 쓴 글(system 메시지,
  LangChain의 `systemPrompt`, 그리고 [메시지 밖의 필드](#메시지-밖의-필드)인 도구 설명, 스키마, 응답 형식, 공급자 옵션)에서는
  그런 글을 쓰인 그대로 둡니다. 프롬프트가 자리표시를 설명할 수 있게 하려는 것이고, 그러면 모델은 그것과 값의 자리표시를 가려
  보지 못하니 예시는 자리표시가 될 수 없는 꼴(`[PERSON_n]`)로 쓰세요. 퍼센트·HTML 꼴(`%5BPERSON_1%5D`, `&#91;PERSON_1]`)은 쓰인
  그대로 보냅니다. 모델이 퍼센트 인코딩한 자리표시는 되돌리지 않고, URL 안으로 되돌린 값을 퍼센트 인코딩하지도 않습니다.
- **도구 호출 인자는 `restoreTools`에 지정한 도구만 되돌립니다**(이름 하나, 이름 목록, 이름을 받는 함수). 다른 도구는 자리표시를
  받습니다. 모델이 읽는 글(웹 페이지, 문서, 도구 결과)에 숨은 지시가 있으면 모델이 도구 호출에 자리표시를 넣을 수 있으니, 값을
  봐도 되는 도구만 지정하세요. JSON 인자는 문자열 값만 되돌리고(키와 숫자는 그대로 두고, 올바른 JSON이 아닌 인자는 가린 채
  넘깁니다), 다음 호출에는 모델이 쓴 인자 그대로 보냅니다.
- 되돌리지 않는 것: LangChain content 블록 안의 도구 입력, `invalid_tool_calls`, `additional_kwargs`(도구는 `tool_calls`로
  돕니다), AI SDK 답변에서 글·추론·도구 호출 말고 다른 part.
- 앞선 답변과 도구 호출은 모델이 쓴 그대로 다시 보내고(미들웨어가 하나하나 기록해 두며 `toJSON()`에 함께 담깁니다) 모델이 한
  번 더 읽으므로, 모델이 직접 써 넣은 값도 가려집니다. 답변의 기록이 없으면(`entries()`만으로 되살린 vault, vault를 붙이기
  전의 답변) 아는 값을 그 글의 어디에서든, 더 긴 단어 안에서도 가립니다. 필요한 것보다 더 가릴 수는 있어도 덜 가리지는
  않습니다. 사용자 메시지와 도구 결과는 늘 받은 그대로 읽습니다.
- JSON 문서인 메시지나 도구 결과는 값마다 읽되, 문서 전체를 모델의 맥락으로 줍니다. 이스케이프("\u00e9")로 값이 숨지 않고 JSON도
  깨지지 않습니다. 모델이 앞서 쓴 JSON 답변과 도구 입력도 올바른 JSON으로 남고, 키가 같은 자리표시가 되어도 필드를 모두
  남깁니다. 찾은 숫자(JSON 숫자로 쓴 카드 번호나 전화번호)는 자리표시 문자열로 바뀝니다. 숫자는 어떻게 쓰였든 같은 숫자로
  보고(8.21023456789e11과 821023456789), 되돌릴 때는 풀어 쓴 십진 꼴로 돌아옵니다(1.25e1은 12.5, 지수는 64까지). 값은 키 옆에
  놓고 읽으므로("name: Kim Minsu") 모델이 그 값이 무엇인지 압니다. 키 자체는 그대로 두되, vault가 이미 아는 값은 키에서도
  숨깁니다(이메일 주소를 키로 쓴 맵). 두 키가 같은 자리표시가 되면 필드를 둘 다 남기고(`[PERSON_1]`, `[PERSON_1] #2`), 가리기
  전에 같던 키는 같게 남습니다.
- 그 밖의 글(JSON이 섞인 로그 한 줄, 코드 블록, 여러 줄의 JSON, 중간에 잘린 문서)은 통째로 읽습니다. 모델에게는 유니코드
  이스케이프를 푼 꼴로 줍니다("Jos\u00e9"는 "José"). vault가 이미 아는 값은 쓰인 그대로의 글(`C:\Users\u1234`의
  `u1234`)과 그 글의 다른 읽기에서도 찾습니다([아래](#아는-값을-다시-찾는-법)).
- **가린 것이 곧 보내는 것입니다.** vault를 주면 JSON이 제 나름의 꼴로 쓰는 호출 안의 값은 가리기 전에 공급자가 쓰는 평범한
  JSON으로 한 번만 만들고, 그 JSON을 가려서 보냅니다(1.7.2부터). 모든 값은 `JSON.stringify`가 읽는 방식대로 읽습니다. 곧 속성
  접근으로 읽으므로 getter가 실행되고 Proxy는 트랩이 답하며(디스크립터에 저장된 값이 아님), 필드와 항목마다 한 번씩 JSON의 순서로
  읽습니다. 클래스 인스턴스는 자기 열거 가능 필드로, `toJSON` 객체(그것을 가진 함수도)는 `toJSON()`이 내는 것으로(한 번만
  부르므로 두 번째에 다른 것을 쓰는 객체도 그것을 보낼 수 없음), 감싼 숫자·문자열·불리언은 그 값으로, `Date`는 ISO 글로,
  `NaN`과 `Infinity`는 `null`로, `Map`과 `Set`은 `{}`로, 바이트는 JSON이 쓰는 대로 만듭니다. getter가 있는 목록이나
  객체(인덱스의 getter도), 빈 칸이 있는 목록, JSON이 쓰지 않는 자기 필드에 getter가 있는 객체, Proxy(Node가 알아보는 것, 또는
  읽은 값이 저장된 값과 다른 것)는 한 번씩 읽은 항목과 필드로 새로 만들므로, 공급자의 writer가 읽는 것이 곧 가린 것입니다. 그런
  필드가 있는 LangChain 메시지와 AI SDK 도구, 응답 형식은 읽은 것으로 만든 사본을 넘깁니다. 도구 결과와 도구 입력, content
  part와 블록과 메시지의 도구 호출, 이 버전이 모르는 종류의 part와 블록, `additional_kwargs`, 도구 정의와 호출의 모든
  스키마(도구의 입력 스키마, 응답 형식, 그 안의 정의와 예시, `inputExamples`. 맨 위가 클래스 인스턴스여도), 공급자 옵션과 모델
  설정 어디서나 그렇습니다.
  바이트와 URL 객체는 파일 데이터 자리(AI SDK 파일 part의 `data`, LangChain 파일 블록의 `data`)에서만 그대로 둡니다.
- 한 호출의 어느 메시지에서 찾은 값은 그 앞의 메시지를 포함해 모든 메시지에서 가립니다. `roles`에서 뺀 역할과 시스템
  프롬프트에서도 vault가 아는 값은 숨깁니다.
- 추론 글은 `restoreReasoning: true`일 때만 되돌립니다. 모델이 쓴 추론 글은 쓴 그대로 다시 보냅니다(공급자가 서명하기
  때문입니다). vault가 나중에 배운 값이 들어 있어도 그렇습니다. 그 값은 모델이 썼으므로 공급자가 이미 가진 값입니다.
  JSON 답변(구조화 출력)은 문자열 값 안에서 되돌리므로 올바른 JSON으로 남습니다. 모델이 코드 블록 안에 써서
  `extractJsonMiddleware`나 고치는 함수가 나중에 벗겨 내는 JSON도 그렇습니다. 스트림에서는 호출이 JSON을 요청했을 때
  (`streamObject`) 그렇게 하며, 자리표시는 도착하는 대로 되돌립니다(모델이 JSON 이스케이프로 쓴 자리표시는 그대로
  둡니다).
- AI SDK에서 사람이 도구 호출을 거절하며 적은 이유도 밖에서 온 다른 글처럼 가립니다.
- 호출을 보내기 직전에 메시지의 모든 글 필드를 모델 없이 한 번 더 살펴, 어떤 종류의 part에 있든 vault가 아는 값을 숨깁니다.
  프레임워크 자신의 part와 블록에서만 건드리지 않는 것: 식별자와 이름(`id`, `name`, `toolName`), 파일 참조(`url`, `fileId`, AI
  SDK 7 `originalUrl`, URL로 준 AI SDK 파일 part의 `data: "https://..."`. 그런 URL 안의 아는 값은 쓰인 그대로 나갑니다), base64
  데이터, `providerMetadata`, 서명된 추론 글. `providerOptions`는 아래 옵션처럼 읽습니다. 이 버전이 모르는 part·블록 종류에서는
  `type`과 `providerOptions` 말고는 `providerMetadata`까지 모든 필드를 살핍니다. part 안의 데이터에서는 모든 문자열과 키를 보며,
  `data` 아래 객체나 목록도 그렇습니다. 통째로 아는 값인 `data` 문자열은 숨깁니다. data URL이나 base64처럼 보이는 64자 이상의 문자열은 그대로 두되, 쓰인 그대로든 그 읽기 중 하나에서든
  6자 이상의 아는 값이 단어 경계에 있으면 가립니다(`Kim+Minsu+asked+…`). 글로 바로 넣은 파일은 밖에서 온 다른 글처럼 읽습니다.
- **가리기를 끝낼 수 없는 호출은 실패하고, 그 호출의 어떤 것도 보내지 않습니다.** 탐지기가 실패할 때, 가리는 도중에 vault를
  비울 때, 읽을 수 없는 입력(자기 자신을 가리키는 객체, 아주 깊이 중첩된 데이터)이 있을 때, JSON이 쓸 수 없는 값(BigInt,
  예외를 던지는 `toJSON()`이나 getter나 Proxy 트랩, 폐기된 Proxy. 오류에 그 자리가 나옵니다)이 있을 때, 호출 자신의 AI SDK
  어댑터가 문자열만 받는 자리에 vault가 아는 숫자가 있을 때(아래), LangChain.js 도구를 함수 정의로 쓸 수 없을 때입니다. 도구 입력의 `NaN`은 JSON이 쓰는 대로 `null`로 보냅니다.
- 스스로 모델을 부르는 다른 미들웨어(요약 등)는 에이전트의 가리지 않은 상태를 읽습니다. 이 미들웨어는 자신을 거치는 호출만
  가립니다.
- **맨 뒤에 두세요.** 이 미들웨어는 받은 요청을 가리므로, 그 뒤에 둔 미들웨어가 넣는 것(동적 시스템 프롬프트, 검색해 온
  맥락)은 가리지 않은 채 모델에 갑니다. `wrapLanguageModel`과 `createAgent`의 `middleware` 목록 끝에 두세요.

```ts
const vault = createVault();
const middleware = piiMiddleware({ redactor, vault, restoreTools: ["lookup_customer"] });
const model = wrapLanguageModel({ model: openai("gpt-6.1"), middleware });                    // AI SDK
const agent = createAgent({ model: "openai:gpt-6.1", tools, middleware: [piiMiddleware({ redactor, vault, restoreTools: ["lookup_customer"] })] }); // LangChain.js
```

LangChain.js에서는 모델 호출 중 콜백으로 스트리밍되는 토큰에는 여전히 자리표시가 보입니다. 에이전트가 보관하는 메시지에는 값이
들어 있습니다. vault를 주면 content 블록 안의 도구 입력과 도구 결과(Anthropic `tool_use`·`tool_result`, OpenAI
`function_call`)도 가립니다. JSON 글은 값마다, JSON 데이터 행은 모든 문자열 값을 가립니다. LangChain과 공급자의 블록 종류만
블록으로 봅니다. 블록에서는 글, 도구 입력, 도구 결과를 모델이 읽고, 나머지 필드에서는 vault가 이미 아는 값만 숨깁니다(위의
보내기 직전 확인). 도구가 돌려주거나 사용자가 보낸 것에서는 그 종류의 내용이 있는 객체만 블록입니다(`text` 블록은 `text`,
`file`·`image`는 `url`이나 base64 데이터나 파일 id, `document`는 `source`, `json` 블록은 `json`). 도구가 돌려준 것에서는
블록의 `id`와 `name`도 살피고, 사용자 메시지에서는 프레임워크의 다른 블록처럼 블록의 `id`를 쓰인 그대로 둡니다(`name`은
살핍니다). 도구가 자기 `type`을 붙여 돌려준 행(`{ type: "contact", name, id }`)과, 블록의 종류를 달았지만 그 내용이 없는
행(저장소 API의 `{ type: "file", id, name, owned_by }`)은 데이터입니다. 모델이 읽고, 모든 필드와 키를 셉니다. 도구가 블록으로
돌려준 클래스 인스턴스나 `toJSON` 객체는 그것이 내는 JSON으로 보내고, 변환기가 이름으로 읽는 블록 필드를 클래스가 getter(`get
text()`)로 주면 그것도 담습니다. `additional_kwargs`도 데이터이며, 그 안의 `tool_calls`와 `function_call`만 예외입니다. 다른
종류의 메시지(`ChatMessage`, `FunctionMessage`)도 밖에서 온 글처럼 가립니다. HumanMessage나 ChatMessage의 `name`에 든 아는 값은
괄호 없는 자리표시(`PERSON_1`, 공급자가 그 자리에 받는 꼴)로 보내며 되돌리지 않습니다. 공급자는 그 자리에 글자, 숫자, `_`,
`-`만 받으므로, 두 단어 이상인 아는 값은 이어 쓴 꼴로도 찾습니다(`Kim Minsu`에 대해 `Kim_Minsu`, `kim-minsu`, `KimMinsu`).
한 단어인 값은 쓰인 그대로 찾습니다. 메시지나 part의 `providerOptions`에 든 OpenAI 호환 `name`도 같게 씁니다. 에이전트의
`structuredResponse`에는 이번 호출의 값을 되돌립니다. 1.7.2부터 AI SDK 미들웨어는 `"tool"` 역할일 때 JSON 도구 결과 안의
글자와 content 도구 결과의 글 항목도 가립니다(1.6은 그대로 두었음).

### 메시지 밖의 필드

vault를 주면 호출의 다음 필드에서도 vault가 아는 값을 모델 없이 숨깁니다. 도구 설명, 스키마의 설명·제목·예시,
`inputExamples`, 공급자 도구 인자, 응답 형식, `providerOptions`(호출, 도구, 메시지, part의 것), LangChain.js의
`modelSettings`. 이 옵션은 데이터입니다. 그 안의 모든 문자열과 키(`openai.user`, `promptCacheKey`, 저장된 프롬프트의
`variables`, `metadata`)와 아는 값인 숫자를 숨깁니다. 예외는 정확한 경로이며, 각각 한 공급자가 식별자, 자격 증명, 숫자
설정, JSON 스키마를 정한 자리입니다(1.7.2부터, 출시 관문 8: 이름은 그 경로에서만 지킵니다). 그 경로의 값은 쓰인 그대로
두고(그 경로 위의 키도), 같은 이름도 다른 자리(한 단계 아래, 다른 공급자나 미들웨어가 모르는 공급자의 이름 아래,
`extra_body` 안, 템플릿 변수, 어느 공급자도 정하지 않은 설정 필드)에서는 데이터이며 그 모든 키와 값을 셉니다. "`*`"는 아무
키나 인덱스 하나, "아래 전부"는 그 아래의 모든 것입니다.

- AI SDK 호출과 도구의 `providerOptions`: `openai`와 `azure`의 `previousResponseId`, `conversation`(또는
  `conversation.id`), `promptCacheOptions.comparisonResponseId`, `prompt.id`, `prompt.version`, `logitBias.*`,
  `allowedTools.toolNames.*`. `anthropic`의 `container`(또는 `container.id`와 `container.skills.*.type`, `.skillId`,
  `.version`), `mcpServers.*.type`, `.name`, `.url`, `.authorizationToken`, `.toolConfiguration.allowedTools.*`,
  `fallbacks.*.model`. `google`과 `vertex`의 `cachedContent`. `gateway`의 `order.*`, `only.*`, `models.*`, `byok`(아래
  전부). `openrouter`의 `provider`(아래 전부), `models.*`.
- AI SDK 메시지와 part의 `providerOptions`: `openai`와 `azure`의 `itemId`, `reasoningEncryptedContent`,
  `additionalTools.*.type`, `.name`. `anthropic`의 `signature`, `redactedData`, `toolChanges.*.type`, `.toolName`,
  `.tool.type`, `.tool.id`, `.tool.name`. `bedrock`의 `signature`, `redactedData`. `google`과 `vertex`의
  `thoughtSignature`.
- AI SDK 공급자 도구의 `args`, 날짜를 뺀 도구 id로(`anthropic.web_search_20250305`는 `anthropic.web_search`):
  `openai.mcp`의 `serverLabel`, `serverUrl`, `connectorId`, `authorization`, `headers.*`, `allowedTools.*`,
  `allowedTools.toolNames.*`, `requireApproval`, `requireApproval.never.toolNames.*`. `openai.file_search`의
  `vectorStoreIds.*`. `openai.code_interpreter`의 `container`(또는 `container.fileIds.*`). `openai.web_search`와
  `openai.web_search_preview`의 `filters.allowedDomains.*`, `filters.blockedDomains.*`. `openai.image_generation`의
  `model`, `inputImageMask.fileId`, `inputImageMask.imageUrl`. `openai.shell`의 `environment.type`, `.containerId`,
  `.fileIds.*`, `.networkPolicy.allowedDomains.*`, `.networkPolicy.domainSecrets`(아래 전부). `openai.custom`의
  `name`. `anthropic.web_search`와 `anthropic.web_fetch`의 `allowedDomains.*`, `blockedDomains.*`. 다른 도구의 인자는
  모두 데이터입니다.
- LangChain.js `modelSettings`와 모델 자신의 요청 필드(아래): `model`, `models.*`, `provider`(아래 전부),
  `previous_response_id`, `conversation`(또는 `conversation.id`), `prompt.id`, `prompt.version`, `container`(또는
  `container.id`와 `container.skills.*.type`, `.skill_id`, `.version`), `mcp_servers.*.type`, `.name`, `.url`,
  `.authorization_token`, `.tool_configuration.allowed_tools.*`, `cachedContent`, `cached_content`, `tools.*.type`,
  `tools.*.name`, `tools.*.function.name`, `functions.*.name`, `response_format.type`,
  `response_format.json_schema.name`, `text.format.type`, `text.format.name`, `tool_choice`(아래 전부),
  `function_call`(아래 전부), 내장 도구의 `tools.*.server_label`, `.server_url`, `.connector_id`, `.authorization`,
  `.headers.*`, `.allowed_tools`(아래 전부), `.require_approval`(아래 전부), `.vector_store_ids.*`, `.container`,
  `.container.file_ids.*`, `.filters.allowed_domains.*`, `.filters.blocked_domains.*`, `.allowed_domains.*`,
  `.blocked_domains.*`, 그리고 `logit_bias.*`, `logitBias.*`, `headers.*`.
- 숫자 그대로 두는 숫자: `maxTokens`, `max_tokens`, `maxOutputTokens`, `max_output_tokens`, `maxCompletionTokens`,
  `max_completion_tokens`, `max_tokens_to_sample`, `temperature`, `topP`, `top_p`, `topK`, `top_k`, `minP`, `min_p`,
  `seed`, `frequencyPenalty`, `frequency_penalty`, `presencePenalty`, `presence_penalty`, `repetitionPenalty`,
  `repetition_penalty`, `n`, `candidateCount`, `candidate_count`, `logprobs`, `topLogprobs`, `top_logprobs`,
  `budgetTokens`, `budget_tokens`, `thinkingBudget`, `thinking_budget`, `maxToolCalls`, `max_tool_calls`, `maxRetries`,
  `max_retries`, `timeout`, `dimensions`, `numCtx`, `num_ctx`, `numPredict`, `num_predict`가 미들웨어가 아는 공급자 바로
  아래(AI SDK: `openai`, `azure`, `anthropic`, `google`, `vertex`, `bedrock`, `mistral`, `groq`, `xai`, `deepseek`,
  `cohere`, `perplexity`, `fireworks`, `togetherai`, `cerebras`, `deepinfra`, `gateway`, `openrouter`,
  `openaiCompatible`, `ollama`, `huggingface`, `baseten`, `vercel`, `alibaba`, `moonshotai`), `modelSettings`의 맨 위와
  그 `generationConfig`·`generation_config` 아래에 있을 때. 그리고 `anthropic.thinking.budgetTokens`,
  `anthropic.taskBudget.total`, `.remaining`, `anthropic.fallbacks.*.max_tokens`, `google`과 `vertex`의
  `thinkingConfig.thinkingBudget`, `bedrock.reasoningConfig.budgetTokens`, 설정의 `thinking.budget_tokens`,
  `thinking.budgetTokens`, `thinkingConfig.thinkingBudget`, `thinking_config.thinking_budget`, 도구의
  `openai.file_search` `maxNumResults`, `ranking.scoreThreshold`, `openai.image_generation` `outputCompression`,
  `partialImages`, `anthropic.web_search`와 `anthropic.web_fetch`의 `maxUses`, `anthropic.web_fetch`의
  `maxContentTokens`, `anthropic.computer`의 `displayWidthPx`, `displayHeightPx`, `displayNumber`. 그 경로의 문자열은
  여전히 읽습니다.
- JSON 스키마는 위의 스키마처럼 읽습니다(속성 이름과 리터럴 제약은 쓰인 그대로. Zod나 Standard Schema 객체는 프레임워크가
  쓰므로 그대로 둠): 설정의 `response_format.json_schema.schema`, `text.format.schema`, `tools.*.parameters`,
  `tools.*.input_schema`, `tools.*.function.parameters`, `functions.*.parameters`, `responseSchema`, `response_schema`,
  `responseJsonSchema`, `response_json_schema`, `output_format.schema`, `outputFormat.schema`,
  `output_config.format.schema`, `outputConfig.format.schema`, `generationConfig.responseSchema`,
  `generationConfig.responseJsonSchema`, `generation_config.response_schema`, `generation_config.response_json_schema`.
  메시지의 `openai.additionalTools.*.parameters`와 `anthropic.toolChanges.*.tool.inputSchema`. `openai.tool_search`의
  `parameters`.

호출 자신의 AI SDK 어댑터가 문자열만
받는 자리(OpenAI의 `user`·`safetyIdentifier`·`promptCacheKey`, OpenAI Chat Completions의 `metadata`, Anthropic의
`metadata.userId`. 어댑터는 `wrapLanguageModel`이 감싼 모델입니다)에 아는 값인 숫자가 있으면 호출은 아무것도 보내지 않고
실패합니다. 어댑터는 거기서 숫자를 거절하는데, 자리표시 문자열은 그 요청을 보내지는 요청으로 바꾸기 때문입니다. 다른 자리에서는
그런 숫자를 자리표시로 보냅니다. `modelSettings`에 든 LangChain
자신의 호출 옵션(`signal`, `callbacks`, `configurable` 등)은 보내지 않으므로 그대로 둡니다. 도구 이름(AI SDK의
`tools[].name`과 `toolChoice`), 스키마 속성 이름, `enum`·`const`·`default`·`pattern`, 호출의 헤더, `providerMetadata`는
쓰인 그대로 둡니다. 이 필드에서 새 값을 찾지는 않으니
개인정보를 넣지 마세요. 호출의 메시지에 자리표시가 있으면 JSON 답변의 스키마는 `format`과 `pattern` 없이 공급자에게
갑니다(날짜나 이메일 필드는 `[DATE_OF_BIRTH_1]`을 담을 수 없음). 답변은 값을 되돌린 뒤 원래 스키마로 검사합니다.
LangChain.js에서 설명이나 스키마에 아는 값이 든 도구는 값을 숨긴 함수 정의로 모델에 가고, 에이전트는 자기 도구를 그대로
돌립니다. 그 호출에서는 공급자별 부가 설정(`tool(..., { extras })`)을 보내지 않습니다. LangChain 도구가 아닌 객체로 준
정의(클래스 인스턴스, getter)와 Zod 스키마를 담은 도구 매개변수 `{ name, description, schema }`도 같게 읽습니다. 다른
LangChain 사본(이 패키지의 ES 모듈 빌드 옆의 CommonJS 빌드)이 만든 응답 전략은 스키마를 숨긴 채 전략으로 남깁니다.
`responseFormat`은 맨 위 제목을
이름으로 그대로 둡니다. `responseFormat`의 도구와 스키마는 에이전트가 모델에 묶는 자리(공급자가 받는 바인딩)에서 숨기고,
에이전트는 이번 호출의 값을 되돌린 구조화 응답을 읽은 뒤 원래 스키마로 검사합니다. langchain 1.2.20은 미들웨어에 응답
형식을 주지 않는데, 거기서도 이렇게 숨깁니다. 이를 위해 복사할 수 없는 모델(`bindTools`가 없는 모델)은 응답 형식에 아는
값이 있으면 호출을 실패시킵니다.

요청에 들어가는 모델 자신의 필드도 데이터입니다(1.7.2부터). LangChain.js 채팅 모델이 요청 매개변수로 알리는 것
(`invocationParams`, 트레이스에 남는 것도 이것)으로 ChatOpenAI의 `user`, `promptCacheKey`, `modelKwargs`(`metadata`,
`safety_identifier`, 저장된 프롬프트의 `variables`)가 있고, 공급자 패키지가 요청에 그대로 펼쳐 넣는 kwargs 객체
(ChatAnthropic의 `invocationKwargs`, 그 안의 `metadata.user_id`)도 그렇습니다. 값이 든 vault가 있으면 에이전트는 이
필드에 아는 값이 있든 없든 호출마다 모델 복사본을 받습니다. 각 필드는 공급자가 쓰는 JSON으로 한 번만 읽고(`toJSON`은 한 번
부르고, getter도 한 번 읽고, 클래스 인스턴스와 Proxy는 읽은 것으로 다시 만듭니다), 복사본은 그것을 위 모델 설정과
같은 규칙과 식별자 예외(저장된 프롬프트의 `id`, `previousResponseId`, Anthropic의 `container`)로 아는 값을 숨겨 보냅니다.
복사본이 만드는 모델(`bindTools`, `withConfig`, `initChatModel`이 호출 때 만드는 모델)도 복사하며, 원래 모델은 바꾸지
않습니다. 복사본은 호출 전에 확인합니다. 숨겨 보낼 수 없는 필드나 JSON으로 쓸 수 없는 필드가 있으면 아무것도 보내기 전에
그 필드 이름을 밝히는 오류로 호출을 실패시킵니다. 복사할 수 없는 모델(필드를 `#` 비공개 필드에 둔 클래스)은 요청
매개변수를 알릴 때, 아무것도 보내기 전에 호출을 실패시킵니다.

### 아는 값을 다시 찾는 법

여섯 패키지 모두에서, vault를 준 `redact`, `hide`, `hideValue`, 미들웨어가 같게 찾습니다(공유 사례:
`python/tests/vault_cases.json`).

- 3자 이상입니다. 띄어 쓰지 않는 문자 체계(확장 J까지의 한자, U+1B000 이후도 포함한 가나, 태국 문자, 라오, 크메르, 미얀마,
  타이 탐, 발리, 자와) 글자가 하나라도 있거나 두 글자가 모두 한글 음절이면(`이준`) 2자도 찾습니다. 그보다 짧은 값은 애플리케이션이
  넣은 것이면 JSON 값, 키, 도구 인자 전체일 때, `hideValue`가
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

미들웨어는 다음도 찾습니다.

- 다르게 쓴 공백. 공백은 어떤 공백 문자와도 맞습니다(탭, NBSP, U+3000, 여러 칸). 줄바꿈은 쉼표와 공백, 또는 들여 쓴 줄과도
  맞습니다. 쉼표와 공백은 줄바꿈, 또는 공백 하나와도 맞습니다(`12 Main St Seoul`). 다른 구두점(공백 없는 `,`, `;`)은 다른
  값이고, 값의 앞뒤 공백, 값 안의 폭 없는 공백·결합자·소프트 하이픈, 공백이 없던 값에 넣은 공백(`김 민수`)도 그렇습니다.
  `\v`와 `\f`는 공백이지 줄바꿈이 아닙니다.
- 다른 표기의 숫자. 자릿수마다 견줍니다(지수는 64까지). `821023456789`에 대한 `8.21023456789e11`, 끝의 0, `E`. 앞에 0이 붙으면
  (`04524`) 숫자가 아닌 글자열이라 쓰인 그대로만 찾습니다. 자리 묶음(`4,524`, `4 524`)과 구두점이 다른 전화번호(`010-1234-5678`에
  대한 `01012345678`)는 다른 값입니다. 부호는 자리표시 밖에 둡니다. 아는 `4524`에 대해 `-4524`와 `-4.524e3`은
  `-[ID_NUMBER_1]`이 되고, JSON 숫자 `-4524`는 문자열 `"-[ID_NUMBER_1]"`이 됩니다.
- ISO 시각의 앞부분(`1990-01-02T00:00:00.000Z`, `Date`가 내는 꼴)과 Python 날짜 repr(`datetime.date(1990, 1, 2)`)에 든 아는 ISO
  날짜(`1990-01-02`). 다른 순서와 구분자(`01/02/1990`, `2 Jan 1990`)는 다른 값입니다.
- 글의 다른 읽기. JSON 이스케이프(줄바꿈을 `\n`으로 쓴 것, `CORP\kim`을 쓴 `CORP\\kim`, `01\/02\/1990`, JSON 문자열 안의
  JSON까지), UTF-8로 읽는 퍼센트 표기(`kim%40example.com`), 공백으로 읽은 `+`와 그대로인 `+`(`name=Kim+Minsu`), Python repr
  이스케이프(`Kim\xa0Minsu`, UTF-8로 읽는 `b'Jos\xc3\xa9'`, `\U00020bb7`). 이들은 네 단계 깊이까지 서로 겹칠 수 있습니다(폼 필드
  안의 JSON, JSON 안의 폼 필드). 글의 다른 곳에 이스케이프가 얼마나 있든 네 단계 안의 읽기를 모두 찾고, vault가 더 배우는 것이
  없을 때까지 다시 찾습니다.
- 쓰인 그대로 읽는 것. 이런 꼴의 값은 모델이 찾은 곳에서만 가려집니다. HTML 엔티티와 태그(`&#233;`, `<br>`), base64,
  quoted-printable과 MIME 인코딩 단어, 겹쳐 쓴 따옴표(SQL, CSV), Markdown과 셸의 역슬래시, `%uXXXX`, punycode 호스트 이름
  (`xn--`), 다른 문자 집합(EUC-KR)과 UTF-8이 아닌 바이트, 다섯 번째 단계의 인코딩.

### 한계

- **미들웨어 하나에 vault 하나.** 한 번 만들어 여러 대화에 쓰는 감싼 모델이나 LangChain.js 에이전트는 vault를 함께 씁니다.
  미들웨어는 대화마다 만드세요. vault는 LangGraph 체크포인터에 들지 않습니다. `vault.toJSON()`을 대화 옆에 직접 저장하세요.
- vault에는 크기 제한이 없습니다. 미들웨어는 가장 최근에 쓴 답변과 도구 호출 4,096개의 기록(그보다 오래된 것은 느슨하게 찾아
  더 가릴 뿐 덜 가리지 않습니다)과 최근 글 256개에 대한 모델의 구간을 기억합니다.
- 구간은 vault 세대와 `sensitivity`(`detect`가 불린 때의 값)마다 따로 두고, vault는 호출마다 다시 적용합니다. 그래서 나중에 배운
  값이 전에 가린 글도 덮습니다. 한 글에서 배운 값은 첫 호출부터 그 글의 어디에서든 가리고, 모델이 아는 값이나 더 큰 합친 구간
  안에서 찾은 것도 배웁니다. 모델이 찾은 값은 구간이 값 둘레에서 함께 잡은 것을 빼고
  배웁니다. 공백, 문장의 마침표와 쉼표, 괄호와 따옴표(`I'm Kim Minsu.`에서 `Kim Minsu`), Python bytes 리터럴의 `b'`, 생년월일 ISO
  타임스탬프의 시각입니다.
- vault와 함께라면 redactor에 `detect()`가 있어야 합니다. 없으면 분명한 메시지로 실패합니다.
- 허용·차단 목록은 vault와 함께도 동작합니다. 모델과 목록은 쓰인 그대로의 글을 읽고, 되돌린 답변을 다시 보낼 때도 둘이 다시
  읽습니다.
- 미들웨어가 돌려주는 것(도구 인자, 내보낸 상태)은 사본입니다. 그것을 바꿔도 vault는 바뀌지 않습니다.
- vault를 준 `applyRedaction`과 `redactTable`은 받은 구간에 번호만 붙이고, 아는 값을 찾지는 않습니다.
- 공급자에 보내지 않으므로 가리지 않는 것: LangChain의 `response_metadata`, `usage_metadata`, ToolMessage의 `artifact`. 도구
  데이터의 `Map`과 `Set`은 JSON이 쓰는 대로(`{}`) 보내고, BigInt는 `JSON.stringify`처럼 호출을 실패시킵니다(앱이
  `BigInt.prototype`에 `toJSON`을 주었다면 그것이 쓰는 대로 보냅니다).
- 자리표시는 `[<계열>_<번호>]`입니다. 계열은 32자까지(허용되지 않는 글자마다 `_`), 번호는 ASCII 숫자 1~12자리이고, 더 긴 비슷한
  꼴은 평범한 글입니다. PERSON_L2는 PERSON의 번호를 함께 씁니다. 모델이 지어낸 번호는 쓰는 번호와 백만 이내일 때 건너뛰며
  `toJSON()`에 남습니다. 번호(999,999,999,999까지)를 다 쓴 계열은 `[PERSON__1]`로 이어지므로, vault가 내보낸 상태는 언제나 다시
  읽힙니다. 빈 값에는 자리표시를 붙이지 않습니다.
- vault의 단어 글자는 런타임의 유니코드 데이터를 씁니다(`wholeWords`는 고정 표를 쓰지만 vault는 아닙니다). Node 25는 유니코드
  17입니다. 패키지는 Node 버전을 정하지 않으며, 더 오래된 런타임은 값 옆의 새 글자를 다르게 볼 수 있습니다.
- 이번 배포에서 돌린 것: AI SDK 5.0.290, 6.0.332, 7.0.173, 그리고 langchain 1.6.6(`@langchain/core` 1.4.1,
  `@langchain/openai` 1.8.4, `@langchain/anthropic` 1.6.5)과 1.5.1(`@langchain/core` 1.2.1, `@langchain/openai` 1.5.3,
  `@langchain/anthropic` 1.5.1)의 LangChain.js `createAgent`. LangChain.js 시험과 공유 코퍼스는 1.6.8에서도 돌렸습니다.
  peer 범위는 langchain 1.5.1과 `@langchain/core` 1.2.1부터입니다(1.6은 1.0.0 이상). 1.5.1부터 1.6.8까지 모든 릴리스가
  구조화 출력 확인, 타입 검사, LangChain.js 시험을 통과합니다. 그보다 이른 릴리스는 지원하지 않습니다. 1.0.1부터 1.1.2까지는 모델 호출을 감싸는
  미들웨어가 하나라도 있으면 `responseFormat`을 준 에이전트가 실패합니다(LangChain 자체의 검사). 1.1.4부터 1.2.22까지는
  에이전트가 공급자 전략의 Anthropic 스키마를 `output_format`에 쓰는데, 이 미들웨어는 거기서 `format`과 `pattern`을
  떼지 않습니다(값은 숨김). 1.2.3부터 1.5.0까지는 이 미들웨어가 있든 없든 에이전트의 상태를 JSON으로 쓰면 도구가 클래스
  인스턴스로 돌려준 것이 빠집니다.
- **시간.** vault의 일은 글 길이 × 아는 값 수에 비례해 늡니다. 아는 값이 수천 개인 큰 데이터는 더 느립니다. 아는 값
  2,000개로 JSON 행 2,000개(181 KB)에 호출마다 2.0~2.1초, 행 4,000개(362 KB)에 4.0~4.2초, 산문 1 MB에 4.7~4.8초, 겹친
  인코딩(퍼센트·폼 인코딩, 유니코드 이스케이프, JSON 안의 JSON) 512 KB에 9.1~9.2초가 걸렸습니다(vault 일만, 모델 없음.
  Apple M5 Pro와 Node 25에서 두 차례, 각각 세 번 부른 중앙값). 모든 모양에서 아는 값 2,000개와 1 MB를 호출당 1초 안에 하는
  더 빠른 찾기는 1.7.3에서 합니다.

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
