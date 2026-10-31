English | [한국어](APPENDIX-experiments.ko.md)

# Appendix: experiment record behind data v1.0

Companion to the technical report ([REPORT.md](REPORT.md)). The report describes how the v1.0 data is made and what it
costs, and [APPENDIX-methods.md](APPENDIX-methods.md) gives that method in full (M1–M15); this appendix keeps the pilots,
intermediate rounds, review history and earlier costs that led there (A–G), and the earlier 10-label models with their
comparison against public detectors (H–L), and the external datasets we considered for training and rejected (N; the
letter M is left out to avoid confusion with the methods appendix). Round names are internal: m1p–m12p are pilot rounds (about 50–400 calls each),
d1/d2 the first 10-label datasets, d12 the 54-language 10-label dataset the earlier models were trained on (H), r11 and
r12/r13 the full rounds before and of v1.0. Reference numbers [n] are those of the report's reference list. The full
experiment records are internal and not published; this appendix carries the published part.

Report history: v0.2 was revised after the GPT-6.1 Sol review of v0.1; v0.3 added the method, samples and cost of data v1.0
and moved the method details, the earlier models and the experiment record into the two appendices.

## A. Generator pilots

Haiku 5.5, Sonnet 5.5 and Opus 5.5 ran as generators in separate pilot rounds (one model per round, the same planner and
guideline version). The change rates come from different rounds and small samples; they are observations, not a controlled
comparison of generators.

| generator | rows changed by both blind reviewers (random audit) | cost per call (40 rows, Message Batches) | notes |
|---|---|---|---|
| Claude Haiku 5.5, effort high | 3 / 100 (m7p); 8 / 278 (r11 validation) | $0.0104 | ≈ 39k output tokens per call (mostly thinking); effort max never finished (64k and 128k limits) |
| Claude Sonnet 5.5, effort high | 7 / 150 (m8p); 0 / 22 (r11 validation) | $0.098 | ≈ 17k output tokens; follows PII-count and focus specs less strictly |
| Claude Opus 5.5, effort high | 2 / 150 (m9p) | ≈ $0.2 | refused 17 of 54 jobs — every refused job asked for secrets (API keys, private keys) |

At these sample sizes a 3% rate has a 95% interval of roughly 1–8.5% (100 rows) or 1.5–5% (300 rows).

## B. Blind double review by round

| round | generator | languages | reviewed rows | rows changed by both reviewers | notes |
|---|---|---|---|---|---|
| m4p | Haiku | 36 | 795 pool + 144 audit | 4.7% pool, 0.7% audit → ≈ 1.8% of all rows | correcting the label errors changed the models' measured miss rate by ≤ 0.2 points; one of 39 agreed changes was reverted on adjudication |
| m6p | Haiku | 18 new | pool + audit | ≈ 0.9% | pool agreement 96.1% |
| m7p (v9) | Haiku | 54 | 281 new-type + 100 audit | 9 of 281 (3.2%) / 3 of 100 | PII-level agreement between reviewers 98.6% |
| m8p | Sonnet | 54 | 150 audit | 7 (4.7%) | |
| m9p | Opus | 54 | 150 audit | 2 (1.3%) | |
| r11 validation (v11) | Haiku 90% / Sonnet 10% | 54 | 300 audit | 8 (2.7%; 95% interval ≈ 1.4–5.2%) | reviewers gave identical spans and labels on 291 of 300 rows (97.0%) |
| m12p pilot (v12) | Haiku 90% / Sonnet 10% | 54 | 150 audit | 6 (4.0%); 3 already held → 3 / 147 (2.0%) | |
| r12 validation (v1.0) | Haiku 90% / Sonnet 10% | 54 | 300 audit | 10 (3.3%); 2 already held → 8 / 298 (2.7%) | |
| r12 validation, Sonnet rows (v1.0) | Sonnet | 54 | 100 audit | 5 (5%); 3 already held → 2 / 97 (2.1%) | all three missed names were held by the focus/count check |

Across these rounds, random audits of 100–300 rows found 0.7–4.7% of rows changed by both reviewers; the audits sample
different populations in different rounds (pool vs audit, APPENDIX-methods M10), so the rates are not directly comparable.

ZCode (GLM, Z.ai), reference-only, Chinese: m7p, 80 rows — boundaries 99.0% of 105 spans, levels 99.0%, subtypes 95.2%, two
generator errors; r11 validation, 100 rows — boundaries 94.2%, labels 100%, subtypes 93.9%, three generator errors (a bank name
left outside an account, a hospital without its department, an address cell split in two) and four guideline gaps; r12
validation, 100 rows — boundaries 100% (98.9% from the generator's side), labels and levels 98.9%, subtypes 95.7%, one generator
error, none of the r11 problems recurred. The first r11 run stopped on Z.ai's content filter (code 1301; the texts contain fake
passwords and ID numbers) and went through in five files of 20 rows.

## C. From r11 to v1.0

The first full round (r11: validation 53,984 rows; a train shard of 135,470 rows that was cancelled too late and kept outside
v1.0) passed the label checks but showed:
- **Value collapse** across calls and languages: card expiry 09/27 in 37% of expiry spans, CVV 417 18%, one-time code 482913
  10%, respondent codes R047/R-0417 about 9%, the date 2024-05-14 in 13% of dated texts. Seeding fresh values per call (v12) was
  ignored (≈ 5% use in the m12p pilot, 1 of 408 dates), which led to the post-generation rewrite. Its first version shifted dates
  next to weekday names ("Tuesday 2024-05-14" → "Tuesday 2024-10-11", found by GPT-6.1 Sol), so the rewrite became conservative.
- **PII-free items** with a twist in only 11% of cases → their own twist list (≈ 53%).
- **Span-edge lapses** already covered by the guideline (endings kept, city or department left out) → a final self-check in the
  prompt; and guideline gaps (speaker codes, host-less paths, national ID vs ID-card number, routing vs branch code, cards called
  accounts) → guideline additions.
- Format failures of 1.5%, mostly spec gaps (Appendix D).

v1.0 was locked after the GPT-6.1 Sol go/no-go review, which required: the conservative date rewrite, the rewrite included in
the frozen round inputs, rules for recovery phrases and payment-only user numbers, an account of the per-language format
failures, and an extra audit of Sonnet rows.

## D. Country-format corrections

