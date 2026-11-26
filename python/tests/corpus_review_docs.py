"""Leak corpus cases of the review round of SDK 1.7.1, group "docs": the edges of a span the model found (lens item
L2-B2).

The medium model can end a span with what follows the value: "Kim Minsu." at the end of a sentence, or a Hangul
address with the " (" after it. The vault learned that span as it was, so the same value written without that
character (the next sentence, the copy of the address written with Unicode escapes) was not the known value and
reached the provider (LEAK-LEARNED in the real-framework scripts with the medium model). Before the vault learns a
span, white space, punctuation, brackets and quotes at its edges that are not part of the value are now left out of
it, when what is left is long enough to be found again; so is a pair of brackets or quotation marks around all the rest.
A SECRET is learned as marked.

The stand-in detector reports each detect value where it stands in the text, so these cases give it the span as the
model marked it, edge and all.
"""


def add(g):
    NL = g.NL
    KIM = "Kim Minsu"
    addr = "서울특별시 강남구" + NL + "테헤란로 123"
    pieces = ["강남구", "테헤란로"]
    f = "review round 1.7.1, L2-B2 (a model span with what follows the value): "

    def at(cid, frm, text, detect, leak, keep, where=None, data=True, extra=()):
        for s in list(leak) + list(keep):
            if s not in text:
                raise ValueError(cid + ": not in the text: " + repr(s))
        kw = {"data": {"note": text}} if data else {}
        g.case(cid, frm, text, detect=detect, where=where or g.DETECTED, leak=leak, keep=keep, extra=extra, **kw)

    # the reviewer's input: a Hangul address marked with the " (" after it, then the address written with Unicode escapes
    spelled = g.u_escaped(addr)
    text = "Contact " + addr + " (also " + spelled + ")"
    at("docs-L2B2-address-open-bracket-then-unicode-escape", f + "the address and \" (\", then its Unicode-escaped copy",
       text, [(addr + " (", "ADDRESS")], g.leaks_for(addr, pieces, spelled, text) + [addr], ["Contact ", "(also "])
    # a name with the sentence's period, then the name without it
    text = "Please look up " + KIM + ". Then call " + KIM + " tomorrow."
    at("docs-L2B2-name-period", f + "a name with the period after it, then the name again", text, [(KIM + ".", "PERSON")],
       [KIM, "Minsu"], ["Please look up", "Then call", "tomorrow."])
    # siblings: other punctuation, brackets and quotes after or before the value, and white space
    for name, marked in [
        ("comma", KIM + ","),
        ("semicolon", KIM + ";"),
        ("exclamation", KIM + "!"),
        ("ellipsis", KIM + "..."),
        ("ideographic-full-stop", KIM + chr(0x3002)),
        ("full-width-comma", KIM + chr(0xFF0C)),
        ("space-and-open-bracket", KIM + " ("),
        ("closing-bracket-alone", KIM + ")"),
        ("closing-quote-alone", KIM + chr(0x201D)),
        ("quote-alone", KIM + chr(34)),
        ("trailing-space", KIM + " "),
        ("open-bracket-before", "(" + KIM),
        ("quote-before", chr(0x201C) + KIM),
        ("space-before", " " + KIM),
        # a pair around the value: brackets that match each other, the only two of one straight quotation mark
        ("brackets-around", "(" + KIM + ")"),
        ("nested-brackets-around", "([" + KIM + "])"),
        ("quotation-marks-around", chr(34) + KIM + chr(34)),
        ("single-quotes-around", chr(39) + KIM + chr(39)),
        ("curly-quotes-around-and-period", chr(0x201C) + KIM + chr(0x201D) + "."),
        ("corner-brackets-around", chr(0x300C) + KIM + chr(0x300D)),
        ("full-width-brackets-around", chr(0xFF08) + KIM + chr(0xFF09)),
    ]:
        text = "Note: " + marked + " wrote. Later " + KIM + " called again"
        at("docs-L2B2-edge-" + name, f + "the name marked with " + name + ", then the name alone", text, [(marked, "PERSON")],
           [KIM, "Minsu"], ["Note:", "wrote.", "called again"])
    # an e-mail address and a URL that the model marked with the sentence's period
    mail = "kim@example.com"
    text = "Write to " + mail + ". Or reply to " + mail + " today"
    at("docs-L2B2-email-period", f + "an e-mail address with the period after it", text, [(mail + ".", "EMAIL")], [mail],
       ["Write to", "Or reply to", "today"])
    url = "https://example.com/kim"
    text = "Visit " + url + ". The page " + url + " is new"
    at("docs-L2B2-url-period", f + "a URL with the period after it", text, [(url + ".", "URL")], [url], ["Visit", "The page", "is new"])
    # a Hangul name with the ideographic comma after it
    hong = "홍길동"
    text = "담당자 " + hong + "、 다음 주에 " + hong + "님이 옵니다"
    at("docs-L2B2-hangul-name-ideographic-comma", f + "a Hangul name with U+3001 after it", text, [(hong + chr(0x3001), "PERSON")],
       [hong], ["담당자", "다음 주에"])
    # a SECRET is learned as marked: a password can end with "!" or hold brackets, and nothing of it may be left out
    secret = "hunter2!"
    text = "The password is " + secret + " and again " + secret + " today"
    at("docs-L2B2-secret-kept-as-marked", f + "a SECRET with a final \"!\" is learned whole", text, [(secret, "SECRET")],
       [secret, "hunter2"], ["The password is", "and again", "today"])
