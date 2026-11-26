"""Writes vault_corpus.json, the shared leak corpus of the vault middlewares (npm and Python), next to this file.

    python3 python/tests/gen_vault_corpus.py

Standard library only. REVIEW-GUIDE rule 3: known values (every label, several scripts) x every spelling the
vault promises to find x every position, every reviewer input of the vault's outside reviews that can be written this
way, and look-alikes that must be kept.

Format (two runners, JS and Python, read it; keep it exactly):

    {"version": 1, "positions": [...], "cases": [{"id", "from", "known", "detect", "where", "text", "data"?,
     "int_keys", "leak", "keep", "fields"?}]}

- known: [{"label", "value"}], put into a fresh vault with vault.tag(label, value) before the call. A label is one of
  the model's or a custom one (capital letters, digits and "_", such as "CUSTOMER").
- detect: [[value, label]], what a stand-in detector reports: every exact occurrence of each value in the text it is
  given (the middleware's own reading, so a value is written as the model reads it: decoded, "key: value" lines).
- where: positions from POSITIONS, each run on its own with a fresh vault:
    user                   a user message text (text)
    system                 the system prompt (text)
    tool-text              a tool result that is a string (text)
    tool-data              a tool result that is JSON data (data)
    block-data             a tool result typed block with the JSON under a field (data): JS {type: "json", value} for
                           the AI SDK, {type: "json", json} for LangChain
    assistant-unrecorded   an earlier assistant answer the middleware has no record of (text)
    tool-input-unrecorded  tool-call input / args of an earlier call with no record (data when present, else text)
- int_keys: Python runners turn object keys that are decimal digits into int keys. (LangChain's AIMessage.tool_calls
  accepts string keys only, so int-keyed tool input goes in a content block: {"type": "tool_use", "input": ...}.)
- leak: strings that must not appear in what the provider receives, written as they appear in the input (their spelled
  form, plus pieces of the value that the spelling leaves as they are). For data, compare against every string and key
  of the data the provider receives (or its JSON written without ASCII escapes); a leak string with a quotation mark,
  a backslash or a control character then never matches the JSON text, which is why every data case also has a plain
  piece.
- keep: strings that must appear unchanged in what the provider receives (over-masking and regression checks). In a
  case with a data position they hold no quotation mark, backslash or control character, so they match the JSON text
  of the data too.
- fields: the number of keys the top-level object of data (or of the JSON text) must keep.

Ids tell the source: "rNN-..." outside review NN, "ipN-..." independent pass N, "gen-..." generated.

Backslashes and control characters are built in code (BS = chr(92)): a file-writing tool or a shell heredoc can turn
an escape in a source file into its character (REVIEW-GUIDE rule 9). The script checks the corpus before it writes it.

Reviewer inputs of those reviews that are not in this corpus, and why (section, finding: reason):

  Fixes after the review (1st outside review)
    hole 1  restore scope of placeholders in the answer: restore
    hole 2  restored reasoning sent back: needs reasoning parts and a restore
    hole 5  clear and refill with cached text: concurrency
    hole 6  word-edge code point ranges: covered by the generated script cases and vault_cases.json (no own input)
  Second review of the fixes
    1  bare strings and other block types in content lists: a message shape that is not one of the positions
    2  answer after a clear and refill: concurrency, restore
    3  JSON keys restored: restore (the unrecorded form of "id 42" is r02-3)
    4  restore_tools="lookup" as letters: restore option
    5  roles that leave out "assistant": needs a roles setting (assistant-unrecorded covers the default roles)
    6  Object.create(null) records: a JS object kind, not JSON
    7  test file location: not an input
    10 placeholder longer than the stream hold-back: streaming
    "Not fixed": characters added in Unicode 17 next to a value: runtime-dependent, a recorded limit
  Third review
    3  cached request cleared and refilled: concurrency
    5, 6, 10  include list, npm pack, demo notice: not inputs
    7  streamed tool input of an allowed tool: streaming, restore
    8  mask_documents output through the middleware: documented, not a middleware position
    9  LangChain.js without a vault: needs no vault
    11 non-ASCII digits in tags: tag grammar, in vault_cases.json
  Fourth review
    2, 3, 8  threads, clear while masking: concurrency
    4  value learned in a later call while text is cached: needs two calls
    9, 13, 14  links, README, demo: not inputs
    10 tag(label, "") and used-up families: vault state, in vault_cases.json
    11 placeholder in a key of truncated tool input, 9007199254740993 in a replay: restore
    12 Rust scan from every "[": time only
  Fifth review
    1  a name the detector found once, the same name again in the text: the stand-in reports every occurrence
    2  tool result with the wording of a restored answer: needs a record (restore)
    3  setSensitivity, clear: settings, concurrency
    6  Python threads: concurrency
    7  unnumbered tag of a used-up family: vault state
    8  placeholder written with JSON escapes in tool input: restore
    notes: documentation
  Sixth review
    1  two wordings of one restored key: needs records
    2  sensitivity set while the model reads: settings
    4  app changes returned arguments in place: application mutation
    7  "1425" with a known "42" in block tool input with a record: needs a record (without one it is masked by design)
    8  mask_documents and restore_value generations: concurrency
    5  (second half) a repeat after a family ran out of numbers: vault state (the chain of merged values is r06-5)
    9  custom label with an emoji: tag grammar, in vault_cases.json
    10 comments: documentation
    "Not changed": other fields of a typed block in a tool result: documented (block-data checks the known values there)
  Seventh round, independent pass 1
    1  class instance / toJSON tool result: JS object kinds, not JSON
    4  value found in a later message masks the system prompt: needs two messages in one call
    5  made-up placeholder numbers: vault state, restore
    6  generateObject answer: restore
    7  jsonb record keys: state export
    8  signed reasoning: reasoning parts
    10, 11  documentation
  Independent pass 2
    B1 skipped numbers in toJSON: state export
    B2 streamed plain answer: streaming
    also: a request of LangChain 1.0 with only system_prompt: a request shape (the system position has the text)
  Seventh outside review
    A1 execution-denied / approval reason: an AI SDK part kind that is not one of the positions
    A2 toJSON: JS object kinds
    A4 "a quoted placeholder": in the corpus (r07-A4); the runners' "fields" check counts the keys of the JSON text
       that is sent, which fails when a placeholder is left outside a JSON string
    B1, B4, B5  records and state export
    B2 settings switched while the model ran: settings
    C1, C2 streamed JSON answer; rows read as one document (needs a model that reads context)
  Eighth outside review
    A2 non-enumerable toJSON: JS object kind
    B1 a block of another kind inside a system message: a system content shape that is not one of the positions
    B3 Redactor subclass: setup, not an input
    B4 made-up placeholder at the highest number: vault state
    C2 JSON answer that is one string: restore
    C3 rows at the top level read one by one: needs a model that reads context
  Independent pass 3
    B1 base64 that holds a short known value: the rule belongs to the check before sending only, which is block-data
       in LangChain but not in the AI SDK, where JSON output is read (the same input would be masked there by design)
    B2 made-up placeholders: vault state
  Ninth outside review
    B2 family of a letter and 31 underscores: vault state
    B3, P2, P3  time only (the inputs of P1 are here as keep cases)
    C2 fileId of a file part: a part kind that is not one of the positions
    C3 documentation
  Tenth outside review
    B1 a known value repeated 4,000 times: time only
    B3 family Z: vault state
    C1 documentation
    "tried and taken back": pieces of escapes that the nano model took for values: needs a real model
  Eleventh outside review
    A1 a value read under the key it stands in: needs a model that reads keys as context
    B1 detector failure: setup
    B2 repeated placeholders: time only
    C1 plain decimal form on restore: restore
  Seventh pass (independent)
    S1 restore to "04524": restore (its over-masking inputs are here)
    S2 the second placeholder of one value: placeholder identity is not expressible (the leaks are here)
    S4 documentation; "left as they are" (EUC-KR, triple encoding, zero-width space): out of scope by decision
  Thirteenth outside review
    B1, B2  which placeholder a value gets: not expressible (the leaks are here)
    B4 time only
    C1 "0e65" on restore: restore
  Fourteenth outside review
    B3 "Lee Lee Lee ..." overlapping itself: time only
    C1, C2  typing, test fixture
  Fifteenth outside review
    C1 documentation
  Sixteenth outside review
    1  tuple keys, and a number key next to the same string key: Python values JSON cannot hold (the int key is here)
    2  time only
  Not in the record's tables: the 34 cases and fuzzing of independent pass 7 and the fuzzing of pass 6 (their own
  corpora); the 70 kinds of pass 6 are here (ip6-kind-*)
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from urllib.parse import quote, quote_plus

BS = chr(92)  # a backslash
NL = chr(10)
CR = chr(13)
TAB = chr(9)
NBSP = chr(0xA0)
NEL = chr(0x85)  # next line
IDEO = chr(0x3000)  # ideographic space
LSEP = chr(0x2028)  # line separator
QUOTE = chr(34)

POSITIONS = ["user", "system", "tool-text", "tool-data", "block-data", "assistant-unrecorded", "tool-input-unrecorded"]
ALL = POSITIONS
# text a model did not write: word edges and the number rule hold (model text without a record is searched loosely,
# inside longer words too, so look-alikes are masked there by design)
NOT_LOOSE = ["user", "system", "tool-text", "tool-data", "block-data"]
# where the stand-in detector is asked (the system prompt and other fields of a typed block only get known values hidden)
DETECTED = ["user", "tool-text", "tool-data", "assistant-unrecorded", "tool-input-unrecorded"]
TEXTS = ["user", "system", "tool-text", "assistant-unrecorded", "tool-input-unrecorded"]
LABELS = {
    "PERSON", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "DATE_OF_BIRTH", "SECRET",
    "PERSON_L2", "URL_L2",
}

_NO_DATA = object()
CASES = []


def case(cid, frm, text, *, known=(), detect=(), where=ALL, data=_NO_DATA, int_keys=False, leak=(), keep=(), fields=None,
         extra=()):
    """One case. extra: leak strings that are not in the input as written (a decoded form that must not come out
    either, or a marker such as " #2")."""
    c = {
        "id": cid,
        "from": frm,
        "known": [{"label": label, "value": value} for label, value in known],
        "detect": [[value, label] for value, label in detect],
        "where": list(where),
        "text": text,
    }
    if data is not _NO_DATA:
        c["data"] = data
        leak = list(leak)
        if leak and not any(plain_for_json(s) for s in leak):
            # a runner that compares the JSON text of data does not see a backslash or a quotation mark as written:
            # the longest plain part of the first leak string stands for it ("uae40" of an escaped Hangul name)
            parts = [p for p in split_plain(leak[0]) if len(p) >= 4]
            if parts:
                leak.append(max(parts, key=len))
    c["int_keys"] = int_keys
    c["leak"] = list(dict.fromkeys(list(leak) + list(extra)))
    c["keep"] = list(dict.fromkeys(keep))
    if fields is not None:
        c["fields"] = fields
    c["_extra"] = set(extra)
    CASES.append(c)
    return c


def plain_for_json(s):
    """Whether s is written the same in JSON text (no quotation mark, backslash or control character)."""
    return not any(c in (QUOTE, BS) or ord(c) < 0x20 for c in s)


def split_plain(s):
    """The parts of s between characters that JSON text writes otherwise."""
    parts, cur = [], ""
    for c in s:
        if plain_for_json(c):
            cur += c
        else:
            parts.append(cur)
            cur = ""
    return [p for p in parts + [cur] if p]


def strings_of(v):
    """Every string, key and number (as JSON writes it) of a JSON value."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from strings_of(x)
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from strings_of(x)
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        yield json.dumps(v)


