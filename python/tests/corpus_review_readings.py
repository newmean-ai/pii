"""Leak corpus cases of the 1.7.1 review, group "readings": every reading the vault makes (each decoding step, each
case, width and NFD fold, each reach window) applies to every intermediate result, and spans map back through the
whole chain (guarantee note rows U1, U3, S1, S5, S13, S16, S17, N1, V2, V5 and section 10; the README section
"How a known value is found again").

- L1-A1: the capitals of a language (Turkish and Azerbaijani "İ", Greek without tonos, German "ẞ") were not a spelling.
- L1-B1: a mark of a script written without spaces after a value counted as a word edge ("สม" in "สมัคร").
- L1-B2: a full-width exponent ("１ｅ＋４５２４") was not read as one when the join was judged.
- L4-REACH: a number written longer than a known value's reach around its escape ("4.524000...e%2B03") was cut.
- L5-A2: a hit that ends before a character joined to the one before it (a variation selector, a mark, a Hangul final)
  was mapped back short of the value in a text that has full width or NFD anywhere.
- 24th outside review A4: full width that a decoding step produced was not read as ASCII before the next step.
- L3-B1, L3-B3, L3-B4: what the model's span took from around a value (white space, a bracket, a sentence's ".", a
  bytes literal's "b'", the time of an ISO timestamp) was learned as part of it; the model was not given the text
  through Python repr escapes. (L3-B2, a short value a model found, is in the package tests: the corpus runs one text.)
- Found with the medium model on these rows: its span ", Tiffany & Co." made a value with an empty line, whose search
  with other white space never ended in npm (and matched everywhere in Python); such a value is found as written only.

Read by gen_vault_corpus.py (load_parts): add(g) calls g.case(...). Backslashes and every character outside ASCII are
built with chr() (REVIEW-GUIDE rule 9).
"""

import unicodedata

BS = chr(92)
NL = chr(10)
NBSP = chr(0xA0)
IDEO = chr(0x3000)


def t(*cps):
    """The characters with these code points."""
    return "".join(chr(c) for c in cps)


def wide(s):
    """s in full-width ASCII (U+FF01 to U+FF5E), a space as U+3000."""
    return "".join(IDEO if c == " " else chr(ord(c) + 0xFEE0) for c in s)


