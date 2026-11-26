"""Leak corpus cases of the 23rd outside review, items A4 and A5: case, width and escape spellings that compose, and
white space that cuts the window an escape is read in (guarantee note rows U1, U2, U3, W1, W2, S1, S5, S6, S13 and
section 10; the README section "How a known value is found again").

A4: a held "José García" written "JOSÉ   GARCÍA" (capitals, other white space) and a held "kim@example.com" written
"kim％40example.com" (a full-width percent sign) reached the provider. The class: every combination of letter case,
full-width ASCII, white space, NFD and escapes. A5: "Contact: Kim" + 45 spaces + "%4Dinsu." for a held "Kim Minsu" was
sent, because the part of the text read around an escape ended inside the run of white space. The class: a run of white
space of any kind and length, line breaks too, before or after an escape, or between two.

Read by gen_vault_corpus.py (load_parts): add(g) calls g.case(...). Backslashes and every character outside ASCII are
built with chr() (REVIEW-GUIDE rule 9).
"""

import unicodedata

BS = chr(92)
NL = chr(10)
CR = chr(13)
TAB = chr(9)
NBSP = chr(0xA0)
IDEO = chr(0x3000)  # ideographic space
ACUTE = chr(0x301)  # combining acute accent


def wide(s):
    """s in full-width ASCII (U+FF01 to U+FF5E), a space as U+3000."""
    return "".join(IDEO if c == " " else chr(ord(c) + 0xFEE0) for c in s)


