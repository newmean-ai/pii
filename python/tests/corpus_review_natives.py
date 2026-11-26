"""Leak corpus cases of the natives review (lens 5, 2026-11-25): capitals as the full Unicode uppercase (A1, with the
capital sharp s), a value of two Hangul syllables inside longer words (B1), values of custom labels that merge (B2),
Greek capitals without accents (B3), and a hit that ends inside a grapheme cluster (C3). Guarantee note
rows U1, V1, V4, O6 and section 10 (the README section "How a known value is found again"). The same rules are shared
cases of all six packages (python/tests/vault_cases.json); here they go through the npm and Python middlewares.

Read by gen_vault_corpus.py (load_parts): add(g) calls g.case(...). Backslashes and every character outside ASCII are
built with chr() (REVIEW-GUIDE rule 9).
"""

from urllib.parse import quote

BS = chr(92)
NL = chr(10)
TAB = chr(9)
SZ = chr(0xDF)  # sharp s
CAP_SZ = chr(0x1E9E)  # capital sharp s


def s(*cps):
    return "".join(chr(c) for c in cps)


def wide(t):
    """t in full-width ASCII (U+FF01 to U+FF5E), a space as U+3000."""
    return "".join(chr(0x3000) if c == " " else chr(ord(c) + 0xFEE0) for c in t)


def uescape(t):
    """t with every character outside ASCII as a JSON Unicode escape."""
    return "".join(c if ord(c) < 0x80 else BS + "u%04x" % ord(c) for c in t)


