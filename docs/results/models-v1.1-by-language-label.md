English | [한국어](models-v1.1-by-language-label.ko.md)

# Models v1.1 by language and label: misses and over-masking

What each size of the shipped v1.1 models misses and over-masks, per language and per label, on newmean-pii-dataset
(public test, 54 languages) and the four external datasets of the README table (OpenPII, 30 languages; Gretel finance,
7; Nemotron-PII, English; KDPII, Korean). Each rate sits next to its counts, so you can see where a sample is small. Use
it to check your languages and labels before you choose a model size, a block list or a review step, and measure on a
sample of your own text too. The same tables, sortable and in the languages of the demo: https://pii.newmean.ai/results/

- **Miss %**: share of PII characters (not spaces) left unmasked, any label counting as masked. **Over-masking %**: share
  of PII-free characters masked. Each cell is miss / over-masking.
- **By label**, miss % is per gold label. Over-masking % is split by the label the model masked with (its level-2 labels
  counted as PERSON and URL), as a share of all PII-free characters of the set, so the label rows add up to the set's
  over-masking, up to rounding. A dash means the set has no gold spans of that label: OpenPII, for example, does not label
  organisations, so an organisation name the model masks there counts as over-masking. The models have no IP_ADDRESS label
  (the optional pattern layer has); an IP address masked under another label counts as found.
- **Model only**: the default file per size, without the pattern layer or the SDK's whole-word extension, measured once on
  2026-11-01. These are the numbers of the README table and the technical report (Section 6); the totals below equal them
  cell for cell. [models-v1.1-public-test.md](models-v1.1-public-test.md) measures the public test through the SDK with its
  defaults, so its language cells are up to 1.2 points lower or 0.4 points higher in miss, and up to 0.7 points higher in
  over-masking.
- **Counts**: gold spans are the set's PII spans in our labels (on Nemotron-PII and Gretel finance, types outside our
  guideline count neither as PII nor as over-masking). † marks fewer than 100 gold spans, where a few spans move the rate by
  a point or more. Every language has 287 to 343 gold spans on the public test (305 to 320 rows), 672 to 832 on OpenPII
  (200 rows) and 1,259 to 1,659 on Gretel finance (394 to 400 rows), so differences under about one point between
  languages are within what a few rows change.
- The public test was written to the same design as our training data, so its rates are lower than on text the models have
  not seen; the external sets are the fairer comparison. Gretel finance is hard for every detector in the README table.

Tables made by `training/scripts/results_by_language.py` from the evaluation reports.

## Each set, all rows (miss / over-masking %)

| set | languages | rows | gold spans | large | medium | small | nano |
|---|---|---|---|---|---|---|---|
| newmean-pii-dataset (public test) | 54 | 17,058 | 17,147 | 1.6 / 0.5 | 1.9 / 0.6 | 2.4 / 1.0 | 2.9 / 1.5 |
| OpenPII | 30 | 6,000 | 23,104 | 4.9 / 4.1 | 6.3 / 4.3 | 5.6 / 5.3 | 5.0 / 6.2 |
| Gretel finance | 7 | 2,794 | 10,154 | 21.4 / 5.5 | 21.5 / 5.4 | 22.3 / 5.8 | 21.4 / 7.6 |
| Nemotron-PII | en | 2,000 | 8,756 | 6.1 / 1.6 | 7.7 / 0.9 | 8.4 / 0.9 | 9.9 / 2.3 |
| KDPII | ko | 8,129 | 2,016 | 9.6 / 1.7 | 11.5 / 1.6 | 10.0 / 2.5 | 9.5 / 4.1 |

## Public test by language