def in_data(s, data):
    """Whether s stands in a string or key of data, or in its JSON text."""
    return any(s in x for x in strings_of(data)) or s in jtext(data)


def jdump(v):
    """JSON text as json.dumps writes it by default (non-ASCII as Unicode escapes)."""
    return json.dumps(v)


def jtext(v):
    """JSON text with characters as they are (no ASCII escapes)."""
    return json.dumps(v, ensure_ascii=False)


def uesc(c, upper=False):
    """A character as a JSON Unicode escape (lower-case hex, as json.dumps writes it; a character outside the basic
    plane as its surrogate pair)."""
    cp = ord(c)
    units = [cp] if cp < 0x10000 else [0xD800 + ((cp - 0x10000) >> 10), 0xDC00 + ((cp - 0x10000) & 0x3FF)]
    return "".join(BS + ("u%04X" if upper else "u%04x") % u for u in units)


# ---------------------------------------------------------------------------------------------------------------------
# (a) Reviewer inputs of the record, section by section
# ---------------------------------------------------------------------------------------------------------------------

KIM = ("PERSON", "Kim Minsu")
JOSE = "Jos" + chr(0xE9) + " " + chr(0xC1) + "lvarez"  # José Álvarez
JOSE_U = "Jos" + uesc(chr(0xE9)) + " " + uesc(chr(0xC1)) + "lvarez"  # as json.dumps writes it
JOSE_UU = "Jos" + BS + uesc(chr(0xE9)) + " " + BS + uesc(chr(0xC1)) + "lvarez"  # one level deeper
ADDR = "12 Main St" + NL + "Seoul 04524"
ADDR_J = "12 Main St" + BS + "n" + "Seoul 04524"  # its line break as a JSON escape
ADDR_PIECES = ["Main St", "Seoul 04524"]

# Fixes after the review (1st outside review)
crm = {"customer": {"name": "Lee Jiwon", "email": "lee.jiwon@example.org", "phone": "010-9876-5432"}}
case("r01-3-crm-json-result", "1st outside review, hole 3 (fresh PII in a JSON tool result)", jdump(crm), data=crm,
     detect=[("Lee Jiwon", "PERSON"), ("lee.jiwon@example.org", "EMAIL"), ("010-9876-5432", "PHONE")], where=DETECTED,
     leak=["Lee Jiwon", "lee.jiwon@example.org", "9876-5432"])
case("r01-4-short-value-in-args", "1st outside review, hole 4 (a restored short value in the next call's arguments)",
     jdump({"id": "42"}), data={"id": "42"}, known=[("ID_NUMBER", "42")], where=["tool-input-unrecorded"], leak=["42"])

case("r01-also-hangul-after-digit", "1st outside review, also (Hangul next to a digit is a word edge)", "부평대로 1번지로 오세요",
     data={"note": "부평대로 1번지로 오세요"}, known=[("ADDRESS", "부평대로 1")], leak=["부평대로 1"])

# Second review of the fixes
case("r02-3-composite-arg", "2nd outside review, point 3 (id 42 in model text without a record)", "id 42",
     data={"q": "id 42"}, known=[("ID_NUMBER", "42")], where=["assistant-unrecorded", "tool-input-unrecorded"], leak=["42"])
case("r02-8-typed-placeholder", "2nd outside review, point 8 (placeholder-like text typed by a user is escaped)",
     "Ask [PERSON_1] about it", data={"note": "Ask [PERSON_1] about it"}, known=[KIM],
     where=["user", "tool-text", "tool-data"], leak=["[PERSON_1]"], keep=["Ask ", " about it"])
case("r02-9-partial-known", "2nd outside review, point 9 (the model finds Kim of a known Kim Minsu)", "Kim Minsu called twice",
     data={"note": "Kim Minsu called twice"}, known=[KIM], detect=[("Kim", "PERSON")], where=DETECTED, leak=["Minsu"],
     keep=["called twice"])

# Third review
case("r03-1-block-list-with-tag", "3rd outside review, point 1 (account [REF_1] cobalt on a block list)",
     "Use account [REF_1] cobalt for the test", data={"note": "Use account [REF_1] cobalt for the test"},
     detect=[("account [REF_1] cobalt", "SECRET")], where=DETECTED, leak=["cobalt"], keep=["for the test"])
case("r03-2-glued-unrecorded", "3rd outside review, point 2 (xid: 42y in model text without a record)", "xid: 42y",
     data={"q": "xid: 42y"}, known=[("ID_NUMBER", "id: 42")], where=["assistant-unrecorded", "tool-input-unrecorded"],
     leak=["id: 42"])
case("r03-2-glued-received", "3rd outside review, point 2 (received text keeps word edges)", "xid: 42y",
     data={"q": "xid: 42y"}, known=[("ID_NUMBER", "id: 42")], where=NOT_LOOSE, keep=["xid: 42y"])
case("r03-4-overlapping-known", "3rd outside review, point 4 (Alex Kim + Kim Lee)", "I met Alex Kim Lee today",
     data={"note": "I met Alex Kim Lee today"}, known=[("PERSON", "Alex Kim"), ("PERSON", "Kim Lee")],
     leak=["Alex", "Kim", "Lee"], keep=["I met ", " today"])

# Fourth review
case("r04-1-answer-with-new-value", "4th outside review, point 1 ([PERSON_1] at kim@example.com without a record)",
     "[PERSON_1] at kim@example.com", data={"q": "[PERSON_1] at kim@example.com"}, known=[KIM],
     detect=[("kim@example.com", "EMAIL")], where=["assistant-unrecorded", "tool-input-unrecorded"],
     leak=["kim@example.com"], keep=["[PERSON_1] at "])
case("r04-5-loose-model-text", "4th outside review, point 5 (model text without a record: inside longer words too)",
     "Call xKim Minsuy now", data={"q": "Call xKim Minsuy now"}, known=[KIM],
     where=["assistant-unrecorded", "tool-input-unrecorded"], leak=["Minsu"])
case("r04-6-overlapping-occurrence", "4th outside review, point 6 (xAnn Ann Ann)", "xAnn Ann Ann",
     data={"note": "xAnn Ann Ann"}, known=[("PERSON", "Ann")], where=NOT_LOOSE, leak=[" Ann"], keep=["xAnn"])
case("r04-6-circled-letter", "4th outside review, point 6 (a circled letter is not a word character)",
     chr(0x24B6) + "Ann and Ann", data={"note": chr(0x24B6) + "Ann and Ann"}, known=[("PERSON", "Ann")], leak=["Ann"])
NFC = "Jos" + chr(0xE9)
NFD = "Jose" + chr(0x301)
case("r04-7-decomposed-spelling", "4th outside review, point 7 (a name with an accent, decomposed, is another value)",
     NFC + " and " + NFD + " wrote", data={"note": NFC + " and " + NFD + " wrote"}, known=[("PERSON", NFC)],
     detect=[(NFD, "PERSON")], where=DETECTED, leak=[NFC, NFD])

# Fifth review
case("r05-4-tool-input-block", "5th outside review, point 4 (tool input without tool_calls)", jdump({"customer": "Kim Minsu"}),
     data={"customer": "Kim Minsu"}, known=[KIM], where=["tool-input-unrecorded", "assistant-unrecorded"], leak=["Kim Minsu"])
case("r05-5-glued-short-unrecorded", "5th outside review, point 5 (x42y without a record)", "x42y", data={"q": "x42y"},
     known=[("ID_NUMBER", "42")], where=["assistant-unrecorded", "tool-input-unrecorded"], leak=["42"])
case("r05-5-glued-short-received", "5th outside review, point 5 (received text: a 2-character value is not searched)",
     "x42y and room 42", data={"q": "x42y and room 42"}, known=[("ID_NUMBER", "42")], where=NOT_LOOSE,
     keep=["x42y and room 42"])
LONG_TAG = "[" + "A" * 46 + "_1]"
case("r05-9-placeholder-grammar", "5th outside review, point 9 (a 46-letter family is ordinary text)", "see " + LONG_TAG + " here",
     data={"note": "see " + LONG_TAG + " here"}, known=[KIM], keep=[LONG_TAG])

# Sixth review
case("r06-3-rows-of-json", "6th outside review, point 3; 7th outside review, B3 (rows of JSON data as a tool result)",
     jdump([{"name": "Alice"}]), data=[{"name": "Alice"}], detect=[("Alice", "PERSON")], where=DETECTED, leak=["Alice"])
WORDS = ["Alfa", "Brav", "Char", "Delt", "Echo", "Foxt", "Golf", "Hote", "Indi", "Juli", "Kilo", "Lima"]
CHAIN = "Team: " + " ".join(WORDS) + "."
case("r06-5-chain-of-merged-values", "6th outside review, point 5 (a chain of overlapping known values longer than eight rounds)",
     CHAIN, data={"note": CHAIN}, known=[("PERSON", a + " " + b) for a, b in zip(WORDS, WORDS[1:])], leak=WORDS, keep=["Team: "])
BIGNUM = '{"n": 9007199254740993, "big": 1e400, "who": "Kim Minsu"}'
case("r06-6-numbers-as-written", "6th outside review, point 6 (numbers of JSON text stay as written)", BIGNUM, known=[KIM],
     where=TEXTS, leak=["Kim Minsu"], keep=["9007199254740993", "1e400"])

# Seventh round, independent pass 1
case("ip1-2-han-edge-id", "independent pass 1, point 2 (注文ZX-88213-Kは)",
     "注文ZX-88213-Kは発送済みです", data={"note": "注文ZX-88213-Kは発送済みです"}, known=[("ID_NUMBER", "ZX-88213-K")],
     leak=["88213"])
case("ip1-2-han-edge-name", "independent pass 1, point 2 (CEO王伟来了)", "CEO王伟来了", data={"note": "CEO王伟来了"},
     known=[("PERSON", "王伟")], leak=["王伟"])
GARCIA = "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a"
case("ip1-3-json-dumps-escapes", "independent pass 1, point 3 (json.dumps escapes in a JSON tool result)",
     jdump({"name": GARCIA, "note": "called"}), data={"raw": jdump({"name": GARCIA, "note": "called"})},
     known=[("PERSON", GARCIA)], leak=["Jos" + uesc(chr(0xE9)) + " Garc" + uesc(chr(0xED)) + "a", "Garc"], extra=[GARCIA])
case("ip1-9-system-message", "independent pass 1, point 9 (a system message names the user)", "You are helping Kim Minsu.",
     known=[KIM], where=["system"], leak=["Kim Minsu"], keep=["You are helping "])

# Independent pass 2
case("ip2-A1-json-number-detected", "independent pass 2, A1 (a phone number written as a JSON number, found by the model)",
     '{"phone": 4155550123, "name": "x"}', data={"phone": 4155550123, "name": "x"}, detect=[("4155550123", "PHONE")],
     where=["user", "tool-text", "tool-data", "assistant-unrecorded"], leak=["4155550123"])
case("ip2-A1-json-number-known", "independent pass 2, A1 (a known phone number written as a JSON number)",
     '{"phone": 4155550123, "name": "x"}', data={"phone": 4155550123, "name": "x"}, known=[("PHONE", "4155550123")],
     leak=["4155550123"])
