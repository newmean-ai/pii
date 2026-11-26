[English](README.md) | [한국어](README.ko.md) | 繁體中文

# @newmean-ai/pii

**在送往 OpenAI、Anthropic 或任何 LLM 之前遮蔽個資。** **在瀏覽器或裝置內**偵測並遮蔽文字中的個人資料（姓名、電子郵件、電話號碼、地址、身分識別碼等），讓原始文字不離開裝置。搭配 vault 時，模型回答與允許的工具呼叫會還原原值（1.7.2 起）。

- 54 種語言、11 種標籤，另有 2 種用於間接識別資訊的第 2 層標籤（IP 位址會被模型辨識為 `ID_NUMBER`）；可選用的
  模式比對層（電子郵件、URL、卡號／帳號、電話、韓國居民登錄號碼）預設關閉
- 四種模型大小：**nano 2.6 MB**、**small 7.1 MB**、**medium 11.3 MB**（預設）、**large 15.5 MB**
- 在 WebAssembly 中執行：使用 SDK 自有的內建引擎（預設，約 13 KB）或 onnxruntime-web
- 任何用途皆可免費使用，包括商業用途
- Python、iOS、Android 與 Flutter 使用相同的模型（[安裝](#安裝)）

```bash
npm install @newmean-ai/pii
```

```ts
import { createRedactor } from "@newmean-ai/pii";

const redactor = await createRedactor({ size: "medium" });
const { text, spans } = await redactor.redact("My email is kim@example.com");
// text: "My email is [EMAIL]"
```

完整 API 請參閱 [js/README.md](js/README.md)。

## 安裝

| 平台 | 套件 | 安裝 |
|---|---|---|
| 瀏覽器、Node.js | [`@newmean-ai/pii`](https://www.npmjs.com/package/@newmean-ai/pii) | `npm install @newmean-ai/pii` |
| Python 3.9+ | [`newmean-pii`](https://pypi.org/project/newmean-pii/) | `pip install newmean-pii` |
| iOS 15+、macOS 12+ | `NewmeanPII`（Swift Package Manager） | 加入 `https://github.com/newmean-ai/pii`，版本從 `1.7.2` 起 |
| Android 7.0+ | [`ai.newmean:pii`](https://central.sonatype.com/artifact/ai.newmean/pii) | `implementation("ai.newmean:pii:1.7.2")` |
| Flutter、Dart | [`newmean_pii`](https://pub.dev/packages/newmean_pii) | `flutter pub add newmean_pii` |
| Rust | [`newmean-pii`](https://crates.io/crates/newmean-pii) | `cargo add newmean-pii` |

Python、iOS、Android 與 Flutter 套件透過同一個 C 引擎
（[`native/core`](native/core)）在裝置上執行相同的模型，其詞元 ID、偏移量與 logits 均與 npm 套件一致。模型檔案會從 Hugging Face 下載一次，
並與固定的 SHA-256 值比對驗證，或隨應用程式一併封裝。

```python
from newmean_pii import create_redactor

create_redactor(size="medium").redact("My email is kim@example.com").text  # 'My email is [EMAIL]'
```

```swift
import NewmeanPII

let (text, _) = try await PIIRedactor(size: .medium).redact("My email is kim@example.com")
```

```kotlin
import ai.newmean.pii.PiiRedactor

val (text, spans) = PiiRedactor.create(context).redact("My email is kim@example.com")  // create: suspend, model from the app's assets
```

```dart
import 'package:newmean_pii/newmean_pii.dart';

final r = await (await NewmeanPii.create(size: 'medium')).redact('My email is kim@example.com');
```

指南：[Python](python/README.md) · [Swift](swift/README.md) · [Android](android/README.md) · [Flutter](flutter/README.md) · [Rust](rust/README.md)

### 表格（CSV、TSV）

```ts
const { text } = await redactor.redactTable(csv);  // delimiter detected: ",", "\t", ";" or a markdown pipe table
const spans = await redactor.detectTable(tsv, { delimiter: "\t", header: true, vote: true });
const named = await redactor.redactTable(csv, { context: "header" });  // also read each cell after its column title
```

```python
create_redactor().redact_table(open("survey.csv", encoding="utf-8").read(), context="header").text
```

表格模式會將整張表格當成一段文字讀取，讓儲存格彼此提供上下文，並將每個區段裁切至儲存格值的範圍內：
分隔符號、引號與換行都不會被遮蔽，因此輸出仍是有效的表格。若某一欄的值較短，且模型將該欄
至少 80% 的儲存格判讀為姓名、電子郵件、電話號碼或使用者名稱，就會遮蔽整欄（來源為 `"table"`）；
標題列與自由文字欄位一律不參與此投票。使用 `context: "header"` 時，若某欄的值大多互不相同，
也會將該欄每個儲存格接在欄位標題後方，單獨讀取（`"<header>: <value>"`），藉此找出分散在
名字與姓氏欄位中的姓名：在 54 種語言的 556 份問卷與表單匯出資料上，large 漏掉的個資字元比例
從 6.2% 降至 1.4%，而不含個資的儲存格被遮蔽的比例則從 9.6% 升至 12.3%（所有大小的結果見[結果](#結果)）。每個這類
儲存格都需要額外呼叫模型一次（耗時約為表格模式的兩倍）。每個套件都具備表格模式（Swift、Kotlin 與 Dart：`detectTable`、
`redactTable`；C：`pii_detect_table`）。

## 連結

- Hugging Face 上的模型：[newmean-pii-nano](https://huggingface.co/newmean-ai/pii-nano) ·
  [newmean-pii-small](https://huggingface.co/newmean-ai/pii-small) · [newmean-pii-medium](https://huggingface.co/newmean-ai/pii-medium) ·
  [newmean-pii-large](https://huggingface.co/newmean-ai/pii-large)
- newmean-pii-dataset（公開測試集）：[newmean-ai/pii-dataset](https://huggingface.co/datasets/newmean-ai/pii-dataset)
- 示範：[pii.newmean.ai](https://pii.newmean.ai)
- 排行榜：[newmean-ai/pii-leaderboard](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)
- 技術報告：[docs/report/REPORT.md](docs/report/REPORT.md)

## 結果

發現遺漏？請用格式相同的虛構文字[回報](https://github.com/newmean-ai/pii/issues/new?template=missed-pii.yml)（議題是公開的）。

各尺寸按標籤、文字格式與語言的遺漏率（SDK 預設設定，公開測試）：[docs/results](docs/results/models-v1.1-public-test.md)。

僅使用模型時，在 newmean-pii-dataset（公開測試集）與四個外部資料集上，未被遮蔽的個資字元比例（漏偵測率 %，越低越好）；
我們的所有模型皆未使用這四個外部資料集訓練。包含過度遮蔽、參數量與速度的完整表格：[技術報告，第 6 節](docs/report/REPORT.md)；
newmean-pii-dataset（公開與非公開測試集）的每週分數：[排行榜](https://huggingface.co/spaces/newmean-ai/pii-leaderboard)。

| 模型 | 下載大小 | 公開測試集（54 種語言） | KDPII（ko） | OpenPII（30 種語言） | Nemotron-PII（en） | Gretel 金融 |
|---|---|---|---|---|---|---|
| **newmean-pii-large** | 15.5 MB | **1.6** | 9.6 | 4.9 | **6.1** | 21.4 |
| **newmean-pii-medium** | 11.3 MB | 1.9 | 11.5 | 6.3 | 7.7 | 21.5 |
| **newmean-pii-small** | 7.1 MB | 2.4 | 10.0 | 5.6 | 8.4 | 22.3 |
| **newmean-pii-nano** | 2.6 MB | 2.9 | 9.5 | 5.0 | 9.9 | 21.4 |
| gliner-pii-edge | 176 MB | 19.6 | 11.9 | 5.7 | 17.3 | **15.7** |
| OpenAI privacy-filter | 2.8 GB | 34.6 | 34.8 | 14.3 | 25.7 | 32.2 |
| Horizon pii-redactor-small | 563 MB | 41.0 | 25.7 | 11.1 | 7.8 | 31.6 |
| KoELECTRA Korean PII NER | － | 43.1 | **8.8** | 24.1* | 45.9 | 37.6 |
| GLiNER2-PII | 1.2 GB | 44.9 | 34.2 | 23.2 | 46.3 | 32.9 |
| Rampart | 15 MB | 56.5 | 36.1 | **3.5*** | 51.6 | 40.5 |
| Mozilla smart-pii-redactor | 15 MB | 56.9 | 46.0 | 25.9 | 48.8 | 32.9 |
| Rampart-edge | 1.5 MB | 74.7 | 89.0 | 46.1 | 55.5 | 48.0 |
| 正規表示式基準 | － | 75.3 | 62.0 | 61.1 | 44.6 | 86.7 |

解讀方式：我們的模型使用與 newmean-pii-dataset（公開測試集）採用相同設計的資料訓練，其他偵測器則是首次接觸這些資料，
且部分偵測器採用不同的標註政策（例如不遮蔽組織名稱），因此該欄呈現的是
各偵測器對這套遮蔽政策的適用程度；四個外部資料集欄位則是較公平的比較。\* 使用 OpenPII 資料（Rampart）
或與其重疊的資料（KoELECTRA）訓練。GLiNER2-PII 在 Gretel 上使用其函式庫的長文字模式執行。在手機上（四款的平均值），
11.3 MB 模型處理一則 40 詞元的訊息約需 26 ms。使用 SDK 1.2（即 Python 套件發布時的版本）時，本表中我們
模型的每個數值變動皆在 0.04 個百分點以內，因為 SDK 只會改變含有 emoji 或鍵帽字元的文字。

1.2 的變更。1.2 版是在 v1.1 模型上搭配 SDK 1.2.0；v1.1 未單獨發布，因此其模型變更隨
此版本推出。*模型：* 短文字、表格儲存格與問卷匯出資料中，單獨出現的日常詞彙被判讀為姓名的情況大幅減少。在
54 種語言的 20,691 個短項目（欄位標題、答案選項、單字、單獨寫出的姓名與值）中，
不含個資的項目被遮蔽的比例從 30.6% 降至 7.9%（large；medium 從 30.6% 降至 7.8%，small 從 36.8% 降至 9.4%，nano 從 39.3% 降至 11.1%），
逐一檢查儲存格時，large 在 228 個問卷 CSV 檔案中，遮蔽了 6.6% 不含個資的儲存格，原先則為 24.9%。取捨是：在 KDPII 上，
large 的漏偵測率為 9.6%（v1.0：7.6%）、medium 為 11.5%（9.8%）、small 為 10.0%（9.5%）；nano 維持 9.5% 不變。*SDK：* 從
模型讀取的文字中移除 emoji，讓模型不再將其判讀為姓名（`keepEmoji: true` 可關閉此功能；不含
emoji 或鍵帽字元的文字會得到與 1.1 完全相同的區段）；像 3️⃣ 這樣的鍵帽字元會以其基本字元讀取，因此使用
鍵帽字元寫出的號碼仍可被找出；每個區段都會擴展至完整的字素叢集，因此 emoji、旗幟或組合序列
都會完整遮蔽或完整保留（npm 套件需要 `Intl.Segmenter` 才能做到）；另外還有具備 `context: "header"`
選項的表格模式（見上文）。短文字、emoji 與表格資料集均為非公開的合成資料。

Emoji，僅使用模型與模型 + SDK 1.2：在 4,501 段文字（於個資之外放置一到三個 emoji）中，有 emoji
被遮蔽的文字比例。沒有任何 emoji 被切成一半；剩下被遮蔽的多半是緊鄰姓名的鍵帽字元，因為鍵帽字元會被讀取
為數字，而與姓名一起被遮蔽。這些文字中漏掉的個資字元比例也下降了（large 從 0.87% 降至 0.31%，medium 從 1.03% 降至 0.61%）。1.0 版
在這些文字中遮蔽 emoji 的比例為 7.42%（large）、9.46%（medium）、16.69%（small）與 21.88%（nano），且在
1.9% 至 7.6% 的文字中將 emoji 切成一半。

| 模型 | 僅使用模型 | 模型 + SDK 1.2 |
|---|---|---|
| newmean-pii-large | 23.95% | 0.87% |
| newmean-pii-medium | 14.89% | 0.78% |
| newmean-pii-small | 20.57% | 0.89% |
| newmean-pii-nano | 26.33% | 1.00% |

問卷與表單匯出資料，漏偵測率 %／不含個資的資料儲存格被遮蔽的比例 %：由 Claude
Opus 5.5 撰寫的 556 個檔案（121,272 個儲存格、54 種語言），我們的訓練資料皆非由其撰寫；此資料集在 SDK 選項確定後才建立，並僅評分一次。

| 模型 | 整個檔案，僅使用模型 | 表格模式（預設） | 表格模式 + `context: "header"` | 對每個儲存格執行 `detect` |
|---|---|---|---|---|
| newmean-pii-large | 6.56 / 9.58 | 6.17 / 9.59 | **1.44** / 12.30 | 5.00 / 12.21 |
| newmean-pii-medium | 8.29 / 9.30 | 7.93 / 9.32 | **1.93** / 11.66 | 5.75 / 12.03 |
| newmean-pii-small | 7.00 / 15.87 | 6.65 / 15.90 | **1.72** / 19.03 | 6.21 / 14.21 |
| newmean-pii-nano | 6.47 / 23.62 | 6.13 / 23.66 | **1.58** / 27.34 | 6.31 / 17.74 |

`context: "header"` 將每種大小模型的漏偵測率降至約四分之一，代價是不含個資的儲存格被遮蔽的比例增加 2.3 至 3.7 個百分點。
單用表格模式的漏偵測率與讀取整個檔案大致相同，但能維持有效的表格格式，並遮蔽整個聯絡資訊欄位。
small 與 nano 在每種模式下都會遮蔽許多不含個資的儲存格（16% 至 27%）。1.0 版（v1.0 模型，無表格模式）會讀取
整個檔案，漏偵測更多：large 7.52 / 11.30、medium 9.50 / 9.37、small 9.80 / 13.43、nano 8.04 / 24.36。用來選定選項的另外兩個資料集，
以及完整數值，請參閱[技術報告，第 6.2 節](docs/report/REPORT.md)。

1.3 的新增功能。SDK 1.3.0，沿用相同的 v1.1 模型；新增兩個選項：`wholeWords` 預設開啟（設為 `false` 會得到 1.2 的區段；
上方數值皆未使用此選項），`strictShortNames` 預設關閉。
`strictShortNames`：對模型讀取時長度不超過 8 個詞元的文字（問卷答案、表格儲存格），需要更強的證據才會
判讀為姓名，因此單獨出現的「경기」、「남성」或「네이버」會保留而不遮蔽。不含個資的短項目被遮蔽的比例從 7.9% 降至 4.3%
（large；medium 從 7.8% 降至 4.5%，small 從 9.4% 降至 4.7%，nano 從 11.1% 降至 5.9%），單獨出現的姓名被找出的比例則從 98.9% 降至 96.5%（medium
從 97.5% 降至 95.4%，small 從 97.0% 降至 93.5%，nano 從 96.7% 降至 94.4%）；較長的文字不變（公開測試集與 KDPII 的變動在 0.07
個百分點以內）。`wholeWords`：若區段在詞語中間結束，則會涵蓋整個詞語（「R23」→ `[ID_NUMBER]`，而非 `[ID_NUMBER]23`；韓文字母與
數字或拉丁字母相接之處視為詞語結尾，因此「010-1234-5678입니다」→ `[PHONE]입니다`；韓文區段後的韓語助詞會保留，「홍길동님께」→ `[PERSON]님께`），
讓每個資料集的漏偵測率降低 0.1 至 1.4 個百分點（large：公開測試集從 1.63% 降至 1.54%，KDPII 從 9.61% 降至 9.08%），同時
略微增加遮蔽量（large 的過度遮蔽率：公開測試集從 0.47% 升至 0.53%，KDPII 從 1.70% 升至 2.09%）。每個套件也都新增 `info`，提供套件與模型版本，以及已驗證的模型檔案。

1.4 的新增功能。SDK 1.4.0，沿用相同的 v1.1 模型；每個套件都新增使用者清單。`block`：確知是個資的值（如
講師姓名），只要起點與終點都位於詞語邊界，就會被遮蔽，也適用於韓語助詞前方
（「박지훈님께」→ `[PERSON]님께`），以及模型漏偵測之處（來源為 `block`）。`allow`：若某區段的完整文字是
確知不屬於個資的值（如問卷中的地區或品牌），就不會被遮蔽。ASCII 字母比對時不區分大小寫，
其餘內容皆不做正規化（請傳入 NFC 文字）。載入任何模型檔案前就會檢查清單，錯誤會指出清單、
項目與原因，但絕不包含文字內容，而 `info` 會提供項目數與你的 `listsId`。在 KDPII 開發集（large）上，將資料集中所有姓名納入 block 清單，作為效果上限，
可將漏掉的 PERSON 字元比例從 24.6% 降至 3.1%，代價是過度遮蔽率增加 0.4 個百分點；包含 136 個項目的通用 allow 清單則如預期，
影響不大（allow 適用於你自己欄位中的值）。詳細說明：
[js/README.md](js/README.md#lists)。直接使用 C 核心（未透過套件）時：C API 僅保證原始碼相容，
因此請針對各版本的 `pii.h` 重新建置；來自其他版本的選項結構會遭到拒絕
（[native/core](native/core/README.md#api)）。

1.4.1 的修正。SDK 1.4.1，沿用相同的 v1.1 模型；僅修正錯誤與說明文件，所有有效輸入都會得到與 1.4.0 相同的區段。
現在，每個套件與 C 核心都會將 NaN 或無限大的 `sensitivity` 視為錯誤（Android 原先就會拒絕）；
先前，NaN 會悄悄停用模型，無限大則會遮蔽全部內容或完全不遮蔽。npm 套件現在也能在
可透過 require 載入 ES 模組的 Node 版本（20.19+、22.12+）上使用 `require()` 載入，且只有在
兩個模型檔案的 SHA-256 都確實經過驗證時，`info.verified` 才會是 true（先前只是複製 `verify` 選項的值）。Flutter 套件會在
載入時、讀取任何檔案之前檢查其原生核心，符合其 1.4.0 版本說明中的敘述。其他小幅修正：npm 的 `detectTable` 會在
執行模型前檢查分隔符號；Flutter 下載時的 User-Agent 會包含套件版本；並修正了幾處 README
段落（內建引擎大小、清單範例、誤列為標籤的名稱）。

1.5 的變更。SDK 1.5.0，沿用相同的 v1.1 模型；僅提升速度。在 arm64（Apple 晶片、iPhone、64 位元 Android）上，Python、Swift、Android
與 Flutter 套件的原生核心現在於矩陣運算核心中使用融合乘加，並一次處理 16 個輸出欄，與 npm 套件的 relaxed-SIMD 版本相同：速度約為
1.5 倍（medium，Apple M5 Pro 單執行緒：短文 14.9 → 10.2 ms，1,000 個 token 550 → 349 ms）。logits 與 1.4.1 在最後幾個位元上不同，
因此極少數情況下區段可能不同：在 18,692 篇開發用文字與四種大小上，74,768 個結果中有 1 個不同。arm64 上的結果現在與 npm 套件的
relaxed-SIMD 版本（Chrome、Edge、Firefox）相同，不再與 x86-64 逐位元相同（x86-64 沒有任何變更）；以 `PII_NO_FMA` 建置核心即可沿用
1.4 的運算核心（[native/core](native/core/README.md#build)）。npm 套件得到與 1.4.1 相同的區段。

1.6 的新增。SDK 1.6.0，沿用相同的 v1.1 模型；僅新增整合用的轉接器，`detect` 與 `redact` 的結果與 1.5.0 相同。Python 套件新增
`newmean_pii.langchain`（供 LangChain 1.0 以上使用的偵測器與現成的 `PIIMiddleware`）、`newmean_pii.litellm`（在 LiteLLM
proxy 內遮蔽請求或回應的 guardrail，不需另外架設服務）、`newmean_pii.presidio`（Microsoft Presidio 的 recognizer，以單一模型
為每種語言各建一個）與 `newmean_pii.langfuse`（用於追蹤紀錄的 `mask` 函式）；npm 套件新增 `@newmean-ai/pii/ai-sdk`（Vercel AI
SDK 的 language model middleware）與 `@newmean-ai/pii/langchain`（LangChain.js 的 agent middleware）。這些函式庫都不是相依
套件：Python 以 extras `[langchain]`、`[litellm]`、`[presidio]` 一併安裝，`langchain` 則是 npm 套件的選用 peer 相依。轉接器
會遮蔽送出或儲存的文字，不會在回應中還原原值。說明：[python/README.md](python/README.md#integrations)、
[js/README.md](js/README.md#vercel-ai-sdk)。Swift、Android 與 Flutter 套件僅更新版本號。

1.6.1 的修正。SDK 1.6.1，沿用相同的 v1.1 模型，結果區段也相同。開啟樣式層（`regex: true`，預設關閉）或直接呼叫 `regexSpans`
時，處理時間會隨輸入長度的平方成長：EMAIL 樣式會從一長串不含「@」的字元中的每個字元重新嘗試，而且每個比對結果都與先前所有
結果逐一比較，因此直接傳入外部長文字的服務可能被長時間佔用（32,000 個字母在 npm 套件需 0.5 秒、Python 需 2 秒，現在 Python 約
1 毫秒、npm 0.05 毫秒）。npm、Python、Swift、Android 與 Flutter 套件已修正；Rust crate 與 C 核心沒有這一層。每個套件都以六個結構字元組成的
7 個字元以內的所有字串，以及隨機 Unicode 字串，將新的比對器與舊的逐一對照。由 @whatisyourname0（Joonho Lee）私下通報。Python 套件另外修正：
LiteLLM guardrail 接受寫在 config.yaml 的 `allow` 與 `block` 項目。1.6.0 之後加入的 Rust crate
[`newmean-pii`](rust/README.md) 也採用相同版本。npm 請使用 1.6.2：npm 上的 1.6.1 發佈時缺少建置後的檔案，無法匯入；
1.6.2 是同一份程式碼建置後的版本，現在缺少建置檔案時會拒絕發佈。

1.7 的變更。SDK 1.7.0，沿用相同的 v1.1 模型，預設設定下結果區段也相同。*還原原值：*vault（`createVault`、`Vault`、
`PIIVault`、`PiiVault`）為每個值配上可還原的編號佔位符（「[PERSON_1]」，同一個值永遠是同一個佔位符），六個套件皆提供。搭配
vault 時，npm 與 Python 的中介層（Vercel AI SDK、LangChain、LangChain.js）會在模型回答以及 `restoreTools`／
`restore_tools` 指定的工具參數中還原原值；其他工具收到的是佔位符，因此模型讀到的文字無法把原值送到別處。AI SDK 中介層也會逐段
還原串流回答；LangChain 中介層還原的是完成後的訊息，因此 token 回呼看到的仍是佔位符。*代理：*AI SDK 中介層
在 `"tool"` 為角色時也會遮蔽 JSON 與 content 形式的工具結果（1.6 保持原樣）；Python 新增用於檢索文件的 `mask_documents`；
新增含 CRM 工具的代理範例（`js/examples`、`python/examples`）與 PDF 文字範例。*結果：*各尺寸按標籤、格式與語言的遺漏率
（[docs/results](docs/results/models-v1.1-public-test.md)），以及[回報遺漏](https://github.com/newmean-ai/pii/issues/new?template=missed-pii.yml)的表單。
以上來自 LinkedIn 貼文留言的請求；vault 在發佈前經外部模型多次審查。

1.7.2 的變更。SDK 1.7.2 沿用相同的 v1.1 模型，是第一個包含 vault 的發佈版本：1.7.0 與 1.7.1 已準備但未發佈，因此上述 1.7
的變更隨 1.7.2 推出。Python 套件的
Langfuse 遮蔽函式（`pii_mask`）現在也會遮蔽 dict 的鍵、set 項目、bytes（以 UTF-8 解讀）以及 dataclass 與 pydantic 模型的
欄位；數字、日期、`uuid.UUID` 物件與 Langfuse 的媒體物件保持原樣，其他任何值則變為 `[UNMASKED: <type>]`，不會原樣送出。遮蔽後相同的
兩個鍵會保留兩個值（` #2`），`keys=False` 則讓鍵保持原樣。來自 @Mramaran 的 [pull request #1](https://github.com/newmean-ai/pii/pull/1)。
追蹤紀錄的 ID（`call_…`、`chatcmpl-…` 等供應商與 LangChain 的 ID，以及追蹤紀錄標為 ID 之處的 UUID：`run_id`、`session_id`
等鍵的值或該處列表的項目，鍵本身不算）與長度不超過 64 個字元、作為欄位名稱的鍵（`user_id`、`userName`），在模式層與 `block` 清單於其中找不到任何內容時保持原樣；其他鍵與其他 UUID
由模型讀取（`ids=False` 時 ID 也由模型讀取）。遮蔽函式也會走訪任意
深度的巢狀結構、在建立時檢查選項，且從不拋出例外：遮蔽失敗時（例如模型無法載入），整個值以 `[MASK FAILED]` 送出，並記錄錯誤的
類型。檔案、名稱、錯誤訊息與其他 OpenTelemetry 檢測套件的 span，Langfuse 不經遮蔽函式就上傳或送出（[python/README.md](python/README.md#integrations)）。

發佈前，vault 依照書面保證與 10,915 個測試案例的語料檢查過，並加入以下改進。六個套件都會找到以大寫寫成的已知值；電子郵件地址、
使用者名稱、URL 主機與兩個詞以上的值不分 ASCII 大小寫；也會找到 NFD 與全形寫法，以及兩個音節的韓文；模型連同標點、括號或引號一起標記的值（`Kim Minsu.`、`"Kim Minsu"`）會去掉這些字元後再記住。npm 與 Python 中介軟體
也會在 Python repr 跳脫、ISO 時間戳記、帶正負號的數字與分成兩行的值中找到它；在工具說明與結構描述、回應格式、供應商選項與
訊息的 `name` 中隱藏已知值；還原代理的結構化回應；無法遮蔽的呼叫會失敗而不送出。搭配 vault 時，npm 中介軟體與 Python 一樣只接受 tag 模式，其他遮蔽設定會拋出錯誤。LangChain 整合現在需要
Python 的 langchain 1.4.0 以上，以及 LangChain.js 的 langchain 1.5.1 以上與 `@langchain/core` 1.2.1 以上（1.6 接受 1.0 以上）；
原因見各套件的 README。這些下限，以及上述追蹤中保留或讀取的 id 與鍵，改變了 1.6.1 的承諾；維護者已核准。含數千個已知值的大型資料每次呼叫需要數秒（2,000 個已知值與 181 KB 的 JSON 列，npm 與 Python 皆約
2 秒）；更快的搜尋預計在 1.7.3。詳見
[js/README.md](js/README.md#putting-the-values-back-vault)、[python/README.md](python/README.md#integrations)。

x86-64（[issue #3](https://github.com/newmean-ai/pii/issues/3)）。Python、Swift、Android、Flutter 與 Rust 套件的原生核心新增
x86-64 的 8 寬 AVX2 矩陣核心：CPU 具備 AVX2 時，每個行程選擇一次（`avx2`，否則 `sse`）。其 logits 與 SSE 核心逐位元相同，
因此所有片段都不變；`NEWMEAN_PII_KERNEL=avx2+fma` 可選用融合乘加（logits 與 arm64 預設相同）。以 MSVC 建置的版本
（Windows wheel）維持純量核心。AMD EPYC 9V74 單執行緒下，一則短文 nano 約 13 ms、medium 約 19 ms（`sse` 為 23 ms 與 35 ms）。
Python README 現在說明每個 `info()["build"]` 值的意義，以及如何為具備 AVX2 與 FMA3 的 x86-64 CPU 從原始碼建置並啟用融合乘加
（`CFLAGS="-march=x86-64-v3 -DPII_FMA"`）（[python/README.md](python/README.md#api)）。

Python 的模型下載。模型檔案無法寫入快取時，`fetch_model()` 與 `python -m newmean_pii download` 現在會失敗（`ModelFileError`；
指令以狀態 1 結束並指出該檔案），不再回傳之後以 `model_dir=` 或離線載入無法使用的資料夾。在這種快取（唯讀或已滿的磁碟，或
`--cache-dir` 指向一個檔案）上原本以狀態 0 結束的建置步驟，現在會失敗。為立即使用而載入時（`Redactor`、`create_redactor`），
即使快取無法寫入，仍與以前一樣把驗證過的位元組保留在記憶體中。來自 @kyleoliver-gnomai 的
[pull request #4](https://github.com/newmean-ai/pii/pull/4)（[python/README.md](python/README.md#offline-use)）。Python 測試中的
`test_info` 現在以平台的路徑分隔符比較模型的本機路徑；先前在貢獻者的 Windows 執行中失敗。

## 儲存庫結構

| 資料夾 | 內容 |
|---|---|
| [`js/`](js) | SDK `@newmean-ai/pii`，以及其測試頁面、公開示範（`playground/`）與手機效能測試（`experiments/`） |
| [`native/core/`](native/core) | Python、iOS、Android 與 Flutter 套件共用的 C 引擎 |
| [`python/`](python) | Python 套件 `newmean-pii` |
| [`swift/`](swift)、`Package.swift` | Swift 套件 `NewmeanPII`（iOS、macOS） |
| [`android/`](android) | Android 函式庫 `ai.newmean:pii` |
| [`flutter/`](flutter) | Flutter 與 Dart 套件 `newmean_pii` |
| [`rust/`](rust) | Rust crate `newmean-pii` |
| [`docs/report/`](docs/report) | 技術報告與其兩份附錄（亦提供韓文版） |
| [`leaderboard/`](leaderboard) | Hugging Face 排行榜 Space 的程式碼 |
| [`site/`](site) | 在 pii.newmean.ai 提供示範與手機效能測試的 Worker |
| [`tools/data-request/`](tools/data-request) | 如何申請訓練資料（研究用途免費，商業用途請洽詢） |
| [`training/`](training) | 訓練、匯出與評估程式碼（不包含資料產生器） |
| [`release/`](release) | newmean-pii-dataset（公開測試集）的資料集說明卡 |

## 授權

- SDK 程式碼與模型權重：[Apache-2.0](LICENSE)。
- 若在產品中使用，歡迎在說明文件、關於畫面或授權畫面加註「Powered by newmean.ai」（這是請求，並非授權條件）。
- newmean-pii-dataset（公開測試集）：CC BY-NC 4.0（研究與評估用途；商業用途請洽詢）。
- 訓練與驗證資料：可透過[資料申請表單](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform)申請（研究用途免費；商業用途請洽詢；目前已開放申請，資料自 December 2026 起提供）；條款會隨核准電子郵件或合約提供（[tools/data-request](tools/data-request)）。
- 第三方著作權聲明：[NOTICE](NOTICE)。

由 [newmean.ai](https://newmean.ai) 製作。
