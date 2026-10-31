"""External PII datasets: rebuild our evaluation samples from a manifest, and convert the training splits to our training format
(2026-10-29). Public: needs only the raw downloads below, external_sets.py and dedupe_splits.py — no generation code.

Raw downloads (not redistributed; --raw, default data/raw):
  ai4privacy/pii-masking-openpii-1.5m (CC BY 4.0)            openpii/data/train.jsonl, openpii/data/validation.jsonl
  KDPII, Zenodo record 16759166 (CC BY 4.0)                   kdpii.json
  nvidia/Nemotron-PII (CC BY 4.0)                             nemotron/train.parquet, nemotron/test.parquet
  gretelai/synthetic_pii_finance_multilingual (Apache-2.0)    gretel_fin/train-00000-of-00001.parquet, gretel_fin/test-00000-of-00001.parquet
  Dataset revisions used: eval_sets/manifest.json ("sources").

1) eval   — rebuilds data/eval/{openpii_val,kdpii_test,nemotron_test,gretel_fin_test}.jsonl from the row ids in
            eval_sets/manifest.json and checks each file's SHA-256 against the manifest.
              openpii   official validation split (there is no test split), 200 random rows per language, 30 languages
              KDPII     no official split: documents shuffled with seed 20261028 and split 85/15 at document level; the 15% is
                        the test set (manifest lists the test and train document ids)
              Nemotron  official test split, 1,000 random rows per locale (us, intl), seed 20261028
              Gretel    official test split, up to 400 random rows per language (7 languages), seed 20261028
            Labels follow external_sets.py (evaluation mapping: types outside our scheme are `ignore` on Nemotron and Gretel).
2) train  — converts the training sides to our training format → data/ext_train/<source>.jsonl:
              openpii train split · KDPII train documents (manifest) · Nemotron train split · Gretel train split
            Never touches the evaluation samples' splits (openpii validation, KDPII test documents, Nemotron and Gretel test).
            Row: {text, spans [{start, end, label}] in the 13 v1 model labels, ignore [{start, end, type}], known [labels],
                  lang, source, id}
              ignore  spans of types we cannot place in our scheme (company names, plain dates, URLs of unknown level, standalone
                      cities, titles, ages …): the training loss should skip their tokens rather than teach "not PII".
              known   the v1 labels this source annotates; a type outside `known` (e.g. ORG or SECRET in openpii) may sit
                      unannotated in the text, so a trainer should not treat its absence as "O" for those labels (mask that
                      part of the loss, or rely on teacher soft labels there).
            Training-label mapping (differs from the evaluation mapping where our v1 scheme is finer):
              a last name / surname alone → PERSON_L2 (level 2); first + last name, or a given name alone → PERSON
              a nickname (KDPII PS_NICKNAME) → PERSON_L2; a date → DATE_OF_BIRTH only with a birth cue, else ignore
              URL → ignore (public vs personal, i.e. URL_L2 vs URL, is not annotated)
            --openpii-per-lang N keeps a seeded random sample of N rows per language (0 = all).
            Afterwards remove near-duplicates of the evaluation samples (templates repeat across splits), e.g.
              python scripts/dedupe_splits.py --split eval=<the four eval files concatenated> --split ext=data/ext_train/all.jsonl \
                --order eval,ext --out data/dedup/ext_train
Usage:
  python scripts/ext_data.py eval  [--raw data/raw] [--out data/eval]
  python scripts/ext_data.py train [--raw data/raw] [--out data/ext_train] [--openpii-per-lang 4000]
"""
import argparse, ast, hashlib, json, random, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from external_sets import (BIRTH_CUE, GRETEL, GRETEL_LANG, NEMOTRON, REGION, convert, kdpii_sentence_row,  # noqa: E402
                           merge_adjacent, openpii_row)

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "eval_sets/manifest.json"
SEED = 20261028
SERIALIZE = lambda rows: "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


# ---------------------------------------------------------------- evaluation samples
def eval_openpii(raw, ids):
    want, got = set(ids), {}
    with open(raw / "openpii/data/validation.jsonl") as f:
        for line in f:
            o = json.loads(line)
            if o["uid"] in want:
                got[o["uid"]] = o
    return [openpii_row(got[u]) for u in ids]


def kdpii_docs(raw):
    return {d["sentence"][0]["id"].rsplit(".", 1)[0]: d for d in json.loads((raw / "kdpii.json").read_text())}


def eval_kdpii(raw, doc_ids):
    docs = kdpii_docs(raw)
    return [kdpii_sentence_row(s) for d in doc_ids for s in docs[d]["sentence"]]


def nemotron_row(r, mapping=NEMOTRON):
    raw = ast.literal_eval(r["spans"]) if isinstance(r["spans"], str) else list(r["spans"])
    spans, ignore = convert(r["text"], raw, mapping)
    return {"text": r["text"], "spans": spans, "ignore": ignore, "lang": "en", "source": f"nemotron-{r['locale']}",
            "id": r["uid"], "domain": r["domain"], "doc_type": r["document_type"]}