for i, t in enumerate([
    'Result: {"name": "' + JOSE_U + '"}',
    "```json" + NL + '{"name": "' + JOSE_U + '"}' + NL + "```",
    '{"a": 1}' + NL + '{"name": "' + JOSE_U + '"}',
    QUOTE + JOSE_U + QUOTE,
    '{"payload": "{' + BS + '"name' + BS + '": ' + BS + '"' + JOSE_UU + BS + '"}"}',
]):
    kinds = ["result-prefix", "code-fence", "two-json-lines", "top-level-string", "json-in-string-value"]
    case("ip2-A2-" + kinds[i], "independent pass 2, A2 (JSON that is not the whole text: " + kinds[i] + ")", t,
         data={"raw": t}, known=[("PERSON", JOSE)], leak=[JOSE_U if "payload" not in t else JOSE_UU, "lvarez"], extra=[JOSE])
case("ip2-B3-system-explains-placeholders", "independent pass 2, B3 (a system prompt that explains placeholders)",
     "Placeholders such as [PERSON_1] stand for names. Kim Minsu is the user.", known=[KIM], where=["system"],
     leak=["Kim Minsu"], keep=["such as [PERSON_1] stand"])

# Seventh outside review
ZOE = "Zo" + chr(0xEB)
ZOE_U = "Zo" + uesc(chr(0xEB))
for name, t in [("false-first", "[false," + QUOTE + ZOE_U + QUOTE + "]"), ("null-first", "[null," + QUOTE + ZOE_U + QUOTE + "]"),
                ("top-level-string", QUOTE + ZOE_U + QUOTE)]:
    case("r07-A3-" + name, "7th outside review, A3 (" + t.replace(BS, "") + "; arrays that start with false or null, a JSON string)",
         t, data={"raw": t}, known=[("PERSON", ZOE)], leak=[ZOE_U, "Zo"], extra=[ZOE])
case("r07-A4-json-number", "7th outside review, A4 ({phone: 2025550148})", '{"phone":2025550148}', data={"phone": 2025550148},
     known=[("PHONE", "2025550148")], leak=["2025550148"], fields=1)
case("r07-A5-system-json-dumps", "7th outside review, A5 (a system prompt that is json.dumps text)",
     jdump({"user": JOSE, "task": "support"}), known=[("PERSON", JOSE)], where=["system"], leak=[JOSE_U, "lvarez"], extra=[JOSE])
ALICE = {"name": "M. Alice Morgan", "note": "Alice Morgan called back"}
case("r07-A6-inside-known-value", "7th outside review, A6 (Alice Morgan found inside a known M. Alice Morgan)",
     jdump(ALICE), data=ALICE, known=[("PERSON", "M. Alice Morgan")], detect=[("Alice Morgan", "PERSON")], where=DETECTED,
     leak=["Alice Morgan"], keep=["called back"])

# Eighth outside review
NAMED = {"name": "Kim Minsu", "text": "Kim Minsu", "source": {"data": "Kim Minsu"}}
case("r08-A1-fields-named-like-framework", "8th outside review, A1 (name, text, data fields inside a tool's data)",
     jdump(NAMED), data=NAMED, known=[KIM], leak=["Kim Minsu"])
case("r08-A3-stray-quote-before-json", "8th outside review, A3 (a stray inch mark before a JSON document)", '24" monitor: {"name": "' + JOSE_U + '"}',
     data={"raw": '24" monitor: {"name": "' + JOSE_U + '"}'}, known=[("PERSON", JOSE)], leak=[JOSE_U, "lvarez"], extra=[JOSE])
INNER = 'log {"msg": "payload {' + BS + '"name' + BS + '": ' + BS + '"' + JOSE_UU + BS + '"}"}'
case("r08-A3-json-in-prefixed-string", "8th outside review, A3 (JSON inside a string of prefixed text)", INNER, data={"raw": INNER},
     known=[("PERSON", JOSE)], leak=[JOSE_UU, "lvarez"], extra=[JOSE])
case("r08-A4-number-in-tool-input", "8th outside review, A4 (a known value the model wrote as a number in tool input)",
     '{"phone": 821023456789}', data={"phone": 821023456789}, known=[("PHONE", "821023456789")], leak=["821023456789"])
TYPED = {"type": ["string", "null"], "who": "Kim Minsu"}
case("r08-B2-type-is-a-list", "8th outside review, B2 (a type field that is a list in tool input)", jdump(TYPED), data=TYPED,
     known=[KIM], where=["tool-input-unrecorded", "tool-data", "block-data"], leak=["Kim Minsu"], keep=["string", "null"])
case("r08-C1-system-json-number", "8th outside review, C1 (a number in a system prompt that is JSON)",
     '{"phone": 821023456789, "name": "x"}', known=[("PHONE", "821023456789")], where=["system"], leak=["821023456789"])

# Independent pass 3
ACME = "ACME" + BS + "tkim"
FS01 = "FS01" + BS + "nina.park"
case("ip3-A1-backslash-t-login", "independent pass 3, A1 (a domain login ACME, backslash, tkim in a quoted string that is not JSON)",
     'login "' + ACME + '" ok', data={"raw": 'login "' + ACME + '" ok'}, known=[("USERNAME", ACME)], leak=[ACME, "tkim"])
case("ip3-A1-backslash-n-login", "independent pass 3, A1 (a domain login FS01, backslash, nina.park)", 'share "' + FS01 + '" mounted',
     data={"raw": 'share "' + FS01 + '" mounted'}, known=[("USERNAME", FS01)], leak=[FS01, "nina.park"])
case("ip3-A2-number-in-last-pass", "independent pass 3, A2 (a known number in fields only the last pass reads)",
     '{"phone": 821023456789}', data={"phone": 821023456789}, known=[("PHONE", "821023456789")], where=["block-data"],
     leak=["821023456789"])
for name, t in [("stray-quote", '24" monitor: {"to": "' + ADDR_J + '"}'),
                ("json-in-prefixed-string", 'log {"msg": "ship {' + BS + '"to' + BS + '": ' + BS + '"12 Main St' + BS + BS + "nSeoul 04524" + BS + '"}"}')]:
    case("ip3-A3-" + name, "independent pass 3, A3 (a known address with a line break, " + name + ")", t, data={"raw": t},
         known=[("ADDRESS", ADDR)], leak=ADDR_PIECES)

LONE = '{"n": "' + BS + 'ud800 Kim Minsu", "m": 1}'
case("ip3-B3-lone-surrogate-escape", "independent pass 3, B3 (a lone surrogate escape next to a masked value stays an escape)",
     LONE, known=[KIM], where=["user", "system", "tool-text"], leak=["Kim Minsu"], keep=[BS + "ud800"])
case("ip3-B4-number-with-fraction-in-tool-input", "independent pass 3, B4 (821023456789.0 in model-written tool input)",
     '{"phone": 821023456789.0}', data={"phone": 821023456789.0}, known=[("PHONE", "821023456789")], leak=["821023456789"])

# Independent pass 4
for name, t in [("cut-off-document", '{"to": "' + ADDR_J + '", "note": "cut'),
                ("result-string", 'Result: "' + ADDR_J + '"'),
                ("log-field", 'level=info note="' + ADDR_J + '"')]:
    case("ip4-F1-" + name, "independent pass 4, F1 (short escapes in quoted text that is not a document: " + name + ")", t,
         data={"raw": t}, known=[("ADDRESS", ADDR)], leak=ADDR_PIECES)
case("ip4-F1-glued-n", "independent pass 4, F1 (an escaped line break glued to a known name in a log field)", 'msg="Dear' + BS + 'nKim Minsu"',
     data={"raw": 'msg="Dear' + BS + 'nKim Minsu"'}, known=[KIM], leak=["Kim Minsu"])
UNCLOSED = "[" * 2000 + ' {"name": "' + JOSE_U + '"}'
case("ip4-F2-unclosed-brackets", "independent pass 4, F2 (many brackets that never close before a document)", UNCLOSED,
     data={"raw": UNCLOSED}, known=[("PERSON", JOSE)], leak=[JOSE_U, "lvarez"])
B64ID = "A" * 40 + "/kim_0142/" + "B" * 30
case("ip4-F3-base64-looking-id", "independent pass 4, F3 (a long base64-looking id holds a known value)", "id " + B64ID,
     data={"id": B64ID}, known=[("USERNAME", "kim_0142")], leak=["kim_0142"])

# Ninth outside review
for name, t in [("stray-quote", '24" monitor; msg="Dear' + BS + 'nkim_0142"'), ("bracket-in-field", 'msg="[1] Dear' + BS + 'nkim_0142"')]:
    case("r09-A1-" + name, "9th outside review, A1 (a known user name after a quoted log field's line break, " + name + ")", t,
         data={"raw": t}, known=[("USERNAME", "kim_0142")], leak=["kim_0142"])
case("r09-A2-bracketed-id", "9th outside review, A2 (Check EMP[3142] please)", "Check EMP[3142] please",
     data={"note": "Check EMP[3142] please"}, known=[("USERNAME", "EMP[3142]")], leak=["EMP[3142]", "3142"], keep=["Check ", " please"])
case("r09-A3-exponent-json", "9th outside review, A3 (8.21023456789e11 for a known phone number, JSON)", '{"phone": 8.21023456789e11}',
     known=[("PHONE", "821023456789")], where=TEXTS, leak=["8.21023456789e11", "21023456789"])
case("r09-A3-exponent-text", "9th outside review, A3 (8.21023456789e11 for a known phone number, text)", "tel 8.21023456789e11 (mobile)",
     data={"tel": "8.21023456789e11", "note": "tel 8.21023456789e11 (mobile)"}, known=[("PHONE", "821023456789")],
     leak=["8.21023456789e11", "21023456789"])
case("r09-B1-near-big-number", "9th outside review, B1 (9007199254740993 is not a known 9007199254740992)",
     "id 9007199254740993 and 9.007199254740993e15", known=[("ID_NUMBER", "9007199254740992")], where=TEXTS,
     keep=["9007199254740993", "9.007199254740993e15"])
case("r09-C1-known-key", "9th outside review, C1 (a known value in a key is hidden)", '{"kim_0142":true}',
     data={"kim_0142": True}, known=[("USERNAME", "kim_0142")], leak=["kim_0142"], fields=1)
for name, t in [("file-path", "/home/kim/Documents/taxes/2026/receipts-november.pdf is the file. Please read it."),
                ("hash-and-file", "9fceb02d0ae598e95dc970b74767f19372d61af8  receipts-november.pdf"),
                ("uuids", "3f2504e0-4f89-11d3-9a0c-0305e82c3301 6ba7b810-9dad-11d1-80b4-00c04fd430c8 1b4e28ba-2fa1-11d2-883f-0016d3cca427")]:
    case("r09-P1-" + name, "9th outside review, P1 (a message that kept the base64 check busy: " + name + ")", t,
         data={"note": t}, known=[KIM, ("EMAIL", "kim@example.com")], keep=[t])
for name, t, known, leak in [
    ("cut-off-then-document", '{"a": "x then {"to": "' + ADDR_J + '"}', [("ADDRESS", ADDR)], ADDR_PIECES),
    ("python-repr", "{'name': 'Kim Minsu', 'address': '" + ADDR_J + "'}", [KIM, ("ADDRESS", ADDR)], ["Kim Minsu"] + ADDR_PIECES),
    ("size-quote", 'Size 24" note="' + ADDR_J + '"', [("ADDRESS", ADDR)], ADDR_PIECES),
    ("grep", 'Run grep -E "' + BS + "bKim Minsu" + BS + "b|" + BS + 'd+" file for me', [KIM], ["Kim Minsu"]),
]:
    case("r09-P4-" + name, "9th outside review, P4 (pairing quotation marks: " + name + ")", t, data={"raw": t}, known=known, leak=leak)
