English | [한국어](APPENDIX-methods.ko.md)

# Appendix: how data v1.0 is made — the method in full

Companion to the technical report ([REPORT.md](REPORT.md)). The report summarises each stage of the v1.0 data pipeline with
its key figure; this appendix gives the full method, the parameters and the caveats, so that the data can be judged without
the generator code (which is not released). Sections M1–M15 are referenced from the report. The pilots, review history,
earlier costs and the earlier 10-label models are in [APPENDIX-experiments.md](APPENDIX-experiments.md) (sections A–L).
Reference numbers [n] are those of the report's reference list. Every number comes from our internal experiment records,
which are not published; this appendix and the report carry the published part.

## M1. Label scheme and guideline

**Task.** Given a text, mark the character spans that identify or help identify a person, with a label. The SDK masks the model's
spans. The model learns structured types (e-mail, cards, IBAN, IP addresses …) as well, so it does not depend on patterns. An
optional pattern layer in the SDK — five plain regular expressions for e-mail, URLs, Korean resident-number-shaped IDs, card or
account digit groups and phone numbers, without checksum validation — is off by default; when turned on, its spans are added to
the model's (experiments appendix, O.5).

**Labels, subtypes and flags.** Thirteen model labels: PERSON, EMAIL, PHONE, ADDRESS, ID_NUMBER, FINANCIAL, ORG, USERNAME, URL,
DATE_OF_BIRTH, SECRET, plus PERSON_L2 and URL_L2 for level-2 spans (IP addresses are learned as ID_NUMBER). The data carries a
finer **subtype** on every span (e.g. ID_NUMBER:passport, FINANCIAL:iban, PHONE:mobile; ~75 in total) and **flags** for
notation — `masked` (`**** 1234`), `partial` (cut off), `obfuscated` (spelled out, "kim at naver dot com"), `payment` (an
e-mail or phone number used as a payment key). Subtypes allow selective masking ("mask resident numbers, keep student
numbers"); the model predicts labels only.

**PII levels.** Every span gets a level, fixed by its subtype (table in the report, Section 3). Level 2 (indirect) is PERSON
surname (family name alone, usually with a title or honorific), alias (nickname or pet name used as a name), initials, and URL
public (a link that does not point to a person); everything else is level 1. The levels are an operational masking policy —
which spans a user may choose to leave visible — not a general classification of identifiability, which depends on context
and on what else is known. Levels emerged from error analysis: the spans on which our earlier models were least stable across
training runs were exactly nicknames and surname-plus-title mentions (experiments appendix, I.3).

**Guideline.** A single English guideline is used verbatim in generation prompts and in review prompts. It defines what to tag
and not to tag per label, span boundaries (e.g. an address keeps the city that follows it; names exclude honorifics and
grammatical endings — Turkish "Ayşe'nin" → Ayşe, Korean "서윤이" → 서윤), overlap rules (tags never nest; a URL containing a
token is a SECRET), and culture-specific rules found during review (a given name after a title in Thai, Vietnamese or
Indonesian is a given name, not a surname; the Chinese prefixes of address 小/老/阿 (as in 小张, 老王) are left out of the
name). The guideline went through eleven versions. Like the generation and review prompts it is part of, the guideline text is
not released; this section summarises its rules.

## M2. Design axes

Each generation call asks a model for 40 items in one language and one country. A planner distributes, per language, countries
(weighted by speakers; 79 locales for 54 languages), 16 domains (healthcare, finance, education, HR, customer support, legal,
government, e-commerce, travel, telecom, research surveys, …) and, per item, one of 20 formats (chat message, e-mail, form, CSV
row, JSON, log line, OCR text from a scanned form, code snippet, …), a length, a voice (e.g. non-native writer, elderly,
teenager, professional), a PII count (none / one / two or more) and a twist (spelled-out values, code-switching, the same person
mentioned again in another form, PII scattered across lines, hard negatives such as place and brand names, romanized writing
for South Asian languages, foreign residents' names). Each item also gets a **required focus subtype** drawn from frequency
weights, so rare types (MRZ personal numbers, IBAN, card expiry, recovery phrases, care-provider organisations) appear in
sufficient numbers. Allocation is by exact quotas (floor plus largest remainder), not sampling, and every call has its own
seed.

## M3. PII-free items

**PII-free items** make up 40% of items by design (real input text is often free of personal data, and a model that rarely sees
it over-masks). They draw from their own twists, so their hard negatives are not only brand and place names: look-alike numbers
(order, ticket and tracking numbers, model codes, prices, ordinary dates, version numbers, 3–5 digit short codes), people
mentioned only by role or kinship, non-PII names (places, brands, shops the person only buys from — never their workplace,
school or clinic), template or placeholder text (empty fields, `[Name]`, fully redacted values) and code-switching without
names. About 53% (52.7% on v1.0 validation) receive a twist; the text follows it (digits for look-alikes, brackets or
asterisks for placeholders) in 99–100% of checked rows, and none of these rows carries a span. (Before PII-free items had their
own twist list, only 11% of them carried a twist; experiments appendix, C.)

## M4. Country formats

For every locale a format card gives the shapes of local identifiers (national ID, tax numbers, passports, residence permits,
health and social-insurance numbers, phone numbering plans, postal codes, bank account and IBAN formats, licence plates),
researched from official sources and recorded with confidence notes and regular expressions. Values must match the **format**,
not the checksum — invalid checksums reduce the chance of colliding with a real person's number. A format check validates
generated values against the specs; a failure is reviewed, not silently discarded, because it can be a gap in the spec as well
as a generator error — reviewing them corrected the specs of about 40 countries (experiments appendix, D). In v1.0 the check
fails 0.5% of checkable values (validation); those rows carry a review flag and stay out of training.

## M5. Names and foreign residents

Language models reuse a small set of names, and Faker [60] covers only part of our locales (experiments appendix, E). We built
name pools from Wikidata [58] (CC0) for all 79 locales: given names by sex and family names, counted by the number of Wikidata
people who carry them, in the native script with a Latin form. Low-count entries are noisy, so the count-1 tail is dropped and
sampling uses weights count^0.25. Given and family names are combined with consistent sex; for 13 languages with gendered
surnames the feminine form is derived (Иванов → Иванова, Novák → Nováková, Kowalski → Kowalska, Kazlauskas → Kazlauskienė,
Jónsson → Jónsdóttir); Vietnamese names are composed as family + middle + given.

**Foreign residents.** Real texts contain names from other countries — migrant workers, students, tourists. Using the UN DESA
International Migrant Stock 2024 [59] (national statistics for Korea and Japan) we draw 2–3 foreign names per call, 80% from the
destination's main origin countries and 20% from typical visitor countries, from 54 additional origin pools (every destination
has ≥ 90% of its origin mass covered). A twist on 8% of items asks the generator to write such a name romanized (order,
hyphenation and capitalisation vary) or transliterated into the local script. In the v1.0 validation set a full name recurs in
another call for a median 1.4% of full-name spans per language (highest: Maltese 11.8%, whose real surnames are concentrated).

## M6. Generator models, SECRET reallocation and output format

Three Claude models write the data; their roles follow from pilot audits and costs (experiments appendix, A).

| generator | role in v1.0 | cost per call (40 rows, Message Batches) |
|---|---|---|
| Claude Haiku 5.5, effort high | 90% of training and validation data | $0.0104 |
| Claude Sonnet 5.5, effort high | 10% of training and validation data, 50% of the test sets, every SECRET value in the tests | $0.098 |
| Claude Opus 5.5, effort high | 50% of the test sets | ≈ $0.2 |

Different generators for training and test reduce the risk that the benchmark rewards one model's style.

**SECRET reallocation.** Opus refuses requests that ask for secrets (API keys, private keys), so SECRET focus items are moved
from Opus jobs to Sonnet jobs of the same language (same country first, then same domain; at most six per receiving job while
others have fewer), Opus prompts carry no fake secrets, and any remaining refusal is regenerated on Sonnet; the test sets' SECRET
spans are therefore written by Sonnet only (a limitation; report Section 7).

**Output format and failure handling.** Each call returns its items by number as text with inline tags,
`<LABEL:subtype+flag>value</LABEL>` (e.g. `<PHONE:mobile>010-…</PHONE>`); the parser strips the tags into character offsets and
rejects an item whose tags are nested, mismatched, unclosed or empty, or that uses an unregistered PII-like tag
(`<EMAIL_ADDRESS>`). A call that fails, is truncated or returns fewer items is regenerated with the same prompt; refused calls
are regenerated on Sonnet. The prompt ends with a self-check of the span edges (honorifics and grammatical endings outside
names, the city and building kept in an address, the bank name kept in an account, the department kept in an organisation).

## M7. Batch and realtime generation, frozen inputs

Full rounds run through the Message Batches API (50% discount); pilots of about a hundred calls and the last few failed calls of
a round go through the realtime API at full price, since a batch can wait an hour in the queue. A round is cut into shards with
different base seeds, so quota combinations differ between shards. On the first submission the round freezes the hashes of
every input that shapes the rows — guideline, country specs, name pools, generator code and the post-generation rewrite (M8) —
and refuses later submissions if any changed; every prompt and every raw response is stored, so a round's rows can be rebuilt
from the raw responses when the parser or the rewrite changes, without new API calls (the planner reproduces
all 53,950 validation item specs exactly). Typical runs: 1,350 validation calls in ≈ 35–75 minutes of queue time, 1–3%
of calls failed or were truncated and were regenerated.

## M8. Post-generation value rewrite

Generators collapse onto favourite values for types without a natural pool — card expiry dates, CVVs, one-time codes,
respondent codes and ordinary dates (one expiry date made up over a third of all expiry spans before the rewrite), and they
ignore fresh example values in the prompt (experiments appendix, C). These values are therefore rewritten after parsing,
deterministically per row:

- the digits of card-expiry (random month, year within ±1 of the generated one), CVV, one-time-code and respondent-code spans
  are replaced, the same value twice in a row getting the same replacement;
- ordinary dates outside spans shift together by one random number of days (−18 to +6 months), so their order and gaps stay —
  unless the row names a weekday (CLDR names of its language and English), writes a year on its own or has a birth date, in
  which case its dates stay (shifting them would contradict the text, e.g. a weekday next to a date);
- the Icelandic kennitala gets a valid century digit.

Span offsets are recomputed; no other span changes. On the v1.0 validation set the most frequent value falls to 3.3% (expiry),
2.2% (CVV), 0.3% (one-time code), 0.4% (respondent code) and 2.4% (dates in texts).

## M9. Automatic gates

Every round is checked for: rows per item spec (PII count, required subtype present ≥ 95%), format validity of checkable spans
(≤ 1%), off-language text (≤ 0.5%, with romanized South Asian chat and CSV-like tables exempted after they were found to be
false alarms), copies of card examples, and a review queue for contract violations. Rows that fail a check are held out of
training until resolved.

## M10. Blind double review

**Sampling.** Two kinds of samples: a *pool* of rows flagged by detectors (our teachers and public models disagreeing with the
generated labels, or rows with a new label type to check) and a *random audit* drawn uniformly from the rows outside the pool
(or from the whole round when no pool is built). An estimate for the whole round is the population-weighted sum of the two
change rates (pool rate × pool size + audit rate × rows outside the pool); the audit alone estimates only the rows outside the
pool. Audit rates of different rounds therefore describe different populations.

**Review.** Two reviewers from different model families — Claude (Opus) and GPT-6.1 Sol (through the codex CLI) — re-tag each
sampled row **from the raw text only**, with the guideline, without seeing the generated labels or each other.

**Definitions.** A row is *changed* when both reviewers give the same spans and labels and these differ from the generator's.
If the reviewers disagree and one equals the generator, the generator's labels are kept; if neither does, the row is held for
adjudication by a person and kept out of training until then. Subtypes are taken from the reviewers when they agree, otherwise
from the generator. The change rate is an agreed-change rate, not an error rate: it misses errors both reviewers share and can
include wrong changes (in a pilot audit one of 39 agreed changes was reverted on adjudication; experiments appendix, B).

**ZCode.** For Chinese, GLM (Z.ai, through the interactive ZCode tool) gives a third, reference-only opinion.

**Results on v1.0 validation** (random rows; per-round results of the pilots in experiments appendix, B):

| sample | rows changed by both reviewers | of these, already held by the gates | error rate of rows that enter training |
|---|---|---|---|
| 300 rows (Haiku 90% / Sonnet 10%) | 10 (3.3%) | 2 | 8 / 298 (2.7%; 95% interval ≈ 1.4–5.2%) |
| 100 Sonnet rows | 5 (5%) | 3 (all three missed names) | 2 / 97 (2.1%) |
| 100 Chinese rows, GLM vs generator | boundaries 100%, labels and levels 98.9%, subtypes 95.7% | | 1 clear generator error |

"Already held" rows carry a review flag from the automatic gates (a required subtype or the PII count missing, an off-format
value) and do not enter training. The two reviewers agree with each other on labels, levels and subtypes of every span they both
found; on 8 of 300 rows they disagreed and the row kept the generator's labels or was held. The test sets get a full double
review instead of a sample (report, Section 4.2): 318 of the 17,058 public-test rows (1.9%) and 223 of the 17,001 private-test
rows (1.3%) changed.

Whether trained teachers can find label errors automatically was tested with the earlier models (experiments appendix, I.4):
they enrich real errors 5–10× but two thirds of their flags are wrong, so we do not relabel automatically.

## M11. Deduplication and leakage

Template reuse is checked in three steps.

1. **Exact duplicates** after NFKC and whitespace collapse are dropped inside each split and against higher-priority splits
   (train, then validation, private test dataset, public test dataset).
2. **Near duplicates.** Every text is masked (PII values replaced by their label), lowercased and stripped of punctuation, and
   shingled into word 5-grams (character 5-grams for Chinese, Japanese, Thai, Khmer and texts with few spaces); MinHash with 128
   permutations and LSH with 32 bands of 4 rows proposes candidates, which are confirmed by the exact Jaccard of the shingle
   sets (≥ 0.7). Texts under 8 words (20 characters in character mode) get only the exact check, since a bare `<PERSON>` cell
   would match everything. A query looks at no more than 50 references per bucket and band for validation and 500 for the
   tests, and capped queries are counted. A row is only ever removed from the lower-priority split, never from train.
3. **Recall check.** Random training rows are perturbed (values swapped, 10% of words shuffled, punctuation changed) and
   searched like a test split.

**Leakage.** Between v1.0 validation and the first training shard there are no near-duplicate templates (35 exact duplicates,
removed from validation), but **values** recur. Against the full training data, 44% of validation PERSON values, 61% of
DATE_OF_BIRTH values and 23% of all spans (29% after normalisation) also occur in train; in the public test dataset 36%, 46%
and 20% (25%), in the private test dataset 36%, 50% and 20% (26%). (Against the first training shard alone, the validation
shares were 26%, 35% and 12% (16%).) We therefore also score the public-test rows whose PII values are all unseen in train:
7,442 of the 10,196 rows with PII (73%; 6,681 after normalisation). There the four students miss 1.5 / 2.0 / 2.6 / 3.2% of PII
characters (large to nano; 1.5 / 2.0 / 2.5 / 3.1% on the normalised subset), against 1.6 / 2.1 / 2.7 / 3.3% on all rows and
1.7 / 2.3 / 3.1 / 3.7% on the rows with at least one seen value (shipped files, same predictions and scorer as report Section 6).

## M12. Diversity

v1.0 validation: a full name recurs in another call for a median 1.4% of full-name spans per language (M5); after the rewrite
the most frequent value of a rewritten type is at most 3.3% of its spans (M8); 52.7% of PII-free items carry a twist (M3);
distinct word 3-gram ratios are about 0.95 for PII-free and for PII items alike (0.955 and 0.953; 3,000 random items per
group, Chinese, Japanese, Thai and Khmer left out). Every training shard gives the same picture: PII-free 0.948–0.957, PII
0.953–0.961 (all of train 0.947 and 0.953).

## M13. Dataset version and label mapping

**Version 1.0** (tag `data-v1.0`): validation and the first eighth of the training data come from the previous prompt version
and were post-processed with the v1.0 rewrite; the final prompt version differs only in where recovery phrases may be required
(not in cells or one-line items) and in three guideline lines (recovery phrases, payment-only user numbers, full toll-free
numbers).

**Label mapping.** Model labels for v1.0 are the 13 of M1: PERSON surname, alias and initials become PERSON_L2 and public URLs
URL_L2 (7.7% of the training spans: 23% of PERSON and 44% of URL spans), IP addresses ID_NUMBER, and SECRET is learned (it was
dropped before, in the earlier 10-label models; experiments appendix, H).

## M14. Related work in detail

The report's Section 2 is a compressed version of this text; reference numbers are those of the report.

### M14.1 PII detection systems

**Rule and NER pipelines.** Microsoft Presidio combines predefined or custom recognizers based on named entity recognition, regular expressions, rule-based logic and checksums [1]. In its default configuration it ships recognizers and models for English only. Other languages need a spaCy, Stanza or transformers model and per-language context words, because "each recognizer can support one language" [2].

**GLiNER and PII variants.** GLiNER is a compact bidirectional-encoder model that extracts arbitrary entity types given as labels [3]. Its PII fine-tunes include `gliner_multi_pii-v1`, trained on a synthetic PII set [4], and GLiNER2 [5] derivatives. One of these, GLiNER2-PII (205M parameters), covers 42 types in seven European languages and states that its performance on non-European scripts has not been measured [6]. Another, `gliner-pii-edge-v1.0`, lists 60+ categories [7].

**OpenAI Privacy Filter.** The model is a bidirectional token classifier with 1.5B total and 50M active parameters and eight span types. Its card describes it as "primarily English" and warns of drops on non-Latin scripts [8]. An independent evaluation across 32 benchmarks reports, on the MultiCoNER v2 general-NER sets, F1 of about 0.038 for Persian (Arabic script) and 0.027 for Ukrainian (Cyrillic). In the same study, XLM-RoBERTa fine-tuned on about 500 English synthetic examples overtakes the filter's zero-shot score [9].

**Browser-side and small models.** Mozilla's smart-pii-redactor fine-tunes mMiniLMv2-L6-H384 with a 20k vocabulary. Its 4-bit ONNX variant is 14.8 MB, it targets chat messages, and its test set covers eight languages that the card does not list [10]. Rampart fine-tunes a MiniLM-L6-H384 encoder (14.7 MB at Q4) on OpenPII 1.5M for in-browser use. It supports seven Latin-script languages and declares non-Latin scripts out of scope [11]. Rampart Edge is a 1.45 MB 4-bit model that runs on a trimmed onnxruntime-web build and is tuned for English and Spanish [12]. Horizon's `pii-redactor-small` (mmBERT-small, 141M) is trained on openpii-1.5m, Nemotron-PII and Gretel data. It self-reports an entity F1 of 0.971 on OpenPII validation but a redaction F1 of 0.351 on the external RedactionBench [13].

**Commercial APIs.** Google Sensitive Data Protection documents a catalogue of built-in infoTypes, many of them country-specific (e.g. `BRAZIL_CPF_NUMBER`, `JAPAN_INDIVIDUAL_NUMBER`) [14]. Amazon Comprehend detects PII in English or Spanish text, with universal types plus country-specific identifiers for Canada, India, the UK and the US [15].

**Recent benchmarks.** Recent benchmarks find that aggregate scores hide failures:
- REDACT (25 languages, 9 scripts) reports Presidio recall of 0.07 on high-sensitivity categories [16].
- RedactionBench finds 47.7% human agreement on contextual redactions [17].
- PII-TRACE reports that single-pass detectors miss about a third of gold characters in long dialogues [18].

Among the public models we found, those under 20 MB document Latin-script-only or unlisted language coverage, while the broader multilingual ones have 100M+ parameters. *Our work* targets 2–20 MB browser models for 54 languages, including non-Latin scripts, and evaluates them on external sets.

### M14.2 PII datasets

**ai4privacy.** The ai4privacy series is synthetic.
- `pii-masking-400k`: 406,896 entries in six European languages, 17 public classes. Academic use is encouraged, and commercial use requires a licence [19].
- `open-pii-masking-500k`: 580,227 entries in eight languages. It was generated with Llama 3.1/3.3 and is subject to the Llama Community License [20].
- `pii-masking-openpii-1.5m`: 1,636,375 examples, 19 labels and 30 languages, including seven Asia-Pacific locales, under CC-BY-4.0 [21].

**Other synthetic sets.** NVIDIA's Nemotron-PII is a persona-grounded set generated with NeMo Data Designer under CC BY 4.0. Its card describes 100,000 English records with 55+ PII/PHI categories across 50+ industries [22]. Gretel's `synthetic_pii_finance_multilingual` has 55,940 financial documents in seven European languages with 29 PII types under Apache 2.0. Its card notes that NER-based labelling "may introduce false negatives … or false positives" [23]. DialogPII adds LLM-generated, manually curated dialogues in 11 languages [24].

**Korean.** KDPII is a Korean dialogic de-identification dataset [25]. Its authors find that models recognise universal PII better than Korean-specific PII. A revised release (CC BY 4.0) has no fixed splits [26].

**Older resources.** Older multilingual NER corpora such as WikiANN (282 languages) [27] and WikiNEuRal [28] provide silver-standard Wikipedia annotations for generic entity types rather than PII. De-identification corpora are English and domain-specific: the 2014 i2b2/UTHealth corpus contains 1,304 clinical records [29], and TAB contains 1,268 ECHR court cases [30].

*Our data* differs in language count (54), in per-country identifier formats and name pools, and in PII levels that separate direct from indirect identifiers.

### M14.3 LLM-generated training data for NER and IE

**Generation and distillation.** Using LLMs as annotators or generators has been shown to cut labelling cost: GPT-3 labels matched human labels at 50–96% lower cost [31]. ZeroGen trains small task models on fully synthetic data [32]. UniversalNER distils ChatGPT's NER ability into smaller models through targeted instruction tuning [33]. NuNER pre-trains a compact encoder on LLM-annotated data and then fine-tunes it for entity recognition with few examples [56].

**Known limitations.** Known limitations concern diversity and fidelity. Simple class-conditioned prompts produce biased, low-diversity data (including regional bias), while attributed prompts that vary properties such as length and style improve downstream models [34]. Synthetic data helps less on tasks with subjective labels [35]. A survey covers annotation quality assessment and learning from noisy LLM labels [36].

*Our generator* follows the attributed-prompt direction with explicit design axes. The difference is that we measure label error rates by blind double review instead of assuming generator labels are correct.

### M14.4 Small multilingual encoders and compression

**Encoders.** XLM-R was pretrained on more than 2 TB of CommonCrawl text in 100 languages [37]. mDeBERTa-v3-base improves on XLM-R Base on zero-shot XNLI by about 3.6 points [38]. MiniLMv2 distils self-attention relations and includes multilingual students distilled from XLM-R [39]. The L6×H384 mMiniLMv2 has 107M parameters and uses the XLM-R tokenizer [40], so most of its size sits in the embedding matrix. mmBERT is a recent encoder pretrained on about 3T tokens in over 1,800 languages [41].

**Distillation for sequence labelling.** Knowledge distillation [42] has been applied to multilingual NER. XtremeDistil compresses mBERT up to 35× in parameters while keeping about 95% of F1 across 41 languages [43]. Structure-level distillation transfers knowledge from several monolingual teachers to one multilingual student [44].

**Vocabulary reduction.** Because multilingual models keep most parameters in the embedding layer, keeping only the needed languages' tokens cut mBERT parameters by up to about 45% at comparable accuracy [45]. Around 50% of the vocabulary is typically enough for a single language [46].

**Quantization and browser runtime.** GPTQ quantizes weights to 3–4 bits with little accuracy loss [47]. ONNX Runtime supports block-wise 4-bit weight-only quantization: MatMul becomes `MatMulNBits` (RTN, GPTQ or HQQ) and Gather becomes `GatherBlockQuantized` [48]. ONNX Runtime Web runs such models in the browser through WebAssembly, WebGPU or WebNN [49].

*We* combine these existing techniques rather than proposing a new one: distillation into a pruned-vocabulary mMiniLMv2 student and 4-bit weights. We report the combination's per-language behaviour across 54 languages.

### M14.5 Evaluation practice

**Leakage and contamination.** Train–test overlap inflates scores. In open-domain QA, about 30% of test questions have near-duplicate paraphrases in training, and models do far worse on non-overlapping ones [50]. Deduplication also reduces train–validation overlap, which affects over 4% of standard validation sets [51]. For NER, unseen entities drive much of the generalisation gap [52]. Contamination of benchmarks by LLM training data is a further concern [53].

**Anonymisation quality.** Span metrics do not show whether a masked text still identifies a person. Textwash evaluates an anonymisation tool by its accuracy, by the information lost, and by whether people can still identify the person from anonymised descriptions [57]. We report span-level metrics only.

**Run-to-run variance.** The random seed alone shifts NER F1 by about one point and can produce significant differences [54]. Weight initialisation and data order contribute similarly to fine-tuning variance [55].

*Because* LLM generators reuse names and values across independently seeded splits, we report seen and unseen-value subsets. Bootstrap intervals cover test-set sampling only; training variance is reported separately from repeated runs where we have them.

## M15. External evaluation sets

**Samples.** `training/scripts/ext_data.py eval` rebuilds each sample from the ids in `training/eval_sets/manifest.json`
(dataset revisions, ids in row order, SHA-256 of the output) and fails if a file differs. Samples were drawn on 2026-10-28,
before any v1.0 model existed:

- **openpii-1.5m** (`ai4privacy/pii-masking-openpii-1.5m`, revision `a785eb5`): no test split, so the validation split; reservoir
  sampling of 200 rows per language with seed 20261028, after an identical pass over the training split (the two passes share
  one random stream; the manifest lists the resulting uids).
- **KDPII** (Zenodo 16759166, v2): no official split. The 4,981 documents are shuffled with seed 20261028; the first 85% (4,233)
  are the training side, the rest (748 documents, 8,129 sentences) the test side. The split is by document, so no conversation
  contributes sentences to both sides. The manifest lists both document lists.
- **Nemotron-PII** (`nvidia/Nemotron-PII`, revision `b70ffaf`): test split, 1,000 rows per locale (`us`, `intl`) with
  `pandas.sample(random_state=20261028)`. A uid repeats across the two locales, so ids are `<locale>:<uid>`.
- **Gretel finance** (`gretelai/synthetic_pii_finance_multilingual`, revision `7b844d1`): test split, up to 400 rows per language
  with the same sampler; ids are row positions in the test file.

**Evaluation mapping** (`external_sets.py`).
- **Names and addresses.** First and last names become PERSON and are merged when adjacent. Street, building number,
  postcode and coordinates become ADDRESS. A city, county, state or country joins an ADDRESS it touches (≤ 4 characters
  apart); otherwise it is ignored.
- **Identifiers and accounts.** Account, card, routing and SWIFT numbers become FINANCIAL. Personal identifiers (SSN,
  passport, licence, plate, device and IP) become ID_NUMBER.
- **Usernames, birth dates and secrets.** User names become USERNAME and dates of birth DATE_OF_BIRTH. Passwords, PINs,
  API keys and cookies become SECRET.
- **Ignored.** Placeholders such as `[Guest Name]` are not values. On Nemotron and Gretel, every other type is an `ignore`
  span (scored neither as PII nor as over-masking).
- **openpii.** A DATE becomes DATE_OF_BIRTH only with a birth cue in the 40 characters before it. A CITY becomes ADDRESS only
  within 3 characters of another address part.
- **KDPII.** Names and nicknames become PERSON, workplace, school and department ORG, and an address ADDRESS only if it
  contains a digit (a bare region such as 서울시 is not masked).
- **Unmapped types.** On openpii and KDPII, unmapped types are simply not spans, as when the sets were first built.

**Training mapping** (`ext_data.py train`, for a model trained on our data plus the external training sides; considered and rejected — experiments appendix, N).
It reads only the sides that the evaluation samples do not come from: the openpii training split, the KDPII training
documents, and the Nemotron and Gretel training splits. Rows that duplicate an evaluation sample exactly or by template are then
removed with `dedupe_splits.py` (evaluation samples ranked first). It differs from the evaluation mapping where v1.0 is finer:
- **Level 2.** A surname that merges with no given name is PERSON_L2, and so is a KDPII nickname.
- **URLs.** Public and personal URLs are not distinguished in these sources, so URLs are `ignore`.
- **Dates.** A date without a birth cue is `ignore` rather than "not PII".
- **Unmapped types.** Every unmapped type is `ignore`.
- **`known` labels.** Each row lists the v1 labels its source annotates (for example, openpii annotates no ORG, URL or
  SECRET), so a trainer can avoid learning "not PII" for types the source never marks.
