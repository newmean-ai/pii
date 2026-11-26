"""Leak corpus cases of the review group "options" (SDK 1.7.1): the seven-lens review's L2-A1 and L1-A2, and the
24th outside review's A1 (guarantee note §10: P13, Q17, G30, G40 for the call's fields; Q13 with G7 for message
names).

The rule for the fields outside the messages: everything the provider receives there is data, except a short list of
identifiers providers define, at the places where they define them (model ids, response, item, conversation and
container ids, signatures, encrypted data, tool names, schema property names, provider enums and the numbers a provider
defines as settings). A name such as "order" or "model" is a provider's only at its provider's path (`gateway.order`);
under a template variable, an unknown provider or a request body's extra fields it is the application's (A1). A number
there that is a known value becomes its placeholder (A1: `metadata.customer_number`); a provider's numeric setting
(`maxTokens`, `budgetTokens`, `seed`) stays a number.

Positions added here (the runners say which framework has which shape):

    option-variables  the text under option and setting names that are a provider's only at another path: the
                      variables of a stored prompt (`prompt.variables.{order, model, customer}`), an unknown
                      provider's fields named like identifiers (`acme.{order, only, model, conversation, container}`),
                      a request body's extra fields (`extra_body.order`) and settings named like a gateway's
                      (`order`, `only`)
    option-numbers    the text as a JSON number (cases whose text is a number) in the application's metadata, an
                      unknown provider's options and nested lists there, and a provider tool's arguments
    option-settings   the text as a JSON number under the numeric settings providers define (`maxTokens`,
                      `budgetTokens`, `seed`, `max_tokens`, `top_k`): keep cases only, the number stays a number

Every case of the system position also runs at option-variables (the text there is hidden as the system prompt is).

Cases at message-name (L1-A2): providers take only letters, digits, "_" and "-" in a message's name, so a value of two
or more words stands there joined ("Kim_Minsu", "kim-minsu", "KimMinsu"). In the name position the middlewares read "_"
and "-" between word characters and a small letter before a capital as white space, for values of two or more words
only. finish() takes message-name off the cases of other files whose keep strings hold such a value joined that way
(the written reason REVIEW-GUIDE rule 6 asks for: in a name, the join is the value): see NAME_JOINED.

L7-11 (a model's span with the punctuation next to the value, learned as it was) is the class of L2-B2, which the review
group "docs" fixed in all six packages (valueSpans) with its own corpus cases (corpus_review_docs.py).

Not expressible as corpus cases, and tested in ai-sdk.test.ts, langchain.test.ts and test_vault.py instead: L2-B1 (a
JSON answer in a code fence gets its values escaped for JSON), L3-B5 (typed fields of a response format), L7-1, L7-2,
A5 (the final tool binding of a response format on LangChain 1.0 to 1.5), B2 (pydantic aliases on restore). The
response-format position of corpus_fields.py runs those shapes through a real agent too.
"""

POSITIONS = ["option-variables", "option-numbers", "option-settings"]

# ids of other files' cases taken off message-name by finish() (filled there; read by nothing else)
NAME_JOINED = []


def _name_reading(s):
    """s with "_" and "-" between word characters, and a small letter before a capital, read as white space (the
    middlewares' reading of a message name)."""
    out = []
    for i, c in enumerate(s):
        if c in "_-":
            j = i
            while j > 0 and s[j - 1] in "_-":
                j -= 1
            k = i
            while k < len(s) and s[k] in "_-":
                k += 1
            if j > 0 and k < len(s) and (s[j - 1].isalnum()) and s[k].isalnum():
                out.append(" ")
                continue
        if i > 0 and s[i - 1].islower() and c.isupper():
            out.append(" ")
        out.append(c)
    return "".join(out)


def _holds_joined(s, known):
    """Whether the name reading of s holds a known value of two or more words (any ASCII case, any white space) at word
    edges, where s as written does not."""
    import re

    def norm(t):
        return re.sub(r"\s+", " ", t).lower()

    reading = norm(_name_reading(s))
    written = norm(s)
    for e in known:
        value = e["value"]
        if not any(c.isspace() for c in value):
            continue
        v = norm(value)
        pattern = r"(?<!\w)" + re.escape(v) + r"(?!\w)"
        if re.search(pattern, reading) and not re.search(pattern, written):
            return True
    return False