case("r09-P5-detector-finds-part", "9th outside review, P5 (the detector finds 12 Main St of an escaped known address)",
     'to: "' + ADDR_J + '"', data={"raw": 'to: "' + ADDR_J + '"'}, known=[("ADDRESS", ADDR)], detect=[("12 Main St", "ADDRESS")],
     leak=ADDR_PIECES)
CORP = "CORP" + BS + "minsu.kim"
DEEP = jdump({"payload": jdump({"user": CORP})})
case("r09-fix-backslash-json-in-json", "9th outside review, found while fixing (a domain login in JSON inside a JSON string)",
     DEEP, data={"raw": DEEP}, known=[("USERNAME", CORP)], leak=["minsu.kim"])
CORP_KO = "CORP" + BS + "김민수"
CORP_KO_J = jdump({"user": CORP_KO})
case("r09-fix-backslash-before-unicode-escape", "9th outside review, found while fixing (backslashes right before a Unicode escape)",
     CORP_KO_J, data={"raw": CORP_KO_J}, known=[("USERNAME", CORP_KO)],
     leak=[uesc("김") + uesc("민") + uesc("수")], extra=["김민수"])
PHP = '{"born":"01' + BS + "/02" + BS + '/1990","name":"x"}'
case("r09-fix-php-slash", "9th outside review, found while fixing (an escaped slash as PHP writes it)", PHP, data={"raw": PHP},
     known=[("DATE_OF_BIRTH", "01/02/1990")], leak=["01" + BS + "/02" + BS + "/1990", "1990"])
REPR = "greeting='Dear O" + BS + "'Brien'"
case("r09-fix-repr-quote", "9th outside review, found while fixing (an escaped apostrophe as a Python repr writes it)", REPR,
     data={"raw": REPR}, known=[("PERSON", "O'Brien")], leak=["O" + BS + "'Brien", "Brien"])
MANY = "[" * 20000 + " Kim Minsu"
case("r09-fix-deep-brackets", "9th outside review, found while fixing (200,000 opening brackets; 20,000 here)", MANY,
     data={"raw": MANY}, known=[KIM], leak=["Kim Minsu"])

# Tenth outside review
WIN = "C:" + BS + "Users" + BS + "u1234" + BS + "Documents"
case("r10-A1-path-like-escape", "10th outside review, A1 (a Windows path whose folder u1234 looks like an escape)", WIN,
     data={"path": WIN}, known=[("USERNAME", "u1234")], leak=["u1234"])
JDOE = "corp" + BS + "jdoe"
case("r10-A2-backslash-value-in-document", "10th outside review, A2 (a domain login with an escaped backslash in a JSON document, empty vault)",
     jtext({"username": JDOE}), data={"username": JDOE}, detect=[(JDOE, "USERNAME")],
     where=["user", "tool-text", "tool-data", "tool-input-unrecorded"], leak=["jdoe"])
case("r10-A3-two-spellings-detected", "10th outside review, A3 (8.21023456789e11 found first, 821023456789 in the same call)",
     '{"phone": 8.21023456789e11, "note": "call 821023456789"}', detect=[("8.21023456789e11", "PHONE")],
     where=["user", "tool-text", "assistant-unrecorded"], leak=["8.21023456789e11", "821023456789"])
EXPZ = "1e" + "0" * 4400 + "5"
case("r10-B2-exponent-with-zeros", "10th outside review, B2 (an exponent written with 4,400 zeros)",
     '{"n": ' + EXPZ + ', "who": "Kim Minsu"}', known=[KIM], where=TEXTS, leak=["Kim Minsu"], keep=[EXPZ])

# Eleventh outside review
case("r11-A2-system-exponent", "11th outside review, A2 (a learned number in another spelling in a system prompt)",
     "Customer tel 8.21023456789e11", known=[("PHONE", "821023456789")], leak=["8.21023456789e11"], data={"tel": "8.21023456789e11"})
R1 = '2026-11-25T10:00:02Z INFO sms.sent {"to":821023456789,"alt":8.21023456789e11,"ok":true}'
case("r11-R1-number-in-log-json", "11th outside review, R1 (a phone number as 8.21023456789e11 in JSON inside a log line)", R1,
     data={"raw": R1}, known=[("PHONE", "821023456789")], leak=["821023456789", "8.21023456789e11"], keep=["INFO sms.sent"])
R2 = "GET /s?name=Kim+Minsu&mail=kim%40example.com 200"
case("r11-R2-url", "11th outside review, R2 (name=Kim+Minsu, kim%40example.com)", R2, data={"note": R2},
     known=[KIM, ("EMAIL", "kim@example.com")], leak=["Kim+Minsu", "Minsu", "kim%40example.com"], keep=["GET /s?name="])
for name, t in [("one-line-comma", "Ship to 12 Main St, Seoul 04524 today"),
                ("yaml-indented", "address:" + NL + "  12 Main St" + NL + "  Seoul 04524" + NL + "name: x")]:
    case("r11-R3-" + name, "11th outside review, R3 (a known two-line address, " + name + ")", t, data={"note": t},
         known=[("ADDRESS", ADDR)], leak=ADDR_PIECES)

# Twelfth outside review (stopped; inputs from its log)
T1 = "https://crm.example/contacts/jos%C3%A9+work@example.org"
case("r12-T1-plus-in-path", "12th outside review (stopped), T1", T1, data={"url": T1},
     known=[("EMAIL", "jos" + chr(0xE9) + "+work@example.org")], leak=["jos%C3%A9+work@example.org", "work@example.org"])
T2 = "Ship to 12 Main St, Apt 8" + NL + "Seoul"
case("r12-T2-three-lines", "12th outside review (stopped), T2", T2, data={"note": T2},
     known=[("ADDRESS", "12 Main St" + NL + "Apt 8" + NL + "Seoul")], leak=["Main St", "Apt 8"])
T3 = "Kim" + NEL + "Minsu signed in"
case("r12-T3-next-line", "12th outside review (stopped), T3 (U+0085 between the names)", T3, data={"note": T3}, known=[KIM],
     leak=["Minsu"])
T4 = "address=%7B%22address%22%3A%2234+Side+Rd%5CnBusan%22%7D"
case("r12-T4-json-in-form-field", "12th outside review (stopped), T4", T4, data={"q": T4}, known=[("ADDRESS", "34 Side Rd" + NL + "Busan")],
     leak=["34+Side+Rd%5CnBusan", "Side+Rd"])
case("r12-T5-two-spaces", "12th outside review (stopped), T5", "User Kim  Minsu signed in", data={"note": "User Kim  Minsu signed in"},
     known=[KIM], leak=["Minsu"])

# Seventh pass (independent)
case("ip7-S1-postal-code-not-a-number", "independent pass 7, S1 (a known 04524 is not the number 4524)",
     "Order 4524 shipped; 4524 units.", data={"note": "Order 4524 shipped; 4524 units."}, known=[("ADDRESS", "04524")],
     keep=["Order 4524 shipped; 4524 units."])
case("ip7-S1-year-not-a-number", "independent pass 7, S1 (a known 02026 is not the year in 2026-11-25)", "Shipped on 2026-11-25.",
     data={"note": "Shipped on 2026-11-25."}, known=[("ID_NUMBER", "02026")], keep=["2026-11-25"])
S2 = "Kim" + NL + "Minsu wrote; Kim Minsu signed."
case("ip7-S2-other-spelling-found", "independent pass 7, S2 (the model finds Kim and Minsu on two lines)", S2, data={"note": S2},
     known=[KIM], detect=[("Kim" + NL + "Minsu", "PERSON")], where=DETECTED, leak=["Minsu"])
S3 = "tel:+82%2010%202345%206789"
case("ip7-S3-plus-in-tel-url", "independent pass 7, S3", S3, data={"tel": S3}, known=[("PHONE", "+82 10 2345 6789")],
     leak=["+82%2010%202345%206789", "6789"])

# Thirteenth outside review
A13 = "https://app.example/?q=%7B%22user%22%3A%22CONTOSO%5C%5Cjdoe%22%7D"
case("r13-A1-login-in-json-in-form", "13th outside review, A1", A13, data={"url": A13}, known=[("USERNAME", "CONTOSO" + BS + "jdoe")],
     leak=["CONTOSO%5C%5Cjdoe", "jdoe"])
B13 = '{"query":"name=Kim' + BS + 'u002BMinsu"}'
case("r13-A2-form-field-in-json", "13th outside review, A2; 14th outside review, C2 (the escape, not a literal +)", B13,
     data={"raw": B13}, known=[KIM], leak=["Kim" + BS + "u002BMinsu", "Minsu"])
case("r13-B1-two-spellings-first-seen", "13th outside review, B1 (two spellings found for the first time in one text)",
     "Kim Minsu and Kim  Minsu", data={"note": "Kim Minsu and Kim  Minsu"},
     detect=[("Kim Minsu", "PERSON"), ("Kim  Minsu", "PERSON")], where=DETECTED, leak=["Minsu"])
case("r13-B2-older-value-on-same-part", "13th outside review, B2 (a known Kim+Minsu user name and a known Kim Minsu)",
     "GET /Kim+Minsu and Kim Minsu", data={"note": "GET /Kim+Minsu and Kim Minsu"},
     known=[("USERNAME", "Kim+Minsu"), KIM], leak=["Kim+Minsu", "Minsu"], keep=["GET /"])
case("r13-B3-dot-between-digits", "13th outside review, B3 (4524.7, v1.4524.3 and a number glued to a word)",
     "Not 4524.7, v1.4524.3 or caf" + chr(0xE9) + "4.524e3", data={"note": "Not 4524.7, v1.4524.3 or caf" + chr(0xE9) + "4.524e3"},
     known=[("ID_NUMBER", "4524")], where=NOT_LOOSE, keep=["4524.7", "v1.4524.3", "caf" + chr(0xE9) + "4.524e3"])

# Fourteenth outside review
case("r14-B1-model-finds-form-spelling", "14th outside review, B1 (the model finds Kim+Minsu after Kim Minsu)", "q=Kim+Minsu&x=1",
     data={"q": "q=Kim+Minsu&x=1"}, known=[KIM], detect=[("Kim+Minsu", "PERSON")], where=DETECTED, leak=["Kim+Minsu", "Minsu"])
case("r14-B2-exponent-and-full-width", "14th outside review, B2 (1e+4524, 1e-4524 and a full-width digit before a dot)",
     "1e+4524, 1e-4524 and " + chr(0xFF14) + ".4524", data={"note": "1e+4524, 1e-4524 and " + chr(0xFF14) + ".4524"},
     known=[("ID_NUMBER", "4524")], where=NOT_LOOSE, keep=["1e+4524", "1e-4524", chr(0xFF14) + ".4524"])

# Fifteenth outside review
case("r15-B1-exponent-in-form-field", "15th outside review, B1 (1e+4524 in a form field)", "x=1e+4524&y=1", data={"q": "x=1e+4524&y=1"},
     known=[("ID_NUMBER", "4524")], where=NOT_LOOSE, keep=["1e+4524"])
case("r15-B1-signed-value-as-exponent", "15th outside review, B1 (a known -4524 in 1e-4524)", "weight 1e-4524 and delta -4524",
     data={"note": "weight 1e-4524 and delta -4524"}, known=[("ID_NUMBER", "-4524")], where=NOT_LOOSE, keep=["1e-4524"],
     leak=["delta -4524"])
AL = {"Al": "open", "%41l": "closed"}
case("r15-B2-keys-that-collide", "15th outside review, B2 (keys Al and %41l for a known Al)", jdump(AL), data=AL,
     known=[("PERSON", "Al")], where=["user", "system", "tool-text", "tool-data", "block-data", "tool-input-unrecorded"],
     leak=["%41l"], keep=["open", "closed"], fields=2)

