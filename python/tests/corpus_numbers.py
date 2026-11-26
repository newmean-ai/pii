"""Leak corpus cases of the class "numbers and dates" (SDK 1.7.1): the rows A1 and S7n, S16 (B2), N4 (G19) and S17
(G36) of the guarantee note (section 10; the README section "How a known value is found again"), each with its
siblings, and the class of the 7th outside review's A4 (an earlier answer that holds a known value as a JSON number
stays valid JSON). Read by gen_vault_corpus.py (load_parts); backslashes are built with g.BS."""


def add(g):
    case, BS, ADDR = g.case, g.BS, g.ADDR
    NOT_LOOSE, TEXTS = g.NOT_LOOSE, g.TEXTS
    # text a model did not write, in the text positions only (a keep string with a backslash cannot be checked in data)
    texts_not_loose = ["user", "system", "tool-text"]

    # A1: the exponent join applies only when the known value is itself a number. A value that is not one, right after
    # "<digit>e+" or "<digit>e-", is masked: written as it is, in a form field, after an escaped sign
    for name, text, known, leak in [
        ("written-phone", "ext 2e+415 555 0123 (desk)", ("PHONE", "415 555 0123"), ["415 555 0123", "555 0123"]),
        ("written-minus-dashed-phone", "ref 2e-010-1234-5678 ok", ("PHONE", "010-1234-5678"), ["010-1234-5678", "1234-5678"]),
        ("written-plus-phone", "tel 1e+82 10 2345 6789 now", ("PHONE", "+82 10 2345 6789"), ["+82 10 2345 6789", "2345 6789"]),
        ("form-field-postal-code", "zip=4E+04524&x=1", ("ADDRESS", "04524"), ["04524"]),
        ("form-field-two-line-address", "q=Unit+4E+12+Main+St%0ASeoul+04524", ("ADDRESS", ADDR), ["12+Main+St%0ASeoul+04524", "Main+St"]),
        ("form-field-escaped-sign-address", "q=Unit+4E%2B12+Main+St%2C+Seoul", ("ADDRESS", "12 Main St, Seoul"),
         ["12+Main+St%2C+Seoul", "Main+St"]),
        ("form-field-user-name", "u=1e+0142kim&x=1", ("USERNAME", "0142kim"), ["0142kim"]),
    ]:
        case("ip8-A1-" + name, "independent pass 8, A1 (siblings: a value that is not a number after an exponent, " + name + ")",
             text, data={"q": text}, known=[known], leak=leak)
    case("ip8-A1-unicode-sign-address", "independent pass 8, A1 (sibling: an address after 4E and an escaped sign)",
         "addr=Unit 4E" + BS + "u002B12 Main St, Seoul", known=[("ADDRESS", "12 Main St, Seoul")], where=texts_not_loose,
         leak=["12 Main St, Seoul", "Main St"])
    # S7n (decided): a known number right after "<digit>e+" stays, also where the "+" may be a form field's space
    keep_numbers = "q=Unit+4E+4524&ext=2e+4155550123&w=1e+4524"
    case("ip8-A1-keep-known-number-after-exponent", "independent pass 8, A1 (S7n: a known number after <digit>e+ stays)",
         keep_numbers, data={"q": keep_numbers}, known=[("ID_NUMBER", "4524"), ("PHONE", "4155550123")], where=NOT_LOOSE,
         keep=["Unit+4E+4524", "2e+4155550123", "1e+4524"])
    case("ip8-A1-keep-signed-number-after-exponent", "independent pass 8, A1 (S7n: a known signed number after <digit>e stays)",
         "w=1e-4524&v=1e+8210", data={"q": "w=1e-4524&v=1e+8210"}, known=[("ID_NUMBER", "-4524"), ("ID_NUMBER", "+8210")],
         where=NOT_LOOSE, keep=["1e-4524", "1e+8210"])

    # S16 (B2): an exponent sign written escaped joins like "+", in every reading; so does an escaped dot between digits
    for name, text in [
        ("percent-exponent-lower-hex", "x=1e%2b4524&y=1"),
        ("percent-exponent-minus", "x=1E%2D4524&y=1"),
        ("percent-twice-exponent", "x=1e%252B4524&y=1"),
        ("percent-dot-before", "v=1%2E4524&w=1"),
        ("percent-signed-value", "x=1e%2B8210&y=1"),
    ]:
        case("ip8-B2-" + name, "independent pass 8, B2 (S16 siblings: an escaped joiner, " + name + ")", text, data={"q": text},
             known=[("ID_NUMBER", "4524"), ("ID_NUMBER", "+8210")], where=NOT_LOOSE, keep=[text.split("=")[1].split("&")[0]])
    for name, spelled, text in [
        ("unicode-exponent", "1e" + BS + "u002B4524", "x=1e" + BS + "u002B4524&y=1"),
        ("unicode-exponent-lower-hex", "1e" + BS + "u002b4524", "x=1e" + BS + "u002b4524&y=1"),
        ("unicode-exponent-twice", "1e" + BS + BS + "u002B4524", "x=1e" + BS + BS + "u002B4524&y=1"),
        ("unicode-dot", "4524" + BS + "u002E7", "v=4524" + BS + "u002E7&w=1"),
        ("unicode-exponent-in-document", "1e" + BS + "u002B4524", '{"x": "1e' + BS + 'u002B4524", "y": 1}'),
    ]:
        case("ip8-B2-" + name, "independent pass 8, B2 (S16 siblings: an escaped joiner, " + name + ")", text,
             known=[("ID_NUMBER", "4524")], where=texts_not_loose, keep=[spelled])
    case("ip8-B2-percent-plus-without-exponent", "independent pass 8, B2 (an escaped plus that is not an exponent's sign does not join)",
         "sum=2%2B4524&x=1", data={"q": "sum=2%2B4524&x=1"}, known=[("ID_NUMBER", "4524")], leak=["2%2B4524"], keep=["sum="])
    case("ip8-B2-percent-dot-without-digit", "independent pass 8, B2 (an escaped dot at the end of a sentence does not join)",
         "id=4524%2E&x=1", data={"q": "id=4524%2E&x=1"}, known=[("ID_NUMBER", "4524")], leak=["4524%2E"], keep=["id="])

    # N4 (G19): one rule for a sign. A number whose plain form without its sign is a known value is masked, the sign kept
    # outside, in every spelling and wherever the number stands (text, a JSON number token, JSON data, tool input)
    signed = "delta -4524, -4.524e3, -4524.0, -45.24e2 and -4.524E+3; up +4524 and +4.524e3"
    case("gen-sign-spellings", "generated (N4: a signed number in every spelling, for a known 4524)", signed, data={"note": signed},
         known=[("ID_NUMBER", "4524")], leak=["4524", "4.524e3", "45.24e2", "4.524E+3"], keep=["delta -", "up +"])
    case("gen-sign-json-tokens", "generated (N4: signed JSON number tokens, for a known 4524)", '{"d": -4.524e3, "e": -4524, "id": 7}',
         known=[("ID_NUMBER", "4524")], where=TEXTS, leak=["4.524e3", "4524"], fields=3)
    signed_data = {"d": -4524, "f": 4524.5, "id": 7}
    case("gen-sign-json-data", "generated (N4: a signed JSON number in data, for a known 4524; 4524.5 is another number)",
         g.jdump(signed_data), data=signed_data, known=[("ID_NUMBER", "4524")],
         where=["user", "system", "tool-text", "tool-data", "block-data", "tool-input-unrecorded"], leak=["-4524"], keep=["4524.5"], fields=3)
    case("gen-sign-phone-data", "generated (N4: a known phone number written as a negative JSON number)",
         '{"tel": -821023456789, "alt": -8.21023456789e11}', data={"tel": -821023456789, "alt": -8.21023456789e11},
         known=[("PHONE", "821023456789")], leak=["821023456789", "21023456789"], fields=2)
    case("gen-sign-keep-unsigned-for-signed-value", "generated (N4: a known -4524 is not found in 4524)", "Order 4524 shipped",
         data={"note": "Order 4524 shipped"}, known=[("ID_NUMBER", "-4524")], where=NOT_LOOSE, keep=["Order 4524 shipped"])
    case("gen-sign-keep-joined-minus", "generated (N4: a minus that joins a number to a word is not a sign)",
         "Ref A-4.524e3.1 and v-45.24e2x", data={"note": "Ref A-4.524e3.1 and v-45.24e2x"}, known=[("ID_NUMBER", "4524")],
         where=NOT_LOOSE, keep=["A-4.524e3.1", "v-45.24e2x"])

    # r07-A4 class: an earlier answer without a record that holds a known value as a JSON number stays valid JSON (the
    # number becomes a quoted placeholder), also where the loose search finds the value inside a longer number
    for name, text, known, leak, fields in [
        ("nested", '{"a": {"phone": 2025550148}, "b": [4524, 7]}', [("PHONE", "2025550148"), ("ID_NUMBER", "4524")], ["2025550148", "4524"], 2),
        ("inside-a-longer-number", '{"n": 145247, "id": 7}', [("ID_NUMBER", "4524")], ["4524"], 2),
        ("spelled-number", '{"tel": 8.21023456789e11, "id": 7}', [("PHONE", "821023456789")], ["8.21023456789e11", "21023456789"], 2),
        ("signed-number", '{"d": -4524, "e": -4.524e3}', [("ID_NUMBER", "4524")], ["4524", "4.524e3"], 2),
        ("array", '[4524, {"n": [2025550148]}]', [("PHONE", "2025550148"), ("ID_NUMBER", "4524")], ["2025550148", "4524"], None),
    ]:
        case("r07-A4-json-number-" + name, "7th outside review, A4 (siblings: a JSON number in an answer without a record, " + name + ")",
             text, known=known, where=["assistant-unrecorded"], leak=leak, fields=fields)
    # the same where the value is not known before the call: the model finds it in a string, and the vault, which learns
    # it there, finds it again as a JSON number in another spelling
    case("r07-A4-json-number-detected-in-a-string", "7th outside review, A4 (sibling: a number the model finds in a string, "
         "and the same number in another spelling as a JSON number)", '{"note": "call 2025550148", "phone": 2.025550148e9}',
         detect=[("2025550148", "PHONE")], where=["assistant-unrecorded", "tool-input-unrecorded"], leak=["2025550148", "2.025550148e9"],
         fields=2)

    # S17 (G36): a known date written as an ISO timestamp (a JS Date, Python isoformat) and as a Python repr or call
    dob = ("DATE_OF_BIRTH", "1990-01-02")
    for name, text, leak in [
        ("iso-timestamps", "a 1990-01-02T00:00:00.000Z b 1990-01-02T09:30:00+09:00 c 1990-01-02T00:00 d 1990-01-02t00:00:00z", ["1990-01-02"]),
        ("python-isoformat", "created 1990-01-02T00:00:00 by import", ["1990-01-02"]),
        ("python-str-datetime", "created 1990-01-02 00:00:00 by import", ["1990-01-02"]),
        ("timestamp-in-url", "GET /people?born=1990-01-02T00%3A00%3A00Z 200", ["1990-01-02"]),
        ("python-repr-date", "{'dob': datetime.date(1990, 1, 2), 'id': 7}", ["1990, 1, 2"]),
        ("python-repr-datetime", "{'at': datetime.datetime(1990, 1, 2, 0, 0), 'tz': datetime.datetime(1990, 1, 2, 9, 30, tzinfo=datetime.timezone.utc)}",
         ["1990, 1, 2"]),
        ("python-call", "dob = date(1990, 1, 2) and born = datetime.date(1990,1,2)", ["1990, 1, 2", "1990,1,2"]),
    ]:
        case("gen-date-" + name, "generated (S17: a known date written another way, " + name + ")", text, data={"note": text}, known=[dob],
             leak=leak)
    two_digits = "{'born': datetime.date(1985, 12, 31)}"
    case("gen-date-python-repr-two-digits", "generated (S17: a Python date repr with a two-digit month and day)", two_digits,
         data={"note": two_digits}, known=[("DATE_OF_BIRTH", "1985-12-31")], leak=["1985, 12, 31"])
    js_date = {"dob": "1990-01-02T00:00:00.000Z", "id": 7}
    case("gen-date-js-date-in-data", "generated (S17: a JS Date in a JSON tool result, as JSON writes it)", g.J(js_date), data=js_date,
         known=[dob], leak=["1990-01-02"], keep=["T00:00:00.000Z"], fields=2)
    # other orders and separators stay out (their order is ambiguous), and so does a date glued to letters or digits
    others = "Not 01/02/1990, 2 Jan 1990, 02.01.1990 or 19900102"
    case("gen-date-keep-other-orders", "generated (S17: other orders and separators of a known ISO date are another value)",
         others, data={"note": others}, known=[dob], where=NOT_LOOSE, keep=["01/02/1990", "2 Jan 1990", "02.01.1990", "19900102"])
    glued = "Not 1990-01-02Tuesday, 1990-01-020, x1990-01-02T00:00, 11990-01-02T00:00 or 1990-01-02T"
    case("gen-date-keep-glued", "generated (S17: a known date glued to letters or digits stays)", glued, data={"note": glued},
         known=[dob], where=NOT_LOOSE, keep=["1990-01-02Tuesday", "1990-01-020", "x1990-01-02T00:00", "11990-01-02T00:00", "1990-01-02T"])
    calls = "datetime.date(1990, 2, 1), update(1990, 1, 2), new Date(1990, 1, 2) and datetime.date(1990, 1, 23)"
    case("gen-date-keep-other-date-and-call", "generated (S17: another date in a repr, a call that only ends in date, a JS Date call)",
         calls, data={"note": calls}, known=[dob], where=NOT_LOOSE,
         keep=["datetime.date(1990, 2, 1)", "update(1990, 1, 2)", "new Date(1990, 1, 2)", "datetime.date(1990, 1, 23)"])
    case("gen-date-keep-known-in-other-order", "generated (S17: a known date in another order is not found in an ISO timestamp)",
         "born 1990-01-02T00:00:00Z", data={"note": "born 1990-01-02T00:00:00Z"}, known=[("DATE_OF_BIRTH", "01/02/1990")], where=NOT_LOOSE,
         keep=["1990-01-02T00:00:00Z"])
