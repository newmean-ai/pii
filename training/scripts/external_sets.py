"""Label mapping of commercially usable external PII datasets to our scheme, for evaluation (2026-10-28) — for comparing our
models and the public detectors outside our own synthetic data.

  nvidia/Nemotron-PII (CC BY 4.0), test split            → data/eval/nemotron_test.jsonl   (2,000 rows: 1,000 us + 1,000 intl)
  gretelai/synthetic_pii_finance_multilingual (Apache-2.0) → data/eval/gretel_fin_test.jsonl (≤ 400 rows per language, 7 languages)
  openpii-1.5m validation and KDPII test documents: openpii_row() and kdpii_sentence_row() (kept here so
  every evaluation set can be rebuilt without the data-generation code)

Labels are mapped to our 12-label scheme. Types our guideline does not tag (company, occupation, plain dates and times, age,
gender and other sensitive attributes, a city/county/state/country standing alone) become `ignore` spans on Nemotron and
Gretel: evaluate.py counts predictions on them as neither PII nor over-masking (on openpii and KDPII, unmapped types are simply
not spans, as when those sets were first built). A city or region right next to a street address or postcode joins the
ADDRESS span, as in our guideline. Row ids are kept so the samples can be reproduced; the raw files are not redistributed.
Rebuild all four sets from their ids: scripts/ext_data.py eval (eval_sets/manifest.json). Original sampling of Nemotron and
Gretel: python scripts/external_sets.py   (expects the parquet files under data/raw/nemotron and data/raw/gretel_fin)
"""
import ast, glob, json, random, re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REGION = "REGION*"   # merged into an adjacent ADDRESS, else ignored

NEMOTRON = {
    "first_name": "PERSON", "last_name": "PERSON", "email": "EMAIL", "url": "URL", "phone_number": "PHONE", "fax_number": "PHONE",
    "street_address": "ADDRESS", "postcode": "ADDRESS", "coordinate": "ADDRESS", "city": REGION, "county": REGION, "state": REGION,
    "country": REGION, "account_number": "FINANCIAL", "credit_debit_card": "FINANCIAL", "bank_routing_number": "FINANCIAL",
    "swift_bic": "FINANCIAL", "cvv": "FINANCIAL", "customer_id": "ID_NUMBER", "employee_id": "ID_NUMBER",
    "medical_record_number": "ID_NUMBER", "health_plan_beneficiary_number": "ID_NUMBER", "ssn": "ID_NUMBER",
    "certificate_license_number": "ID_NUMBER", "vehicle_identifier": "ID_NUMBER", "license_plate": "ID_NUMBER",
    "mac_address": "ID_NUMBER", "device_identifier": "ID_NUMBER", "tax_id": "ID_NUMBER", "ipv4": "ID_NUMBER", "ipv6": "ID_NUMBER",
    "user_name": "USERNAME", "date_of_birth": "DATE_OF_BIRTH", "password": "SECRET", "api_key": "SECRET", "pin": "SECRET",
    "http_cookie": "SECRET",
}   # everything else (company_name, occupation, date, time, age, gender, unique_id, biometric_identifier, …) → ignore
GRETEL = {
    "name": "PERSON", "first_name": "PERSON", "last_name": "PERSON", "email": "EMAIL", "phone_number": "PHONE",
    "street_address": "ADDRESS", "local_latlng": "ADDRESS", "date_of_birth": "DATE_OF_BIRTH", "swift_bic_code": "FINANCIAL",
    "iban": "FINANCIAL", "bban": "FINANCIAL", "credit_card_number": "FINANCIAL", "credit_card_security_code": "FINANCIAL",
    "bank_routing_number": "FINANCIAL", "customer_id": "ID_NUMBER", "employee_id": "ID_NUMBER", "ssn": "ID_NUMBER",
    "driver_license_number": "ID_NUMBER", "passport_number": "ID_NUMBER", "ipv4": "ID_NUMBER", "ipv6": "ID_NUMBER",
    "user_name": "USERNAME", "password": "SECRET", "api_key": "SECRET", "account_pin": "SECRET",
}   # company, date, time, date_time → ignore
GRETEL_LANG = {"English": "en", "German": "de", "Dutch": "nl", "Spanish": "es", "Italian": "it", "Swedish": "sv", "France": "fr"}