def eval_nemotron(raw, ids):
    import pandas as pd
    df = pd.read_parquet(raw / "nemotron/test.parquet")
    df = df.set_index(df["locale"] + ":" + df["uid"])   # uid repeats across the us and intl locales
    return [nemotron_row(df.loc[k]) for k in ids]


def gretel_row(idx, r, mapping=GRETEL):
    spans, ignore = convert(r["generated_text"], json.loads(r["pii_spans"]), mapping)
    return {"text": r["generated_text"], "spans": spans, "ignore": ignore, "lang": GRETEL_LANG[r["language"]], "source": "gretel-fin",
            "id": int(idx), "doc_type": r["document_type"]}


def eval_gretel(raw, ids):
    import pandas as pd
    df = pd.read_parquet(raw / "gretel_fin/test-00000-of-00001.parquet").reset_index(drop=True)
    return [gretel_row(i, df.loc[i]) for i in ids]


EVAL = {"openpii_val": eval_openpii, "kdpii_test": eval_kdpii, "nemotron_test": eval_nemotron, "gretel_fin_test": eval_gretel}


def cmd_eval(a):
    m = json.loads(MANIFEST.read_text())
    ok = True
    for name, spec in m["sets"].items():
        text = SERIALIZE(EVAL[name](Path(a.raw), spec["ids"]))
        h = hashlib.sha256(text.encode()).hexdigest()
        out = Path(a.out) / f"{name}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        same = h == spec["sha256"]
        ok &= same
        print(f"{name}: {len(spec['ids']):,} ids → {out} sha256 {'matches the manifest' if same else 'DIFFERS from the manifest'}")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- training conversion
IGNORE, SURNAME, DATE, CITY = "IGNORE", "SURNAME*", "DATE*", "CITY*"
PLACEHOLDER = re.compile(r"\s*[\[\{<]")   # "[Guest Name]", and "[Applicant's Email" where the source span stops before the bracket closes
MONTH = re.compile(r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|월|月|janv|févr|avr|juil|août|déc|ene|abr|ago|dic|mär|mai|okt|dez|"
                   r"gen|giu|lug|set|ott|maa|mei|maj|juni|juli", re.I)


def plausible(label, v):
    """Value check per v1 label (quality pass 2026-10-29): a tag whose text cannot be such a value is a source error."""
    d = sum(c.isdigit() for c in v)
    if label == "EMAIL":
        return "@" in v
    if label == "PHONE":
        return d >= 5
    if label == "FINANCIAL":
        return d >= 3 or len(v) >= 8   # CVVs have 3 digits; SWIFT/BIC codes are letters
    if label == "ID_NUMBER":
        return sum(c.isalnum() for c in v) >= 4   # openpii ids may be letters only
    if label == "DATE_OF_BIRTH":
        return d >= 1 or bool(MONTH.search(v))
    if label in ("PERSON", "PERSON_L2", "USERNAME"):
        return any(c.isalpha() for c in v)
    return True