# Sixteenth outside review (stopped before its report)
case("r16-1-int-key", "16th outside review, 1 (a phone number as an int key of tool-result data)", '{"4155550123": "open"}',
     data={"4155550123": "open"}, int_keys=True, known=[("PHONE", "4155550123")], leak=["4155550123"], keep=["open"], fields=1)

# The 70 kinds of text an agent sees, around one persona whose values the vault holds: the corpus of independent pass 6
# (record: Eleventh outside review), run by it with no detector and with the nano and medium models. leak: the pieces of
# the persona's values that stand in a kind as it is written, except "example.com" (a URL host that equals the domain of
# the known e-mail address is not that address; recorded in the Eleventh outside review).
P = {
    "name": "Kim Minsu", "nameKo": "김민수", "nameEs": JOSE, "nameJa": "山田太郎", "user": "kim_0142",
    "domUser": CORP, "email": "kim@example.com", "phone": "010-1234-5678", "phoneNum": "821023456789",
    "card": "4111 1111 1111 1111", "addr": ADDR, "emp": "EMP[3142]", "dob": "01/02/1990", "win": "u1234",
}
SEED = [("PERSON", P["name"]), ("PERSON", P["nameKo"]), ("PERSON", P["nameEs"]), ("PERSON", P["nameJa"]),
        ("USERNAME", P["user"]), ("USERNAME", P["domUser"]), ("EMAIL", P["email"]), ("PHONE", P["phone"]),
        ("PHONE", P["phoneNum"]), ("FINANCIAL", P["card"]), ("ADDRESS", P["addr"]), ("USERNAME", P["emp"]),
        ("DATE_OF_BIRTH", P["dob"]), ("USERNAME", P["win"])]
PIECES = ["Minsu", "김민수", "민수", "Jos" + chr(0xE9), "lvarez", "山田", "太郎", "kim_0142", "0142", "minsu.kim", "kim@example",
          "1234-5678", "821023456789", "8.21023456789e11", "8.21023456789E+11", "1111 1111", "Main St", "Seoul 04524", "04524",
          "3142", "02/1990", "02" + BS + "/1990", "u1234"]


def J(v, indent=None):
    """JSON.stringify: no spaces, characters as they are."""
    return json.dumps(v, ensure_ascii=False, indent=indent) if indent else json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def A(v):
    """JSON.stringify with every non-ASCII character as a Unicode escape."""
    return json.dumps(v, separators=(",", ":"))


n, ko, es, ja, us, dom, em, ph, num = (P[k] for k in ("name", "nameKo", "nameEs", "nameJa", "user", "domUser", "email", "phone", "phoneNum"))
card, addr, emp, dob, win = (P[k] for k in ("card", "addr", "emp", "dob", "win"))
REC = {"name": n, "email": em, "phone": ph, "address": addr, "username": us}
REC2 = {"name": ko, "alias": es, "kana": ja, "account": dom, "employee": emp, "born": dob}
ADDR_ESC = "12 Main St" + BS + "nSeoul 04524"
DOM2 = "CORP" + BS + BS + "minsu.kim"
WINDOC = {"path": "C:" + BS + "Users" + BS + win + BS + "Documents", "owner": dom, "share": BS + BS + "FS01" + BS + "home" + BS + us}
KO_U = BS + "uae40" + BS + "ubbfc" + BS + "uc218"
ES_U = "Jos" + BS + "u00e9 " + BS + "u00c1lvarez"
JA_U = BS + "u5c71" + BS + "u7530" + BS + "u592a" + BS + "u90ce"
KINDS = [
    ("plain", "user", "Please call " + n + " on " + ph + " or write to " + em + ". Employee id " + emp + ", born " + dob + "."),
    ("plain-ko", "user", ko + "님께 " + ph + "으로 연락해 주세요. 이메일은 " + em + "입니다. 계정은 " + us + "."),
    ("plain-ja", "user", ja + "さんに連絡してください。メールは" + em + "です。"),
    ("plain-es", "user", "Llama a " + es + " al " + ph + "; su cuenta es " + dom + "."),
    ("plain-addr", "tool", "Ship to:" + NL + addr + NL + "Attn: " + n),
    ("json-doc", "tool", J({"customer": REC, "note": "Called " + n + " twice"})),
    ("json-doc-2", "tool", J(REC2)),
    ("json-doc-ascii", "tool", A({"customer": REC, "more": REC2})),
    ("json-doc-indent", "tool", J({"customer": REC, "more": REC2}, 2)),
    ("json-array", "tool", J([REC, REC2, [n, em]])),
    ("json-number", "tool", '{"phone": ' + num + ', "alt": 8.21023456789e11, "f": ' + num + '.0, "n": 3}'),
    ("json-keyed", "tool", J({em: {"name": n}, us: True, "user:" + ko: 1})),
    ("json-data", "json", {"customer": REC, "more": REC2, "phone": int(num), "list": [n, {em: us}]}),
    ("json-in-log", "tool", "2026-11-25T10:00:00Z INFO user.updated " + J(REC) + " took=3ms"),
    ("json-in-log-ascii", "tool", "2026-11-25T10:00:01Z INFO user.updated " + A(REC2) + " took=3ms"),
    ("json-in-log-number", "tool", '2026-11-25T10:00:02Z INFO sms.sent {"to":' + num + ',"alt":8.21023456789e11,"ok":true}'),
    ("result-prefix", "tool", "Result: " + J(REC)),
    ("ndjson", "tool", NL.join(J(r) for r in [REC, REC2, {"name": n, "n": 2}])),
    ("ndjson-ascii", "tool", NL.join(A(r) for r in [REC, REC2])),
    ("json-in-json", "tool", J({"event": "audit", "payload": J({**REC, "account": dom})})),
    ("json-in-json-ascii", "tool", A({"event": "audit", "payload": A({**REC2, "address": addr})})),
    ("json-in-json-in-log", "tool", "audit: " + J({"row": J({**REC, "account": dom})})),
    ("json-3deep-in-log", "tool", "audit: " + J({"a": J({"b": J({**REC, "account": dom, "kana": ja})})})),
    ("json-3deep-ascii-in-log", "tool", "audit: " + A({"a": A({"b": A({**REC2, "address": addr})})})),
    ("json-cut-off", "tool", J({"customer": REC, "more": REC2})[:-9]),
    ("json-two-docs", "tool", J(REC) + " " + J(REC2)),
    ("pydict", "tool", "{'name': '" + n + "', 'address': '" + ADDR_ESC + "', 'account': '" + DOM2 + "', 'email': '" + em + "', 'ok': True, 'n': None}"),
    ("pylist", "tool", "[('" + n + "', '" + em + "', '" + ph + "'), ('" + es + "', None, '" + us + "')]"),
    ("pyrepr-obj", "tool", "Customer(name='" + n + "', email='" + em + "', address='" + ADDR_ESC + "', account='" + DOM2 + "')"),
    ("traceback-py", "tool", "Traceback (most recent call last):" + NL + '  File "C:' + BS + "Users" + BS + win + BS + "app" + BS + 'main.py", line 12, in <module>'
     + NL + '    load_user("' + us + '")' + NL + '  File "/srv/app/users.py", line 88, in load_user' + NL
     + '    raise ValueError(f"no such user: {name!r}")' + NL + "ValueError: no such user: '" + us + "' (" + n + " <" + em + ">)"),
    ("trace-java", "tool", "java.lang.IllegalStateException: Customer " + n + " (" + em + ") not found" + NL + TAB
     + "at com.acme.crm.CustomerService.find(CustomerService.java:42)" + NL + TAB + "at com.acme.crm.Api.get(Api.java:17)" + NL
     + "Caused by: java.sql.SQLException: no row for username='" + us + "'"),
    ("trace-node", "tool", "Error: ENOENT: no such file or directory, open 'C:" + BS + "Users" + BS + win + BS + "Documents" + BS + n + ".pdf'"
     + NL + "    at Object.openSync (node:fs:603:3)" + NL + "    at readFileSync (node:fs:471:35)"),
    ("csv", "tool", "id,name,email,phone,born" + NL + "1," + n + "," + em + "," + ph + "," + dob + NL + '2,"' + es + '",jose@example.org,' + num
     + ",1985-03-04" + NL + "3," + ko + ",," + ph + ","),
    ("csv-quoted-newline", "tool", "id,name,address" + NL + '1,"' + n + '","' + addr + '"' + NL + '2,"' + ja + '","Tokyo"'),
    ("tsv", "tool", TAB.join(["id", "name", "email", "username"]) + NL + TAB.join(["1", n, em, us]) + NL + TAB.join(["2", ko, "", dom])),
    ("md-table", "tool", "| name | email | phone |" + NL + "|---|---|---|" + NL + "| " + n + " | " + em + " | " + ph + " |" + NL + "| " + ja + " | | " + num + " |"),
    ("winpath", "user", "The file is at C:" + BS + "Users" + BS + win + BS + "Documents" + BS + "report.docx, owner " + dom + ", share " + BS + BS
     + "FS01" + BS + "home" + BS + us + BS + "notes.txt."),
    ("winpath-json", "tool", J(WINDOC)),
    ("winpath-json-in-log", "tool", "fs.audit " + J(WINDOC)),
    ("url", "tool", "GET https://example.com/users/" + us + "/profile?email=" + em + "&name=Kim+Minsu 200 OK (owner " + n + ")"),
    ("fence-json", "user", "Here is the record:" + NL + "```json" + NL + J({"customer": REC}, 2) + NL + "```" + NL + "Please summarise."),
    ("fence-sql", "user", "```sql" + NL + "INSERT INTO users (name, email, account) VALUES ('" + n + "', '" + em + "', '" + dom + "');" + NL + "```"),
    ("yaml", "tool", "customer:" + NL + "  name: " + n + NL + '  email: "' + em + '"' + NL + '  address: "' + ADDR_ESC + '"' + NL
     + "  account: '" + dom + "'" + NL + "  aliases: [" + ko + ', "' + es + '"]'),
    ("xml", "tool", '<user id="7" name="' + n + '" email="' + em + '"><phone>' + ph + "</phone><alias>" + ko + "</alias></user>"),
    ("brackets", "user", "[" + n + "] (" + us + ") {" + emp + "} <" + em + '> "' + n + QUOTE + " '" + ko + "' rows[0]=" + es + " [1] [PERSON_9]"),
    ("unicode-escaped-text", "tool", "name=" + KO_U + " alias=" + ES_U + " kana=" + JA_U),
    ("unicode-escaped-quoted", "tool", 'msg="' + KO_U + " (" + ES_U + ') wrote" by="' + n + '"'),
    ("numbers", "tool", "phone " + num + ", alt +" + num + "; card " + card + "; tel:" + ph),
    ("logfmt", "tool", 'level=info msg="Dear' + BS + "n" + us + '" user="' + n + '" addr="' + ADDR_ESC + '" acct="' + DOM2 + '"'),
    ("stray-quote", "tool", '24" monitor; msg="Dear' + BS + "n" + us + '" to="' + ADDR_ESC + '"'),
    ("bracket-in-quote", "tool", 'msg="[1] Dear' + BS + "n" + us + '" id=' + emp),
    ("env", "tool", "USER=" + us + NL + "HOME=/home/" + us + NL + "EMAIL=" + em + NL + 'FULLNAME="' + n + '"' + NL + "USERDOMAIN_USER=" + dom),
    ("mail-headers", "tool", 'From: "' + n + '" <' + em + ">" + NL + "To: =?UTF-8?Q?x?= <ops@example.org>" + NL + "Subject: Re: " + ko + " 계정 ("
     + us + ")" + NL + NL + "Regards," + NL + n + NL + ph),
    ("git-log", "tool", "commit 9fceb02d0ae598e95dc970b74767f19372d61af8" + NL + "Author: " + n + " <" + em + ">" + NL
     + "Date:   Wed Nov 25 10:00:00 2026 +0900" + NL + NL + "    Fix login for " + us),
    ("diff", "tool", "--- a/owners.json" + NL + "+++ b/owners.json" + NL + "@@ -1,3 +1,3 @@" + NL + '-  "owner": "' + n + '",' + NL
     + '+  "owner": "' + es + '",' + NL + '   "addr": "' + ADDR_ESC + '"'),
    ("http", "tool", "GET /users/" + us + " HTTP/1.1" + NL + "Host: example.com" + NL + "X-User: " + n + NL + "Cookie: user=" + us + NL + NL
     + '{"email":"' + em + '","address":"' + ADDR_ESC + '"}'),
    ("js-code", "user", 'const user = { name: "' + n + '", addr: "' + ADDR_ESC + '", acct: "' + DOM2 + '", re: /' + BS + "b" + n + BS + "b/ };"),
    ("grep", "user", 'Run grep -E "' + BS + "b" + n + BS + "b|" + BS + 'd+" file for me, then find ' + us),
    ("php-json", "tool", '{"born":"01' + BS + "/02" + BS + '/1990","home":"' + BS + "/home" + BS + "/" + us + '","name":"' + n + '"}'),
    ("php-json-in-log", "tool", 'resp={"born":"01' + BS + "/02" + BS + '/1990","home":"' + BS + "/home" + BS + "/" + us + '","name":"' + n + '"} 200'),
    ("system", "system", "You are helping " + n + " (" + em + '). Their address is "' + ADDR_ESC + '". Never reveal ' + emp + ". Keep [PERSON_1] as it is."),
    ("system-json", "system", J({"role": "assistant for", "user": REC, "phone": int(num)})),
    ("sse", "tool", "event: message" + NL + "data: " + J(REC) + NL + NL + "event: message" + NL + "data: " + A(REC2) + NL + NL),
    ("html", "tool", "<p>Dear <b>" + n + "</b>,</p><p>your account <code>" + us + '</code> &amp; mail <a href="mailto:' + em + '">' + em + "</a></p>"),
    ("ini", "tool", "[user]" + NL + "name = " + n + NL + "email = " + em + NL + "path = C:" + BS + "Users" + BS + win + BS + "AppData" + NL + "["
     + us + "]" + NL + "active = true"),
    ("sql-result", "tool", "+----+-----------+-----------------+" + NL + "| id | name      | email           |" + NL
     + "+----+-----------+-----------------+" + NL + "|  1 | " + n + " | " + em + " |" + NL + "+----+-----------+-----------------+" + NL + "1 row in set"),
    ("kv-colon", "tool", "name: " + n + NL + "email: " + em + NL + "phone: " + ph + NL + "address: |" + NL + "  12 Main St" + NL + "  Seoul 04524"),
    ("toml", "tool", "[owner]" + NL + 'name = "' + n + '"' + NL + 'addr = "' + ADDR_ESC + '"' + NL + "acct = '" + dom + "'" + NL + 'acct2 = "' + DOM2 + '"'
     + NL + 'u = "' + KO_U + '"'),
    ("double-escaped-unicode", "tool", "outer=" + J(A({"name": ko, "alias": es}))),
    ("long-sentence", "user", "Hi, this is " + n + " writing from " + addr.replace(NL, ", ") + ". My old username was " + us + " and the new one is "
     + dom + "; my card " + card + " was charged twice on " + dob + ". Can you e-mail " + em + " or call " + ph + "? 감사합니다, " + ko + "."),
]
assert len(KINDS) == 70 and len({k[0] for k in KINDS}) == 70
for kind, role, content in KINDS:
    if role == "json":
        case("ip6-kind-" + kind, "independent pass 6 corpus (70 kinds; record: Eleventh outside review), " + kind, jtext(content),
             data=content, known=SEED, where=["tool-data", "block-data", "tool-input-unrecorded"],
             leak=[p for p in PIECES if in_data(p, content)])
    else:
        case("ip6-kind-" + kind, "independent pass 6 corpus (70 kinds; record: Eleventh outside review), " + kind, content, known=SEED,
             where=TEXTS, leak=[p for p in PIECES if p in content])