| language | rows | gold spans | large | medium | small | nano |
|---|---|---|---|---|---|---|
| Albanian (sq) | 317 | 330 | 1.5 / 0.3 | 2.9 / 0.4 | 1.8 / 0.5 | 2.2 / 0.9 |
| Arabic (ar) | 319 | 305 | 1.8 / 0.9 | 2.3 / 1.1 | 2.5 / 1.7 | 4.3 / 2.9 |
| Azerbaijani (az) | 314 | 326 | 1.7 / 0.4 | 1.7 / 0.6 | 3.5 / 1.1 | 3.2 / 1.5 |
| Bengali (bn) | 316 | 314 | 1.8 / 0.8 | 1.8 / 1.1 | 3.7 / 1.4 | 4.1 / 2.4 |
| Bulgarian (bg) | 314 | 319 | 0.7 / 0.6 | 1.8 / 0.7 | 1.4 / 1.4 | 2.2 / 1.5 |
| Catalan (ca) | 315 | 302 | 1.3 / 0.2 | 2.5 / 0.3 | 2.5 / 0.6 | 2.3 / 1.0 |
| Chinese (zh) | 318 | 342 | 0.3 / 0.4 | 0.5 / 0.5 | 0.7 / 0.9 | 0.7 / 1.4 |
| Croatian (hr) | 316 | 334 | 2.4 / 0.4 | 2.2 / 0.4 | 2.2 / 0.9 | 2.5 / 1.1 |
| Czech (cs) | 319 | 307 | 1.6 / 0.4 | 2.4 / 0.5 | 2.4 / 0.8 | 4.4 / 1.1 |
| Danish (da) | 319 | 327 | 1.8 / 0.3 | 2.1 / 0.3 | 2.5 / 0.6 | 2.3 / 0.8 |
| Dutch (nl) | 319 | 335 | 0.8 / 0.4 | 0.6 / 0.4 | 1.1 / 0.7 | 1.3 / 0.9 |
| English (en) | 315 | 327 | 3.2 / 0.4 | 3.1 / 0.5 | 3.1 / 0.8 | 4.2 / 1.6 |
| Estonian (et) | 317 | 336 | 1.2 / 0.5 | 2.4 / 0.5 | 2.4 / 1.2 | 3.0 / 1.6 |
| Finnish (fi) | 311 | 343 | 1.9 / 0.5 | 2.5 / 0.6 | 2.6 / 1.2 | 4.0 / 1.7 |
| French (fr) | 315 | 325 | 1.5 / 0.5 | 2.1 / 0.5 | 2.3 / 1.0 | 2.8 / 1.4 |
| Georgian (ka) | 315 | 330 | 1.7 / 0.5 | 3.0 / 0.6 | 2.9 / 1.1 | 4.7 / 1.5 |
| German (de) | 314 | 299 | 2.9 / 0.5 | 3.0 / 0.8 | 2.8 / 1.3 | 3.6 / 1.9 |
| Greek (el) | 318 | 323 | 2.5 / 0.6 | 1.6 / 0.6 | 2.8 / 1.0 | 3.3 / 1.5 |
| Hebrew (he) | 312 | 287 | 2.9 / 0.7 | 2.0 / 0.8 | 2.3 / 1.9 | 5.0 / 2.9 |
| Hindi (hi) | 313 | 309 | 2.1 / 0.6 | 2.7 / 0.7 | 5.8 / 1.4 | 4.1 / 2.4 |
| Hungarian (hu) | 317 | 337 | 1.7 / 0.5 | 1.7 / 0.7 | 3.1 / 1.2 | 3.4 / 1.3 |
| Icelandic (is) | 318 | 317 | 1.3 / 0.4 | 2.0 / 0.6 | 2.3 / 0.9 | 2.9 / 1.2 |
| Indonesian (id) | 319 | 308 | 2.1 / 0.7 | 2.2 / 0.7 | 1.9 / 1.1 | 2.3 / 1.8 |
| Irish (ga) | 316 | 309 | 0.3 / 0.4 | 0.5 / 0.6 | 0.6 / 1.3 | 1.8 / 1.9 |
| Italian (it) | 315 | 311 | 1.0 / 0.4 | 1.7 / 0.6 | 1.5 / 1.1 | 2.1 / 1.3 |
| Japanese (ja) | 314 | 298 | 0.8 / 0.3 | 1.0 / 0.2 | 1.3 / 0.6 | 1.7 / 1.1 |
| Kazakh (kk) | 315 | 315 | 4.2 / 0.3 | 4.1 / 0.5 | 5.8 / 0.8 | 6.0 / 1.3 |
| Khmer (km) | 319 | 305 | 0.7 / 0.4 | 1.2 / 0.6 | 2.4 / 0.9 | 1.9 / 1.4 |
| Korean (ko) | 316 | 322 | 2.4 / 0.5 | 2.3 / 0.7 | 2.4 / 0.8 | 2.5 / 1.3 |
| Latvian (lv) | 310 | 304 | 1.7 / 0.5 | 1.9 / 0.5 | 3.1 / 0.8 | 3.7 / 1.4 |
| Lithuanian (lt) | 319 | 339 | 2.1 / 0.4 | 1.3 / 0.4 | 1.6 / 0.9 | 2.8 / 1.3 |
| Malay (ms) | 315 | 317 | 0.4 / 0.4 | 0.7 / 0.4 | 1.5 / 0.8 | 2.0 / 1.3 |
| Maltese (mt) | 320 | 322 | 1.7 / 0.5 | 2.3 / 0.6 | 3.1 / 1.0 | 3.5 / 1.3 |
| Marathi (mr) | 308 | 296 | 2.5 / 0.5 | 2.6 / 0.6 | 2.7 / 1.3 | 3.1 / 2.1 |
| Norwegian (no) | 317 | 311 | 1.3 / 0.4 | 1.8 / 0.4 | 1.6 / 0.7 | 3.2 / 1.1 |
| Persian (fa) | 317 | 310 | 3.7 / 0.7 | 4.5 / 0.9 | 4.0 / 1.4 | 5.1 / 2.0 |
| Polish (pl) | 316 | 317 | 0.7 / 0.4 | 0.7 / 0.5 | 0.6 / 0.9 | 1.3 / 1.3 |
| Portuguese (pt) | 316 | 343 | 1.1 / 0.4 | 1.7 / 0.8 | 3.3 / 1.0 | 4.1 / 1.5 |
| Romanian (ro) | 316 | 335 | 2.5 / 0.4 | 1.6 / 0.7 | 2.4 / 0.7 | 2.5 / 1.2 |
| Russian (ru) | 305 | 292 | 0.8 / 0.6 | 1.0 / 0.6 | 1.5 / 0.9 | 2.0 / 1.5 |
| Serbian (sr) | 314 | 305 | 1.9 / 0.4 | 2.1 / 0.4 | 3.0 / 0.6 | 2.3 / 1.1 |
| Slovak (sk) | 316 | 326 | 2.0 / 0.3 | 1.1 / 0.4 | 2.4 / 0.7 | 2.7 / 1.0 |
| Slovenian (sl) | 318 | 334 | 1.1 / 0.2 | 1.9 / 0.3 | 2.4 / 0.7 | 3.3 / 0.9 |
| Spanish (es) | 318 | 335 | 1.3 / 0.5 | 1.8 / 0.7 | 2.1 / 0.9 | 3.2 / 1.3 |
| Swahili (sw) | 315 | 328 | 2.3 / 0.5 | 2.1 / 0.8 | 2.9 / 1.6 | 3.3 / 1.8 |
| Swedish (sv) | 314 | 311 | 1.3 / 0.4 | 1.4 / 0.5 | 2.1 / 1.4 | 2.2 / 1.6 |
| Tagalog (tl) | 314 | 315 | 1.4 / 0.4 | 1.3 / 0.5 | 2.1 / 0.7 | 2.3 / 1.2 |
| Telugu (te) | 317 | 294 | 0.9 / 0.7 | 1.7 / 0.6 | 1.8 / 1.0 | 2.1 / 1.9 |
| Thai (th) | 317 | 307 | 3.7 / 0.7 | 3.9 / 0.8 | 4.6 / 1.1 | 5.0 / 2.2 |
| Turkish (tr) | 319 | 308 | 0.5 / 0.4 | 1.4 / 0.6 | 1.1 / 1.1 | 1.9 / 2.0 |
| Ukrainian (uk) | 317 | 299 | 0.3 / 0.2 | 0.5 / 0.4 | 1.5 / 0.6 | 1.5 / 1.2 |
| Urdu (ur) | 318 | 306 | 1.3 / 0.9 | 1.1 / 0.9 | 1.5 / 1.2 | 2.0 / 2.0 |
| Uzbek (uz) | 317 | 313 | 0.6 / 0.4 | 0.7 / 0.4 | 1.1 / 0.9 | 2.1 / 1.3 |
| Vietnamese (vi) | 320 | 338 | 1.7 / 0.5 | 1.4 / 0.8 | 1.3 / 1.2 | 2.0 / 1.7 |