# openpii-1.5m and KDPII (kept here so the evaluation sets can be rebuilt without the data-generation code)
OPENPII_MAP = {
    "GIVENNAME": "PERSON", "SURNAME": "PERSON", "EMAIL": "EMAIL", "TELEPHONENUM": "PHONE",
    "STREET": "ADDRESS", "BUILDINGNUM": "ADDRESS", "ZIPCODE": "ADDRESS", "CITY": "CITY*",
    "IDCARDNUM": "ID_NUMBER", "SOCIALNUM": "ID_NUMBER", "PASSPORTNUM": "ID_NUMBER",
    "DRIVERLICENSENUM": "ID_NUMBER", "TAXNUM": "ID_NUMBER", "CREDITCARDNUMBER": "FINANCIAL", "DATE": "DATE*",
}
BIRTH_CUE = re.compile(r"birth|born|DOB|d\.o\.b|생년|생일|출생|태어|生年|誕生|出生|生日|nacimiento|nacido|nacida|naissance|né |née |geboren|Geburt|nascimento|nascid|nato|nata|sinh|lahir|เกิด|जन्म|geboorte", re.I)
KDPII_MAP = {
    "PS_NAME": "PERSON", "PS_NICKNAME": "PERSON", "PS_ID": "USERNAME", "TMI_EMAIL": "EMAIL", "TMI_SITE": "URL",
    "QT_PHONE": "PHONE", "QT_MOBILE": "PHONE", "QT_CARD_NUMBER": "FINANCIAL", "QT_ACCOUNT_NUMBER": "FINANCIAL",
    "DT_BIRTH": "DATE_OF_BIRTH", "QT_RESIDENT_NUMBER": "ID_NUMBER", "QT_ALIEN_NUMBER": "ID_NUMBER",
    "QT_PASSPORT_NUMBER": "ID_NUMBER", "QT_DRIVER_NUMBER": "ID_NUMBER", "QT_PLATE_NUMBER": "ID_NUMBER",
    "QT_IP": "ID_NUMBER", "OG_WORKPLACE": "ORG", "OGG_EDUCATION": "ORG", "OG_DEPARTMENT": "ORG",
    "LC_ADDRESS": "ADDRESS*",
}


def merge_adjacent(text, spans, labels=("PERSON", "ADDRESS"), gap=r"[\s,.\-/、，]{0,3}"):
    """Merges same-label spans separated only by whitespace/commas into one span (surname + given name, address fragments)."""
    spans = sorted(spans, key=lambda s: s["start"])
    out = []
    for s in spans:
        if out and out[-1]["label"] == s["label"] and s["label"] in labels \
                and re.fullmatch(gap, text[out[-1]["end"]:s["start"]]):
            out[-1]["end"] = s["end"]
        else:
            out.append(dict(s))
    return out


def openpii_row(o):
    text, spans = o["source_text"], []
    for m in o["privacy_mask"]:
        lab = OPENPII_MAP.get(m["label"])
        if not lab:
            continue
        if lab == "DATE*":
            if not BIRTH_CUE.search(text[max(0, m["start"] - 40):m["start"]]):
                continue
            lab = "DATE_OF_BIRTH"
        spans.append({"start": m["start"], "end": m["end"], "label": lab})
    # a city counts as an address only when adjacent to other address fragments (a standalone city is not masked — guideline)
    addr = [s for s in spans if s["label"] == "ADDRESS"]
    fixed = []
    for s in spans:
        if s["label"] == "CITY*":
            near = any(abs(a["start"] - s["end"]) <= 3 or abs(s["start"] - a["end"]) <= 3 for a in addr)
            if not near:
                continue
            s = {**s, "label": "ADDRESS"}
        fixed.append(s)
    return {"text": text, "spans": merge_adjacent(text, fixed), "lang": o["language"], "source": "openpii"}