# ---------------------------------------------------------------------------------------------------------------------
# (b) Open findings at 43da689 (independent pass 8)
# ---------------------------------------------------------------------------------------------------------------------

A1A = "Customer profile: https://crm.test/c?id=77&addr=Unit+4E+12+Main+St%2C+Seoul"
case("ip8-A1-form-field-exponent-address", "independent pass 8, A1 (fixed in 1.7.1: 4E+ before a known address in a form field)", A1A,
     data={"profile": A1A}, known=[("ADDRESS", "12 Main St, Seoul")], leak=["12+Main+St%2C+Seoul", "Main+St"],
     keep=["Customer profile: https://crm.test/c?id=77"])
A1P = "callback?ext=2e+415+555+0123"
case("ip8-A1-form-field-exponent-phone", "independent pass 8, A1 (fixed in 1.7.1: 2e+ before a known phone number)", A1P,
     data={"q": A1P}, known=[("PHONE", "415 555 0123")], leak=["415+555+0123", "555+0123"])
B1D = [{"type": "json", "data": {"kim@example.com": "x"}}]
case("ip8-B1-data-field-keys", "independent pass 8, B1 (fixed in 1.7.1: an object under a block field named data)", jdump(B1D), data=B1D,
     known=[("EMAIL", "kim@example.com")], where=["user", "tool-text", "tool-data"], leak=["kim@example.com"])
case("ip8-B2-percent-exponent", "independent pass 8, B2 (fixed in 1.7.1: 1e%2B4524 for a known 4524)", "x=1e%2B4524&y=1",
     data={"q": "x=1e%2B4524&y=1"}, known=[("ID_NUMBER", "4524")], where=NOT_LOOSE, keep=["1e%2B4524"])
case("ip8-B2-percent-dot", "independent pass 8, B2 (same class: a dot written as %2E between two digits)", "v=4524%2E7&w=1",
     data={"q": "v=4524%2E7&w=1"}, known=[("ID_NUMBER", "4524")], where=NOT_LOOSE, keep=["4524%2E7"])
case("ip8-C1-identical-keys", "independent pass 8, C1 (fixed in 1.7.1: two identical keys are left as they are, per the record)",
     '{"Kim Minsu":1,"Kim Minsu":2}', known=[KIM], where=["user", "system", "tool-text"], leak=["Kim Minsu"], extra=[" #2"])

# ---------------------------------------------------------------------------------------------------------------------
# (c) Generated: values of every label in several scripts x every spelling the vault promises x positions
# ---------------------------------------------------------------------------------------------------------------------

# (label, value, script, pieces): pieces are parts of the value that must not survive either (a partial mask leaks them)
VALUES = [
    ("PERSON", "Kim Minsu", "latin", ["Minsu"]),
    ("PERSON", JOSE, "latin", ["lvarez"]),
    ("EMAIL", "kim@example.com", "latin", ["kim@example"]),
    ("PHONE", "010-1234-5678", "latin", ["1234-5678"]),
    ("PHONE", "821023456789", "number", ["21023456789"]),
    ("PHONE", "+82 10 2345 6789", "latin", ["2345 6789", "6789"]),
    ("ADDRESS", ADDR, "latin", ["Main St", "Seoul 04524", "04524"]),
    ("ID_NUMBER", "ZX-88213-K", "latin", ["88213"]),
    ("ID_NUMBER", "4524", "number", ["4524"]),
    ("FINANCIAL", "4111 1111 1111 1111", "latin", ["1111 1111", "4111"]),
    ("ORG", "Acme Robotics", "latin", ["Robotics"]),
    ("USERNAME", "kim_0142", "latin", ["kim_0142"]),
    ("USERNAME", CORP, "latin", ["minsu.kim"]),
    ("URL", "https://social.example/u/minsu.k", "latin", ["social.example/u", "minsu.k"]),
    ("DATE_OF_BIRTH", "01/02/1990", "latin", ["02/1990", "1990"]),
    ("SECRET", "sk-test-4f9a8b7c6d5e", "latin", ["4f9a8b7c6d5e"]),
    ("PERSON_L2", "Mr. Lee", "latin", ["Mr. Lee", "Lee"]),
    ("URL_L2", "x.com/kimminsu", "latin", ["kimminsu"]),
    ("PERSON", "Иван Петров", "cyrillic", ["Петров"]),
    ("PERSON", "김민수", "hangul", ["민수"]),
    ("ADDRESS", "서울특별시 강남구" + NL + "테헤란로 123", "hangul", ["강남구", "테헤란로"]),
    ("ORG", "삼성전자", "hangul", ["삼성전자"]),
    ("PERSON", "王伟", "han", ["王伟"]),
    ("PERSON", "山田太郎", "han", ["山田", "太郎"]),
    ("ORG", "北京大学", "han", ["北京大学"]),
    ("PERSON", chr(0x20BB7) + "田", "han-extension-b", [chr(0x20BB7) + "田"]),  # a surname with a character outside the basic plane
    ("PERSON", "สมชาย ใจดี", "thai", ["สมชาย", "ใจดี"]),
    ("PERSON", "محمد علي", "arabic", ["محمد", "علي"]),
    ("ORG", "شركة النور", "arabic", ["النور"]),
]
SLUG = {
    "Kim Minsu": "kim-minsu", JOSE: "jose-alvarez", "kim@example.com": "email", "010-1234-5678": "phone-dashed",
    "821023456789": "phone-number", "+82 10 2345 6789": "phone-plus", ADDR: "address-2-lines", "ZX-88213-K": "id-code",
    "4524": "id-4524", "4111 1111 1111 1111": "card", "Acme Robotics": "org", "kim_0142": "username",
    CORP: "domain-login", "https://social.example/u/minsu.k": "url", "01/02/1990": "dob", "sk-test-4f9a8b7c6d5e": "secret",
    "Mr. Lee": "person-l2", "x.com/kimminsu": "url-l2", "Иван Петров": "cyrillic-name", "김민수": "hangul-name",
    "서울특별시 강남구" + NL + "테헤란로 123": "hangul-address", "삼성전자": "hangul-org", "王伟": "han-name",
    "山田太郎": "kanji-name", "北京大学": "han-org", chr(0x20BB7) + "田": "astral-name", "สมชาย ใจดี": "thai-name",
    "محمد علي": "arabic-name",
    "شركة النور": "arabic-org",
}
SPACE_VARIANTS = [("double-space", "  "), ("tab", TAB), ("nbsp", NBSP), ("ideographic-space", IDEO), ("next-line", NEL),
                  ("line-break", NL), ("line-break-indent", NL + "    ")]
LINE_VARIANTS = [("comma", ", "), ("space", " "), ("crlf", CR + NL), ("indent", NL + "    "), ("line-separator", LSEP)]
NUMBER_SPELLINGS = {
    "821023456789": ["8.21023456789e11", "8.21023456789E+11", "8.21023456789e+11", "821023456789.0", "821023456789.000",
                     "82102345678.9e1"],
    "4524": ["4.524e3", "4.524E+3", "4524.0", "4524.00", "45.24e2"],
}