## Public test by label

| label | gold spans | PII characters | large | medium | small | nano |
|---|---|---|---|---|---|---|
| PERSON | 5,878 | 55,982 | 2.3 / 0.2 | 3.1 / 0.2 | 4.1 / 0.4 | 5.1 / 0.7 |
| ID_NUMBER | 2,691 | 33,392 | 1.0 / 0.1 | 1.3 / 0.1 | 1.4 / 0.1 | 1.2 / 0.2 |
| EMAIL | 1,221 | 33,305 | 0.0 / 0.0 | 0.0 / 0.0 | 0.2 / 0.0 | 0.3 / 0.0 |
| PHONE | 1,980 | 28,544 | 0.1 / 0.0 | 0.2 / 0.0 | 0.8 / 0.0 | 0.6 / 0.0 |
| ADDRESS | 1,129 | 27,552 | 3.3 / 0.1 | 3.4 / 0.1 | 4.4 / 0.1 | 5.9 / 0.2 |
| SECRET | 620 | 22,844 | 0.4 / 0.0 | 0.4 / 0.0 | 0.3 / 0.0 | 0.5 / 0.1 |
| FINANCIAL | 1,231 | 21,398 | 0.4 / 0.0 | 0.6 / 0.0 | 0.6 / 0.1 | 1.1 / 0.1 |
| ORG | 902 | 19,041 | 7.0 / 0.1 | 6.4 / 0.1 | 7.9 / 0.2 | 9.6 / 0.2 |
| URL | 330 | 13,997 | 0.6 / 0.0 | 1.1 / 0.0 | 0.6 / 0.0 | 0.8 / 0.0 |
| USERNAME | 612 | 7,901 | 2.4 / 0.0 | 2.7 / 0.0 | 3.5 / 0.0 | 4.5 / 0.0 |
| DATE_OF_BIRTH | 370 | 3,861 | 1.8 / 0.0 | 3.0 / 0.0 | 2.3 / 0.0 | 4.5 / 0.0 |
| IP_ADDRESS | 183 | 3,555 | 0.1 / 0.0 | 0.2 / 0.0 | 0.0 / 0.0 | 0.7 / 0.0 |

