"""Spelling steps of the vault guarantee, shared by the finite product (corpus_product.py, gate 2 of the 1.7.1
release standard) and the fuzzer (gen_vault_fuzz.py, gate 3).

Standard library only. A text is kept as segments, neighbors and values in turn: [n0, v1, n1, ..., vk, nk]. A step
changes the text in one of two ways:

- a human step (a spelling a person or a program writes for a value: section 10 of the guarantee note, rows U1, U2, U3,
  W1, W2 and W4; the README section "How a known value is found again") changes one value segment and leaves the
  neighbors alone;
- a layer (a serializer setting) writes the whole text again: every segment character by character, and a container
  around the whole (a JSON object, a Python dict, a URL, a form). Each layer is checked against the serializer itself
  (json.dumps, repr, str, quote, quote_plus, urlencode) on the whole text, so the segments always add up to it.

The expected masked text is then the segments with each value segment replaced by its placeholder, written as it is
(the vault puts the placeholder where the written span was, in every reading: "To%20[PERSON_1]%2C" in a URL). A JSON
document may be written again as JSON writes it (section 5: "escapes it did not need are gone"), so runners compare
JSON documents by what they hold (canonical form), and all other text exactly.

Backslashes and control characters are built with chr() (REVIEW-GUIDE rule 9): this file has no escape sequences.
"""
from __future__ import annotations

import json
import unicodedata
from urllib.parse import quote, quote_plus, unquote, urlencode

BS = chr(92)
NL = chr(10)
CR = chr(13)
TAB = chr(9)
NBSP = chr(0xA0)
NEL = chr(0x85)
IDEO = chr(0x3000)
NNBSP = chr(0x202F)
QUOTE = chr(34)
APOS = chr(39)
WIDE_BS = chr(0xFF3C)
WIDE_PCT = chr(0xFF05)
HEX = "0123456789abcdefABCDEF"

# EMAIL and USERNAME values match in any ASCII case; so does a value of two or more words (section 10, U1). A URL
# matches in any case only in its host, so it is left to the capitals.
ASCII_CASE_LABELS = {"EMAIL", "USERNAME"}

URL_PREFIX = "https://crm.test/c?q="


def family(label):
    """The placeholder family of a label (PERSON_L2 is numbered with PERSON)."""
    return label[:-3] if label.endswith("_L2") else label


def placeholders(known):
    """The placeholder of each known value, in the order the values are tagged into a fresh vault."""
    count, out = {}, []
    for label, _value in known:
        f = family(label)
        count[f] = count.get(f, 0) + 1
        out.append("[" + f + "_" + str(count[f]) + "]")
    return out


class Value:
    """A known value: its label and the value as the vault holds it."""

    def __init__(self, label, held):
        self.label = label
        self.held = held
        self.words = any(c.isspace() for c in held)
        has_ascii_letter = any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in held)
        self.ascii_case = label in ASCII_CASE_LABELS or (self.words and has_ascii_letter)
        self.non_ascii_cased = any(ord(c) > 127 and c.lower() != c.upper() for c in held)