def json_str(v, force=False):
    """v inside a JSON string as json.dumps writes it; force: also when that needs no backslash, one character is written
    as a Unicode escape (its first one that is not a letter or digit, else its first)."""
    s = json.dumps(v)[1:-1]
    if force and BS not in s:
        i = next((k for k, c in enumerate(v) if not c.isalnum()), 0)
        s = json.dumps(v[:i])[1:-1] + uesc(v[i]) + json.dumps(v[i + 1:])[1:-1]
    return s


def u_escaped(v):
    """v with Unicode escapes in plain text: every non-ASCII character, or for an ASCII value one character."""
    if any(ord(c) > 127 for c in v):
        return "".join(uesc(c) if ord(c) > 127 else c for c in v)
    i = next((k for k, c in enumerate(v) if not c.isalnum() and not c.isspace()), 0)
    return v[:i] + uesc(v[i]) + v[i + 1:]


def pct(v, plus=False):
    """v percent-encoded (UTF-8); with plus a form field ("+" for a space). A value that needs no escape gets its first
    character escaped, which is still the same value in a URL."""
    s = quote_plus(v, safe="") if plus else quote(v, safe="")
    if s == v:
        s = "%%%02X" % ord(v[0]) + v[1:]
    return s


def leaks_for(value, pieces, spelled, *contexts):
    """The spelled form, and each piece of the value (as written, or spelled the same way) that stands in it; only
    what stands in every context (the text, the data)."""
    out = [spelled]
    for p in pieces:
        for form in (p, quote(p, safe=""), quote_plus(p, safe=""), json.dumps(p)[1:-1]):
            if form and form in spelled:
                out.append(form)
    out = [s for s in dict.fromkeys(out) if all(s in ctx for ctx in contexts)]
    if not out:
        raise ValueError("no leak string for " + repr(value) + " spelled " + repr(spelled))
    return out


def spellings(value):
    """(name, spelled value, text, keep) for every spelling of value; text holds the spelled value in a context."""
    out = []
    raw_tpl = ("Please contact ", " today.")
    out.append(("raw", value, raw_tpl[0] + value + raw_tpl[1], ["Please contact", "today."]))
    out.append(("unicode-escape", u_escaped(value), "name=" + u_escaped(value) + " (from the export)", ["from the export"]))
    js = json_str(value)
    out.append(("json-document", js, '{"name": "' + js + '", "id": 7}', []))
    jf = json_str(value, force=True)
    if jf != js:
        out.append(("json-document-escaped", jf, '{"name": "' + jf + '", "id": 7}', []))
    deep = json.dumps(jf)[1:-1]
    out.append(("json-in-json-string", deep, json.dumps({"payload": '{"name": "' + jf + '"}'}), []))
    out.append(("json-in-log-line", jf, 'INFO user.updated {"name": "' + jf + '"} took=3ms', ["INFO user.updated", "took=3ms"]))
    p = pct(value)
    out.append(("percent", p, "GET /contacts/" + p + " HTTP/1.1", ["GET /contacts/", "HTTP/1.1"]))
    pp = quote(p, safe="")
    out.append(("percent-twice", pp, "GET /r?next=%2Fcontacts%2F" + pp + " HTTP/1.1", ["GET /r?next=", "HTTP/1.1"]))
    if any(c.isspace() for c in value):
        f = pct(value, plus=True)
        out.append(("form-field", f, "POST name=" + f + "&ok=1", ["POST name=", "ok=1"]))
        fj = json.dumps({"query": "name=" + f + "&ok=1"})
        out.append(("form-field-in-json", f, fj, []))
        fu = fj.replace("+", BS + "u002B")
        if fu != fj:
            out.append(("form-field-in-json-escaped-plus", f.replace("+", BS + "u002B"), fu, []))
    jfp = quote_plus('{"name": "' + js + '"}', safe="")
    out.append(("json-in-form-field", jfp, "https://app.example/find?q=" + jfp + "&x=1", ["https://app.example/find?q=", "x=1"]))
    # three steps: JSON inside a form field inside a JSON document
    out.append(("json-in-form-field-in-json", jfp, json.dumps({"next": "https://app.example/find?q=" + jfp + "&x=1"}), []))
    if any(ord(c) > 127 for c in value):
        up = "".join(uesc(c, upper=True) if ord(c) > 127 else c for c in value)
        out.append(("unicode-escape-upper-case", up, "name=" + up + " (from the export)", ["from the export"]))
    low = re.sub("%[0-9A-F]{2}", lambda m: m.group(0).lower(), p)
    if low != p:
        out.append(("percent-lower-case", low, "GET /contacts/" + low + " HTTP/1.1", ["GET /contacts/", "HTTP/1.1"]))
    return out


def data_for(spelled, text):
    return {"note": text, "who": spelled}


for label, value, script, pieces in VALUES:
    slug = SLUG[value]
    for name, spelled, text, keep in spellings(value):
        case("gen-" + slug + "-" + name, "generated (" + label + ", " + script + ", " + name + ")", text,
             data=data_for(spelled, text), known=[(label, value)], leak=leaks_for(value, pieces, spelled, text), keep=keep)
    if NL in value:
        for name, sep in LINE_VARIANTS:
            spelled = value.replace(NL, sep)
            text = "Ship to " + spelled + " today."
            case("gen-" + slug + "-line-break-as-" + name, "generated (" + label + ", " + script + ", a line break as " + name + ")",
                 text, data=data_for(spelled, text), known=[(label, value)], leak=leaks_for(value, pieces, spelled, text),
                 keep=["Ship to", "today."])
        # the line break written another way, inside a form field and inside a JSON document
        one = value.replace(NL, ", ")
        for name, spelled, text, keep in [
            ("form-field", quote_plus(one, safe=""), "POST addr=" + quote_plus(one, safe="") + "&ok=1", ["POST addr=", "ok=1"]),
            ("json-document", json_str(one), '{"addr": "' + json_str(one) + '", "id": 7}', []),
        ]:
            case("gen-" + slug + "-line-break-as-comma-" + name,
                 "generated (" + label + ", " + script + ", a line break as a comma, " + name + ")", text,
                 data=data_for(spelled, text), known=[(label, value)], leak=leaks_for(value, pieces, spelled, text), keep=keep)
    elif any(c == " " for c in value):
        for name, sep in SPACE_VARIANTS:
            spelled = sep.join(value.split(" "))
            text = "Please contact " + spelled + " today."
            case("gen-" + slug + "-white-space-" + name, "generated (" + label + ", " + script + ", white space as " + name + ")",
                 text, data=data_for(spelled, text), known=[(label, value)], leak=leaks_for(value, pieces, spelled, text),
                 keep=["Please contact", "today."])
        # the other white space inside a JSON document and percent-encoded in a URL
        for name, sep in [("tab", TAB), ("nbsp", NBSP), ("line-break", NL)]:
            other = sep.join(value.split(" "))
            for where_name, spelled, text, keep in [
                ("json-document", json_str(other), '{"name": "' + json_str(other) + '", "id": 7}', []),
                ("percent", quote(other, safe=""), "GET /contacts/" + quote(other, safe="") + " HTTP/1.1", ["GET /contacts/", "HTTP/1.1"]),
            ]:
                case("gen-" + slug + "-white-space-" + name + "-" + where_name,
                     "generated (" + label + ", " + script + ", white space as " + name + ", " + where_name + ")", text,
                     data=data_for(spelled, text), known=[(label, value)], leak=leaks_for(value, pieces, spelled, text), keep=keep)
    if value in NUMBER_SPELLINGS:
        for spelled in NUMBER_SPELLINGS[value]:
            text = "tel " + spelled + " (mobile)"
            case("gen-" + slug + "-number-" + spelled.replace("+", "plus").replace(".", "_"),
                 "generated (" + label + ", a number spelled " + spelled + ")", text, data=data_for(spelled, text),
                 known=[(label, value)], leak=[spelled] + [p for p in pieces if p in spelled], keep=["tel ", "(mobile)"])
            # the same spelling as a JSON number token (text only: JSON data keeps no spelling), and with its sign
            # percent-encoded in a URL
            doc = '{"tel": ' + spelled + ', "id": 7}'
            case("gen-" + slug + "-number-" + spelled.replace("+", "plus").replace(".", "_") + "-json-token",
                 "generated (" + label + ", a JSON number spelled " + spelled + ")", doc, known=[(label, value)], where=TEXTS,
                 leak=[spelled] + [p for p in pieces if p in spelled])
            if "+" in spelled:
                url = "https://sms.example/send?tel=" + spelled.replace("+", "%2B") + "&x=1"
                case("gen-" + slug + "-number-" + spelled.replace("+", "plus").replace(".", "_") + "-percent-sign",
                     "generated (" + label + ", a number spelled " + spelled + " with its sign percent-encoded)", url,
                     data=data_for(spelled.replace("+", "%2B"), url), known=[(label, value)],
                     leak=[spelled.replace("+", "%2B")] + [p for p in pieces if p in spelled], keep=["https://sms.example/send?tel="])
        num = int(value)
        doc = {"phone": num, "alt": float(num), "id": 7}
        case("gen-" + slug + "-json-numbers", "generated (" + label + ", the value as a JSON integer and a float)",
             '{"phone": ' + value + ', "alt": ' + NUMBER_SPELLINGS[value][0] + ', "id": 7}', data=doc, known=[(label, value)],
             leak=[value] + pieces, extra=[NUMBER_SPELLINGS[value][0]], fields=3)
        case("gen-" + slug + "-int-key", "generated (" + label + ", the value as a number key)", jdump({value: "open", "id": "7"}),
             data={value: "open", "id": "7"}, int_keys=True, known=[(label, value)], leak=[value], keep=["open"], fields=2)
    # the value as a key of JSON data (a map keyed by the value), written as it is and percent-encoded
    for name, spelled in [("key", value), ("key-percent", pct(value))]:
        doc = {spelled: "open", "status": "ok"}
        case("gen-" + slug + "-" + name, "generated (" + label + ", " + script + ", the value as a JSON " + name + ")", jtext(doc),
             data=doc, known=[(label, value)], leak=leaks_for(value, pieces, spelled, jtext(doc), spelled), keep=["open"], fields=2)
    # learned in the same text: the model finds the value as written, the vault finds its other spellings
    for name, spelled in [("percent", pct(value)), ("unicode-escape", u_escaped(value))]:
        text = "Contact " + value + " (also " + spelled + ")"
        case("gen-" + slug + "-learned-then-" + name, "generated (" + label + ", " + script + ", found by the model, then " + name + ")",
             text, data={"note": text}, detect=[(value, label)], where=DETECTED,
             leak=leaks_for(value, pieces, spelled, text) + [value], keep=["Contact "])

# word edges that a value meets: no-space scripts, Korean particles, punctuation (found)
EDGES = [
    ("korean-particle", ("PERSON", "김민수"), "김민수님께 연락해 주세요", ["김민수"]),
    ("thai-glued", ("PERSON", "สมชาย ใจดี"), "คุณสมชาย ใจดีครับ", ["สมชาย", "ใจดี"]),
    ("kana-after-latin", KIM, "Kim Minsuさんに連絡", ["Minsu"]),
    ("han-after-latin", ("PERSON", "王伟"), "Contact王伟today", ["王伟"]),
    ("number-end-of-sentence", ("ID_NUMBER", "4524"), "Your id is 4524.", ["4524"]),
    ("number-punctuation", ("ID_NUMBER", "4524"), "(4524), 4524, and 4524.x", ["4524"]),
    ("number-after-sign-or-letter", ("ID_NUMBER", "4524"), "size-4524, e-4524, 2+4524", ["4524"]),
    ("dotted-value-after-dot", ("USERNAME", "kim.01"), "user x.kim.01 logged in", ["kim.01"]),
    ("signed-value", ("ID_NUMBER", "+8210"), "call +8210 now", ["call +8210"]),
    ("brackets-around", KIM, "[Kim Minsu] (Kim Minsu) {Kim Minsu} <Kim Minsu>", ["Minsu"]),
]
for name, known, text, leak in EDGES:
    case("gen-edge-" + name, "generated (word edges where a value is found: " + name + ")", text, data={"note": text},
         known=[known], leak=leak)