## OpenPII by language

| language | rows | gold spans | large | medium | small | nano |
|---|---|---|---|---|---|---|
| Bulgarian (bg) | 200 | 814 | 5.9 / 3.8 | 7.5 / 3.7 | 6.6 / 4.6 | 6.5 / 6.0 |
| Chinese (zh) | 200 | 720 | 5.4 / 8.2 | 6.2 / 8.3 | 5.5 / 8.2 | 4.4 / 9.9 |
| Croatian (hr) | 200 | 795 | 6.1 / 4.3 | 6.5 / 4.5 | 6.6 / 4.9 | 6.0 / 5.3 |
| Czech (cs) | 200 | 779 | 6.6 / 4.8 | 6.6 / 5.4 | 6.6 / 6.6 | 6.0 / 7.1 |
| Danish (da) | 200 | 711 | 2.9 / 4.0 | 4.5 / 4.1 | 3.9 / 4.7 | 3.5 / 5.0 |
| Dutch (nl) | 200 | 745 | 4.5 / 3.4 | 5.8 / 3.5 | 4.5 / 4.1 | 5.1 / 4.9 |
| English (en) | 200 | 798 | 4.9 / 3.3 | 6.4 / 3.6 | 6.3 / 4.4 | 6.5 / 5.4 |
| Estonian (et) | 200 | 726 | 5.8 / 3.4 | 7.2 / 3.6 | 6.9 / 4.5 | 4.7 / 5.5 |
| Finnish (fi) | 200 | 733 | 4.2 / 4.6 | 5.9 / 4.4 | 5.5 / 5.6 | 4.5 / 6.1 |
| French (fr) | 200 | 786 | 5.1 / 3.3 | 5.9 / 3.7 | 4.4 / 4.7 | 4.7 / 5.2 |
| German (de) | 200 | 699 | 4.2 / 3.4 | 6.5 / 3.9 | 6.7 / 4.8 | 7.0 / 5.2 |
| Greek (el) | 200 | 753 | 5.3 / 4.7 | 5.0 / 4.5 | 4.0 / 6.4 | 4.0 / 8.2 |
| Hungarian (hu) | 200 | 768 | 4.5 / 4.6 | 7.9 / 4.7 | 6.2 / 5.8 | 5.0 / 6.8 |
| Indonesian (id) | 200 | 822 | 3.7 / 4.2 | 7.3 / 4.2 | 5.8 / 5.1 | 4.3 / 6.1 |
| Italian (it) | 200 | 832 | 4.3 / 3.2 | 4.9 / 3.4 | 5.3 / 4.5 | 5.1 / 5.3 |
| Japanese (ja) | 200 | 793 | 6.1 / 7.5 | 7.4 / 7.2 | 7.6 / 8.1 | 6.4 / 11.2 |
| Korean (ko) | 200 | 803 | 5.6 / 7.1 | 7.9 / 7.6 | 4.9 / 8.4 | 4.7 / 9.8 |
| Latvian (lv) | 200 | 738 | 6.2 / 5.2 | 7.5 / 5.7 | 5.9 / 6.2 | 5.5 / 6.9 |
| Lithuanian (lt) | 200 | 817 | 4.9 / 4.8 | 6.6 / 4.9 | 5.3 / 6.2 | 5.7 / 6.8 |
| Malay (ms) | 200 | 794 | 3.6 / 3.9 | 5.8 / 3.8 | 6.3 / 5.2 | 5.0 / 6.6 |
| Polish (pl) | 200 | 709 | 5.4 / 3.6 | 7.5 / 3.9 | 4.7 / 4.8 | 4.8 / 5.3 |
| Portuguese (pt) | 200 | 822 | 3.0 / 3.3 | 4.8 / 4.0 | 4.7 / 5.4 | 4.0 / 6.4 |
| Romanian (ro) | 200 | 783 | 3.5 / 4.0 | 5.1 / 4.3 | 3.6 / 5.4 | 3.7 / 5.7 |
| Serbian (sr) | 200 | 816 | 3.8 / 5.1 | 6.8 / 5.2 | 5.8 / 6.5 | 4.5 / 7.1 |
| Slovak (sk) | 200 | 770 | 5.7 / 3.3 | 6.2 / 3.4 | 5.8 / 4.3 | 5.5 / 5.1 |
| Slovenian (sl) | 200 | 746 | 8.0 / 3.0 | 8.5 / 3.2 | 9.0 / 4.0 | 5.5 / 5.1 |
| Spanish (es) | 200 | 770 | 3.0 / 3.9 | 4.4 / 4.1 | 3.2 / 5.6 | 3.9 / 6.4 |
| Swedish (sv) | 200 | 672 | 4.8 / 3.7 | 5.9 / 4.2 | 5.3 / 4.9 | 4.1 / 5.5 |
| Tagalog (tl) | 200 | 775 | 4.8 / 3.9 | 7.0 / 3.9 | 6.7 / 5.1 | 4.4 / 5.4 |
| Vietnamese (vi) | 200 | 815 | 4.7 / 4.2 | 5.9 / 4.1 | 5.9 / 5.5 | 4.7 / 7.0 |