def add(g):
    KIM = ("PERSON", "Kim Minsu")
    LEE = ("PERSON", "Lee Jiwoo")
    EMAIL = ("EMAIL", "kim@example.com")
    PHONE = ("PHONE", "821023456789")
    ID = ("ID_NUMBER", "4524")
    JOSE = ("PERSON", "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a")
    KNOWN = [KIM, EMAIL, PHONE]

    def at(cid, frm, text, known, where, leak=(), keep=(), extra=()):
        for s in list(leak) + list(keep):
            if s not in text:
                raise ValueError(cid + ": not in the text: " + repr(s))
        g.case(cid, frm, text, known=known, where=where, leak=leak, keep=keep, extra=extra)

    # A1 and L2-A1: names a provider defines at one path are the application's anywhere else -----------------------------
    a1 = "review options, A1 (24th outside review) and L2-A1: "
    at("rv-opt-A1-variable-name", a1 + "a held name as a template variable called order, model, customer", "Kim Minsu", KNOWN,
       ["option-variables"], leak=["Kim Minsu"])
    at("rv-opt-A1-variable-email", a1 + "a held e-mail address under option names a gateway uses", "kim@example.com", KNOWN,
       ["option-variables"], leak=["kim@example.com"])
    at("rv-opt-A1-variable-text", a1 + "a held name inside a sentence in a template variable", "Order for Kim Minsu, gift wrap",
       KNOWN, ["option-variables"], leak=["Kim Minsu"], keep=["Order for", "gift wrap"])
    at("rv-opt-A1-variable-ordinary", a1 + "ordinary text in those fields stays", "priority shipping", KNOWN, ["option-variables"],
       keep=["priority shipping"])

    # A1: numbers in the application's data and in fields no provider defines; a provider's numeric settings stay --------
    num = "review options, A1 (24th outside review): "
    at("rv-opt-A1-number-phone", num + "a held phone number as a JSON number in metadata and unknown options", "821023456789",
       KNOWN, ["option-numbers"], leak=["821023456789"])
    at("rv-opt-A1-number-exponent", num + "the held number written with an exponent", "8.21023456789e11", KNOWN, ["option-numbers"],
       leak=["8.21023456789e11"], extra=["821023456789"])
    at("rv-opt-A1-number-short", num + "a held four-digit number", "4524", [ID], ["option-numbers"], leak=["4524"])
    at("rv-opt-A1-number-other", num + "a number that is no known value stays", "1024", [ID, PHONE], ["option-numbers"],
       keep=["1024"])
    at("rv-opt-A1-settings-known", num + "a provider's numeric setting that equals a held number stays a number", "4524", [ID],
       ["option-settings"], keep=["4524"])
    at("rv-opt-A1-settings-tokens", num + "a token limit equal to a held number stays", "4096", [("ID_NUMBER", "4096")],
       ["option-settings"], keep=["4096"])

    # Release gate 8, items 3 to 5 (the reviewer's inputs): the description of a schema made from classes or read
    # through a getter, and an e-mail address at identifier names away from the paths where a provider defines them.
    # The npm runner gives these positions those shapes (class-property, class-root, getter-examples, misplaced-ids), and
    # the Python runner gives option-variables its misplaced-ids and model-misplaced-ids (the Python sibling of item 5)
    g8 = "release gate 8, items 3 to 5: "
    at("rv-g8-schema-description", g8 + "the reviewer's schema description, in class-backed schemas and through a getter",
       "Contact Kim Minsu.", [KIM], ["tool-schema", "response-format", "option-variables"], leak=["Kim Minsu"], keep=["Contact"])
    at("rv-g8-misplaced-email", g8 + "the reviewer's e-mail address at identifier names away from their paths", "kim@example.com",
       [EMAIL], ["option-variables"], leak=["kim@example.com"])

    # L1-A2: a value of two or more words joined the way a provider accepts a name ------------------------------------------
    name = "review options, L1-A2 (a message name in the spelling providers accept): "
    for cid, text, known, leak in [
        ("underscore", "Kim_Minsu", [KIM], ["Kim_Minsu", "Minsu"]),
        ("hyphen", "Kim-Minsu", [KIM], ["Kim-Minsu", "Minsu"]),
        ("lower-hyphen", "kim-minsu", [KIM], ["kim-minsu", "minsu"]),
        ("capitals", "KIM_MINSU", [KIM], ["KIM_MINSU", "MINSU"]),
        ("camel", "KimMinsu", [KIM], ["KimMinsu", "Minsu"]),
        ("double-underscore", "Kim__Minsu", [KIM], ["Kim__Minsu", "Minsu"]),
        ("inside", "Dear_Kim_Minsu-VIP", [KIM], ["Kim_Minsu", "Minsu"]),
        ("two-values", "Lee_Jiwoo_and_Kim_Minsu", [KIM, LEE], ["Kim_Minsu", "Lee_Jiwoo", "Jiwoo", "Minsu"]),
        ("accented", "Jos" + chr(0xE9) + "_Garc" + chr(0xED) + "a", [JOSE], ["Jos" + chr(0xE9) + "_Garc" + chr(0xED) + "a", "Garc" + chr(0xED) + "a"]),
        ("email", "kim@example.com", [EMAIL], ["kim@example.com"]),
        ("phone-in-name", "call-821023456789", [PHONE], ["821023456789"]),
    ]:
        at("rv-opt-L1A2-name-" + cid, name + text, text, known, ["message-name"], leak=leak)
    for cid, text, known in [
        ("lookalike", "Kim_Minsuk", [KIM]),
        ("lookalike-camel", "KimMinsukova", [KIM]),
        ("one-word-camel", "xAnn", [("PERSON", "Ann")]),
        ("one-word-joined", "Minsu_VIP", [("PERSON", "Minsu")]),
        ("exponent", "1e-4524", [ID]),
        ("bot", "support-bot", [KIM]),
    ]:
        at("rv-opt-L1A2-keep-" + cid, name + "kept: " + text, text, known, ["message-name"], keep=[text])

    # gate 8 of the release standard 1.7.1, item 1: the system prompt and a message given in a model's model_kwargs, which
    # langchain-anthropic sends instead of what it builds from the messages (Python: the model-system and model-messages
    # shapes of option-variables; every position: the same text in the messages and the system prompt)
    g8 = "release gate 8 (outside review), item 1, text given in a model's model_kwargs as the system prompt or a message: "
    where = ["user", "system", "option-variables"]
    at("g8-model-system-name", g8 + "Contact Kim Minsu.", "Contact Kim Minsu.", [KIM], where, leak=["Kim Minsu"], keep=["Contact "])
    at("g8-model-system-two", g8 + "an e-mail address and a name", "Write to kim@example.com for Kim Minsu.", KNOWN, where,
       leak=["kim@example.com", "Kim Minsu"], keep=["Write to ", " for "])
    at("g8-model-system-ordinary", g8 + "ordinary text stays", "Contact support.", KNOWN, where, keep=["Contact support."])


def finish(g):
    """After every class file has added its cases: every case of the system position runs at option-variables too, and
    message-name is taken off the cases whose keep strings hold a known value of two or more words joined by "_", "-"
    or a change of case (the name reading masks it there: in a name, the join is the value)."""
    g.POSITIONS = g.POSITIONS + [p for p in POSITIONS if p not in g.POSITIONS]
    for c in g.CASES:
        if "system" in c["where"] and "option-variables" not in c["where"]:
            c["where"] = c["where"] + ["option-variables"]
        if "message-name" in c["where"] and not c["id"].startswith("rv-opt-L1A2-"):
            if any(_holds_joined(s, c["known"]) for s in c["keep"]):
                c["where"] = [p for p in c["where"] if p != "message-name"]
                NAME_JOINED.append(c["id"])
