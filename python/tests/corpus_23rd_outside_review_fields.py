"""Leak corpus cases of the 23rd outside review, items A1 and B4 (SDK 1.7.1): provider options and model settings, and
the JSON schemas of a call (guarantee note §10: P13, Q17, G30, G40).

A1: an application's metadata in provider options went out unchanged when its field was named "name" (`providerOptions
.openai.metadata.name`), because every field named name, id, ...Id or ...Key was left as an identifier wherever it
stood. Identifiers are now left only where a provider defines them; inside the application's metadata (`metadata`,
`labels`, `tags`) every string value and key counts. B4: a literal constraint of a schema (`const: {"description":
"Kim Minsu"}`) was rewritten as if it were a schema; literals stay as written and the walk goes on only through the
keywords that hold schemas.

Positions added here (each text is put into the field; the runners say which framework has which shape):

    option-metadata  the application's metadata in provider options and model settings, under fields named like
                     identifiers: AI SDK `providerOptions` of the call (`openai.metadata` {name, id, customerId},
                     `google.labels.name`), of a message and of a part (`openai.metadata.name`), and an unknown
                     provider's options ({name, customerId, user_key}); LangChain.js `modelSettings` (`metadata`,
                     `modelKwargs.metadata`, unknown settings); Python `model_settings` (`metadata`,
                     `extra_body.metadata`, unknown settings)
    option-ids       the identifiers, signatures and encrypted data that providers define in their options: they stay
                     as written (`previousResponseId`, `itemId`, `signature`, `thoughtSignature`,
                     `reasoningEncryptedContent`, `container.id`, `cachedContent`; `previous_response_id`,
                     `conversation.id` in model settings). Keep cases only: the scope row, so that a change shows
    schema-literal   a literal constraint of a schema property: `const`, `enum`, `default` and `pattern`, as a string
                     and as an object with schema-like keys (`{"description": text}`), in a tool's input schema and in
                     the response format. Keep cases only: the literal stays as written
    schema-nested    a description under every keyword that holds schemas (`items`, `prefixItems`, `anyOf`, `oneOf`,
                     `allOf`, `not`, `if`, `then`, `else`, `additionalProperties`, `patternProperties`, `$defs`,
                     `definitions`, `dependentSchemas`, `dependencies`, `contains`, `propertyNames`,
                     `unevaluatedProperties`, `unevaluatedItems`, `additionalItems`, `contentSchema`): still hidden
                     now that the walk goes only through these keywords

option-metadata gets the known values hidden as the system prompt does, so every case of the system position runs
there as well, and so do the cases of corpus_fields.py. B2 (a message name must not add its placeholder to the call's
restore set) and B3 (keys of a structured response are restored) are about restoring: ai-sdk.test.ts,
langchain.test.ts and test_vault.py test them.
"""

POSITIONS = ["option-metadata", "option-ids", "schema-literal", "schema-nested"]


def add(g):
    # (the positions are added to g.POSITIONS in finish: a class file read after this one may take g.POSITIONS as the
    # positions of the messages, as corpus_data.py does)
    KIM = ("PERSON", "Kim Minsu")
    EMAIL = ("EMAIL", "kim@example.com")
    PHONE = ("PHONE", "821023456789")
    JOSE = ("PERSON", "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a")
    KNOWN = [KIM, EMAIL, PHONE]

    def at(cid, frm, text, known, where, leak=(), keep=()):
        for s in list(leak) + list(keep):
            if s not in text:
                raise ValueError(cid + ": not in the text: " + repr(s))
        g.case(cid, frm, text, known=known, where=where, leak=leak, keep=keep)

    a1 = "23rd outside review A1 (application metadata in provider options): "
    at("r23-A1-metadata-name", a1 + "a held name as metadata.name, sent unchanged by AI SDK 5, 6 and 7", JOSE[1], [JOSE],
       ["option-metadata"], leak=[JOSE[1]])
    at("r23-A1-metadata-email", a1 + "a held e-mail address under metadata fields named like identifiers", "kim@example.com", KNOWN,
       ["option-metadata"], leak=["kim@example.com"])
    at("r23-A1-metadata-text", a1 + "a held name inside a note in metadata", "VIP customer Kim Minsu, call back", KNOWN,
       ["option-metadata"], leak=["Kim Minsu"], keep=["VIP customer", "call back"])

    ids = "23rd outside review A1 (provider-defined identifiers stay as written, the scope row): "
    at("r23-A1-ids-name", ids + "a held name in an identifier field", "Kim Minsu", KNOWN, ["option-ids"], keep=["Kim Minsu"])
    at("r23-A1-ids-email", ids + "a held e-mail address in an identifier field", "kim@example.com", KNOWN, ["option-ids"],
       keep=["kim@example.com"])

    b4 = "23rd outside review B4 (a literal constraint of a schema stays as written): "
    at("r23-B4-literal-name", b4 + "the held name as a const, enum, default and pattern", "Kim Minsu", KNOWN, ["schema-literal"],
       keep=["Kim Minsu"])
    at("r23-B4-literal-email", b4 + "a held e-mail address as a literal", "kim@example.com", KNOWN, ["schema-literal"],
       keep=["kim@example.com"])
    at("r23-B4-literal-text", b4 + "a literal sentence with a held name in it", "Ask Kim Minsu first", KNOWN, ["schema-literal"],
       keep=["Ask Kim Minsu first"])

    nested = "23rd outside review B4 (descriptions under every keyword that holds schemas stay hidden): "
    at("r23-B4-nested-name", nested + "a held name in each description", "Looks up the orders of Kim Minsu", KNOWN,
       ["schema-nested"], leak=["Kim Minsu"], keep=["Looks up the orders of"])
    at("r23-B4-nested-email", nested + "a held e-mail address in each description", "Write to kim@example.com", KNOWN,
       ["schema-nested"], leak=["kim@example.com"], keep=["Write to"])


def finish(g):
    """After every class file has added its cases: every case of the system position runs at option-metadata too (it
    is hidden as the system prompt is), and the cases of the fields outside the messages (corpus_fields.py) run at
    option-metadata and schema-nested."""
    g.POSITIONS = g.POSITIONS + [p for p in POSITIONS if p not in g.POSITIONS]
    for c in g.CASES:
        extra = []
        if "system" in c["where"] or "call-options" in c["where"]:
            extra.append("option-metadata")
        if "tool-schema" in c["where"]:
            extra.append("schema-nested")
        c["where"] = c["where"] + [p for p in extra if p not in c["where"]]