# look-alikes that must be kept (text a model did not write: model text without a record is searched loosely by design)
LOOKALIKES = [
    ("dot-after", ("ID_NUMBER", "4524"), "Version 4524.7 is out", ["4524.7"]),
    ("dot-before", ("ID_NUMBER", "4524"), "Ratio 1.4524 measured", ["1.4524"]),
    ("dotted-version", ("ID_NUMBER", "4524"), "Build v1.4524.3 passed", ["v1.4524.3"]),
    ("exponent-plus", ("ID_NUMBER", "4524"), "Weight 1e+4524 units", ["1e+4524"]),
    ("exponent-minus-upper", ("ID_NUMBER", "4524"), "Weight 1E-4524 units", ["1E-4524"]),
    ("full-width-digit-dot", ("ID_NUMBER", "4524"), "Size " + chr(0xFF14) + ".4524 in", [chr(0xFF14) + ".4524"]),
    ("dot-full-width-digit", ("ID_NUMBER", "4524"), "Size 4524." + chr(0xFF17) + " in", ["4524." + chr(0xFF17)]),
    ("order-id-digit-after", ("ID_NUMBER", "4524"), "Order ORD-45241 shipped", ["ORD-45241"]),
    ("order-id-letters", ("ID_NUMBER", "4524"), "Order A4524B shipped", ["A4524B"]),
    ("order-id-digits-around", ("ID_NUMBER", "4524"), "Order 145247 shipped", ["145247"]),
    ("number-other-spelling-joined", ("ID_NUMBER", "4524"), "Number 4.524e3.1 here", ["4.524e3.1"]),
    ("phone-decimal", ("PHONE", "821023456789"), "Value 821023456789.5 recorded", ["821023456789.5"]),
    ("phone-other-number", ("PHONE", "821023456789"), "Value 1.821023456789e11 recorded", ["1.821023456789e11"]),
    ("phone-longer", ("PHONE", "821023456789"), "Serial 8210234567890 and 9821023456789", ["8210234567890", "9821023456789"]),
    ("phone-letter-before", ("PHONE", "821023456789"), "Serial a821023456789", ["a821023456789"]),
    ("phone-dotted", ("PHONE", "821023456789"), "Version 821023456789.1.2", ["821023456789.1.2"]),
    ("phone-exponent", ("PHONE", "821023456789"), "Exp 2e+821023456789", ["2e+821023456789"]),
    ("dashed-phone-longer", ("PHONE", "010-1234-5678"), "Ref 010-1234-56789 and 1010-1234-5678", ["010-1234-56789", "1010-1234-5678"]),
    ("name-glued", KIM, "xKim Minsuy and Kim Minsuk", ["xKim Minsuy", "Kim Minsuk"]),
    ("name-underscore", KIM, "file Kim Minsu_2", ["Kim Minsu_2"]),
    ("name-comma", KIM, "Kim, Minsu and Kim-Minsu", ["Kim, Minsu", "Kim-Minsu"]),
    ("username-glued", ("USERNAME", "kim_0142"), "kim_01420 xkim_0142 kim_0142a", ["kim_01420", "xkim_0142", "kim_0142a"]),
    ("email-glued", ("EMAIL", "kim@example.com"), "akim@example.com and kim@example.company", ["akim@example.com", "kim@example.company"]),
    ("hangul-before-hangul", ("PERSON", "김민수"), "이김민수 씨", ["이김민수"]),
    ("code-glued", ("ID_NUMBER", "ZX-88213-K"), "ZX-88213-KX and AZX-88213-K", ["ZX-88213-KX", "AZX-88213-K"]),
    ("title-glued", ("PERSON_L2", "Mr. Lee"), "Mr. Leeds wrote", ["Mr. Leeds"]),
    ("dotted-date", ("DATE_OF_BIRTH", "15.03.1985"), "Not 15.03.1985.2 or 0.15.03.1985", ["15.03.1985.2", "0.15.03.1985"]),
    ("signed-exponent", ("ID_NUMBER", "-4524"), "Weight 2E-4524 units", ["2E-4524"]),
    ("signed-plus-exponent", ("ID_NUMBER", "+8210"), "Weight 1e+8210 units", ["1e+8210"]),
    ("card-longer", ("FINANCIAL", "4111 1111 1111 1111"), "Ref 4111 1111 1111 11112", ["4111 1111 1111 11112"]),
    ("name-inside-word", ("PERSON", "Ann"), "Annabel and Joann", ["Annabel", "Joann"]),
    ("short-value-not-searched", ("ID_NUMBER", "42"), "Room 42 is free", ["Room 42 is free"]),
    ("short-name-not-searched", ("PERSON", "Al"), "Al went home", ["Al went home"]),
    ("leading-zero-is-not-a-number", ("ADDRESS", "04524"), "Invoice 4524 and 4524.0", ["Invoice 4524 and 4524.0"]),
]
for name, known, text, keep in LOOKALIKES:
    case("gen-keep-" + name, "generated (look-alike to keep: " + name + ")", text, data={"note": text}, known=[known],
         where=NOT_LOOSE, keep=keep)

# ordinary text with known values that do not occur in it: nothing changes, wherever it stands
KNOWN_SET = [KIM, ("EMAIL", "kim@example.com"), ("PHONE", "821023456789"), ("ADDRESS", ADDR), ("ID_NUMBER", "ZX-88213-K")]
ORDINARY = [
    ("math-and-percent", "1+1=2, C++ and a+b; 50% off; 100%; %zz; %"),
    ("url-and-time", "Meeting at 10:30 in room 4.2, see https://example.org/docs?page=2&sort=asc"),
    ("korean", "회의는 3시에 시작합니다. 자료는 공유 폴더에 있습니다."),
    ("japanese", "会議は午後三時に始まります。"),
    ("numbers", "Totals: 1500, 1.5e3, 15e2, 0.25 and 821023456788"),
    ("placeholder-explained", "Tags look like PERSON_1 or EMAIL_2 in the docs."),
]
for name, text in ORDINARY:
    case("gen-ordinary-" + name, "generated (ordinary text, nothing to mask: " + name + ")", text, data={"note": text}, known=KNOWN_SET,
         keep=[text])
PATHS = "Path C:" + BS + "new" + BS + "table and a literal " + BS + "u00e9 in docs"
case("gen-ordinary-backslashes", "generated (ordinary text with backslashes, nothing to mask)", PATHS, known=KNOWN_SET, where=TEXTS,
     keep=[PATHS])
DOC = '{"id": 7, "ok": true, "items": [1, 2.50, 1e3, -0.0], "s": "a' + BS + '/b"}'
case("gen-ordinary-json-document", "generated (a JSON document with nothing to mask stays as written)", DOC, known=KNOWN_SET,
     where=TEXTS, keep=[DOC])


# ---------------------------------------------------------------------------------------------------------------------
# checks and output
# ---------------------------------------------------------------------------------------------------------------------

def check(c):
    problems = []
    where = c["where"]
    if not where or any(w not in POSITIONS for w in where) or len(set(where)) != len(where):
        problems.append("where")
    has_data = "data" in c
    text_at = [w for w in where if w in ("user", "system", "tool-text", "assistant-unrecorded")]
    if "tool-input-unrecorded" in where and not has_data:
        text_at.append("tool-input-unrecorded")
    data_at = [w for w in where if w in ("tool-data", "block-data")]
    if "tool-input-unrecorded" in where and has_data:
        data_at.append("tool-input-unrecorded")
    if data_at and not has_data:
        problems.append("data positions without data")
    if not isinstance(c["text"], str):
        problems.append("text")
    for e in c["known"]:
        if not (e["label"] in LABELS or re.fullmatch("[A-Z][A-Z0-9_]*", e["label"])) or not e["value"]:
            problems.append("known " + repr(e))
    for value, label in c["detect"]:
        if label not in LABELS or not value:
            problems.append("detect " + repr(value))
    if not c["leak"] and not c["keep"]:
        problems.append("nothing to check")
    for s in c["leak"]:
        if s in c["_extra"]:
            continue
        if text_at and s not in c["text"]:
            problems.append("leak not in text: " + repr(s))
        if data_at and not in_data(s, c["data"]):
            problems.append("leak not in data: " + repr(s))
    if data_at and c["leak"] and not any(plain_for_json(s) for s in c["leak"] if s not in c["_extra"]) and "exact" not in c:
        # (a case with an exact output is checked span for span: see corpus_product.py)
        problems.append("data case without a plain leak string")
    for s in c["keep"]:
        if text_at and s not in c["text"]:
            problems.append("keep not in text: " + repr(s))
        if data_at and not in_data(s, c["data"]):
            problems.append("keep not in data: " + repr(s))
        if data_at and not plain_for_json(s):
            problems.append("keep with a character JSON escapes, in a data case: " + repr(s))
    if c["int_keys"] and not (has_data and isinstance(c["data"], dict)):
        problems.append("int_keys without an object")
    if "fields" in c:
        if not isinstance(c["fields"], int):
            problems.append("fields")
        elif has_data and isinstance(c["data"], dict) and len(c["data"]) != c["fields"]:
            problems.append("fields differs from the keys of data")
    return problems


def load_parts():
    """Cases kept in python/tests/corpus_<name>.py, one file per class of fixes (so that work on several classes at
    once does not collide in this file): each file defines add(g), where g is this module, and calls g.case(...). A
    file may also define finish(g), which runs after every file has added its cases (to extend the cases of all of
    them, whatever their order)."""
    import glob
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    me = sys.modules[__name__]
    mods = []
    for path in sorted(glob.glob(os.path.join(here, "corpus_*.py"))):
        name = "vault_corpus_part_" + os.path.basename(path)[len("corpus_"):-len(".py")]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.add(me)
        mods.append(mod)
    for mod in mods:
        if hasattr(mod, "finish"):
            mod.finish(me)


def main():
    load_parts()
    ids = [c["id"] for c in CASES]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    errors = ["duplicate id " + i for i in dup]
    for c in CASES:
        errors += [c["id"] + ": " + p for p in check(c)]
    if errors:
        sys.stderr.write(NL.join(errors) + NL)
        sys.exit(1)
    out = []
    for c in CASES:
        c = dict(c)
        del c["_extra"]
        out.append(c)
    corpus = {"version": 1, "positions": POSITIONS, "cases": out}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault_corpus.json")
    # (characters outside ASCII as they are, except the invisible ones: control and format characters and line and
    # paragraph separators are written as JSON escapes, so that a scan of the file finds none of them)
    def visible(ch):
        if ch == NL or unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp"):
            return ch
        return json.dumps(ch)[1:-1]  # (as JSON escapes it: a code point above U+FFFF as two)

    written = "".join(visible(ch) for ch in json.dumps(corpus, ensure_ascii=False, indent=1))
    with open(path, "w", encoding="utf-8") as f:
        f.write(written + NL)
    by = {}
    for c in out:
        key = ("generated" if c["id"].startswith("gen-") else "open findings" if c["id"].startswith("ip8-")
               else "pass 6 kinds" if c["id"].startswith("ip6-") else "record tables")
        by[key] = by.get(key, 0) + 1
    print("wrote", path, len(out), "cases", by)


if __name__ == "__main__":
    main()