class Doc:
    """A text as segments [n0, v1, n1, ..., vk, nk] with the known values v1..vk. layered: a layer has written the text
    (the neighbors are no longer as a person wrote them)."""

    def __init__(self, segs, values, layered=False, done=None):
        assert len(segs) == 2 * len(values) + 1
        self.segs = list(segs)
        self.values = list(values)
        self.layered = layered
        # the kinds of human step each value has had (a second change of case is not a spelling in scope)
        self.done = [frozenset(x) for x in done] if done is not None else [frozenset() for _v in values]

    def text(self):
        return "".join(self.segs)

    def written(self, i):
        return self.segs[2 * i + 1]

    def expect(self, marks):
        """The text with value i replaced by marks[i]."""
        out = []
        for j, s in enumerate(self.segs):
            out.append(marks[j // 2] if j % 2 else s)
        return "".join(out)

    def with_value(self, i, written, kind=None):
        segs = list(self.segs)
        segs[2 * i + 1] = written
        done = list(self.done)
        if kind is not None:
            done[i] = done[i] | {kind}
        return Doc(segs, self.values, self.layered, done)


# ---------------------------------------------------------------------------------------------------------------------
# layers (serializer settings)
# ---------------------------------------------------------------------------------------------------------------------

def _json_body(s, ascii_only):
    return json.dumps(s, ensure_ascii=ascii_only)[1:-1]


def _upper_hex(body):
    """A JSON string body with the hex digits of its Unicode escapes in capitals (the escapes stay as they are)."""
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c == BS and i + 1 < len(body):
            if body[i + 1] == "u":
                out.append(BS + "u" + body[i + 2:i + 6].upper())
                i += 6
            else:
                out.append(body[i:i + 2])
                i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _repr_quote(text):
    """The quotation mark Python's repr() puts around a str."""
    return QUOTE if (APOS in text and QUOTE not in text) else APOS


def _repr_body(s, q):
    """s as Python's repr() writes it inside the quotation mark q."""
    out = []
    for c in s:
        if c == q or c == BS:
            out.append(BS + c)
        elif c == TAB:
            out.append(BS + "t")
        elif c == NL:
            out.append(BS + "n")
        elif c == CR:
            out.append(BS + "r")
        elif not c.isprintable():
            cp = ord(c)
            out.append(BS + ("x%02x" % cp if cp < 0x100 else "u%04x" % cp if cp < 0x10000 else "U%08x" % cp))
        else:
            out.append(c)
    return "".join(out)


def _bytes_quote(raw):
    return QUOTE if (ord(APOS) in raw and ord(QUOTE) not in raw) else APOS


def _bytes_body(raw, q):
    """Bytes as Python's repr() writes them inside the quotation mark q."""
    out = []
    for b in raw:
        c = chr(b)
        if c == q or c == BS:
            out.append(BS + c)
        elif c == TAB:
            out.append(BS + "t")
        elif c == NL:
            out.append(BS + "n")
        elif c == CR:
            out.append(BS + "r")
        elif 0x20 <= b < 0x7F:
            out.append(c)
        else:
            out.append(BS + "x%02x" % b)
    return "".join(out)


def _lower_hex_percent(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s) and all(ch in HEX for ch in s[i + 1:i + 3]):
            out.append("%" + s[i + 1:i + 3].lower())
            i += 3
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


class Layer:
    """A serializer setting: seg(text_of_segment, whole_text) writes one segment, wrap(whole_text) gives the container
    (prefix, suffix), and whole(whole_text) is the serializer itself, which the segments are checked against."""

    kind = "layer"

    def __init__(self, name, doc, seg, wrap, whole):
        self.name = name
        self.doc = doc
        self.seg = seg
        self.wrap = wrap
        self.whole = whole

    def apply(self, d):
        full = d.text()
        pre, post = self.wrap(full)
        segs = [self.seg(s, full) for s in d.segs]
        segs[0] = pre + segs[0]
        segs[-1] = segs[-1] + post
        out = Doc(segs, d.values, True, d.done)
        want = self.whole(full)
        if out.text() != want:
            raise AssertionError("layer " + self.name + " does not add up: " + repr(out.text()) + " != " + repr(want))
        return out


def _layer(name, doc, seg, wrap, whole):
    return Layer(name, doc, seg, wrap, whole)


LAYERS = {}


def _add(layer):
    LAYERS[layer.name] = layer


_add(_layer("json-ascii", "json.dumps({'note': text}) (ensure_ascii=True: Unicode escapes)",
            lambda s, full: _json_body(s, True), lambda full: ('{"note": "', '"}'),
            lambda full: json.dumps({"note": full})))
_add(_layer("json-utf8", "JSON.stringify({note: text}) (json.dumps with ensure_ascii=False and compact separators)",
            lambda s, full: _json_body(s, False), lambda full: ('{"note":"', '"}'),
            lambda full: json.dumps({"note": full}, ensure_ascii=False, separators=(",", ":"))))
_add(_layer("py-str", "str({'note': text}) (Python repr of a dict: repr escapes)",
            lambda s, full: _repr_body(s, _repr_quote(full)),
            lambda full: ("{'note': " + _repr_quote(full), _repr_quote(full) + "}"),
            lambda full: str({"note": full})))
_add(_layer("quote", "a URL with quote(text) (percent escapes, a space as %20)",
            lambda s, full: quote(s), lambda full: (URL_PREFIX, ""), lambda full: URL_PREFIX + quote(full)))
_add(_layer("quote-plus", "a URL with quote_plus(text) (percent escapes, a space as +)",
            lambda s, full: quote_plus(s), lambda full: (URL_PREFIX, ""), lambda full: URL_PREFIX + quote_plus(full)))
_add(_layer("form", "a form body, urlencode({'name': text, 'id': '7'})",
            lambda s, full: quote_plus(s), lambda full: ("name=", "&id=7"),
            lambda full: urlencode({"name": full, "id": "7"})))
# (fuzzer only: more settings a program writes)
_add(_layer("json-slash", "json.dumps({'note': text}) with the slash escaped as JSON allows (PHP json_encode)",
            lambda s, full: _json_body(s, True).replace("/", BS + "/"), lambda full: ('{"note": "', '"}'),
            lambda full: json.dumps({"note": full}).replace("/", BS + "/")))
_add(_layer("json-upper-hex", "a JSON string body, Unicode escapes with hex digits in capitals, no container",
            lambda s, full: _upper_hex(_json_body(s, True)), lambda full: ("", ""),
            lambda full: _upper_hex(_json_body(full, True))))
_add(_layer("py-set", "str({text}) (Python repr of a set of one string)",
            lambda s, full: _repr_body(s, _repr_quote(full)),
            lambda full: ("{" + _repr_quote(full), _repr_quote(full) + "}"), lambda full: str({full})))
_add(_layer("py-bytes", "str(text.encode()) (Python repr of UTF-8 bytes, b'...')",
            lambda s, full: _bytes_body(s.encode("utf-8"), _bytes_quote(full.encode("utf-8"))),
            lambda full: ("b" + _bytes_quote(full.encode("utf-8")), _bytes_quote(full.encode("utf-8"))),
            lambda full: str(full.encode("utf-8"))))
_add(_layer("quote-lower", "a URL with quote(text), hex digits in small letters",
            lambda s, full: _lower_hex_percent(quote(s)), lambda full: (URL_PREFIX, ""),
            lambda full: URL_PREFIX + _lower_hex_percent(quote(full))))

# the named settings of gate 2 (model_dump(mode="json") is the data layer of corpus_product.py): the default settings of
# the serializers an application or a framework writes a prompt with, and the bytes repr LangChain writes for a tool
# that returns bytes (section 10, S13 and G8)
PRODUCT_LAYERS = ["json-ascii", "json-utf8", "py-str", "quote", "quote-plus", "form", "py-bytes"]
FUZZ_LAYERS = PRODUCT_LAYERS + ["json-slash", "json-upper-hex", "py-set", "quote-lower"]


# ---------------------------------------------------------------------------------------------------------------------
# human steps (one value)
# ---------------------------------------------------------------------------------------------------------------------

WHITE_SPACE = [
    ("tab", TAB), ("no-break-space", NBSP), ("ideographic-space", IDEO), ("next-line", NEL),
    ("narrow-no-break-space", NNBSP), ("three-spaces", "   "), ("line-feed", NL), ("crlf-indent", CR + NL + "  "),
]
COMMA_BREAKS = [
    ("comma-as-lf", NL), ("comma-as-crlf", CR + NL), ("comma-lf", "," + NL), ("comma-as-indented", NL + "    "),
]
LINE_BREAKS = [("lf-as-comma", ", "), ("lf-as-crlf", CR + NL), ("lf-as-indented", NL + "  ")]

# Frozen seeds: known values of every label in several scripts (the finite product uses these; the fuzzer these and
# EXTRA_SEEDS). (id, label, value as the vault holds it)
SEEDS = [
    ("person-latin", "PERSON", "Kim Minsu"),
    ("person-accents", "PERSON", "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a"),
    ("person-hangul", "PERSON", "김민수"),
    ("person-han-astral", "PERSON", chr(0x20BB7) + "田"),
    ("person-l2-greek", "PERSON_L2", "Πα" + chr(0x390) + "σιος Λάμπρου"),
    ("person-arabic", "PERSON", "محمد علي"),
    ("person-thai", "PERSON", "สมชาย ใจดี"),
    ("email", "EMAIL", "kim.minsu@example.com"),
    ("phone-spaced", "PHONE", "415 555 0123"),
    ("address-comma", "ADDRESS", "12 Main St, Seoul"),
    ("address-lines", "ADDRESS", "221B Baker Street" + NL + "London NW1 6XE"),
    ("address-hangul", "ADDRESS", "서울특별시 강남구 테헤란로 152"),
    ("id-number", "ID_NUMBER", "900101-1234567"),
    ("financial", "FINANCIAL", "DE89 3704 0044 0532 0130 00"),
    ("org", "ORG", "Acme Robotics GmbH"),
    ("url", "URL", "https://crm.example.com/u/kim"),
    ("url-l2", "URL_L2", "kim-minsu.example.dev"),
    ("date-of-birth", "DATE_OF_BIRTH", "1990-01-02"),
    ("secret", "SECRET", "sk-live-4f9a8b7c6d5e"),
    ("username", "USERNAME", "kim_0142"),
]
EXTRA_SEEDS = [
    ("person-hangul-two", "PERSON", "이준"),
    ("person-han", "PERSON", "王小明"),
    ("person-kana", "PERSON", "さとう はなこ"),
    ("person-cyrillic", "PERSON", "Иван Петров"),
    ("person-devanagari", "PERSON", "राहुल शर्मा"),
    ("person-sharp-s", "PERSON", "J" + chr(0xFC) + "rgen Wei" + chr(0xDF)),
    ("person-l2-latin", "PERSON_L2", "Ana Lima"),
    ("org-hangul", "ORG", "한빛전자"),
    ("phone-dashes", "PHONE", "010-1234-5678"),
    ("id-digits", "ID_NUMBER", "821023456789"),
    ("email-accents", "EMAIL", "jos" + chr(0xE9) + "@b" + chr(0xFC) + "ro.de"),
    ("username-dots", "USERNAME", "lee.jiwon"),
    ("secret-token", "SECRET", "ghp_7Hq2Lw9Xz4Kp"),
    ("address-lines-hangul", "ADDRESS", "부산광역시 해운대구" + NL + "센텀로 99"),
]
# A one-word value in small letters stays out (U1: "Will" and "will"): text that must come out unchanged.
KEEP_SEEDS = [("keep-one-word", "PERSON", "Rose", "rose")]


def _has_backslash(w):
    """Whether w holds a backslash, also one written as a percent escape at any depth ("%5C", "%255C") or full width:
    a change of case there would make another escape (the u of a Unicode escape as U)."""
    for _depth in range(6):
        if BS in w or WIDE_BS in w:
            return True
        decoded = unquote(w)
        if decoded == w:
            return False
        w = decoded
    return True


def _has_percent(w):
    return "%" in w or WIDE_PCT in w


def _marked(w, j):
    """Whether the character at j carries a combining mark (in NFD, "e" and U+0301 are "é", which is not ASCII)."""
    return j + 1 < len(w) and unicodedata.combining(w[j + 1]) != 0


def _ascii_lower(w):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" and not _marked(w, j) else c for j, c in enumerate(w))


def _ascii_mixed(w):
    out, n = [], 0
    for j, c in enumerate(w):
        if (("a" <= c <= "z") or ("A" <= c <= "Z")) and not _marked(w, j):
            out.append(c.upper() if n % 2 else c.lower())
            n += 1
        else:
            out.append(c)
    return "".join(out)


def _wide(w):
    return "".join(IDEO if c == " " else chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in w)


def _replace_spaces(w, by):
    out, i = [], 0
    while i < len(w):
        if w[i] == " ":
            while i < len(w) and w[i] == " ":
                i += 1
            out.append(by)
        else:
            out.append(w[i])
            i += 1
    return "".join(out)


class Human:
    """A spelling of one value. change(written, value) gives the new written form, or None where the step does not
    apply (it would change nothing, or the result is not a spelling the guarantee puts in scope)."""

    kind = "human"

    def __init__(self, name, doc, change, family=None):
        self.name = name
        self.doc = doc
        self.change = change
        # steps of one family (a change of case) apply to a value once
        self.family = family

    def apply(self, d, i):
        if self.family is not None and self.family in d.done[i]:
            return None
        new = self.change(d.written(i), d.values[i])
        if new is None or new == d.written(i):
            return None
        return d.with_value(i, new, self.family)


def _capitals(w, v):
    # all capitals, Unicode default mapping (D2: sharp s as SS). Not across a backslash escape (u would become U);
    # across a percent escape only where the letters it hides need not change (ASCII case, or no other letters)
    if _has_backslash(w) or (_has_percent(w) and v.non_ascii_cased and not v.ascii_case):
        return None
    return w.upper()


def _lower(w, v):
    if not v.ascii_case or _has_backslash(w):
        return None
    return _ascii_lower(w)


def _mixed(w, v):
    if not v.ascii_case or _has_backslash(w):
        return None
    return _ascii_mixed(w)


def _nfd(w, v):
    return unicodedata.normalize("NFD", w)


def _full_width(w, v):
    return _wide(w)


HUMAN = {}


def _add_human(h):
    HUMAN[h.name] = h


_add_human(Human("capitals", "all capitals (U1: any value in all capitals)", _capitals, "case"))
_add_human(Human("lower", "ASCII letters in small letters (U1: EMAIL, USERNAME, a value of two or more words)", _lower, "case"))
_add_human(Human("mixed", "ASCII letters in mixed case (U1, as lower)", _mixed, "case"))
_add_human(Human("nfd", "Unicode NFD (U2)", _nfd))
_add_human(Human("full-width", "ASCII as full-width forms, a space as U+3000 (U3)", _full_width))
for _name, _by in WHITE_SPACE:
    _add_human(Human("white-space:" + _name, "each run of spaces of the value as " + _name + " (W1)",
                     lambda w, v, by=_by: _replace_spaces(w, by) if (" " in w and " " in v.held) else None))
for _name, _by in COMMA_BREAKS:
    _add_human(Human("line-break:" + _name, "a comma and a space of the value as " + _name + " (W2, W4)",
                     lambda w, v, by=_by: w.replace(", ", by) if (", " in w and ", " in v.held) else None))
for _name, _by in LINE_BREAKS:
    _add_human(Human("line-break:" + _name, "a line break of the value as " + _name + " (W2)",
                     lambda w, v, by=_by: w.replace(NL, by) if (NL in v.held and NL in w and CR + NL not in w) else None))


class Around(Human):
    """A known date written inside other text (section 10, S17 and G36): an ISO timestamp, which starts with the date,
    and a Python repr, which holds its year, month and day. The vault replaces the date (the timestamp's first ten
    characters; the repr's "1990, 1, 2"), and the text around it stays. Only before a layer: the text around the value
    is written by a person or a program, as written."""

    def __init__(self, name, doc, write):
        Human.__init__(self, name, doc, None)
        self.write = write

    def apply(self, d, i):
        held = d.values[i].held
        if d.layered or d.written(i) != held or not _iso_date(held):
            return None
        before, written, after = self.write(held)
        segs = list(d.segs)
        segs[2 * i] = segs[2 * i] + before
        segs[2 * i + 1] = written
        segs[2 * i + 2] = after + segs[2 * i + 2]
        return Doc(segs, d.values, d.layered)


def _iso_date(v):
    return len(v) == 10 and v[4] == "-" and v[7] == "-" and (v[:4] + v[5:7] + v[8:]).isdigit()


def _ymd(v):
    return "%d, %d, %d" % (int(v[:4]), int(v[5:7]), int(v[8:]))


for _name, _doc, _write in [
    ("date:js-date", "as JSON writes a JS Date (1990-01-02T00:00:00.000Z)", lambda v: ("", v, "T00:00:00.000Z")),
    ("date:iso-utc", "an ISO timestamp in UTC (1990-01-02T09:30:00Z)", lambda v: ("", v, "T09:30:00Z")),
    ("date:py-isoformat", "datetime.isoformat() with an offset (1990-01-02T09:30:00+09:00)", lambda v: ("", v, "T09:30:00+09:00")),
    ("date:py-date-repr", "repr of a Python date (datetime.date(1990, 1, 2))", lambda v: ("datetime.date(", _ymd(v), ")")),
    ("date:py-datetime-repr", "repr of a Python datetime (datetime.datetime(1990, 1, 2, 9, 30))",
     lambda v: ("datetime.datetime(", _ymd(v), ", 9, 30)")),
]:
    _add_human(Around(_name, _doc + " (S17)", _write))


def step(name):
    return LAYERS.get(name) or HUMAN[name]


# ---------------------------------------------------------------------------------------------------------------------
# comparing what the provider received with what is expected
# ---------------------------------------------------------------------------------------------------------------------

def canon(x):
    """A value for comparison: a JSON document (an object or an array, as text) is compared by what it holds, at
    every depth, so that the rewrites section 5 decides (escapes written again as JSON writes them, spacing) count as
    intact; any other text exactly."""
    if isinstance(x, str):
        t = x.strip()
        if t[:1] in ("{", "["):
            try:
                parsed = json.loads(t)
            except (ValueError, RecursionError):
                return x
            if isinstance(parsed, (dict, list)):
                return canon(parsed)
        return x
    if isinstance(x, list):
        return [canon(v) for v in x]
    if isinstance(x, dict):
        return {json.dumps(canon(k), ensure_ascii=False, sort_keys=True): canon(v) for k, v in x.items()}
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int, float)):
        return float(x)
    return x


def fold(s):
    """A rough reading of s for checks of the generator (not the vault's): NFC, width and case folded."""
    s = unicodedata.normalize("NFKC", s)
    return s.casefold()