def add(g):
    ALL, NOT_LOOSE = g.ALL, g.NOT_LOOSE
    garcia = ("PERSON", "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a")
    kim = ("PERSON", "Kim Minsu")
    email = ("EMAIL", "kim@example.com")
    JOSE, GARCIA = "JOS" + chr(0xC9), "GARC" + chr(0xCD) + "A"

    def both(cid, frm, text, **kw):
        """A case in text positions and, with the text under a key, in data positions."""
        kw.setdefault("data", {"note": text})
        return g.case(cid, frm, text, **kw)

    # ---- A4: capitals with their white space written another way (the capitals were not indexed for it)
    spelled = JOSE + "   " + GARCIA
    both("r23-A4-capitals-white-space", "23rd outside review A4: a name in capitals with three spaces",
         "Signed: " + spelled + ".", known=[garcia], leak=[spelled, GARCIA], keep=["Signed: "])
    for name, space in (("tab", TAB), ("no-break-space", NBSP), ("ideographic-spaces", IDEO + IDEO),
                        ("line-break", NL), ("crlf-indented", CR + NL + "    "), ("mixed", " " + TAB + NBSP + " ")):
        written = JOSE + space + GARCIA
        both("g23-A4-capitals-" + name, "23rd outside review A4, siblings: capitals with other white space (%s)" % name,
             "Signed: " + written + ".", known=[garcia], leak=[written, GARCIA])
    nfd = unicodedata.normalize("NFD", spelled)
    both("g23-A4-capitals-nfd-white-space", "A4 siblings: capitals in NFD with three spaces", "Signed: " + nfd + ".",
         known=[garcia], leak=[nfd, "GARCI"])
    full = wide("JOS") + chr(0xC9) + IDEO + IDEO + wide("GARC") + chr(0xCD) + wide("A")
    both("g23-A4-capitals-full-width-white-space", "A4 siblings: capitals in full width with two U+3000",
         "Signed: " + full + ".", known=[garcia], leak=[full, wide("GARC")])
    pct = "JOS%C3%89%20%20GARC%C3%8DA"
    both("g23-A4-capitals-percent-white-space", "A4 siblings: capitals percent-encoded with two spaces",
         "https://crm.test/c?name=" + pct + "&id=7", known=[garcia], leak=[pct, "GARC%C3%8DA"])
    form = "JOS%C3%89++GARC%C3%8DA"
    both("g23-A4-capitals-form-white-space", "A4 siblings: capitals in a form field, two spaces as plus signs",
         "name=" + form + "&id=7", known=[garcia], leak=[form, "GARC%C3%8DA"])
    uesc = "JOS" + BS + "u00c9" + BS + "t" + BS + "tGARC" + BS + "u00cdA"
    both("g23-A4-capitals-json-escapes-white-space", "A4 siblings: capitals with Unicode escapes and two escaped tabs",
         '{"name": "' + uesc + '"}', known=[garcia], leak=[uesc, "tGARC"])
    pyrepr = JOSE + BS + "xa0" + GARCIA  # (repr() escapes the no-break space only)
    both("g23-A4-capitals-repr-white-space", "A4 siblings: capitals with Python repr escapes, a no-break space",
         "{'name': '" + pyrepr + "'}", known=[garcia], leak=[pyrepr, "xa0GARC"])
    weiss = ("PERSON", "J" + chr(0xFC) + "rgen Wei" + chr(0xDF))
    weiss_up = "J" + chr(0xDC) + "RGEN" + NL + "  WEISS"
    both("g23-A4-capitals-longer-white-space", "A4 siblings: capitals longer than the value (sharp s), over two lines",
         "Signed: " + weiss_up + ".", known=[weiss], leak=[weiss_up, "WEISS"])
    malaga = ("ADDRESS", "Calle Mayor 5, M" + chr(0xE1) + "laga")
    malaga_up = "CALLE MAYOR 5" + NL + "M" + chr(0xC1) + "LAGA"
    both("g23-A4-capitals-address-lines", "A4 siblings: an address held with a comma, in capitals over two lines",
         "Ship to:" + NL + malaga_up + NL, known=[malaga], leak=[malaga_up, "LAGA"])
    # (Python's str.upper() and JavaScript's toUpperCase() write an iota with dialytika and tonos as three code points,
    # which the vault's reading composes again: the capitals are compared composed)
    paisios = ("PERSON", "Pa" + chr(0x390) + "sios")
    paisios_up = "PA" + chr(0x399) + chr(0x308) + chr(0x301) + "SIOS"
    both("g23-A4-capitals-composed", "A4 siblings: capitals that are not composed as written (upper() of a Greek name)",
         "Signed: " + paisios_up + ".", known=[paisios], leak=[paisios_up, "SIOS"])
    greek = ("PERSON", "Pa" + chr(0x390) + "sios Lambrou")
    greek_up = "PA" + chr(0x399) + chr(0x308) + chr(0x301) + "SIOS" + TAB + "LAMBROU"
    both("g23-A4-capitals-composed-white-space", "A4 siblings: the same with a second word after a tab",
         "Signed: " + greek_up + ".", known=[greek], leak=[greek_up, "LAMBROU"])
    key = {spelled: "open", "n": 1}
    g.case("g23-A4-capitals-white-space-key", "A4 siblings: the same spelling as a JSON key", g.jtext(key), data=key,
           known=[garcia], where=["user", "system", "tool-text", "tool-data", "block-data"], leak=[spelled, GARCIA],
           fields=2)

    # ---- A4: full-width characters in escapes (escapes were decoded before full-width characters were read as ASCII)
    fw = "kim" + chr(0xFF05) + "40example.com"
    both("r23-A4-full-width-percent", "23rd outside review A4: an e-mail address with a full-width percent sign",
         "mail " + fw + " now", known=[email], leak=[fw, "40example.com"], keep=["mail "])
    for name, written, piece in (
        ("full-width-hex", "kim%" + wide("40") + "example.com", "example.com"),
        ("full-width-percent-and-hex", "kim" + wide("%40") + "example.com", "example.com"),
        ("full-width-percent-nested", "kim" + chr(0xFF05) + "2540example.com", "2540example.com"),
        ("full-width-percent-capitals", "KIM" + chr(0xFF05) + "40EXAMPLE.COM", "40EXAMPLE.COM"),
        ("full-width-backslash", "kim" + chr(0xFF3C) + "u0040example.com", "u0040example.com"),
        ("full-width-escape", "kim" + wide(BS + "u0040") + "example.com", "example.com"),
    ):
        both("g23-A4-" + name, "A4 siblings: an e-mail address with an escape in full width (%s)" % name,
             "mail " + written + " now", known=[email], leak=[written, piece])
    for name, written in (
        ("full-width-percent-space", "Kim" + chr(0xFF05) + "20Minsu"),
        ("full-width-percent-spaces", "Kim" + chr(0xFF05) + "20" + chr(0xFF05) + "09Minsu"),
        ("full-width-plus-form", "Kim" + chr(0xFF0B) + "Minsu"),
        ("full-width-repr", "Kim" + chr(0xFF3C) + "xa0Minsu"),
        ("full-width-percent-letter", "Kim" + chr(0xFF05) + "20" + chr(0xFF05) + "4Dinsu"),
    ):
        both("g23-A4-" + name, "A4 siblings: a name with white space or a letter escaped in full width (%s)" % name,
             "name=" + written + "&id=7", known=[kim], leak=[written, "insu"])
    nfd_pct = "Jose" + chr(0xFF05) + "CC" + chr(0xFF05) + "81 Garc" + chr(0xED) + "a"
    both("g23-A4-full-width-percent-nfd", "A4 siblings: a combining accent percent-encoded with full-width signs",
         "name=" + nfd_pct + "&id=7", known=[garcia], leak=[nfd_pct, "81 Garc"])
    both("g23-A4-full-width-kept", "A4 siblings: full-width signs in ordinary text stay as written",
         "Save " + wide("50%") + " today, " + wide("1+1") + " offer", known=[email], where=NOT_LOOSE,
         keep=["Save " + wide("50%") + " today, " + wide("1+1") + " offer"])

    # ---- A5: a run of white space between the parts of a value, next to an escape
    run = " " * 45
    a5 = "Kim" + run + "%4Dinsu"
    both("r23-A5-white-space-run", "23rd outside review A5: 45 spaces between a name and its escaped part",
         "Contact: " + a5 + ".", known=[kim], leak=[a5, "%4Dinsu"], keep=["Contact: "])
    for name, space in (("tabs", TAB * 45), ("no-break-spaces", NBSP * 45), ("ideographic-spaces", IDEO * 45),
                        ("line-breaks", NL * 45), ("crlf", (CR + NL) * 30), ("mixed", (" " + TAB + NBSP + NL) * 12),
                        ("long", " " * 1000), ("longer", NL * 5000)):
        written = "Kim" + space + "%4Dinsu"
        both("g23-A5-" + name, "A5 siblings: other white space between a name and its escaped part (%s)" % name,
             "Contact: " + written + ".", known=[kim], leak=[written, "%4Dinsu"])
    for name, written, piece in (
        ("escape-before", "%4Bim" + run + "Minsu", "Minsu"),
        ("escapes-both-sides", "%4Bim" + run + "%4Dinsu", "%4Dinsu"),
        ("escape-inside", "Kim" + run + "Mi%6Esu", "Mi%6Esu"),
        ("unicode-escape", "Kim" + run + BS + "u004Dinsu", "u004Dinsu"),
        ("capitals", "KIM" + run + "%4DINSU", "%4DINSU"),
        ("full-width", "Kim" + IDEO * 45 + chr(0xFF05) + "4Dinsu", "4Dinsu"),
        ("escaped-space-and-run", "Kim%20" + run + "%4Dinsu", "%4Dinsu"),
        ("between-escapes", "%4Bim" + NL * 200 + "%4Dinsu" + " " * 200 + "%4Bim", "%4Dinsu"),
    ):
        both("g23-A5-" + name, "A5 siblings: a run of white space and an escape (%s)" % name,
             "Contact: " + written + ".", known=[kim], leak=[written, piece])
    addr = ("ADDRESS", "12 Main St, Seoul")
    lines = "12 Main St," + NL + " " * 45 + "Seo%75l"
    both("g23-A5-address-lines", "A5 siblings: an address over two lines, indented by 45 spaces, an escape in it",
         "Ship to " + lines + " today", known=[addr], leak=[lines, "Seo%75l"])
    both("g23-A5-other-name-kept", "A5 siblings: another name after the run stays as written",
         "Contact: Kim" + run + "%4Dorothy.", known=[kim], where=NOT_LOOSE, keep=["Kim" + run + "%4Dorothy"])