Of 571 format failures in an early round, about half were gaps in our specs (Kazakhstan's personal tax ID is the IIN; Iran's
12-digit economic code; Ukrainian sole proprietors use their personal tax number; the 12-digit Philippine PhilSys number;
India's ESIC number; Spain's NIE), the rest generator errors (Azerbaijan's VÖEN check digit, Serbian region codes), several
caused by format text that the card truncated at 110 characters; after the fixes, failures on previously checked spans fell
from 571 to 363 with no regressions. From the r11 failures, 18 more specs were corrected (Japanese plates and health insurance
numbers, Turkish/Estonian/Latvian/Lithuanian/Dutch ID-card numbers, Chinese social insurance = resident ID, sole-proprietor tax
numbers in Russia and Malaysia, Mexican INE numbers and others; 55 of 173 failures were spec gaps), and the checker learned to
compare IBANs in upper case, to accept a bank name kept in an account span, and to skip a foreign resident's own documents.

## E. Names: pilot results

Where Faker had no locale, the 100 most frequent PERSON values made up about half of all PERSON spans (Maltese, Georgian, Khmer,
Urdu). With the Wikidata pools and foreign residents' names (m11p, 120 calls), full names reused across calls fell from 12.9% to
4.0% (Georgian), 13.4% to 6.6% (Urdu) and 10.4% to 8.4% (Khmer); Maltese stayed at ≈ 26% (r11: 21.8%, v1.0: 11.8%), which
reflects genuinely concentrated Maltese surnames. Half of the sampled low-count Taiwanese family names were split errors, which
is why the count-1 tail is dropped.

## F. Cost of the work before v1.0, and every model call of the project

**Data generation before v1.0** (API, Message Batches at 50%; pilots, the 10-label data and the superseded r11 round): $263.83
in total by the batch records (183.7M input and 679.6M output tokens).

| part | calls | cost |
|---|---|---|
| pilots m1p–m12p | ≈ 1.2k | ≈ $48 |
| d1, d2 (36 languages, 10-label data) | ≈ 11k | ≈ $118 |
| r11: validation + a train shard (superseded; kept, not in v1.0) | 4,754 | ≈ $95 |
| total | | ≈ $261 (≈ $264 with the batch that completed after it was cancelled) |

**Every model call, by phase and model** (report §4.6 sums these). Tokens from the tools' own records: Claude Code transcripts
(the coding agent, its subagents and the headless `claude -p` calls, deduplicated by message id), codex session totals, the
generation batch records, and the Colab balance log; priced at API list prices ($ per million tokens — Opus 5.5 4 / 20, cache
read 0.20; Sonnet 5.5 2 / 10, cache read 0.20; Haiku 5.5 0.10 / 0.50; cache writes 1.25× input for 5 minutes, 2× for 1 hour;
GPT-6.1 Sol 2.00 / 10.00, cached input 0.10, reasoning counted as output; Colab $9.99 per 100 compute units). Snapshot of
2026-10-29 21:30 (KST).

| phase | model | calls / records | input | cache write | cache read / cached input | output | API-price $ |
|---|---|---|---|---|---|---|---|
| data generation: API (Message Batches 50%, realtime refills full price) | claude-haiku-5-5 (batch) | 43 | 518.47M | — | — | 1,778.98M | 470.67 |
| data generation: API (Message Batches 50%, realtime refills full price) | claude-haiku-5-5 (realtime) | 5 | 2.53M | — | — | 9.41M | 4.96 |
| data generation: API (Message Batches 50%, realtime refills full price) | claude-opus-5-5 (batch) | 10 | 7.39M | — | — | 10.95M | 124.23 |
| data generation: API (Message Batches 50%, realtime refills full price) | claude-opus-5-5 (realtime) | 2 | 0.03M | — | — | 0.05M | 1.13 |
| data generation: API (Message Batches 50%, realtime refills full price) | claude-sonnet-5-5 (batch) | 25 | 50.96M | — | — | 69.11M | 396.52 |
| data generation: API (Message Batches 50%, realtime refills full price) | claude-sonnet-5-5 (realtime) | 3 | 0.46M | — | — | 0.63M | 7.23 |
| data generation: CLI pilots (claude -p) | claude-opus-5-5 | 141 | 0.00M | 2.69M | 1.90M | 0.91M | 40.05 |
| data generation: CLI pilots (claude -p) | claude-sonnet-5-5 | 382 | 0.00M | 7.20M | 5.21M | 1.57M | 45.53 |
| design, code and document reviews (codex) | gpt-6.1-sol | 353 | 3.06M | — | 17.98M | 0.90M | 16.96 |
| planning · implementation · analysis · writing: main sessions (Claude Code) | claude-opus-5-5 | 2,527 | 0.01M | 5.43M | 1,193.93M | 2.57M | 332.35 |
| planning · implementation · analysis · writing: subagents (Claude Code) | claude-opus-5-5 | 1,017 | 0.00M | 6.07M | 120.05M | 1.89M | 92.13 |
| review: adjudication (claude -p) | claude-opus-5-5 | 23 | 0.00M | 0.42M | 0.31M | 0.01M | 3.59 |
| review: blind re-tagging (claude -p) | claude-opus-5-5 | 1,139 | 0.00M | 27.86M | 15.46M | 3.66M | 299.13 |
| review: blind re-tagging (codex) | gpt-6.1-sol | 526 | 4.09M | — | 7.96M | 0.89M | 17.86 |
| review: external-data sample (claude -p) | claude-opus-5-5 | 40 | 0.00M | 0.90M | 0.56M | 0.07M | 8.67 |
| review: external-data sample (codex) | gpt-6.1-sol | 39 | 0.24M | — | 0.61M | 0.03M | 0.89 |
| review: label audit (claude -p) | claude-opus-5-5 | 6 | 0.00M | 0.12M | 0.08M | 0.02M | 1.39 |
| review: label audit (claude -p) | claude-sonnet-5-5 | 36 | 0.00M | 0.77M | 0.49M | 0.14M | 4.53 |
| review: label audit (codex) | gpt-6.1-sol | 30 | 0.19M | — | 0.43M | 0.05M | 0.88 |
| tests (codex) | gpt-6.1-sol | 4 | 0.01M | — | 0.06M | 0.00M | 0.03 |
| tests and probes (claude -p) | claude-haiku-5-5 | 1 | 0.00M | 0.02M | 0.01M | 0.00M | 0.00 |
| tests and probes (claude -p) | claude-opus-5-5 | 1 | 0.00M | 0.02M | 0.01M | 0.00M | 0.14 |
| tests and probes (claude -p) | claude-sonnet-5-5 | 2 | 0.00M | 0.04M | 0.03M | 0.00M | 0.15 |
| training and evaluation: Colab GPUs | Colab | — | — | — | — | — | 9.63 (96.36 CU) |
| **total** | | | | | | | **1,878.63** |

**ZCode (GLM-5.3, z.ai coding plan, app usage):** 7.3M tokens in total, no input/output split in the console; $10.22 if all
input ($1.40 per million), $32.12 if all output ($4.40 per million; docs.z.ai pricing, 2026-10-29).

Two observations that changed how we review. (1) Each headless `claude -p` call carries the coding CLI's own prompt (about 29k
tokens: a 17.6k cache write and an 11.6k cache read) before the 15 rows, so about a third of the Claude review cost is that
overhead. (2) In the public-test review, the blind Claude labels matched the generator's labels on 98.0% of 12,657 rows, and
under our merge rule a third opinion cannot change those rows; the second reviewer (GPT-6.1 Sol) now sees only the rows where
the first disagrees with the generator (about 2%).