def add(g):
    NOT_LOOSE = g.NOT_LOOSE

    def both(cid, frm, text, **kw):
        """A case in text positions and, with the text under a key, in data positions."""
        kw.setdefault("data", {"note": text})
        return g.case(cid, frm, text, **kw)

    # ---- A1: capitals are the full Unicode uppercase (one letter may become two); and the capital sharp s
    anna = ("PERSON", "Anna Wei" + SZ)
    haupt = ("ADDRESS", "Hauptstra" + SZ + "e 5, 10115 Berlin")
    ship = "SHIP TO: ANNA WEISS, HAUPTSTRASSE 5, 10115 BERLIN"
    both("rn-A1-sharp-s-capitals", "natives review A1: a name and an address with a sharp s, in capitals (SS)", ship,
         known=[anna, haupt], leak=["WEISS", "HAUPTSTRASSE"], keep=["SHIP TO: "])
    cap = "ANNA WEI" + CAP_SZ + ", HAUPTSTRA" + CAP_SZ + "E 5, 10115 BERLIN"
    both("rn-A1-capital-sharp-s", "A1 siblings: the same in capitals with the capital sharp s", "Ship to " + cap,
         known=[anna, haupt], leak=["WEI" + CAP_SZ, "HAUPTSTRA" + CAP_SZ + "E"], keep=["Ship to "])
    both("rn-A1-capitals-lines", "A1 siblings: the address in capitals over two lines", "Ship to:" + NL + "HAUPTSTRASSE 5" + NL
         + "10115 BERLIN" + NL, known=[haupt], leak=["HAUPTSTRASSE"])
    for name, value, up in (
        ("ligature", chr(0xFB01) + "nn", "FINN"),
        ("j-caron", chr(0x1F0) + "an", "J" + chr(0x30C) + "AN"),
        ("n-apostrophe", chr(0x149) + "aa", chr(0x2BC) + "NAA"),
        ("alpha-iota", chr(0x1FB3) * 3, s(0x391, 0x399) * 3),
        ("one-word-sharp-s", "Gro" + SZ + "mann", "GROSSMANN"),
    ):
        both("rn-A1-" + name, "A1 siblings: a one-word value whose capitals are longer (%s)" % name, "Mr " + up + " called.",
             known=[("PERSON", value)], leak=[up])
    both("rn-A1-percent-capitals", "A1 siblings: the capitals percent-encoded in a form field",
         "q=" + quote("ANNA WEISS") + "&id=7", known=[anna], leak=["WEISS"])
    strasse = "Stra" + SZ + "e"
    both("rn-A1-one-word-mixed-kept", "A1 siblings: a one-word value with its sharp s kept in capitals is another spelling",
         "STRA" + SZ + "E 5", known=[("ADDRESS", strasse)], where=NOT_LOOSE, keep=["STRA" + SZ + "E 5"])

    # ---- B3: a Greek value in capitals as Greek writes them (without accents and breathings, the dialytika kept)
    nikos = ("PERSON", s(0x39D, 0x3AF, 0x3BA, 0x3BF, 0x3C2) + " " + s(0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3CC, 0x3C0, 0x3BF, 0x3C5, 0x3BB, 0x3BF, 0x3C2))
    caps = s(0x39D, 0x399, 0x39A, 0x39F, 0x3A3) + " " + s(0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A3)
    papa = s(0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A3)
    both("rn-B3-greek-capitals", "natives review B3: a Greek name in capitals without its accents",
         "As Greek writes capitals: " + caps + ".", known=[nikos], leak=[caps, papa], keep=["As Greek writes capitals: "])
    both("rn-B3-greek-capitals-tab", "B3 siblings: the same with a tab between the words", s(0x39D, 0x399, 0x39A, 0x39F, 0x3A3)
         + TAB + papa, known=[nikos], leak=[papa])
    both("rn-B3-greek-capitals-escaped", "B3 siblings: the capitals as JSON Unicode escapes", '{"name": "' + uescape(caps) + '"}',
         known=[nikos], leak=[uescape(papa)])
    both("rn-B3-greek-capitals-percent", "B3 siblings: the capitals percent-encoded", "q=" + quote(caps) + "&id=7",
         known=[nikos], leak=[quote(papa)])
    paisios = ("PERSON", s(0x3A0, 0x3B1, 0x390, 0x3C3, 0x3B9, 0x3BF, 0x3C2))
    both("rn-B3-greek-dialytika", "B3 siblings: an iota with dialytika and tonos, in capitals with the dialytika only",
         "Signed: " + s(0x3A0, 0x391, 0x3AA, 0x3A3, 0x399, 0x39F, 0x3A3) + ".", known=[paisios], leak=[s(0x3A3, 0x399, 0x39F, 0x3A3)])
    homer = ("PERSON", s(0x1F4D, 0x3BC, 0x3B7, 0x3C1, 0x3BF, 0x3C2))
    both("rn-B3-greek-polytonic", "B3 siblings: a polytonic name (a breathing and an accent), in capitals without them",
         "Signed: " + s(0x39F, 0x39C, 0x397, 0x3A1, 0x39F, 0x3A3) + ".", known=[homer], leak=[s(0x39F, 0x39C, 0x397, 0x3A1)])
    ermou = ("ADDRESS", s(0x39F, 0x3B4, 0x3CC, 0x3C2) + " " + s(0x395, 0x3C1, 0x3BC, 0x3BF, 0x3CD) + " 25, " + s(0x391, 0x3B8, 0x3AE, 0x3BD, 0x3B1))
    ermou_caps = s(0x39F, 0x394, 0x39F, 0x3A3) + " " + s(0x395, 0x3A1, 0x39C, 0x39F, 0x3A5) + " 25" + NL + s(0x391, 0x398, 0x397, 0x39D, 0x391)
    both("rn-B3-greek-address-lines", "B3 siblings: an address in capitals over two lines", "Ship to:" + NL + ermou_caps + NL,
         known=[ermou], leak=[s(0x395, 0x3A1, 0x39C, 0x39F, 0x3A5)])
    plain = s(0x39D, 0x3B9, 0x3BA, 0x3BF, 0x3C2) + " " + s(0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3BF, 0x3C0, 0x3BF, 0x3C5, 0x3BB, 0x3BF, 0x3C2)
    both("rn-B3-greek-mixed-kept", "B3 siblings: not in capitals and without accents is another spelling",
         "Not the same: " + plain + ".", known=[nikos], where=NOT_LOOSE, keep=[plain])

    # ---- B1: a value of two Hangul syllables: Hangul right after it is an edge only as particles, 님, 씨 or the copula
    jiwon = ("PERSON", s(0xC9C0, 0xC6D0))
    jungsu = ("PERSON", s(0xC815, 0xC218))
    subsidy = s(0xC815, 0xBD80) + " " + s(0xC9C0, 0xC6D0, 0xAE08, 0xC744) + " " + s(0xC9C0, 0xC6D0, 0xD588, 0xC2B5, 0xB2C8, 0xB2E4) + "."
    both("rn-B1-subsidy-kept", "natives review B1: 'applied for the government subsidy' with a known name that is also the word",
         subsidy, known=[jiwon], where=NOT_LOOSE, keep=[subsidy])
    purifier = s(0xC815, 0xC218, 0xAE30, 0xB97C) + " " + s(0xC0C0, 0xB2E4)
    both("rn-B1-purifier-kept", "B1: 'bought a water purifier' with a known name that is also a word", purifier, known=[jungsu],
         where=NOT_LOOSE, keep=[purifier])
    forms = s(0xC9C0, 0xC6D0, 0xC790, 0xC640) + " " + s(0xC9C0, 0xC6D0, 0xC11C) + ", " + s(0xC815, 0xC218, 0xB9AC)
    both("rn-B1-longer-words-kept", "B1 siblings: other longer words (applicants, application forms, cleanup)", forms,
         known=[jiwon, jungsu], where=NOT_LOOSE, keep=[forms])
    for name, tail in (
        ("nim-kkeseo", s(0xB2D8, 0xAED8, 0xC11C)),
        ("i-neun", s(0xC774, 0xB294)),
        ("vocative", s(0xC544)),
        ("ege", s(0xC5D0, 0xAC8C)),
        ("copula", s(0xC785, 0xB2C8, 0xB2E4)),
        ("past-copula", s(0xC774, 0xC5C8, 0xC5B4, 0xC694)),
        ("ssi", s(0xC528)),
    ):
        text = s(0xC624, 0xB298) + " " + s(0xC9C0, 0xC6D0) + tail + " " + s(0xC6C3, 0xC5C8, 0xB2E4) + "."
        both("rn-B1-name-" + name, "B1 siblings: the name with particles, an honorific or the copula (%s)" % name, text,
             known=[jiwon], leak=[s(0xC9C0, 0xC6D0)])

    # ---- B2: custom labels that merge (the npm and Python packages kept the family; the natives now do too)
    both("rn-B2-custom-merge", "natives review B2: two known values of a custom label that overlap",
         "Order AB-1234-CD for Lee Jiwon shipped.", known=[("CUSTOMER", "AB-1234"), ("CUSTOMER", "1234-CD")],
         leak=["AB-1234", "1234-CD"], keep=["Order ", " for Lee Jiwon shipped."])

    # ---- C3: a hit takes in what joins its last character, so that nothing of the value is left after the placeholder
    # (with a full-width character elsewhere, the text has a reading whose map once cut such a hit short)
    watanabe = ("PERSON", s(0x6E21, 0x8FBA))
    kim = ("PERSON", s(0xAE40, 0xBBFC, 0xC218))
    for name, after in (("variation-selector", chr(0xE0100)), ("selector-16", chr(0xFE0F)), ("joiner", chr(0x200D)),
                        ("mark", chr(0x301)), ("marks", chr(0x301) * 3)):
        text = s(0x6E21, 0x8FBA) + after + s(0x3055, 0x3093) + wide("(") + s(0x6771, 0x4EAC) + wide(")") + s(0x306B, 0x9023, 0x7D61)
        both("rn-C3-han-" + name, "natives review C3: a Han name followed by what joins it (%s), in a text with a reading" % name,
             text, known=[watanabe], leak=[s(0x8FBA)])
    for name, after in (("tone-mark", chr(0x302E)), ("acute", chr(0x301))):
        text = s(0xAE40, 0xBBFC, 0xC218) + after + s(0xB2D8, 0xAED8) + " " + wide("OK")
        both("rn-C3-hangul-" + name, "C3 siblings: a Hangul name followed by what joins it (%s), in a text with a reading" % name,
             text, known=[kim], leak=[s(0xBBFC, 0xC218)])
    # a final consonant written as a conjoining jamo makes another syllable with the name's last one (U+C218 and U+11AB
    # read as U+C21C, NFC): another name, kept whole, nothing of it masked
    other = s(0xAE40, 0xBBFC, 0xC218, 0x11AB, 0xB2D8, 0xAED8)
    both("rn-C3-hangul-final-jamo-kept", "C3 siblings: a final jamo after the name makes another syllable, another name (kept whole)",
         other + " " + wide("OK"), known=[kim], keep=[other])
    both("rn-C3-escaped-mark", "C3 siblings: the mark written as a JSON Unicode escape", '{"to": "' + uescape(s(0x6E21, 0x8FBA)) + BS
         + "u0301" + s(0x3055, 0x3093) + '"}', known=[watanabe], leak=[uescape(s(0x8FBA))])