## OpenPII by label

| label | gold spans | PII characters | large | medium | small | nano |
|---|---|---|---|---|---|---|
| PERSON | 5,657 | 81,054 | 6.2 / 0.5 | 10.5 / 0.6 | 7.9 / 1.0 | 7.7 / 1.6 |
| ADDRESS | 4,196 | 60,065 | 10.1 / 0.7 | 10.7 / 0.8 | 10.7 / 0.9 | 9.4 / 1.4 |
| ID_NUMBER | 5,727 | 58,270 | 3.7 / 0.1 | 4.8 / 0.1 | 4.8 / 0.1 | 2.5 / 0.1 |
| EMAIL | 3,239 | 51,125 | 0.2 / 0.0 | 0.5 / 0.1 | 0.3 / 0.1 | 0.5 / 0.1 |
| PHONE | 2,394 | 32,761 | 0.3 / 0.0 | 0.3 / 0.0 | 0.3 / 0.0 | 0.6 / 0.1 |
| FINANCIAL | 1,426 | 22,755 | 0.9 / 0.1 | 0.6 / 0.0 | 0.3 / 0.1 | 0.3 / 0.1 |
| DATE_OF_BIRTH | 465 | 5,643 | 24.8 / 2.3 | 28.7 / 2.3 | 28.4 / 2.4 | 29.4 / 2.1 |
| ORG | — | — | — / 0.4 | — / 0.4 | — / 0.6 | — / 0.8 |
| SECRET | — | — | — / 0.0 | — / 0.0 | — / 0.0 | — / 0.0 |
| URL | — | — | — / 0.0 | — / 0.0 | — / 0.0 | — / 0.0 |
| USERNAME | — | — | — / 0.0 | — / 0.0 | — / 0.0 | — / 0.0 |