## G. Pilot incidents
- Thinking tokens count against the output limit: 37 of 90 calls were truncated at 16k tokens → higher limits per model.
- A "non-native writer" voice was read as "write in English": 8.7% off-language rows → reworded, 0.9%.
- 6% of rows copied example values verbatim from the format cards → a no-copy rule placed first and an automatic copy check.
- Opus refused 17 of 54 jobs in m9p, all with a SECRET focus → SECRET focus moved to Sonnet jobs.
- A batch cancelled while it was finishing still completed 3,395 of 3,402 requests (≈ $68 billed); the rows were collected and
  kept outside v1.0.

## H. Earlier models and their data

All models in H–L were trained on the d1/d12 data, before the v1.0 label scheme (report Section 3); they have since been
replaced by the v1.0 models (report Section 5, Appendix O). Their tables are model-only results without the SDK's regex layer; public detectors were run as
published. Section I.1 uses the d1 bake-off and reruns; I.2–L use the d12 models.

| version | data | labels learned | used in |
|---|---|---|---|
| d1 | 36 languages, 285k rows | 10 (no SECRET, no level-2 labels; personal URLs only) | teacher bake-off (I.1) |
| d12 | 54 languages, 427,661 rows | same 10 | teachers, students, I.2–L |
| v1.0 | 54 languages, ≈ 1.07M rows | 13 (adds SECRET, PERSON_L2, URL_L2) | v1.0 teachers and the four released students (report Section 5; O, P) |

## I. Teachers

### I.1 Bake-off
We fine-tuned candidate encoders for token classification on the first synthetic round (36 languages, 285k rows) and evaluated
on in-distribution sets and four external sets. mDeBERTa-v3-base and XLM-RoBERTa-large were best; mmBERT-base, Granite Embedding
Multilingual R2 and a fully fine-tuned EmbeddingGemma 2 followed; a frozen EmbeddingGemma 2 with an MLP head was far behind
(sentence embeddings lack token-boundary information). Training three epochs instead of one did not close the gaps.

### I.2 The two teachers on 54 languages
On 54 languages (d12, two epochs) the two teachers are complementary (single runs; paired bootstrap over test rows, 95%
intervals):

| set | mDeBERTa-v3-base | XLM-R-large | union of both |
|---|---|---|---|
| KDPII (ko) | **8.3** / 0.8 | 11.2 / 0.8 | **5.6** / 1.1 |
| openpii (8 languages) | 3.0 / 3.3 | **2.5** / 3.2 | **1.4** / 3.9 |
| Nemotron-PII (en) | **23.9** / 0.1 | 27.0 / 0.1 | **22.2** / 0.1 |
| Gretel finance (7 languages) | 22.7 / 1.7 | **20.7** / 2.1 | **19.0** / 2.6 |

*Character miss rate % / over-masking %; bold = significantly better of the two single models, and the best overall.*

### I.3 Run-to-run variance is as large as most model gaps
Retraining the same mDeBERTa configuration with another seed moved the KDPII miss rate from 7.0% to 8.6–8.8% and Nemotron from
21.8% to 22.4–23.0% on identical data; length-grouped batches (random batches had spent 69% of the compute on padding; grouping
made training 2–3× faster) changed nothing beyond that noise. We measured this variance on mDeBERTa reruns only; the tables in
H–L are fixed single runs, and their bootstrap intervals cover test sampling, not training variance. Gaps of 1.5–2 points
between single runs are therefore not established. The spans on which the models were least stable across runs were exactly
nicknames and surname-plus-title mentions — the error analysis that led to the PII levels (report Section 3).

### I.4 Can the teachers find label errors?
After training, both teachers predicted a fixed 60,000-row sample of their own training data. Rows where the two teachers agree
with each other but not with the gold labels (1.7% of rows) were reviewed blind: 33% of rows with a letter/digit disagreement
had a real label error, versus ≈ 3% of random rows — a 5–10× enrichment, but two thirds of flagged rows were correct. Most
teacher-only "misses" were one punctuation character next to an entity that a subword token swallowed. We therefore do not
relabel automatically.

## J. Students: vocabulary and distillation

### J.1 The vocabulary matters most
All students use the mMiniLMv2-L6-H384 body (distilled from XLM-R-large). For a browser budget of ≈ 20 MB the embedding
table is the problem: the full 250k-piece vocabulary alone is 96M parameters.

| student (gold labels only, d12, 2 epochs, single run) | parameters | KDPII | openpii | Nemotron | Gretel |
|---|---|---|---|---|---|
| full XLM-R vocabulary (reference) | 117M | 9.3 | 6.0 | 24.1 | 24.1 |
| our own 24k SentencePiece + embedding transfer | 20.1M | 14.0 | 10.1 | 29.7 | 33.2 |
| **XLM-R vocabulary pruned to 35k** (pretrained embeddings kept) | 24.5M | **11.5** | **7.5** | **25.5** | **28.7** |

