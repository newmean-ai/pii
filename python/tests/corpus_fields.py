"""Leak corpus cases of the class "fields" (SDK 1.7.1): known values in the fields of a model call outside the messages
and the system prompt, and in the name of a person's message (guarantee note §10: Q13 with G7, P13 and Q17 with G30,
P12 with G40).

Positions added here (each text is put into the field; the runners say which framework has which):

    message-name      the name of a HumanMessage and of a ChatMessage (LangChain JS and Python; the AI SDK has none)
    tool-description  the description of a tool given to the model (a framework tool and a tool definition)
    tool-schema       a property of a tool's input schema: its description and an example
    response-format   the response format: its description and a property's description (LangChain through a real
                      agent, as JSON schema, provider strategy and tool strategy)
    call-options      the call's provider options (AI SDK `providerOptions.openai.user`) or model settings (LangChain
                      `modelSettings` / `model_settings` `user`)
    message-options   the provider options of a message and of a part (AI SDK only)

These fields get the known values hidden without the model, as the system prompt does, so every case of the system
position also runs at "tool-description" and "message-name" (every spelling the system prompt is checked in). The
other positions run the cases below. Not expressible as corpus cases, and tested in test_vault.py, ai-sdk.test.ts and
langchain.test.ts instead: G11 (restoring a structured response), G42 (options a vault cannot honour) and G43 (a call
whose masking cannot finish sends nothing).
"""

FIELDS = ["message-name", "tool-description", "tool-schema", "response-format", "call-options", "message-options"]
# where every case of the system position runs as well
EVERY = ["tool-description", "message-name"]


def add(g):
    BS = g.BS
    NL = g.NL
    # (a new list: the module's ALL, the default of g.case, is the old one, so cases of other files keep their default)
    g.POSITIONS = g.POSITIONS + [p for p in FIELDS if p not in g.POSITIONS]

    KIM = ("PERSON", "Kim Minsu")
    EMAIL = ("EMAIL", "kim@example.com")
    PHONE = ("PHONE", "821023456789")
    JOSE = ("PERSON", "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a")
    KOREAN = ("PERSON", chr(0xAE40) + chr(0xBBFC) + chr(0xC218))  # Kim Minsu in Hangul
    KNOWN = [KIM, EMAIL, PHONE]

    def field_case(cid, frm, text, known, leak=(), keep=(), extra=()):
        for s in list(leak) + list(keep):
            if s not in text:
                raise ValueError(cid + ": not in the text: " + repr(s))
        g.case(cid, frm, text, known=known, where=FIELDS, leak=leak, keep=keep, extra=extra)

    f = "class fields (G7, G30, G40): "
    field_case("fields-whole-name", f + "a known name as the whole field", "Kim Minsu", [KIM], leak=["Kim Minsu"])
    field_case("fields-name-in-text", f + "a known name inside the field's text", "Looks up the orders of Kim Minsu (VIP)", KNOWN,
               leak=["Kim Minsu"], keep=["Looks up the orders of", "(VIP)"])
    field_case("fields-email", f + "a known e-mail address", "Write to kim@example.com, not to support", KNOWN,
               leak=["kim@example.com"], keep=["Write to", "not to support"])
    field_case("fields-json-document", f + "a JSON document with a known number and name", '{"phone": 821023456789, "note": "call Kim Minsu"}',
               KNOWN, leak=["821023456789", "Kim Minsu"], keep=['"note"'])
    escaped = "Ask Jos" + BS + "u00e9 Garc" + BS + "u00eda today"
    field_case("fields-unicode-escape", f + "a known name written with Unicode escapes (S1)", escaped, [JOSE],
               leak=["Jos" + BS + "u00e9 Garc" + BS + "u00eda"], keep=["Ask", "today"], extra=[JOSE[1]])
    field_case("fields-percent", f + "a known name in a percent-escaped query", "user=Kim%20Minsu&plan=gold", [KIM],
               leak=["Kim%20Minsu"], keep=["plan=gold"], extra=["Kim Minsu"])
    field_case("fields-two-lines", f + "a known name over two lines", "Customer:" + NL + "Kim" + NL + "Minsu", [KIM],
               leak=["Kim" + NL + "Minsu"], keep=["Customer:"])
    field_case("fields-hangul", f + "a known Hangul name", KOREAN[1] + " " + chr(0xACE0) + chr(0xAC1D) + chr(0xB2D8), [KOREAN],
               leak=[KOREAN[1]], keep=[chr(0xACE0) + chr(0xAC1D) + chr(0xB2D8)])
    plain = "Returns the order status for a customer id."
    field_case("fields-ordinary", f + "ordinary text, nothing to hide", plain, KNOWN, keep=[plain])
    explained = "Names come as PERSON_1 or [PERSON_1] in the text."
    field_case("fields-placeholder-explained", f + "placeholder-like text the developer wrote stays", explained, KNOWN, keep=[explained])
    lookalike = "Kimberly Minsukova, Kim Minsuk and kim@example.community"
    field_case("fields-lookalike", f + "look-alikes at word edges stay", lookalike, KNOWN, keep=[lookalike])
    # (the confirmation of release gate 8, items 3 and 4: the reviewer's designated ordinary word as a schema's
    # description and example, with the name it starts with held; the JS runners also give every field through Proxies)
    jiwon = ("PERSON", chr(0xC9C0) + chr(0xC6D0))
    word = jiwon[1] + chr(0xAE08)
    field_case("fields-designated-word", f + "a designated ordinary word that starts with a held name stays (gate 8 confirmation)", word, [jiwon], keep=[word])


def finish(g):
    """After every class file has added its cases: every case of the system position runs at EVERY as well (the
    cases of the class files read after this one too)."""
    for c in g.CASES:
        if "system" in c["where"]:
            c["where"] = c["where"] + [p for p in EVERY if p not in c["where"]]
