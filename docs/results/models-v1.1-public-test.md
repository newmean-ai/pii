English | [한국어](models-v1.1-public-test.ko.md)

# Models v1.1 on the public test, by label, format and language

What each model size misses on newmean-pii-dataset (public test, 17,058 rows in 54 languages), through the SDK with its
default settings (SDK 1.3 to 1.7: whole words on, pattern layer off, emoji left out of what the model reads). For deciding
which labels local masking covers well in your setting and where you still want a larger model, a block list or a review
step. Our models were trained on data of the same design, so these numbers are lower than on text the models have not seen
(the README's external datasets are the fairer comparison); measure on a sample of your own text too.

- **Miss %**: share of PII characters (not spaces) left unmasked, any label counting as masked. **Over-masking %**: share of
  non-PII characters masked. Cells with two numbers are miss / over-masking.
- Measured 2026-11-03 for large, medium and small (the 1.3 release run) and 2026-11-24 for nano; SDK 1.4 to 1.7 give the
  same spans with default settings, and a medium run on 2026-11-24 matched the 1.3 run in every cell. Single runs.
- Rows per language: 305 to 320; per format: 529 to 1,767. Differences under about one point between languages or formats
  are within what a few rows change.
- Model only, with counts, over-masking per label and the four external datasets by language and label:
  [models-v1.1-by-language-label.md](models-v1.1-by-language-label.md).

## All rows

|  | rows | large | medium | small | nano |
|---|---|---|---|---|---|
| all | 17,058 | 1.5 / 0.5 | 1.8 / 0.7 | 2.1 / 1.1 | 2.6 / 1.8 |

## By label (miss %)

| label | PII characters | large | medium | small | nano |
|---|---|---|---|---|---|
| PERSON | 55,982 | 2.2 | 3.0 | 3.8 | 4.5 |
| ID_NUMBER | 33,392 | 0.9 | 1.3 | 1.2 | 1.1 |
| EMAIL | 33,305 | 0.0 | 0.1 | 0.2 | 0.3 |
| PHONE | 28,544 | 0.1 | 0.1 | 0.6 | 0.4 |
| ADDRESS | 27,552 | 3.1 | 3.1 | 4.2 | 5.4 |
| SECRET | 22,844 | 0.3 | 0.3 | 0.1 | 0.2 |
| FINANCIAL | 21,398 | 0.4 | 0.6 | 0.5 | 0.8 |
| ORG | 19,041 | 6.6 | 6.0 | 7.4 | 9.1 |
| URL | 13,997 | 0.6 | 1.1 | 0.6 | 0.7 |
| USERNAME | 7,901 | 2.2 | 2.8 | 2.9 | 4.2 |
| DATE_OF_BIRTH | 3,861 | 1.8 | 2.8 | 2.1 | 3.7 |
| IP_ADDRESS | 3,555 | 0.1 | 0.2 | 0.0 | 0.3 |

## By format (miss / over-masking %)

| format | rows | large | medium | small | nano |
|---|---|---|---|---|---|
| CSV/TSV row | 891 | 1.5 / 0.7 | 1.9 / 0.9 | 1.8 / 1.6 | 2.5 / 1.8 |
| JSON, config, code, logs | 695 | 0.6 / 0.3 | 1.4 / 0.4 | 1.1 / 0.7 | 1.3 / 1.4 |
| OCR text | 529 | 2.6 / 1.4 | 3.0 / 1.7 | 3.2 / 2.1 | 3.8 / 3.1 |
| SMS, one-line message | 693 | 1.0 / 0.7 | 1.5 / 0.8 | 2.1 / 1.4 | 1.7 / 2.3 |
| email | 900 | 1.3 / 0.5 | 1.6 / 0.6 | 1.3 / 1.1 | 2.0 / 1.7 |
| field and case notes | 693 | 2.5 / 0.5 | 2.7 / 0.5 | 2.4 / 0.9 | 2.6 / 1.5 |
| form field answer | 898 | 0.6 / 0.4 | 0.6 / 0.6 | 1.3 / 0.9 | 0.9 / 1.6 |
| formal document | 700 | 1.7 / 0.5 | 1.3 / 0.6 | 1.9 / 1.2 | 1.7 / 2.0 |
| group chat | 1,065 | 2.6 / 0.5 | 3.4 / 0.7 | 4.0 / 1.0 | 5.5 / 1.6 |
| interview transcript | 695 | 1.2 / 0.4 | 1.2 / 0.4 | 1.6 / 0.7 | 2.9 / 1.2 |
| meeting notes, memo | 694 | 0.6 / 0.8 | 0.9 / 0.8 | 1.4 / 1.2 | 2.6 / 1.7 |
| prompt to an AI assistant | 1,598 | 0.7 / 0.3 | 0.8 / 0.4 | 1.2 / 0.8 | 1.2 / 1.2 |
| resume, profile, bio | 540 | 3.1 / 0.4 | 2.9 / 0.7 | 4.6 / 1.3 | 5.5 / 2.1 |
| single spreadsheet cell | 1,358 | 1.8 / 3.8 | 2.5 / 4.5 | 2.4 / 6.2 | 2.4 / 7.4 |
| small table (CSV/TSV) | 694 | 1.9 / 1.4 | 2.4 / 1.5 | 2.9 / 3.1 | 3.0 / 4.5 |
| social post, review | 887 | 1.2 / 0.5 | 1.4 / 0.7 | 1.0 / 1.2 | 2.6 / 2.0 |
| speech-to-text transcript | 530 | 2.8 / 0.9 | 2.7 / 0.9 | 4.2 / 1.9 | 3.9 / 2.6 |
| support ticket, live chat | 695 | 0.7 / 0.3 | 0.5 / 0.4 | 1.1 / 0.7 | 1.5 / 1.2 |
| survey free-text answer | 1,767 | 1.6 / 0.3 | 1.8 / 0.3 | 1.9 / 0.7 | 2.8 / 1.0 |
| web data (HTML, URLs, headers) | 536 | 1.3 / 0.4 | 1.3 / 0.4 | 1.8 / 0.7 | 1.9 / 0.9 |

## By language (miss / over-masking %)

| language | rows | large | medium | small | nano |
|---|---|---|---|---|---|
| Albanian (sq) | 317 | 1.4 / 0.4 | 2.9 / 0.5 | 1.7 / 0.6 | 1.7 / 1.1 |
| Arabic (ar) | 319 | 1.8 / 0.9 | 2.1 / 1.2 | 2.3 / 2.0 | 3.5 / 3.6 |
| Azerbaijani (az) | 314 | 1.6 / 0.6 | 1.7 / 0.7 | 3.3 / 1.4 | 2.9 / 1.8 |
| Bengali (bn) | 316 | 1.6 / 0.9 | 1.3 / 1.3 | 3.1 / 1.6 | 3.6 / 2.9 |
| Bulgarian (bg) | 314 | 0.6 / 0.7 | 1.4 / 0.8 | 0.9 / 1.5 | 1.5 / 1.7 |
| Catalan (ca) | 315 | 1.2 / 0.2 | 2.4 / 0.4 | 2.3 / 0.7 | 1.9 / 1.1 |
| Chinese (zh) | 318 | 0.3 / 0.4 | 0.5 / 0.5 | 0.7 / 0.9 | 0.7 / 1.6 |
| Croatian (hr) | 316 | 2.4 / 0.5 | 2.0 / 0.5 | 2.1 / 1.0 | 2.4 / 1.2 |
| Czech (cs) | 319 | 1.4 / 0.5 | 2.1 / 0.5 | 2.3 / 0.9 | 4.0 / 1.3 |
| Danish (da) | 319 | 1.7 / 0.3 | 2.0 / 0.3 | 2.3 / 0.7 | 2.2 / 0.9 |
| Dutch (nl) | 319 | 0.8 / 0.4 | 0.5 / 0.5 | 1.0 / 0.8 | 1.1 / 1.1 |
| English (en) | 315 | 3.1 / 0.5 | 2.9 / 0.6 | 2.7 / 0.9 | 3.9 / 1.8 |
| Estonian (et) | 317 | 1.2 / 0.5 | 2.3 / 0.5 | 2.2 / 1.3 | 2.5 / 1.8 |
| Finnish (fi) | 311 | 2.0 / 0.6 | 2.3 / 0.7 | 2.4 / 1.5 | 3.5 / 2.1 |
| French (fr) | 315 | 1.5 / 0.5 | 2.1 / 0.5 | 2.2 / 1.1 | 2.2 / 1.7 |
| Georgian (ka) | 315 | 1.5 / 0.7 | 2.4 / 0.8 | 2.3 / 1.4 | 3.5 / 2.0 |
| German (de) | 314 | 2.8 / 0.5 | 2.9 / 0.9 | 2.7 / 1.5 | 3.3 / 2.2 |
| Greek (el) | 318 | 2.3 / 0.6 | 1.5 / 0.7 | 2.4 / 1.1 | 2.9 / 1.8 |
| Hebrew (he) | 312 | 2.5 / 0.9 | 1.6 / 1.0 | 1.4 / 2.2 | 4.7 / 3.3 |
| Hindi (hi) | 313 | 1.9 / 0.7 | 2.5 / 0.8 | 5.0 / 1.7 | 3.4 / 2.7 |
| Hungarian (hu) | 317 | 1.6 / 0.6 | 1.5 / 0.8 | 2.5 / 1.4 | 3.2 / 1.5 |
| Icelandic (is) | 318 | 1.1 / 0.4 | 1.8 / 0.6 | 2.1 / 1.0 | 2.3 / 1.5 |
| Indonesian (id) | 319 | 2.0 / 0.8 | 2.2 / 0.7 | 1.9 / 1.2 | 2.0 / 2.0 |
| Irish (ga) | 316 | 0.3 / 0.4 | 0.5 / 0.7 | 0.6 / 1.4 | 1.4 / 2.1 |
| Italian (it) | 315 | 1.0 / 0.5 | 1.6 / 0.7 | 1.4 / 1.2 | 1.5 / 1.5 |
| Japanese (ja) | 314 | 0.8 / 0.3 | 0.9 / 0.3 | 1.3 / 0.6 | 1.6 / 1.2 |
| Kazakh (kk) | 315 | 4.0 / 0.4 | 3.8 / 0.7 | 5.5 / 0.9 | 5.5 / 1.7 |
| Khmer (km) | 319 | 0.7 / 0.4 | 1.2 / 0.6 | 2.0 / 1.0 | 1.8 / 1.5 |
| Korean (ko) | 316 | 2.3 / 0.6 | 2.1 / 0.9 | 2.2 / 1.0 | 2.2 / 1.7 |
| Latvian (lv) | 310 | 1.7 / 0.6 | 1.9 / 0.7 | 3.1 / 0.9 | 3.4 / 1.6 |
| Lithuanian (lt) | 319 | 1.9 / 0.4 | 1.2 / 0.4 | 1.5 / 1.1 | 2.6 / 1.6 |
| Malay (ms) | 315 | 0.4 / 0.5 | 0.5 / 0.4 | 1.2 / 0.8 | 1.6 / 1.5 |
| Maltese (mt) | 320 | 1.4 / 0.6 | 2.2 / 0.8 | 2.9 / 1.2 | 3.2 / 1.6 |
| Marathi (mr) | 308 | 2.3 / 0.6 | 2.4 / 0.7 | 2.3 / 1.6 | 2.7 / 2.5 |
| Norwegian (no) | 317 | 1.3 / 0.4 | 1.6 / 0.5 | 1.1 / 0.8 | 2.8 / 1.2 |
| Persian (fa) | 317 | 3.3 / 0.8 | 4.5 / 1.0 | 3.8 / 1.6 | 4.9 / 2.2 |
| Polish (pl) | 316 | 0.6 / 0.4 | 0.6 / 0.5 | 0.5 / 1.1 | 1.0 / 1.6 |
| Portuguese (pt) | 316 | 1.0 / 0.5 | 1.6 / 0.8 | 3.2 / 1.1 | 3.8 / 1.7 |
| Romanian (ro) | 316 | 2.5 / 0.4 | 1.6 / 0.7 | 2.1 / 0.7 | 2.0 / 1.3 |
| Russian (ru) | 305 | 0.7 / 0.6 | 1.0 / 0.6 | 1.3 / 1.1 | 1.9 / 1.8 |
| Serbian (sr) | 314 | 1.7 / 0.4 | 2.0 / 0.4 | 2.9 / 0.7 | 2.2 / 1.2 |
| Slovak (sk) | 316 | 1.8 / 0.3 | 1.5 / 0.4 | 2.1 / 0.8 | 2.6 / 1.2 |
| Slovenian (sl) | 318 | 1.1 / 0.2 | 1.9 / 0.4 | 2.1 / 0.8 | 3.0 / 1.1 |
| Spanish (es) | 318 | 1.1 / 0.6 | 1.8 / 0.8 | 2.0 / 1.0 | 3.0 / 1.5 |
| Swahili (sw) | 315 | 2.3 / 0.6 | 2.1 / 0.8 | 2.8 / 1.8 | 2.7 / 2.1 |
| Swedish (sv) | 314 | 1.3 / 0.4 | 1.3 / 0.6 | 2.1 / 1.6 | 2.0 / 1.8 |
| Tagalog (tl) | 314 | 1.3 / 0.5 | 1.3 / 0.5 | 2.0 / 0.8 | 2.2 / 1.3 |
| Telugu (te) | 317 | 0.7 / 0.8 | 1.5 / 0.7 | 1.4 / 1.2 | 1.8 / 2.3 |
| Thai (th) | 317 | 3.6 / 0.7 | 3.8 / 0.8 | 4.4 / 1.2 | 4.8 / 2.5 |
| Turkish (tr) | 319 | 0.4 / 0.6 | 1.7 / 1.0 | 1.1 / 1.5 | 1.8 / 2.6 |
| Ukrainian (uk) | 317 | 0.3 / 0.3 | 0.3 / 0.5 | 1.1 / 0.7 | 1.4 / 1.5 |
| Urdu (ur) | 318 | 1.3 / 1.0 | 1.2 / 1.0 | 1.4 / 1.4 | 1.8 / 2.4 |
| Uzbek (uz) | 317 | 0.5 / 0.5 | 0.6 / 0.4 | 0.8 / 1.1 | 1.7 / 1.6 |
| Vietnamese (vi) | 320 | 1.4 / 0.6 | 1.3 / 0.8 | 1.2 / 1.3 | 1.9 / 1.9 |