*Character miss rate %, model only.* Replacing the vocabulary — even with each new piece initialised as the mean of its XLM-R
sub-pieces — lost 4–9 points on external sets in these runs. Pruning keeps every XLM-R piece that is a single character seen in
the d12 training split (7,243) plus the 30,000 most frequent pieces on that split (35,129 in total; +17% tokens on validation);
XLM-R's tokenizer has no byte fallback, so a character outside the kept set becomes the unknown token — which is why every
character seen in training is kept. The kept embeddings are copied unchanged, which kept most of the quality. Mozilla's
browser redactor uses the same body with a 20k pruned vocabulary. In our runs, keeping the pretrained vocabulary (pruned) made
the largest difference of the choices we tried at this size.

### J.2 Distillation
Teachers and students use different tokenizers, so teacher outputs are stored per **character span**: for every teacher token
whose probability of O (at T = 1) is below 0.99, one entry with the token's character offsets (in the original, un-normalised
text) and its 11 class probabilities at T = 2 (O + 10 labels, B and I summed), quantised to 0–255; on average 6.8 entries per
row. Every other character counts as O. Two teachers are averaged character by character; where only one teacher has an entry,
the other counts as O there. Each student token receives the mean of the entries it overlaps, weighted by the number of
overlapping characters, with the rest of the token counted as O; tokens with no overlapping entry get no KD target. The loss is
0.5 · cross-entropy on the gold BIO tags of every labelled token + 0.5 · T² · KL(teacher ‖ student) over the 11 classes (the
student's B and I probabilities summed), averaged over the tokens that have a KD target. The student still predicts BIO tags,
so boundaries come from the gold labels.

| student (d12, single run) | KDPII | openpii | Nemotron | Gretel | over-masking KDPII / openpii | exact F1 (validation) |
|---|---|---|---|---|---|---|
| full XLM-R vocabulary, gold only (reference) | 9.3 | 6.0 | 24.1 | 24.1 | 1.2 / 3.2 | 0.93 |
| pruned 35k, gold only | 11.5 | 7.5 | 25.5 | 28.7 | 1.2 / 3.2 | 0.91 |
| **pruned 35k, distilled from both teachers** | **10.0** | **5.7** | **22.3** | **25.0** | 1.7 / 4.1 | 0.89 |
| own 24k, distilled | 12.4 | 6.4 | 24.9 | 25.9 | 2.7 / 5.4 | 0.77 |

*Character miss rate %; exact F1 on 4,000 validation rows, `TaggerPredictor` (no merging of adjacent spans).* In this run the
distilled pruned student's miss rate is within 2 points (0.3–1.8) of the full-vocabulary model, which has 4.8× the parameters,
on all four sets, with more over-masking and slightly lower exact F1; we did not test equivalence. With our own vocabulary,
distillation also fragmented entity boundaries (exact F1 0.90 → 0.77); counting punctuation as O in the distillation targets
made it worse. With the pruned XLM-R vocabulary the drop was not observed in this run (0.91 → 0.89). Merging adjacent
same-label fragments (as Rampart does) recovers part of it in deployment (K).

## K. Export, quantization and tiers

At the d12 stage only one student was exported, the own-24k distilled student (20.1M parameters): torch → ONNX (opset 17, fp32,
80.4 MB) → every MatMul, including the classifier, to `MatMulNBits` 4-bit, block 32, symmetric (onnxruntime's
`MatMulNBitsQuantizer`) → the word-embedding `Gather` dynamically quantized to uint8: **16.4 MB**. On an 18-language pilot set,
KDPII and openpii the miss rate changed by +0.2, +0.3 and +0.7 points and over-masking by 0.0, −0.2 and −0.3 points; exact F1
was unchanged (scored with the ONNX predictor, which merges adjacent same-label spans). Sizes are the ONNX file only; the
tokenizer (≈ 1–2 MB) and the onnxruntime-web WASM runtime come on top. The d12 pruned student (24.5M parameters) was not
exported.

The tiers planned at that stage were: large ≈ 20 MB (pruned 30–35k vocabulary, 6 layers), medium ≈ 10 MB (factorized embeddings, rank ≈ 96),
small ≈ 5 MB (4 layers, rank ≈ 32), nano < 2 MB (4 layers sharing one layer's weights, rank ≈ 16, after Rampart-edge). A
randomly initialised 7.5 MB model without pretraining was far behind (KDPII 17.8% even with distillation), which motivates
pretrained bodies for every tier. The tiers as built for v1.0 (report Section 5) keep the pretrained body but differ from this
plan: large 24.4M parameters, 15.5 MB; medium rank 192, 11.3 MB; small 4 layers, rank 64, 7.1 MB; nano one layer applied 4
times, rank 32, 2.6 MB. Their results are in Appendix O; browser latency in Appendix P.

## L. Comparison with public detectors (earlier models)

*Superseded for the v1.0 models by Section 6 and Appendix O.* These runs used the earlier label scheme, in which the
secret-like labels of public detectors (password, API key …) were dropped instead of mapped to SECRET. Under the v1 scheme only
Horizon pii-redactor-small changes noticeably (Nemotron miss 18.6% → 7.8%, Gretel 32.8% → 31.6%); the other detectors move by at
most 0.1 points.

**Metrics.** Character miss rate (share of non-space PII characters left unmasked — the privacy-relevant number),
over-masking (share of non-PII characters masked), entity miss rate (entities less than half covered) and exact-boundary F1
(start, end and label identical). Earlier internal records used a span F1 that counted ≥ 50% overlap as correct; those values
are not comparable and are not used here. Our models are read in 256-token windows with stride 64 (the window in which a token
lies farther from the edge wins; adjacent spans are not merged). External sets carry "ignore" spans for types outside our
scheme, which count neither as misses nor as over-masking.

**External sets.** KDPII (Korean dialogues, human labels) [25, 26], ai4privacy openpii validation (8 languages) [21], NVIDIA
Nemotron-PII test sample (English, 2,000 rows) [22], Gretel synthetic_pii_finance_multilingual test sample (7 languages, 2,794
rows) [23]. Their label conventions differ from ours: Nemotron tags every URL and secret; Gretel tags user names in finance
documents broadly.

**Comparison by size class** (character miss % / over-masking % / exact F1; saved predictions of every model re-scored with the
same scorer; model only; our models are d12 single runs; parameter counts read from the weight files):

| model | size | KDPII | openpii | Nemotron | Gretel | 18 new languages |
|---|---|---|---|---|---|---|
| our teachers, union (private) | 278M + 559M params | **5.6** / 1.1 / 0.70 | **1.4** / 3.9 / 0.69 | 22.2 / 0.1 / 0.81 | 19.0 / 2.6 / 0.56 | **8.9** / 0.1 / 0.89 |
| OpenAI privacy-filter | 1.4B, 2.8 GB (bf16) | 34.9 / 2.1 / 0.11 | 14.3 / 6.6 / 0.16 | 25.7 / 0.2 / 0.18 | 32.2 / 2.7 / 0.16 | 25.8 / 4.2 / 0.15 |
| Horizon pii-redactor-small | 141M, 563 MB (fp32) | 25.8 / 1.1 / 0.21 | 11.1 / 0.0 / 0.36 | 18.6 / 0.1 / 0.27 | 32.8 / 1.7 / 0.20 | 45.6 / 0.4 / 0.21 |
| gliner-pii-edge | 45M, 176 MB (fp32) | 11.9 / 45.9 / 0.01 | 5.7 / 15.0 / 0.34 | **17.3** / 0.6 / 0.53 | **15.7** / 8.7 / 0.28 | 23.2 / 28.0 / 0.08 |
| **our student (pruned 35k + KD)** | 24.5M (d12, not exported) | 10.0 / 1.7 / 0.55 | 5.7 / 4.1 / 0.61 | 22.3 / 0.3 / 0.75 | 25.0 / 3.5 / 0.43 | 9.2 / 0.3 / 0.86 |
| Mozilla smart-pii-redactor | ≈ 19M, 15 MB (q4) | 46.0 / 2.1 / 0.10 | 25.9 / 3.8 / 0.47 | 48.8 / 0.1 / 0.49 | 32.9 / 0.4 / 0.59 | — |
| Rampart | ≈ 19M, 15 MB (q4) | 36.1 / 3.5 / 0.05 | 3.5 / 2.2 / 0.77 | 51.6 / 0.1 / 0.47 | 40.5 / 1.0 / 0.32 | 56.3 / 2.9 / 0.23 |
| Rampart-edge | ≈ 2M, 1.5 MB | 89.1 / 0.0 / 0.06 | 46.2 / 2.5 / 0.33 | — | — | — |

In these single runs the student misses fewer PII characters than the public detectors of similar size (Mozilla, Rampart,
Rampart-edge) on every set except openpii, on which Rampart was trained.

**Conditions that limit this comparison.**
- *Adapters and policies.* OpenAI privacy-filter was run through the transformers pipeline with default aggregation, not the
  Viterbi decoder its card recommends, which can break boundaries and lowers its exact F1. Mozilla's redactor intentionally
  keeps some labels (organisations, locations, public names); we counted those as unmasked. GLiNER models were queried with a
  fixed label list at their cards' thresholds (0.3 / 0.5). Mozilla, Rampart and Rampart-edge were run as their published q4
  ONNX files. No threshold was tuned for any model. The results hold for these adapters and default policies only.
- *Training exposure.* Rampart was trained on openpii data, so its openpii score is not out-of-distribution; our models never
  saw any of the four external sets.
- *Label policy.* The d12 models did not learn SECRET (it was dropped from training then) and tag only personal URLs, while
  Nemotron tags every URL and secret. On Nemotron our teachers miss 93% of SECRET characters and 47% of URL characters; on
  names, addresses and IDs our teachers miss 0.9% / 4.1% / 1.1% against Horizon's 1.0% / 49.2% / 31.0%. Label policy and
  training scope both affect the ranking, and we have not separated their contributions.
- gliner-pii-edge's low miss rates come with masking 46% of ordinary characters on KDPII.

The v1.0 models on the public test are in Appendix O.

## N. External datasets considered for training, and why they were rejected

We considered a second model line trained on our synthetic data plus the training sides of the four external datasets (openpii-1.5m,
KDPII, Nemotron-PII, Gretel finance; all CC BY 4.0 or Apache-2.0). We converted them (1,504,512 rows after removing duplicates of the
evaluation samples; Appendix M15 of the methods), checked them, and decided on 2026-10-29 not to train on them. They remain
evaluation sets only.

**Checks.**
- **Structural.** Every row was checked automatically (offsets, overlaps, value shapes). Gretel had 601 rows whose offsets
  pointed outside the text or at the wrong words; these were dropped.
- **Sample review.** A stratified sample of 390 rows was reviewed blind by Claude Opus 5.5 and GPT-6.1 Sol against our
  guideline. Rows flagged by both reviewers:

  | source | rows | flagged by both | typical problems |
  |---|---|---|---|
  | Gretel finance | 100 | 48 (48%) | names inside company names tagged as persons; headings and field names tagged as values ("Ricavi", "IPv6"); tax ids, account numbers and postcodes left untagged |
  | Nemotron-PII | 100 | 11 (11%) | names split around a middle initial; SIM, ICCID and IMSI numbers untagged |
  | openpii-1.5m | 90 | 10 (11%) | source boundaries one character short; brand names tagged as names |
  | KDPII | 100 | 2 (2%) | — |

- **Mapping review** by GPT-6.1 Sol (19 findings, 11 rated high). The main ones:
  - The sources do not tag every instance of the types they support. Untagged text therefore cannot be treated as "not PII".
  - Several source labels mix roles that our guideline separates:
    - customer accounts vs bank accounts;
    - corporate vs personal tax ids;
    - nicknames vs user names vs aliases;
    - surname-only vs full names.
  - Some labelled spans are redactions or field names rather than values.

**Why we rejected them.** Making the data consistent with our guideline would mean re-labelling a large share of it. The
alternative was to train only on the source's positive spans and leave everything else to the teachers. Both options weaken the
link between the model and a single, documented guideline. That link is what the package's privacy claims rest on: two PII
levels, SECRET, the country formats, and an audited 2.7% label error rate. Training on data with a 2–48% two-reviewer error rate
and incomplete negatives would make the model's behaviour harder to explain and to audit.

The external sets keep their value as out-of-distribution evaluation, because none of our models has seen them. The conversion
code (`training/scripts/ext_data.py train`) and the trainer's support for `ignore` and partial labels
(`training/scripts/train_ner.py`) remain in the repository for a future, reviewed attempt; the review is summarised above, and
its per-row records are not published.

## O. v1.0 models on the public test

Public test, 17,058 rows; `evaluate.py --scheme v1`, model only unless noted, single runs. Our students run as the
shipped ONNX files (onnxruntime CPU); the public detectors with the adapters and settings of report Section 6. O.1, O.3 and O.4 come from the
Colab L4 runs of Section 6 (students and q4 ONNX models on the VM's CPU, transformer models on the L4 GPU), except the OpenAI
privacy-filter and Rampart-edge rows of O.3–O.4, which come from earlier runs on the Apple M5 Pro (its GPU and CPU) and are within
0.5 points of the L4 runs; every other model gave identical spans on both machines. Speed: report Section 6.

**O.1 Weight files** (miss % / over-masking % / exact F1):

| tier | q4 (8-bit embeddings) | q4e (4-bit embeddings) |
|---|---|---|
| large | 1.6 / 0.6 / 0.78 | 1.6 / 0.6 / 0.78 |
| medium | 2.0 / 0.6 / 0.77 | 2.1 / 0.6 / 0.77 |
| small | 2.7 / 1.2 / 0.68 | 2.9 / 1.2 / 0.69 |
| nano | 3.3 / 2.1 / 0.59 | 3.6 / 1.8 / 0.60 |

**O.2 Decision threshold.** A bias b added to the "outside" logit (PyTorch, v1.0 validation and two external sets; miss % /
over-masking %). The SDK's `sensitivity` is −b.

| b | medium v1.0 val | medium KDPII | medium Nemotron | nano v1.0 val | nano KDPII | nano Nemotron |
|---|---|---|---|---|---|---|
| −1.0 | 0.50 / 0.51 | 8.34 / 2.91 | 6.09 / 1.30 | 0.89 / 1.81 | 7.55 / 6.21 | 8.64 / 2.80 |
| −0.5 | 0.61 / 0.44 | 8.88 / 2.59 | 6.50 / 1.11 | 1.03 / 1.51 | 8.46 / 5.26 | 9.38 / 2.34 |
| 0 | 0.69 / 0.38 | 9.25 / 2.32 | 6.88 / 0.96 | 1.20 / 1.27 | 9.17 / 4.48 | 10.14 / 1.97 |
| +0.5 | 0.75 / 0.34 | 9.68 / 2.10 | 7.32 / 0.82 | 1.40 / 1.08 | 9.88 / 3.89 | 10.92 / 1.66 |
| +1.0 | 0.83 / 0.30 | 10.10 / 1.88 | 7.96 / 0.68 | 1.64 / 0.93 | 10.64 / 3.37 | 11.72 / 1.37 |

**O.3 Miss % per label** (default files: large and medium q4e, small and nano q4):

| model | PERSON | PHONE | EMAIL | ADDRESS | ID_NUMBER | FINANCIAL | ORG | USERNAME | URL | DATE_OF_BIRTH | SECRET | IP_ADDRESS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ours, large | 2.2 | 0.1 | 0.0 | 3.2 | 1.2 | 0.8 | 5.5 | 2.8 | 0.8 | 1.7 | 0.3 | 0.1 |
| ours, medium | 3.3 | 0.2 | 0.1 | 4.4 | 1.2 | 1.0 | 7.0 | 2.8 | 0.7 | 1.5 | 0.6 | 0.0 |
| ours, small | 4.8 | 0.9 | 0.4 | 4.9 | 1.4 | 1.2 | 8.6 | 4.5 | 0.6 | 2.1 | 0.4 | 0.1 |
| ours, nano | 5.9 | 1.0 | 0.5 | 6.6 | 1.4 | 1.3 | 9.6 | 5.0 | 1.0 | 3.6 | 0.5 | 0.1 |
| gliner-pii-edge | 14.3 | 25.7 | 9.5 | 29.2 | 14.1 | 19.7 | 52.3 | 6.9 | 8.0 | 31.7 | 19.2 | 12.9 |
| OpenAI privacy-filter | 35.4 | 40.1 | 13.5 | 41.3 | 30.2 | 27.8 | 76.7 | 28.1 | 41.0 | 35.9 | 27.3 | 15.9 |
| Horizon pii-redactor-small | 15.1 | 37.3 | 24.8 | 56.9 | 30.0 | 41.6 | 87.8 | 43.0 | 72.2 | 89.8 | 59.5 | 8.9 |
| KoELECTRA Korean PII NER | 43.0 | 43.6 | 24.4 | 64.3 | 54.8 | 29.0 | 65.0 | 24.9 | 17.8 | 90.9 | 37.5 | 6.4 |
| GLiNER2-PII | 13.0 | 34.6 | 11.4 | 79.7 | 60.7 | 38.4 | 96.9 | 13.4 | 98.4 | 9.3 | 63.9 | 53.6 |
| Rampart | 46.4 | 41.1 | 51.1 | 58.3 | 38.3 | 49.3 | 86.4 | 56.5 | 74.3 | 91.4 | 76.9 | 63.3 |
| Mozilla smart-pii-redactor v1 | 41.1 | 47.2 | 73.4 | 70.1 | 28.4 | 46.5 | 88.8 | 55.8 | 95.8 | 42.5 | 65.8 | 79.8 |
| Rampart-edge | 75.7 | 69.9 | 29.7 | 88.0 | 83.7 | 66.7 | 95.6 | 93.8 | 80.5 | 87.5 | 91.8 | 94.7 |
| regex only | 100.0 | 69.9 | 25.7 | 100.0 | 74.7 | 60.9 | 100.0 | 100.0 | 8.6 | 88.3 | 85.0 | 100.0 |

**O.4 Miss % per level, script, language and generator** (language groups are means over languages):

| model | level 1 | level 2 | Latin-script | other scripts | en | ko | ja | zh | ar | hi | th | Sonnet rows | Opus rows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ours, large | 1.5 | 3.4 | 1.5 | 1.8 | 3.7 | 1.4 | 1.3 | 0.5 | 1.9 | 2.1 | 2.7 | 1.2 | 2.1 |
| ours, medium | 2.0 | 4.6 | 1.9 | 2.4 | 3.6 | 2.1 | 1.5 | 0.4 | 3.0 | 3.0 | 3.7 | 1.5 | 2.9 |
| ours, small | 2.6 | 6.2 | 2.6 | 3.0 | 4.4 | 1.4 | 1.2 | 0.8 | 3.6 | 5.9 | 4.6 | 1.8 | 4.0 |
| ours, nano | 3.1 | 6.7 | 3.0 | 3.8 | 3.5 | 2.5 | 1.8 | 0.8 | 4.2 | 6.7 | 5.8 | 2.3 | 4.6 |
| gliner-pii-edge | 20.0 | 14.5 | 15.3 | 27.3 | 22.7 | 14.7 | 18.4 | 16.0 | 31.2 | 33.6 | 43.6 | 18.6 | 21.2 |
| OpenAI privacy-filter | 33.4 | 64.2 | 36.8 | 31.0 | 32.6 | 23.6 | 25.2 | 27.2 | 28.3 | 36.9 | 26.2 | 30.9 | 40.2 |
| Horizon pii-redactor-small | 39.9 | 53.8 | 40.3 | 40.4 | 35.0 | 26.0 | 38.3 | 26.2 | 35.3 | 41.5 | 36.0 | 39.6 | 41.8 |
| KoELECTRA Korean PII NER | 43.1 | 39.4 | 38.2 | 50.7 | 49.8 | 15.1 | 39.8 | 34.0 | 53.3 | 53.0 | 53.2 | 39.6 | 47.5 |
| GLiNER2-PII | 44.2 | 63.4 | 44.1 | 46.8 | 41.3 | 38.2 | 58.1 | 53.6 | 44.0 | 48.5 | 49.4 | 44.4 | 46.2 |
| Rampart | 53.7 | 77.9 | 51.0 | 61.2 | 43.5 | 32.7 | 53.7 | 40.0 | 82.2 | 61.8 | 64.3 | 54.3 | 55.7 |
| Mozilla smart-pii-redactor v1 | 55.4 | 80.0 | 52.2 | 63.6 | 47.5 | 43.3 | 61.5 | 49.5 | 72.2 | 64.4 | 65.8 | 56.0 | 57.3 |
| Rampart-edge | 74.3 | 96.9 | 68.9 | 87.2 | 53.4 | 90.9 | 85.4 | 85.4 | 85.5 | 90.9 | 85.1 | 73.8 | 77.5 |
| regex only | 76.8 | 58.5 | 77.2 | 73.3 | 76.3 | 60.9 | 65.2 | 53.6 | 69.0 | 74.7 | 72.2 | 75.6 | 76.3 |

**O.5 Regex layer** (miss % / over-masking %, model only vs model + the SDK's patterns):

| model | model only | model + regex |
|---|---|---|
| ours, large | 1.6 / 0.6 | 1.6 / 1.2 |
| ours, medium | 2.1 / 0.6 | 2.1 / 1.2 |
| ours, small | 2.7 / 1.2 | 2.7 / 1.8 |
| ours, nano | 3.3 / 2.1 | 3.3 / 2.6 |

The patterns add almost nothing to coverage. Of the characters they mask outside gold spans, 85% come from the account-number
pattern (`\d{2,6}-\d{2,6}-\d{2,8}`) matching ISO dates and log timestamps; most of the rest are tracking, ticket and barcode
numbers matched as phone or ID numbers, and e-mail matches that run into the CJK text before the address.

## P. Browser runtimes and native speed

Headless browsers, one
thread, `detect()` without regex (tokenizer + model + decoding), median of repeated runs after a warm-up. Texts from the parity
set: 40 tokens, 251 tokens (one window) and 2,476 tokens (13 windows). Machines: Apple M5 Pro (Chrome 156) and a Colab L4 VM
(Xeon 2.20 GHz, 12 vCPUs; Chrome 156). Parity: spans identical to the Python evaluator on the same file, 412 texts.

**P.1 Runtime size** (the WebAssembly runtime only; model and tokenizer come on top):

| runtime | raw | brotli |
|---|---|---|
| onnxruntime-web 1.31.1, npm (`ort-wasm-simd-threaded.wasm`) | 14.85 MB | 2.45 MB |
| onnxruntime-web, own build without threads | 12.42 MB | 2.11 MB |
| onnxruntime-web, own build with our operators only (reads .onnx) | 7.88 MB | 1.48 MB |
| onnxruntime-web, own minimal build (.ort format), Release / MinSizeRel | 1.90 / 1.25 MB | 0.43 / 0.34 MB |
| LiteRT.js 2.6.0 | 11.13 MB | 2.15 MB |
| own TFLite + XNNPack build, 21 operators | 2.34 MB | 0.46 MB |
| built-in engine (SIMD128 / relaxed SIMD) | 17.0 / 18.0 KB | 5.6 / 5.9 KB |
| onnxruntime-web WebGPU bundle (`asyncify` wasm) | 28.7 MB | — |

**P.2 Latency, Apple M5 Pro, Chrome** (ms for 40 / 251 / 2,476 tokens; load = model load):

| runtime | nano | medium | load (medium) | parity |
|---|---|---|---|---|
| onnxruntime-web npm, q4e | 20 / 94 / 1,227 | 30 / 140 / 1,860 | 161 | 412/412 |
| onnxruntime-web, own builds (all four), q4e | 18–24 / 85–123 / 1,157–1,531 | 27–31 / 128–139 / 1,670–1,958 | 34–146 | 412/412 |
| LiteRT.js wasm, block-wise 4-bit (d4e) | 68 / 334 / 3,626 | 80 / 328 / 4,308 | 74 | 405–410 vs Python LiteRT |
| LiteRT.js wasm, fp32 (14 / 71 MB files) | 17 / 89 / 1,024 | 21 / 88 / 1,188 | 61 | 412 vs PyTorch |
| own TFLite build, d4e | 20 / 87 / 1,129 | 31 / 133 / 1,737 | 44 | 402–411 vs Python LiteRT |
| onnxruntime-web WebGPU, q4e | 10 / 19 / 68 | 14 / 15 / 103 | 334 | 412/412 |
| LiteRT.js WebGPU, fp32 | 15 / 34 / 195 | 2 / 5 / 64 | 173 | 360/412 vs PyTorch |

The built-in engine was measured in its own benchmark (40 / 252 / 1,000 tokens; start-up from page navigation), medium q4e:
SIMD128 23 / 147 / 714 ms, relaxed SIMD 14 / 91 / 463 ms, onnxruntime-web 31 / 146 / 734 ms; start-up 63 vs 230 ms. In Safari
26 (WebKit) SIMD128 24 / 154 / 748 vs onnxruntime-web 36 / 154 / 784; in Firefox 159 SIMD128 25 / 142 / 722, relaxed 17 / 95 / 476,
onnxruntime-web 41 / 166 / 810.

**P.3 Latency, Colab L4 VM, Chrome, nano** (ms for 40 / 251 / 2,476 tokens):

| runtime | q4 | q4e | load | parity |
|---|---|---|---|---|
| built-in engine, SIMD128 | 38 / 240 / 3,173 | 39 / 249 / 3,144 | 76 | 410 / 412 |
| built-in engine, relaxed SIMD | 33 / 222 / 2,882 | 33 / 223 / 2,892 | 77 | 410 / 412 |
| onnxruntime-web npm | 64 / 287 / 3,632 | 65 / 294 / 3,659 | 461–505 | 412 / 412 |
| onnxruntime-web, own minimal build | 63 / 288 / 3,632 | 63 / 287 / 3,560 | 81–82 | 412 / 412 |
| LiteRT.js wasm, d4 | 273 / 1,102 / 14,300 | — | 283 | 406 vs Python LiteRT |
| own TFLite build, d4 / d4e | 158 / 649 / 8,415 | 159 / 649 / 8,480 | 163–166 | 406 / 405 vs Python LiteRT |

WebGPU did not start on this VM (only the SwiftShader software adapter; the Vulkan loader rejected the NVIDIA driver). Parity on small–large in Chrome: built-in engine, onnxruntime-web npm and minimal 412/412 against ONNX for q4 and q4e.

Firefox 159 and WebKit 2248 on the same VM (2,476 tokens, nano / large): built-in engine with relaxed SIMD 2.24 / 3.35 s
(Firefox; WebKit has no relaxed SIMD), SIMD128 3.07 / 4.63 s (Firefox) and 3.43 / 5.06 s (WebKit); onnxruntime-web npm
3.66 / 5.60 s and minimal 3.74 / 5.56 s (WebKit). The built-in engine is 412/412 in Firefox too (nano q4 410). Not measured:
onnxruntime-web, LiteRT.js and the own TFLite build in Firefox, and LiteRT.js/TFLite above nano in Chrome (time limit); the
own TFLite build does not compile in WebKit (it uses relaxed SIMD).


**P.4 Native speed, Colab L4 VM CPU** (Python; ms end to end for 40 / 250 / 938 tokens; onnxruntime 1.31.1, ai-edge-litert 2.3.0
with XNNPack):

| tier | threads | ONNX q4 | ONNX q4e | TFLite d4 | TFLite d4e | TFLite fp32 |
|---|---|---|---|---|---|---|
| large | 1 | 14 / 74 / 395 | 16 / 81 / 403 | 8 / 40 / 198 | 8 / 39 / 197 | 18 / 74 / 367 |
| medium | 1 | 14 / 73 / 395 | 15 / 79 / 399 | 8 / 40 / 198 | 8 / 39 / 200 | 18 / 70 / 352 |
| small | 1 | 10 / 51 / 273 | 11 / 56 / 274 | 5 / 28 / 137 | 5 / 27 / 135 | 12 / 50 / 246 |
| nano | 1 | 9 / 50 / 269 | 11 / 54 / 274 | 5 / 28 / 136 | 5 / 27 / 135 | 12 / 46 / 245 |
| large | 12 | 5 / 26 / 129 | 7 / 27 / 140 | 3 / 10 / 47 | 3 / 9 / 45 | 5 / 16 / 77 |
| medium | 12 | 5 / 26 / 128 | 7 / 28 / 132 | 3 / 10 / 47 | 3 / 9 / 45 | 5 / 17 / 78 |
| small | 12 | 4 / 18 / 90 | 5 / 20 / 91 | 2 / 7 / 33 | 2 / 7 / 31 | 3 / 11 / 49 |
| nano | 12 | 4 / 17 / 89 | 5 / 19 / 92 | 2 / 7 / 33 | 2 / 7 / 32 | 3 / 11 / 51 |

Natively, XNNPack's dynamic int8 × int4 kernels make the .tflite files 2–3× faster than onnxruntime on this CPU, while in the
browser the prebuilt LiteRT.js is the slowest runtime: the gap is in that WebAssembly build, not in the format (our own TFLite
build runs the same file at onnxruntime-web speed on the Mac). The .tflite files are quantized separately and give spans that
differ from the ONNX files on 6–20% of texts at similar accuracy (validation miss within about ±0.1–0.9 points).

**P.5 Failures.** LiteRT.js WebGPU gives wrong spans with weight-only 4-bit files (124–147/412) and fails on every dynamic-range
file; LiteRT.js large w4c crashes the tab. The onnxruntime-web minimal (.ort) build needs the 4-bit embedding tables stored as
uint8 (bit-identical output) because the .ort format has no INT4 type, and Gelu added to the operator list.

**P.6 Phones.** The page `pii.newmean.ai/mobile-bench` runs both engines on the four default
files (nano and small q4, medium and large q4e) in the phone's browser: one thread, regex off, the model files already in memory;
median of 15 / 10 / 4 runs on texts of 40 / 252 / 1,000 tokens after two warm-up runs. A run during which the page went to the
background is timed again (one session from before this rule existed is excluded). Phones: iPhone 16 Pro (iOS 27; Safari 27,
Chrome 156 and an in-app WebView, five sessions), Samsung Galaxy SM-S942N (Android 17), SM-S928N and SM-S918N (Android 16), all
in an in-app Chromium 156 WebView with relaxed SIMD. Each phone's median over its sessions, then the mean of the four phones:

| ms, 40 / 252 / 1,000 tokens | nano | small | medium | large |
|---|---|---|---|---|
| built-in engine | 17 / 112 / 574 | 17 / 115 / 583 | 26 / 175 / 890 | 26 / 174 / 881 |
| onnxruntime-web 1.31 | 33 / 155 / 755 | 32 / 156 / 772 | 48 / 237 / 1,155 | 48 / 237 / 1,161 |

First load (engine and nano model): built-in 772 ms, onnxruntime-web 2,850 ms; loading another model once the engine is ready:
102 vs 134 ms. On the iPhone the two engines are equal on long texts and the built-in engine is about 30% faster on short ones;
on the three Android phones it is 1.9–2.1× faster on short texts and 25–31% faster on long ones. Consistency: each size and engine
also runs 108 public test texts (two per language) and compares the spans with a desktop reference (Chrome, onnxruntime-web):
identical on all 48 runs, and a second pass over 20 of them gave the same spans every time. Per-phone numbers will be published
once more phones have been measured; the page stays open after the release.