def kdpii_sentence_row(s):
    """One KDPII sentence → an evaluation row (PNE labels mapped by KDPII_MAP; unmapped PNE types are not spans)."""
    text, spans = s["form"], []
    for p in s["PNE"]:
        lab = KDPII_MAP.get(p["label"])
        if not lab:
            continue
        b, e = p["begin"], p["end"]
        if text[b:e] != p["form"]:
            b = text.find(p["form"])
            if b < 0:
                continue
            e = b + len(p["form"])
        if lab == "ADDRESS*":
            if not re.search(r"\d", p["form"]):
                continue  # a standalone region such as "서울시" (Seoul) is not masked
            lab = "ADDRESS"
        spans.append({"start": b, "end": e, "label": lab})
    return {"text": text, "spans": merge_adjacent(text, spans), "lang": "ko", "source": "kdpii"}


def convert(text, raw_spans, mapping):
    spans, ignore, region = [], [], []
    import re
    for s in sorted(raw_spans, key=lambda s: s["start"]):
        lab = mapping.get(s["label"])
        if re.fullmatch(r"\s*[\[\{<].*[\]\}>]\s*", text[s["start"]:s["end"]]):
            lab = None   # template placeholders labelled as PII ("[Guest Name]", 331 in the Gretel sample) are not values
        if lab is None:
            ignore.append({"start": s["start"], "end": s["end"], "type": s["label"]})
        elif lab == REGION:
            region.append(s)
        else:
            spans.append({"start": s["start"], "end": s["end"], "label": lab})
    addr = [s for s in spans if s["label"] == "ADDRESS"]
    for s in region:   # "146 County Rd 86 in Knox County" — a region joins only when it touches an address part (≤ 4 chars apart)
        if any(-4 <= a["start"] - s["end"] <= 4 or -4 <= s["start"] - a["end"] <= 4 for a in addr):
            spans.append({"start": s["start"], "end": s["end"], "label": "ADDRESS"})
        else:
            ignore.append({"start": s["start"], "end": s["end"], "type": s["label"]})
    spans = merge(text, sorted(spans, key=lambda s: s["start"]))
    return spans, ignore


def merge(text, spans, labels=("PERSON", "ADDRESS")):
    import re
    out = []
    for s in spans:
        if out and out[-1]["label"] == s["label"] and s["label"] in labels and re.fullmatch(r"[\s,.\-/]{0,4}|\s+in\s+", text[out[-1]["end"]:s["start"]]):
            out[-1]["end"] = max(out[-1]["end"], s["end"])
        elif out and s["start"] < out[-1]["end"]:
            continue   # overlapping source spans: keep the first
        else:
            out.append(dict(s))
    return out


def nemotron(n_per_locale=1000, seed=20261028):
    df = pd.read_parquet(ROOT / "data/raw/nemotron/test.parquet")
    rows = []
    for loc, g in df.groupby("locale"):
        for _, r in g.sample(n=min(n_per_locale, len(g)), random_state=seed).iterrows():
            raw = ast.literal_eval(r["spans"]) if isinstance(r["spans"], str) else list(r["spans"])
            spans, ignore = convert(r["text"], raw, NEMOTRON)
            rows.append({"text": r["text"], "spans": spans, "ignore": ignore, "lang": "en", "source": f"nemotron-{loc}",
                         "id": r["uid"], "domain": r["domain"], "doc_type": r["document_type"]})
    return rows


def gretel(per_lang=400, seed=20261028):
    df = pd.read_parquet(ROOT / "data/raw/gretel_fin/test-00000-of-00001.parquet").reset_index(drop=True)
    rows = []
    for lang, g in df.groupby("language"):
        for idx, r in g.sample(n=min(per_lang, len(g)), random_state=seed).iterrows():
            spans, ignore = convert(r["generated_text"], json.loads(r["pii_spans"]), GRETEL)
            rows.append({"text": r["generated_text"], "spans": spans, "ignore": ignore, "lang": GRETEL_LANG[lang],
                         "source": "gretel-fin", "id": int(idx), "doc_type": r["document_type"]})
    return rows


if __name__ == "__main__":
    for name, rows in (("nemotron_test", nemotron()), ("gretel_fin_test", gretel())):
        out = ROOT / f"data/eval/{name}.jsonl"
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        from collections import Counter
        print(name, len(rows), "rows", sum(len(r["spans"]) for r in rows), "spans", sum(len(r["ignore"]) for r in rows), "ignored",
              dict(Counter(s["label"] for r in rows for s in r["spans"])))