OPENPII_TRAIN = {
    "GIVENNAME": "PERSON", "SURNAME": SURNAME, "EMAIL": "EMAIL", "TELEPHONENUM": "PHONE", "STREET": "ADDRESS", "BUILDINGNUM": "ADDRESS",
    "ZIPCODE": "ADDRESS", "CITY": CITY, "IDCARDNUM": "ID_NUMBER", "SOCIALNUM": "ID_NUMBER", "PASSPORTNUM": "ID_NUMBER",
    "DRIVERLICENSENUM": "ID_NUMBER", "TAXNUM": "ID_NUMBER", "CREDITCARDNUMBER": "FINANCIAL", "ACCOUNTNUM": "FINANCIAL",
    "DATE": DATE, "USERNAME": "USERNAME", "PASSWORD": "SECRET", "IPV4": "ID_NUMBER",
}   # everything else (TITLE, AGE, GENDER, SEX, TIME, URL, ORGANISATION, BANKNAME, COUNTRY, AMOUNT, …) → ignore
OPENPII_KNOWN = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "DATE_OF_BIRTH"]
KDPII_TRAIN = {
    "PS_NAME": "PERSON", "PS_NICKNAME": "PERSON_L2", "PS_ID": "USERNAME", "TMI_EMAIL": "EMAIL", "QT_PHONE": "PHONE", "QT_MOBILE": "PHONE",
    "QT_CARD_NUMBER": "FINANCIAL", "QT_ACCOUNT_NUMBER": "FINANCIAL", "DT_BIRTH": "DATE_OF_BIRTH", "QT_RESIDENT_NUMBER": "ID_NUMBER",
    "QT_ALIEN_NUMBER": "ID_NUMBER", "QT_PASSPORT_NUMBER": "ID_NUMBER", "QT_DRIVER_NUMBER": "ID_NUMBER", "QT_PLATE_NUMBER": "ID_NUMBER",
    "QT_IP": "ID_NUMBER", "OG_WORKPLACE": "ORG", "OGG_EDUCATION": "ORG", "OG_DEPARTMENT": "ORG", "LC_ADDRESS": "ADDRESS*",
}   # TMI_SITE (URL level unknown), clubs, places, positions, majors, ages, heights, religion, blood type … → ignore
KDPII_KNOWN = ["PERSON", "PERSON_L2", "USERNAME", "EMAIL", "PHONE", "FINANCIAL", "DATE_OF_BIRTH", "ID_NUMBER", "ORG", "ADDRESS"]
NEMOTRON_TRAIN = {**NEMOTRON, "last_name": SURNAME, "url": IGNORE}
GRETEL_TRAIN = {**GRETEL, "last_name": SURNAME}
EXT_KNOWN = ["PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "USERNAME", "DATE_OF_BIRTH", "SECRET"]


def train_spans(text, raw, mapping, region_join=4):
    """raw = [{start, end, label}] in source labels → (spans in v1 labels, ignore). Same span rules as the evaluation mapping
    (adjacent PERSON / ADDRESS parts merge; a city or region joins a touching address, else ignore), plus: a surname that
    merges with nothing is PERSON_L2, a date is DATE_OF_BIRTH only with a birth cue in the 40 characters before it."""
    spans, ignore, region = [], [], []
    for s in sorted(raw, key=lambda s: (s["start"], s["end"])):
        lab = mapping.get(s["label"], IGNORE)
        b, e = s["start"], s["end"]
        if not 0 <= b < e <= len(text):
            continue   # offsets outside the text
        while b < e and text[b].isspace():   # trailing or leading whitespace inside a source span (openpii phones)
            b += 1
        while e > b and text[e - 1].isspace():
            e -= 1
        if b >= e:
            continue
        if lab != IGNORE and (PLACEHOLDER.match(text[b:e]) or (s.get("text") is not None and text[s["start"]:s["end"]] != s["text"])):
            lab = IGNORE   # template placeholders are not values; a span whose stored text disagrees with the offsets is unreliable
        if lab == DATE:
            lab = "DATE_OF_BIRTH" if BIRTH_CUE.search(text[max(0, b - 40):b]) else IGNORE
        if lab not in (IGNORE, REGION, CITY) and not plausible("PERSON" if lab == SURNAME else lab, text[b:e]):
            lab = IGNORE   # source label error ("email marketing" tagged EMAIL, a phone number tagged EMAIL …)
        if lab in (REGION, CITY):
            region.append({**s, "start": b, "end": e})
        elif lab == IGNORE:
            ignore.append({"start": b, "end": e, "type": s["label"]})
        else:
            spans.append({"start": b, "end": e, "label": lab, "sur": lab == SURNAME, "type": s["label"]})
    for s in spans:
        if s["label"] == SURNAME:
            s["label"] = "PERSON"
    addr = [s for s in spans if s["label"] == "ADDRESS"]
    for s in region:
        if any(-region_join <= a["start"] - s["end"] <= region_join or -region_join <= s["start"] - a["end"] <= region_join for a in addr):
            spans.append({"start": s["start"], "end": s["end"], "label": "ADDRESS", "sur": False, "type": s["label"]})
        else:
            ignore.append({"start": s["start"], "end": s["end"], "type": s["label"]})
    # merge adjacent PERSON / ADDRESS parts; a PERSON made of surname parts only is level 2
    out = []
    for s in sorted(spans, key=lambda s: (s["start"], s["end"])):
        p = out[-1] if out else None
        if p and p["label"] == s["label"] and s["label"] in ("PERSON", "ADDRESS") and re.fullmatch(r"[\s,.\-/、，]{0,3}", text[p["end"]:s["start"]]):
            p["end"] = max(p["end"], s["end"]); p["sur"] = p["sur"] and s["sur"]
        elif p and s["start"] < p["end"]:
            continue   # overlapping source spans: keep the first
        else:
            out.append(dict(s))
    res = []
    for s in out:
        if any(g["start"] < s["end"] and s["start"] < g["end"] for g in ignore):
            ignore.append({"start": s["start"], "end": s["end"], "type": f"overlaps-ignore:{s.get('type', s['label'])}"})
            continue
        res.append({"start": s["start"], "end": s["end"], "label": "PERSON_L2" if s["label"] == "PERSON" and s["sur"] else s["label"]})
    return res, sorted(ignore, key=lambda g: g["start"])


DROPPED = Counter()


def train_openpii(raw, per_lang):
    rng = random.Random(SEED)
    buckets, seen = defaultdict(list), Counter()
    with open(raw / "openpii/data/train.jsonl") as f:
        for line in f:
            o = json.loads(line)
            lang = o["language"]; seen[lang] += 1
            b = buckets[lang]
            if not per_lang or len(b) < per_lang:
                b.append(o)
            else:
                j = rng.randrange(seen[lang])
                if j < per_lang:
                    b[j] = o
    for o in (o for b in buckets.values() for o in b):
        spans, ignore = train_spans(o["source_text"], o["privacy_mask"], OPENPII_TRAIN)
        yield {"text": o["source_text"], "spans": spans, "ignore": ignore, "known": OPENPII_KNOWN, "lang": o["language"],
               "source": "openpii-train", "id": o["uid"]}


def train_kdpii(raw, doc_ids):
    docs = kdpii_docs(raw)
    for d in doc_ids:
        for s in docs[d]["sentence"]:
            text, raw_sp = s["form"], []
            for p in s["PNE"]:
                b, e = p["begin"], p["end"]
                if text[b:e] != p["form"]:   # same offset repair as the evaluation mapping
                    b = text.find(p["form"])
                    if b < 0:
                        continue
                    e = b + len(p["form"])
                lab = p["label"]
                if KDPII_TRAIN.get(lab) == "ADDRESS*":
                    lab = "LC_ADDRESS" if re.search(r"\d", p["form"]) else "LC_ADDRESS_REGION"   # a region without numbers is not masked
                raw_sp.append({"start": b, "end": e, "label": lab})
            spans, ignore = train_spans(text, raw_sp, {**KDPII_TRAIN, "LC_ADDRESS": "ADDRESS"})
            yield {"text": text, "spans": spans, "ignore": ignore, "known": KDPII_KNOWN, "lang": "ko", "source": "kdpii-train", "id": s["id"]}


def train_nemotron(raw):
    import pandas as pd
    for _, r in pd.read_parquet(raw / "nemotron/train.parquet").iterrows():
        sp = ast.literal_eval(r["spans"]) if isinstance(r["spans"], str) else list(r["spans"])
        spans, ignore = train_spans(r["text"], sp, NEMOTRON_TRAIN)
        yield {"text": r["text"], "spans": spans, "ignore": ignore, "known": EXT_KNOWN, "lang": "en", "source": f"nemotron-train-{r['locale']}",
               "id": f"{r['locale']}:{r['uid']}"}


def train_gretel(raw):
    import pandas as pd
    df = pd.read_parquet(raw / "gretel_fin/train-00000-of-00001.parquet").reset_index(drop=True)
    for i, r in df.iterrows():
        t, sp = r["generated_text"], json.loads(r["pii_spans"])
        if any(s["end"] > len(t) or not t[s["start"]:s["end"]].strip() or (s["label"] in ("name", "first_name", "last_name")
               and not any(c.isalpha() for c in t[s["start"]:s["end"]])) for s in sp):
            DROPPED["gretel: offsets outside the text or a name span without letters (the row's offsets are shifted)"] += 1
            continue
        spans, ignore = train_spans(t, sp, GRETEL_TRAIN)
        yield {"text": r["generated_text"], "spans": spans, "ignore": ignore, "known": EXT_KNOWN, "lang": GRETEL_LANG[r["language"]],
               "source": "gretel-fin-train", "id": int(i)}


def cmd_train(a):
    raw, out = Path(a.raw), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads(MANIFEST.read_text())
    gens = {"openpii": lambda: train_openpii(raw, a.openpii_per_lang), "kdpii": lambda: train_kdpii(raw, m["kdpii_split"]["train_docs"]),
            "nemotron": lambda: train_nemotron(raw), "gretel": lambda: train_gretel(raw)}
    for name in a.sources:
        n, labs, ign = 0, Counter(), Counter()
        with open(out / f"{name}.jsonl", "w") as f:
            for r in gens[name]():
                if not r["text"].strip():
                    continue
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                n += 1; labs.update(s["label"] for s in r["spans"]); ign.update(s["type"] for s in r["ignore"])
        print(f"{name}: {n:,} rows → {out / (name + '.jsonl')}\n  labels {dict(labs.most_common())}\n  ignored {dict(ign.most_common(12))}", flush=True)
    if DROPPED:
        print("dropped rows:", dict(DROPPED))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["eval", "train"])
    ap.add_argument("--raw", default=str(ROOT / "data/raw"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--openpii-per-lang", type=int, default=0)
    ap.add_argument("--sources", nargs="+", default=["openpii", "kdpii", "nemotron", "gretel"])
    a = ap.parse_args()
    a.out = a.out or str(ROOT / ("data/eval" if a.cmd == "eval" else "data/ext_train"))
    cmd_eval(a) if a.cmd == "eval" else cmd_train(a)