def add(g):
    ALL, NOT_LOOSE, DETECTED = g.ALL, g.NOT_LOOSE, g.DETECTED

    def both(cid, frm, text, **kw):
        """A case in text positions and, with the text under a key, in data positions."""
        kw.setdefault("data", {"note": text})
        return g.case(cid, frm, text, **kw)

    # ---- L1-A1: the capitals of a language
    I_DOT, S_UP, S_LOW, U_LOW, U_UP = chr(0x130), chr(0x15E), chr(0x15F), chr(0xFC), chr(0xDC)
    emine = ("PERSON", "Emine " + S_UP + "ahin")
    emine_tr = "EM" + I_DOT + "NE " + S_UP + "AH" + I_DOT + "N"
    musteri = "M" + U_LOW + S_LOW + "teri: "
    both("l171-L1A1-turkish", "1.7.1 review L1-A1: a Turkish name in Turkish capitals (İ for i)", musteri + emine_tr,
         known=[emine], leak=[emine_tr, "EM" + I_DOT + "NE"], keep=[musteri])
    lines = "EM" + I_DOT + "NE" + NL + S_UP + "AH" + I_DOT + "N"
    both("l171-L1A1-turkish-lines", "L1-A1 siblings: Turkish capitals over two lines", musteri[:-1] + NL + lines,
         known=[emine], leak=[lines, "EM" + I_DOT + "NE"])
    both("l171-L1A1-turkish-one-word", "L1-A1 siblings: a one-word Turkish name in capitals", musteri + "FAT" + I_DOT + "H",
         known=[("PERSON", "Fatih")], leak=["FAT" + I_DOT + "H"])
    elmira = ("PERSON", "Elmira " + chr(0x18F).lower() + "liyeva")
    elmira_up = "ELM" + I_DOT + "RA " + chr(0x18F) + "L" + I_DOT + "YEVA"
    both("l171-L1A1-azerbaijani", "L1-A1 siblings: an Azerbaijani name in Azerbaijani capitals", "M" + U_LOW + S_LOW + "t" + chr(0x259) + "ri: " + elmira_up,
         known=[elmira], leak=[elmira_up, "ELM" + I_DOT + "RA"])
    eleni = ("PERSON", t(0x395, 0x3BB, 0x3AD, 0x3BD, 0x3B7) + " " + t(0x3A0, 0x3B1, 0x3C0, 0x3B1, 0x3B4, 0x3BF, 0x3C0, 0x3BF, 0x3CD, 0x3BB, 0x3BF, 0x3C5))
    eleni_up = t(0x395, 0x39B, 0x395, 0x39D, 0x397) + " " + t(0x3A0, 0x391, 0x3A0, 0x391, 0x394, 0x39F, 0x3A0, 0x39F, 0x3A5, 0x39B, 0x39F, 0x3A5)
    both("l171-L1A1-greek", "L1-A1: a Greek name in Greek capitals (no tonos)", t(0x3A0, 0x3B5, 0x3BB, 0x3AC, 0x3C4, 0x3B7, 0x3C2) + ": " + eleni_up,
         known=[eleni], leak=[eleni_up, eleni_up[6:]])
    both("l171-L1A1-greek-one-word", "L1-A1 siblings: a one-word Greek name in Greek capitals", "x: " + t(0x39D, 0x399, 0x39A, 0x39F, 0x3A3),
         known=[("PERSON", t(0x39D, 0x3AF, 0x3BA, 0x3BF, 0x3C2))], leak=[t(0x39D, 0x399, 0x39A, 0x39F, 0x3A3)])
    paisios_el = t(0x3A0, 0x391, 0x3AA, 0x3A3, 0x399, 0x39F, 0x3A3)
    both("l171-L1A1-greek-dialytika", "L1-A1 siblings: Greek capitals keep the dialytika and drop the tonos (ΐ as Ϊ)", "x: " + paisios_el,
         known=[("PERSON", t(0x3A0, 0x3B1, 0x390, 0x3C3, 0x3B9, 0x3BF, 0x3C2))], leak=[paisios_el])
    mueller = ("ADDRESS", "M" + U_LOW + "llerstra" + chr(0xDF) + "e 5, 10115 Berlin")
    mueller_up = "M" + U_UP + "LLERSTRA" + chr(0x1E9E) + "E 5, 10115 BERLIN"
    both("l171-L1A1-german", "L1-A1: a German address in capitals with ẞ for ß", "An: " + mueller_up, known=[mueller],
         leak=[mueller_up, "LLERSTRA" + chr(0x1E9E) + "E"], keep=["An: "])
    mueller_lines = "M" + U_UP + "LLERSTRA" + chr(0x1E9E) + "E 5" + NL + "10115 BERLIN"
    both("l171-L1A1-german-lines", "L1-A1 siblings: the same over two lines (the comma as a line break)", "An:" + NL + mueller_lines,
         known=[mueller], leak=[mueller_lines, "LLERSTRA" + chr(0x1E9E) + "E"])
    both("l171-L1A1-german-one-word", "L1-A1 siblings: a one-word German name in capitals with ẞ", "Herr WEI" + chr(0x1E9E) + ", bitte",
         known=[("PERSON", "Wei" + chr(0xDF))], leak=["WEI" + chr(0x1E9E)])
    pct = "EM%C4%B0NE%20%C5%9EAH%C4%B0N"
    both("l171-L1A1-turkish-percent", "L1-A1 siblings: Turkish capitals percent-encoded in a URL",
         "https://crm.test/c?name=" + pct + "&id=7", known=[emine], leak=[pct, "C5%9EAH"])
    uesc = "EM" + BS + "u0130NE " + BS + "u015eAH" + BS + "u0130N"
    both("l171-L1A1-turkish-json-escapes", "L1-A1 siblings: Turkish capitals with JSON Unicode escapes", '{"name": "' + uesc + '"}',
         known=[emine], leak=[uesc, "u015eAH"])
    key = {emine_tr: "open", "n": 1}
    g.case("l171-L1A1-turkish-key", "L1-A1 siblings: Turkish capitals as a JSON key", g.jtext(key), data=key, known=[emine],
           where=["user", "system", "tool-text", "tool-data", "block-data"], leak=[emine_tr], fields=2)
    both("l171-L1A1-turkish-kept", "L1-A1 siblings: a longer word in Turkish capitals stays", "FAT" + I_DOT + "HA geldi",
         known=[("PERSON", "Fatih")], where=NOT_LOOSE, keep=["FAT" + I_DOT + "HA geldi"])

    # ---- L1-B1: a mark of a script written without spaces after the value belongs to it
    som = ("PERSON", t(0xE2A, 0xE21))
    please = t(0xE01, 0xE23, 0xE38, 0xE13, 0xE32)
    apply_ = t(0xE2A, 0xE21, 0xE31, 0xE04, 0xE23)
    both("l171-L1B1-thai", "1.7.1 review L1-B1: a Thai word that starts with a known name and goes on with a vowel sign",
         please + apply_ + t(0xE2A, 0xE21, 0xE32, 0xE0A, 0xE34, 0xE01), known=[som], where=NOT_LOOSE, keep=[please + apply_])
    notebook = t(0xE0B, 0xE37, 0xE49, 0xE2D, 0xE2A, 0xE21, 0xE38, 0xE14, 0xE40, 0xE25, 0xE48, 0xE21, 0xE43, 0xE2B, 0xE21, 0xE48)
    both("l171-L1B1-thai-below", "L1-B1 siblings: a Thai vowel sign below (สมุด)", notebook, known=[som], where=NOT_LOOSE, keep=[notebook])
    sokha = t(0x179B, 0x17C4, 0x1780, 0x179F, 0x17BB, 0x1781, 0x17B6, 0x1798, 0x1780, 0x178A, 0x179B, 0x17CB)
    both("l171-L1B1-khmer", "L1-B1 siblings: Khmer, another name that starts with a known one (សុខា)", sokha,
         known=[("PERSON", t(0x179F, 0x17BB, 0x1781))], where=NOT_LOOSE, keep=[sokha])
    maung = t(0x1019, 0x1031, 0x102C, 0x1004, 0x103A)
    both("l171-L1B1-myanmar", "L1-B1 siblings: Myanmar, a visarga after the value", "x " + maung + chr(0x1038) + " y",
         known=[("PERSON", maung)], where=NOT_LOOSE, keep=[maung + chr(0x1038)])
    lao = t(0xEAA, 0xEBB, 0xEA1)
    both("l171-L1B1-lao", "L1-B1 siblings: Lao, a vowel sign after the value", "x " + lao + t(0xEB1, 0xE94) + " y",
         known=[("PERSON", lao)], where=NOT_LOOSE, keep=[lao + t(0xEB1, 0xE94)])
    for name, value, mark in (
        ("balinese", t(0x1B13, 0x1B2E), chr(0x1B36)),
        ("javanese", t(0xA98F, 0xA9AD), chr(0xA9B6)),
        ("tai-tham", t(0x1A20, 0x1A3E), chr(0x1A6E)),
    ):
        both("l171-L1B1-" + name, "L1-B1 siblings: %s, a vowel sign after the value" % name, "x " + value + mark + " y",
             known=[("PERSON", value)], where=NOT_LOOSE, keep=[value + mark])
    both("l171-L1B1-thai-found", "L1-B1 siblings: the same name before a space or a letter is still found",
         t(0xE04, 0xE38, 0xE13) + t(0xE2A, 0xE21) + " " + t(0xE21, 0xE32), known=[som], leak=[t(0xE04, 0xE38, 0xE13) + t(0xE2A, 0xE21)])

    # ---- L1-B2: a full-width exponent joins as the ASCII one does (a known number after it stays, S7n)
    num = ("ID_NUMBER", "4524")
    for name, written in (
        ("all", wide("1e+4524")),
        ("plus", "1e" + wide("+") + "4524"),
        ("capital-e", "1" + wide("E") + "+4524"),
        ("minus", "1e" + wide("-") + "4524"),
        ("dot", "4524" + wide(".") + "7"),
        ("percent-plus", "1e%EF%BC%8B4524"),
    ):
        both("l171-L1B2-" + name, "1.7.1 review L1-B2 and siblings: a known number after a full-width exponent (%s)" % name,
             "x=" + written + " y", known=[num], where=NOT_LOOSE, keep=[written])
    doc = '{"v":"' + wide("1e+4524") + '"}'
    g.case("l171-L1B2-json", "L1-B2: the same in a JSON document", doc, data={"v": wide("1e+4524")}, known=[num], where=NOT_LOOSE,
           keep=[wide("1e+4524")])
    both("l171-L1B2-phone", "L1-B2: a known phone number after a full-width exponent in a form field", "ext=" + wide("2e+") + "4155550123",
         known=[("PHONE", "4155550123")], where=NOT_LOOSE, keep=[wide("2e+") + "4155550123"])
    both("l171-L1B2-number-found", "L1-B2 siblings: the number in full width on its own is still found", "x=" + wide("4524") + " y",
         known=[num], leak=[wide("4524")])

    # ---- L4-REACH: a number written longer than a known value's reach
    long_ = "4.52400000000000000000e"
    for name, written in (
        ("percent", long_ + "%2B03"),
        ("unicode-escape", long_ + BS + "u002B03"),
        ("signed", "-" + long_ + "%2B03"),
        ("zeros-after", "45.240000000000000000000000000e%2B2"),
    ):
        both("l171-L4-" + name, "1.7.1 review L4-REACH and siblings: a number spelled longer than the reach (%s)" % name,
             "x=" + written + " y", known=[num], leak=[written, "240000000000000000"])
    repr_ = "{'x': '" + long_ + BS + "x2b03'}"
    both("l171-L4-repr", "L4-REACH: the same with a Python repr escape", repr_, known=[num], leak=[long_ + BS + "x2b03", "52400000000000000000e"])
    both("l171-L4-other-values", "L4-REACH: the same with a longer value in the vault as well", "x=" + long_ + "%2B03 y",
         known=[num, ("ADDRESS", "12 Main Street, Springfield, Seoul 04524")], leak=[long_ + "%2B03", "52400000000000000000e"])

    # ---- L5-A2: a hit that ends before a joining character covers the value
    watanabe = t(0x6E21, 0x8FBA)
    tokyo = wide("(") + t(0x6771, 0x4EAC) + wide(")")
    both("l171-L5A2-variation-selector", "1.7.1 review L5-A2: a name with a variation selector, full width elsewhere",
         watanabe + chr(0xE0100) + t(0x3055, 0x3093) + tokyo, known=[("PERSON", watanabe)], leak=[watanabe, chr(0x8FBA)])
    kim = t(0xAE40, 0xBBFC, 0xC218)
    both("l171-L5A2-hangul-mark", "L5-A2 siblings: a Hangul name with a combining acute, full width elsewhere",
         kim + chr(0x301) + ", Kim Minsu" + wide("!"), known=[("PERSON", kim), ("PERSON", "Kim Minsu")],
         leak=[kim, chr(0xC218) + chr(0x301), "Minsu"])
    nfd = unicodedata.normalize("NFD", kim)
    both("l171-L5A2-nfd-mark", "L5-A2 siblings: the same name in conjoining jamo with a combining acute",
         nfd + chr(0x301) + wide("!"), known=[("PERSON", kim)], leak=[nfd, nfd[-2:]])
    both("l171-L5A2-phone-final", "L5-A2 siblings: a phone number before a Hangul final, full width elsewhere",
         "tel 415 555 0123" + chr(0x11AB) + " " + wide("(x)"), known=[("PHONE", "415 555 0123")], leak=["415 555", "3" + chr(0x11AB)])
    # (a user name that ends in a letter: the placeholder written without brackets in a message name ends in a digit)
    both("l171-L5A2-username-final", "L5-A2 siblings: a user name before a Hangul final, U+3000 elsewhere",
         "id kim_xy" + chr(0x11AB) + IDEO + "x", known=[("USERNAME", "kim_xy")], leak=["kim_x", "y" + chr(0x11AB)])

    # ---- 24th outside review A4: full width that a decoding step produced
    email = ("EMAIL", "kim@example.com")
    for name, written, piece in (
        ("percent-encoded", "kim%EF%BC%8540example.com", "40example.com"),
        ("percent-encoded-lower", "kim%ef%bc%8540example.com", "40example.com"),
        ("percent-twice", "kim%25EF%25BC%258540example.com", "258540example.com"),
        ("json-escape", "kim" + BS + "uff0540example.com", "uff0540example.com"),
        ("repr-bytes", "kim" + BS + "xef" + BS + "xbc" + BS + "x8540example.com", "x8540example.com"),
        ("backslash", "kim%EF%BC%BCu0040example.com", "u0040example.com"),
        ("digits", "kim%25%EF%BC%94%EF%BC%90example.com", "example.com"),
    ):
        both("r24-A4-" + name, "24th outside review A4 and siblings: full width that a decoding step produced (%s)" % name,
             "mail " + written + " now", known=[email], leak=[written, piece], keep=["mail "])
    form = "Kim%EF%BC%8BMinsu"
    both("r24-A4-form-plus", "A4 siblings: a percent-encoded full-width plus in a form field", "name=" + form + "&id=7",
         known=[("PERSON", "Kim Minsu")], leak=[form, "BMinsu"])
    both("r24-A4-kept", "A4 siblings: ordinary text with a percent-encoded full-width sign stays", "Save 50%EF%BC%85 off",
         known=[email], where=NOT_LOOSE, keep=["Save 50%EF%BC%85 off"])

    # ---- L3-B1: what the model's span took from around a value
    addr = t(0xC11C, 0xC6B8, 0xD2B9, 0xBCC4, 0xC2DC) + " " + t(0xAC15, 0xB0A8, 0xAD6C) + NL + t(0xD14C, 0xD5E4, 0xB780, 0xB85C) + " 123"
    esc = "".join(c if ord(c) < 128 else BS + "u%04x" % ord(c) for c in addr)
    both("l171-L3B1-bracket", "1.7.1 review L3-B1: the model's span ends with ' (' (medium), then the address in Unicode escapes",
         "Contact " + addr + " (also " + esc + ")", detect=[(addr + " (", "ADDRESS")], where=DETECTED,
         leak=[esc, BS + "ub85c 123", addr], keep=["Contact "])
    both("l171-L3B1-period", "L3-B1: the model's span ends with the sentence's period (medium: 'Kim Minsu.')",
         "I'm Kim Minsu. Please write to Kim Minsu at the office.", detect=[("Kim Minsu.", "PERSON")], where=DETECTED,
         leak=["Kim Minsu"], keep=["I'm ", ". Please write to ", " at the office."])
    both("l171-L3B1-comma", "L3-B1 siblings: a comma", "Kim Minsu, and later Kim Minsu.", detect=[("Kim Minsu,", "PERSON")],
         where=DETECTED, leak=["Kim Minsu"], keep=[", and later "])
    both("l171-L3B1-open-bracket", "L3-B1 siblings: an opening bracket at the start", "Call (Kim Minsu) or Kim Minsu",
         detect=[("(Kim Minsu", "PERSON")], where=DETECTED, leak=["Kim Minsu"], keep=["Call (", ") or "])
    tanaka = t(0x7530, 0x4E2D, 0x592A, 0x90CE)
    both("l171-L3B1-cjk-brackets", "L3-B1 siblings: a closing corner bracket and an ideographic full stop",
         t(0x9023, 0x7D61, 0x5148) + chr(0x300C) + tanaka + chr(0x300D) + chr(0x3002) + tanaka, detect=[(tanaka + chr(0x300D) + chr(0x3002), "PERSON")],
         where=DETECTED, leak=[tanaka], keep=[chr(0x300C), chr(0x300D) + chr(0x3002)])
    both("l171-L3B1-address-bracket", "L3-B1 siblings: an English address and ' ('", "Ship to 12 Main St, Seoul (home) or 12 Main St, Seoul.",
         detect=[("12 Main St, Seoul (", "ADDRESS")], where=DETECTED, leak=["Main St"], keep=["Ship to ", " (home) or "])
    esc_at = "kim" + BS + "u0040example.com"
    both("l171-L3B1-wrapped", "L3-B1 siblings: the span takes the brackets around an e-mail address, which is then written with an escape",
         "Mail <kim@example.com>, or " + esc_at, detect=[("<kim@example.com>", "EMAIL")], where=DETECTED,
         leak=["kim@example.com", esc_at, "u0040example"], keep=["Mail <", ">, or "])
    both("l171-L3B1-quoted", "L3-B1 siblings: the span takes the quotation marks around a name, which is then written in capitals",
         "Ask 'Kim Minsu' or KIM MINSU", detect=[("'Kim Minsu'", "PERSON")], where=DETECTED, leak=["Kim Minsu", "KIM MINSU"],
         keep=["Ask '", "' or "])
    both("l171-L3B1-leading-comma", "L3-B1 siblings: the medium model's span takes the comma before an organization",
         "Bought at Tiffany & Co. yesterday, Tiffany & Co. again", detect=[(", Tiffany & Co.", "ORG")], where=DETECTED,
         leak=["Tiffany"], keep=["Bought at ", " yesterday, ", " again"])
    both("l171-L3B1-value-with-comma", "L3-B1 siblings: a value held with a comma and white space at its start is found as written only",
         "Bought at Tiffany & Co. yesterday, Tiffany & Co. again", known=[("ORG", ", Tiffany & Co.")], where=NOT_LOOSE,
         leak=[", Tiffany"], keep=["Bought at Tiffany & Co. yesterday"])
    both("l171-L3B1-org-dot-kept", "L3-B1 siblings: an organization keeps its own final period",
         "Bought at Tiffany & Co. yesterday, Tiffany & Co. again", detect=[("Tiffany & Co.", "ORG")], where=DETECTED, leak=["Tiffany"])

    # ---- L3-B3: the model is given the text through Python repr escapes
    both("l171-L3B3-nbsp", "1.7.1 review L3-B3: a name with a no-break space in a Python repr (str() of a dict with a set)",
         "{'name': 'Kim" + BS + "xa0Minsu', 'tags': {'vip'}}", detect=[("Kim" + NBSP + "Minsu", "PERSON")], where=DETECTED,
         leak=["Kim" + BS + "xa0Minsu", "xa0Minsu"], keep=["'tags'"])
    jose = "Jos" + BS + "xc3" + BS + "xa9 Garc" + BS + "xc3" + BS + "xada"
    both("l171-L3B3-bytes", "L3-B3: a name in a bytes literal, read as UTF-8 (the model's span takes b')",
         "{'name': b'" + jose + "'}", detect=[("b'Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a", "PERSON")], where=DETECTED,
         leak=[jose, "xada"], keep=["{'name': b'"])
    both("l171-L3B3-bytes-known", "L3-B3 (c): a known name encoded to bytes at the top level (the model's span takes b')",
         "b'" + jose + "'", known=[("PERSON", "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a")],
         detect=[("b'Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a", "PERSON")], where=DETECTED, leak=[jose, "xada", "Garc"])
    both("l171-L3B3-bytes-key", "L3-B3: a known e-mail address as a bytes key (the model's span takes b')",
         "{b'maria.lopez@example.com': 'x'}", known=[("EMAIL", "maria.lopez@example.com")],
         detect=[("b'maria.lopez@example.com", "EMAIL")], where=DETECTED, leak=["maria.lopez"], keep=["{b'"])
    both("l171-L3B3-astral", "L3-B3 siblings: a name with a character outside the BMP as a repr escape",
         "{'name': '" + BS + "U00020bb7" + t(0x7530) + "'}", detect=[(chr(0x20BB7) + t(0x7530), "PERSON")], where=DETECTED,
         leak=[BS + "U00020bb7" + t(0x7530), "U00020bb7"])

    # ---- L3-B4: the model's span takes the hour of an ISO timestamp of a known date
    dob = ("DATE_OF_BIRTH", "1990-01-02")
    for name, stamp, found in (
        ("iso", "1990-01-02T00:00:00Z", "1990-01-02T00"),
        ("iso-ms", "1990-01-02T00:00:00.000Z", "1990-01-02T00"),
        ("iso-offset", "1990-01-02T00:00:00+00:00", "1990-01-02T00:00:00+00"),
    ):
        data = {"name": "x", "dob": stamp}
        g.case("l171-L3B4-" + name, "1.7.1 review L3-B4 and siblings: the model's span on an ISO timestamp of a known date (%s)" % name,
               g.jtext(data), data=data, known=[dob], detect=[(found, "DATE_OF_BIRTH")], where=DETECTED,
               leak=["1990-01-02"], keep=[stamp[10:]], fields=2)
    both("l171-L3B4-text", "L3-B4 siblings: the timestamp in text", "born 1990-01-02T00:00:00 in Seoul", known=[dob],
         detect=[("1990-01-02T00:00:00", "DATE_OF_BIRTH")], where=DETECTED, leak=["1990-01-02"], keep=["T00:00:00 in Seoul"])
    both("l171-L3B4-unknown", "L3-B4 siblings: the same date not known yet", "born 1990-01-02T00:00:00 in Seoul",
         detect=[("1990-01-02T00", "DATE_OF_BIRTH")], where=DETECTED, leak=["1990-01-02"], keep=["T00:00:00 in Seoul"])
