"""Leak corpus cases of SDK 1.7.1 for readings, spellings, case, width, Unicode forms and scripts (guarantee note rows
G1/S9, G44/O5, G8/S13, G9/W4, G5/U1, G5/U2, G6/U3, V4, G18/V8; the README section "How a known value is found again").

Read by gen_vault_corpus.py (load_parts): add(g) calls g.case(...). Backslashes are built with chr(92) (REVIEW-GUIDE
rule 9).
"""

import unicodedata
from urllib.parse import quote

BS = chr(92)
NL = chr(10)


def add(g):
    ALL, NOT_LOOSE, DETECTED = g.ALL, g.NOT_LOOSE, g.DETECTED
    kim = ("PERSON", "Kim Minsu")
    email = ("EMAIL", "kim@example.com")

    def both(cid, frm, text, **kw):
        """A case in text positions and, with the text under a key, in data positions."""
        kw.setdefault("data", {"note": text})
        return g.case(cid, frm, text, **kw)

    # ---- G1 / S9: readings up to four steps deep, also next to unrelated escapes, "%" and "+"
    noise = "C:" + BS + BS + "tmp" + BS + "n q=a+b x%20y "
    for depth in (1, 2, 3, 4):
        spelled = "kim%" + "25" * (depth - 1) + "40example.com"
        both("g171-G1-percent-depth-%d-next-to-escapes" % depth,
             "guarantee note G1, S8, S9: an e-mail address percent-encoded %d deep next to unrelated escapes" % depth,
             noise + "mail " + spelled + " now", known=[email], leak=[spelled])
    five = "kim%" + "25" * 4 + "40example.com"
    both("g171-G1-percent-depth-5-kept", "guarantee note S10: a fifth step is not read (a bound on the work)",
         noise + "mail " + five + " now", known=[email], where=NOT_LOOSE, keep=[five])
    contoso = "CONTOSO%5C%5Cjdoe"
    both("g171-G1-13th-review-next-to-escapes", "13th outside review (CONTOSO%5C%5Cjdoe) next to unrelated escapes",
         noise + "user=" + contoso + " ok", known=[("USERNAME", "CONTOSO" + BS + "jdoe")], leak=[contoso])
    t4 = "kim%2540example.com"
    both("g171-G1-record-T4-next-to-escapes", "the record's T4 (kim%2540example.com) next to a line break, a path and a +",
         "line1" + BS + "nC:" + BS + BS + "tmp q=a+b " + t4, known=[email], leak=[t4])

    # ---- G44 / O5: a chain of overlapping known values longer than 16 rounds would need (in one text)
    words = ["W%02dx" % i for i in range(24)]
    chain = "Team: " + " ".join(words) + "."
    both("g171-G44-chain-of-24", "guarantee note G44, O5: a chain of 23 overlapping known values",
         chain, known=[("PERSON", a + " " + b) for a, b in zip(words, words[1:])], leak=words, keep=["Team: "])

    # ---- G8 / S13: Python repr escapes and bytes literals
    nbsp_repr = "{'name': 'Kim" + BS + "xa0Minsu'}"
    both("g171-G8-repr-xa0", "guarantee note G8: a dict written with str(), a no-break space as an x escape", nbsp_repr,
         known=[kim], leak=["Kim" + BS + "xa0Minsu", "Minsu"])
    garcia = "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a"
    b_lit = "b'Jos" + BS + "xc3" + BS + "xa9 Garc" + BS + "xc3" + BS + "xada'"
    both("g171-G8-bytes-literal", "guarantee note G8, T5: a bytes value a tool returns, written with str()", b_lit,
         known=[("PERSON", garcia)], leak=[b_lit[2:-1], "Garc"])
    set_repr = "{'Kim" + BS + "xa0Minsu', 'other'}"
    both("g171-G8-set-repr", "guarantee note T5: a set a tool returns, written with str()", set_repr,
         known=[kim], leak=["Kim" + BS + "xa0Minsu"])
    astral = chr(0x20BB7) + chr(0x7530)  # 𠮷田
    u8 = BS + "U00020bb7" + BS + "u7530"
    both("g171-G8-U-escape", "guarantee note S13: an astral character as a U escape in a repr", "{'family': '" + u8 + "'}",
         known=[("PERSON", astral)], leak=[u8])
    x_low = "{'id': 'A" + BS + "x2dB" + BS + "x2dC'}"
    both("g171-G8-x-ascii", "guarantee note S13: an x escape of an ASCII character", x_low,
         known=[("ID_NUMBER", "A-B-C")], leak=[x_low[8:-2]])

    # ---- G9 / W4: a value held with ", " found over two lines or without the comma
    addr = ("ADDRESS", "12 Main St, Seoul")
    both("g171-G9-over-two-lines", "guarantee note G9, W4: held with a comma, written over two lines",
         "Ship to 12 Main St" + NL + "Seoul today", known=[addr], leak=["12 Main St"])
    both("g171-G9-without-comma", "guarantee note W4: held with a comma, written without it",
         "Ship to 12 Main St Seoul today", known=[addr], leak=["12 Main St Seoul"])
    both("g171-G9-indented", "guarantee note W4: held with a comma, written over two indented lines",
         "Ship to:" + NL + "  12 Main St" + NL + "  Seoul" + NL, known=[addr], leak=["12 Main St"])

    # ---- G5 / U1: letter case
    both("g171-U1-capitals", "guarantee note U1: a name in capitals", "Ask KIM MINSU today", known=[kim], leak=["KIM MINSU"])
    both("g171-U1-two-words-lower", "guarantee note U1: a value of two words in lower case", "ask kim minsu today",
         known=[kim], leak=["kim minsu"])
    both("g171-U1-email-case", "guarantee note U1: an e-mail address in another case", "write to Kim@Example.COM now",
         known=[email], leak=["Kim@Example.COM"])
    both("g171-U1-username-case", "guarantee note U1: a user name in another case", "login JDoe_42 failed",
         known=[("USERNAME", "jdoe_42")], leak=["JDoe_42"])
    url = "https://intra.example.com/u/Kim7"
    both("g171-U1-url-host-case", "guarantee note U1: a URL's scheme and host in another case",
         "open HTTPS://Intra.Example.COM/u/Kim7 now", known=[("URL", url)], leak=["Intra.Example.COM/u/Kim7"])
    both("g171-U1-url-path-case-kept", "guarantee note U1: a URL path in another case is another value",
         "open https://intra.example.com/u/kim7 now", known=[("URL", url)], where=NOT_LOOSE,
         keep=["https://intra.example.com/u/kim7"])
    both("g171-U1-one-word-lower-kept", "guarantee note U1: a one-word value in lower case is another word",
         "you will see Will", known=[("PERSON", "Will")], where=NOT_LOOSE, keep=["you will see"], leak=["see Will"])
    both("g171-U1-one-word-capitals", "guarantee note U1: a one-word value in capitals", "WILL said hi",
         known=[("PERSON", "Will")], leak=["WILL"])
    email_key = {"Kim@Example.com": "primary", "n": 1}
    g.case("g171-U1-email-key-case", "guarantee note U1: an e-mail key in another case", g.jdump(email_key),
           data=email_key, known=[email], where=["user", "system", "tool-text", "tool-data", "block-data"],
           leak=["Kim@Example.com"], fields=2)

    # ---- G5 / U2: NFC and NFD
    jose = "Jos" + chr(0xE9) + " " + chr(0xC1) + "lvarez"
    jose_nfd = unicodedata.normalize("NFD", jose)
    both("g171-U2-nfd-of-nfc", "guarantee note U2: a value held in NFC, written in NFD", "from " + jose_nfd + " today",
         known=[("PERSON", jose)], leak=[jose_nfd, "lvarez"])
    both("g171-U2-nfc-of-nfd", "guarantee note U2: a value held in NFD, written in NFC", "from " + jose + " today",
         known=[("PERSON", jose_nfd)], leak=[jose, "lvarez"])
    ko = "김민수"
    ko_nfd = unicodedata.normalize("NFD", ko)
    both("g171-U2-hangul-jamo", "guarantee note U2: Hangul written as conjoining jamo (a macOS file name)",
         "file " + ko_nfd + "_report.pdf", known=[("PERSON", ko)], leak=[ko_nfd])
    both("g171-U2-hangul-jamo-particle", "guarantee note U2: Hangul as jamo, a particle after it",
         ko_nfd + unicodedata.normalize("NFD", "님께") + " send", known=[("PERSON", ko)], leak=[ko_nfd])

    # ---- G6 / U3: full-width ASCII and U+3000
    wide = "".join(chr(ord(c) + 0xFEE0) for c in "kim@example.com")
    both("g171-U3-full-width-email", "guarantee note U3: a full-width e-mail address", "mail " + wide + " now",
         known=[email], leak=[wide])
    wide_phone = "".join(chr(ord(c) + 0xFEE0) for c in "010-1234-5678")
    both("g171-U3-full-width-phone", "guarantee note U3: a full-width phone number", "電話" + wide_phone + "まで",
         known=[("PHONE", "010-1234-5678")], leak=[wide_phone])
    both("g171-U3-ideographic-space", "guarantee note U3: U+3000 in a value", "担当 Kim" + chr(0x3000) + "Minsu です",
         known=[kim], leak=["Kim" + chr(0x3000) + "Minsu"])
    mixed = "ｋｉｍ@example.com"
    both("g171-U3-partly-full-width", "guarantee note U3: part of a value in full width", "to " + mixed,
         known=[email], leak=[mixed])

    # ---- V4: two Hangul syllables
    both("g171-V4-two-syllables", "guarantee note V4: a two-syllable Hangul value at word edges", "담당 이준 님",
         known=[("PERSON", "이준")], leak=["이준"])
    both("g171-V4-two-syllables-particle", "guarantee note V4: a particle after a two-syllable Hangul value",
         "이준님께 전달", known=[("PERSON", "이준")], leak=["이준"])
    both("g171-V4-hangul-before-kept", "guarantee note V5: Hangul right before a Hangul value is no edge",
         "김이준 씨", known=[("PERSON", "이준")], where=NOT_LOOSE, keep=["김이준"])

    # ---- G18 / V8: no-space scripts
    ext_j = chr(0x323B0) + chr(0x323B1)
    both("g171-V8-han-extension-j", "guarantee note V8: two Han Extension J characters", "名" + ext_j + "様",
         known=[("PERSON", ext_j)], leak=[ext_j])
    kana = chr(0x1B001) + chr(0x1B002)
    both("g171-V8-kana-supplement", "guarantee note V8: kana from U+1B000, two characters glued to other kana",
         "は" + kana + "です", known=[("PERSON", kana)], leak=[kana])
    tai_tham = chr(0x1A20) + chr(0x1A21)
    both("g171-V8-tai-tham", "guarantee note V8: Tai Tham, two letters glued to others", chr(0x1A30) + tai_tham + chr(0x1A31),
         known=[("PERSON", tai_tham)], leak=[tai_tham])
    javanese = chr(0xA98F) + chr(0xA990)
    both("g171-V8-javanese", "guarantee note V8: Javanese, two letters glued to others", chr(0xA991) + javanese + chr(0xA992),
         known=[("PERSON", javanese)], leak=[javanese])
    balinese = chr(0x1B13) + chr(0x1B14)
    both("g171-V8-balinese", "guarantee note V8: Balinese, two letters glued to others", chr(0x1B15) + balinese + chr(0x1B16),
         known=[("PERSON", balinese)], leak=[balinese])

    # ---- siblings
    # (G1: escapes of other kinds far from the value, and one value written with two kinds of escape)
    far = noise + ("plain words without escapes. " * 120) + "mail " + t4 + " now"
    both("g171-G1-far-from-other-escapes", "guarantee note G1: a value two steps deep, far from the other escapes of a text",
         far, known=[email], leak=[t4])
    jg = "Jos" + BS + "u00e9%20Garc%C3%ADa"
    both("g171-G1-two-kinds-in-one-value", "guarantee note G1, S8: one value written with a Unicode escape and percent escapes",
         noise + "name " + jg + " ok", known=[("PERSON", garcia)], leak=[jg, "Garc%C3%ADa"])
    long_addr = "12 Main St Apt 8 Gangnam-gu Seoul 04524"
    both("g171-G1-long-value-escape-at-its-start", "guarantee note G1: a long value with one escape at its start",
         noise + "to 12%20Main St Apt 8 Gangnam-gu Seoul 04524 ok", known=[("ADDRESS", long_addr)],
         leak=["12%20Main St Apt 8", "Gangnam-gu Seoul"])
    # (G8: bytes literals of Hangul and of a character outside the basic plane)
    ko_b = "b'" + "".join(BS + "x%02x" % b for b in "김민수".encode("utf-8")) + "'"
    # (text positions only: every piece of these escapes is shorter than a plain piece a data case needs)
    g.case("g171-G8-bytes-hangul", "guarantee note G8: a Hangul name as a bytes literal", "{'name': " + ko_b + "}",
           known=[("PERSON", "김민수")], where=g.TEXTS, leak=[ko_b[2:-1]])
    astral_b = "b'" + "".join(BS + "x%02x" % b for b in (chr(0x20BB7) + chr(0x7530)).encode("utf-8")) + "'"
    g.case("g171-G8-bytes-astral", "guarantee note G8: a bytes literal of a character outside the basic plane", astral_b,
           known=[("PERSON", astral)], where=g.TEXTS, leak=[astral_b[2:-1]])
    # (G9: a value held with two commas, written over three lines; W3: a comma without a space is another value)
    addr3 = ("ADDRESS", "12 Main St, Apt 8, Seoul")
    both("g171-G9-three-lines", "guarantee note W4: held with two commas, written over three lines",
         "Ship to 12 Main St" + NL + "Apt 8" + NL + "Seoul today", known=[addr3], leak=["12 Main St", "Apt 8"])
    both("g171-W3-comma-without-space-kept", "guarantee note W3 (out): a comma with no space after it is another value",
         "Ship to 12 Main St,Seoul today", known=[addr], where=NOT_LOOSE, keep=["12 Main St,Seoul"])
    # (U1: capitals beyond ASCII; a one-word value in mixed case stays)
    both("g171-U1-unicode-capitals", "guarantee note U1: a value in capitals with letters beyond ASCII",
         "from " + jose.upper() + " today", known=[("PERSON", jose)], leak=[jose.upper(), "LVAREZ"])
    both("g171-U1-one-word-mixed-case-kept", "guarantee note U1 (out): a one-word value in mixed case is another word",
         "WiLL said hi", known=[("PERSON", "Will")], where=NOT_LOOSE, keep=["WiLL said"])
    # (U3: a number and a URL in full width)
    wide_num = "".join(chr(ord(c) + 0xFEE0) for c in "821023456789")
    both("g171-U3-full-width-number", "guarantee note U3: a known number in full width", "番号" + wide_num + "です",
         known=[("ID_NUMBER", "821023456789")], leak=[wide_num])
    wide_url = "".join(chr(ord(c) + 0xFEE0) for c in "https://intra.example.com/u/Kim7")
    both("g171-U3-full-width-url", "guarantee note U3: a URL in full width", "リンク " + wide_url + " を開く",
         known=[("URL", url)], leak=[wide_url])
    # (V4: Hangul letters that are not syllables keep the 3-character rule)
    both("g171-V4-jamo-pair-kept", "guarantee note V4: two Hangul letters that are not syllables are not found",
         "ㅋㅋ 웃겨", known=[("PERSON", "ㅋㅋ")], where=NOT_LOOSE, keep=["ㅋㅋ 웃겨"])

    # (a percent-encoded value in a form field next to a repr escape: two kinds of step in one text)
    mixed_text = "q=" + quote("Kim Minsu").replace("%20", "+") + " and {'n': 'x" + BS + "xa0y'}"
    both("g171-G1-form-field-next-to-repr", "guarantee note G1, G8: a form field next to a repr escape", mixed_text,
         known=[kim], leak=["Kim+Minsu"])