## Gretel finance by language

| language | rows | gold spans | large | medium | small | nano |
|---|---|---|---|---|---|---|
| Dutch (nl) | 400 | 1,651 | 34.7 / 5.8 | 34.5 / 5.8 | 36.6 / 6.5 | 35.3 / 7.9 |
| English (en) | 400 | 1,261 | 10.1 / 4.2 | 9.6 / 4.2 | 11.6 / 4.5 | 11.4 / 6.0 |
| French (fr) | 394 | 1,259 | 12.8 / 5.9 | 13.1 / 5.9 | 13.3 / 7.0 | 12.7 / 8.2 |
| German (de) | 400 | 1,471 | 20.2 / 5.8 | 20.8 / 5.7 | 21.8 / 5.5 | 21.3 / 7.5 |
| Italian (it) | 400 | 1,426 | 18.4 / 5.4 | 18.5 / 5.3 | 18.8 / 6.3 | 17.8 / 7.6 |
| Spanish (es) | 400 | 1,427 | 22.9 / 4.7 | 23.2 / 4.8 | 23.2 / 5.5 | 21.6 / 6.9 |
| Swedish (sv) | 400 | 1,659 | 26.4 / 6.9 | 26.6 / 6.5 | 26.7 / 5.6 | 25.7 / 9.5 |

## Gretel finance by label

| label | gold spans | PII characters | large | medium | small | nano |
|---|---|---|---|---|---|---|
| PERSON | 5,796 | 78,527 | 36.2 / 0.3 | 37.2 / 0.4 | 37.2 / 0.6 | 36.1 / 1.0 |
| ADDRESS | 2,023 | 48,798 | 10.1 / 0.5 | 8.7 / 0.6 | 11.2 / 0.7 | 10.0 / 1.0 |
| EMAIL | 564 | 13,562 | 1.4 / 0.0 | 1.4 / 0.0 | 1.5 / 0.1 | 1.4 / 0.1 |
| ID_NUMBER | 573 | 7,770 | 5.6 / 0.7 | 4.8 / 1.4 | 5.4 / 0.9 | 6.0 / 1.0 |
| FINANCIAL | 465 | 6,570 | 10.2 / 0.5 | 9.1 / 0.5 | 9.6 / 1.0 | 7.5 / 1.0 |
| PHONE | 436 | 5,327 | 1.3 / 0.2 | 1.9 / 0.1 | 1.4 / 0.1 | 1.5 / 0.1 |
| SECRET | 151 | 2,187 | 6.9 / 1.2 | 13.4 / 0.4 | 12.2 / 0.3 | 13.7 / 1.3 |
| DATE_OF_BIRTH | 107 | 1,126 | 10.2 / 0.0 | 12.3 / 0.0 | 17.1 / 0.0 | 22.2 / 0.0 |
| USERNAME | 39 † | 381 | 31.8 / 0.0 | 39.4 / 0.0 | 36.0 / 0.0 | 47.5 / 0.0 |
| ORG | — | — | — / 0.1 | — / 0.1 | — / 0.2 | — / 0.3 |
| URL | — | — | — / 1.8 | — / 1.8 | — / 1.9 | — / 1.8 |

† fewer than 100 gold spans.

## Nemotron-PII (English) by label

| label | gold spans | PII characters | large | medium | small | nano |
|---|---|---|---|---|---|---|
| URL | 728 | 38,124 | 6.9 / 0.0 | 11.7 / 0.0 | 12.4 / 0.0 | 15.0 / 0.2 |
| EMAIL | 1,106 | 28,830 | 0.2 / 0.0 | 0.2 / 0.0 | 0.3 / 0.1 | 0.6 / 0.1 |
| ID_NUMBER | 1,757 | 21,825 | 0.7 / 0.1 | 0.8 / 0.4 | 1.2 / 0.1 | 2.9 / 0.2 |
| PERSON | 1,918 | 17,058 | 2.8 / 0.2 | 5.5 / 0.2 | 6.9 / 0.2 | 8.9 / 0.7 |
| SECRET | 485 | 17,049 | 30.2 / 1.1 | 30.2 / 0.1 | 30.1 / 0.4 | 29.5 / 0.8 |
| ADDRESS | 573 | 10,494 | 6.5 / 0.1 | 6.0 / 0.1 | 7.8 / 0.1 | 9.4 / 0.2 |
| FINANCIAL | 872 | 9,890 | 0.8 / 0.0 | 1.3 / 0.0 | 1.1 / 0.0 | 2.7 / 0.1 |
| PHONE | 615 | 7,355 | 0.1 / 0.0 | 0.4 / 0.0 | 0.3 / 0.0 | 0.6 / 0.0 |
| USERNAME | 350 | 3,855 | 10.8 / 0.0 | 14.2 / 0.0 | 24.0 / 0.0 | 28.8 / 0.0 |
| DATE_OF_BIRTH | 352 | 3,520 | 0.0 / 0.0 | 0.3 / 0.0 | 2.3 / 0.0 | 5.1 / 0.0 |
| ORG | — | — | — / 0.1 | — / 0.0 | — / 0.1 | — / 0.1 |

## KDPII (Korean) by label

| label | gold spans | PII characters | large | medium | small | nano |
|---|---|---|---|---|---|---|
| FINANCIAL | 243 | 4,502 | 7.1 / 0.0 | 7.1 / 0.0 | 5.7 / 0.0 | 6.7 / 0.0 |
| ID_NUMBER | 243 | 2,649 | 2.0 / 0.0 | 3.6 / 0.0 | 2.3 / 0.1 | 5.3 / 0.1 |
| PHONE | 197 | 2,395 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| EMAIL | 88 † | 1,897 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| URL | 63 † | 1,738 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| ORG | 424 | 1,698 | 65.7 / 0.3 | 72.0 / 0.3 | 63.6 / 0.5 | 52.5 / 0.8 |
| PERSON | 514 | 1,368 | 20.8 / 1.0 | 27.4 / 0.9 | 27.9 / 1.4 | 27.1 / 2.4 |
| USERNAME | 98 † | 1,158 | 2.8 / 0.0 | 8.7 / 0.0 | 5.9 / 0.0 | 3.0 / 0.0 |
| DATE_OF_BIRTH | 102 | 889 | 0.0 / 0.1 | 3.9 / 0.1 | 2.1 / 0.1 | 2.1 / 0.1 |
| ADDRESS | 44 † | 463 | 0.0 / 0.2 | 0.7 / 0.3 | 1.7 / 0.3 | 3.2 / 0.6 |
| SECRET | — | — | — / 0.1 | — / 0.1 | — / 0.1 | — / 0.1 |

† fewer than 100 gold spans.
