"""The newmean PII metric, vendored verbatim from training/scripts/evaluate.py (data v1.0, --scheme v1).

Do not edit the vendored definitions here: change evaluate.py and re-vendor. tests/test_scoring_parity.py checks that each
name in VENDORED is identical (AST) to the one in evaluate.py, and that the leaderboard reproduces the stored L4 numbers.

Metrics (masking perspective):
  leak         share of gold PII characters not covered by any prediction (lower is better, top priority)
  overmask     share of non-PII characters that get masked (lower is better)
  span_f1      span F1 with matching labels (prediction/gold overlap >= 50 percent)
  exact_f1     span F1 with exact boundaries and label
  entity_leak  share of gold entities with less than half of their characters masked
"""
import re
from collections import defaultdict

VENDORED = ['L2_SUBTYPES', 'SECRET_KEYS', 'level', 'collapse_label', 'is_secret', 'map_label', 'REGEX', 'regex_predict', 'strip_bio', 'merge_adjacent', 'score', 'trim_spans', 'needs_native', 'collapse_gold']   # names checked against evaluate.py by the parity test

L2_SUBTYPES = {"PERSON": {"surname", "alias", "initials"}, "URL": {"public"}}   # PII level 2 (fixed by subtype, data v1.0)
SECRET_KEYS = ("PASSWORD", "PASSWD", "PASSCODE", "PASSPHRASE", "API_KEY", "APIKEY", "ACCESS_KEY", "PRIVATE_KEY", "SECRET",
               "TOKEN", "CREDENTIAL")


def level(label, subtype=None):
    """PII level of a span from its data or model label: 2 for *_L2 and the level-2 subtypes, else 1."""
    return 2 if label.endswith("_L2") or subtype in L2_SUBTYPES.get(label, ()) else 1


def collapse_label(label):
    """v1 model label → public-detector granularity (PERSON_L2 → PERSON, URL_L2 → URL)."""
    return label[:-3] if label.endswith("_L2") else label


def is_secret(raw: str) -> bool:
    l = re.sub(r"[\s-]+", "_", strip_bio(raw).upper())
    parts = set(l.split("_"))
    return any(k in l for k in SECRET_KEYS) or bool(parts & {"PIN", "OTP"}) and "CODE" not in parts   # PIN_CODE = Indian postcode


def map_label(raw: str, secret: bool = False):
    """Generic label of a public HF token classifier → our label. secret=True (--scheme v1) adds the SECRET rule."""
    l = raw.upper().replace("B-", "").replace("I-", "")
    if l in ("O", ""):
        return None
    if "IP_ADDRESS" in l or l in ("IP", "IPV4", "IPV6"):
        return "ID_NUMBER"  # the model label scheme treats IP as ID_NUMBER (IP_ADDRESS is regex-only) — it used to hit the ADDRESS rule first
    if secret and is_secret(l):
        return "SECRET"
    rules = [
        (("EMAIL",), "EMAIL"), (("BIRTH", "DOB"), "DATE_OF_BIRTH"), (("URL", "SITE"), "URL"),
        (("USER", "HANDLE", "NICK_ID"), "USERNAME"), (("PHONE", "TEL", "MOBILE", "FAX"), "PHONE"),
        (("CARD", "ACCOUNT", "BANK", "IBAN", "FINANC"), "FINANCIAL"),
        (("STREET", "ADDRESS", "ZIP", "POSTAL", "BUILDING"), "ADDRESS"),
        (("RRN", "SSN", "PASSPORT", "LICENSE", "DRIVER", "ALIEN", "IPIN", "TAX", "NATIONAL", "ID_NUM", "IDCARD",
          "PLATE", "VEHICLE", "SOCIAL", "ACCOUNT_NUMBER", "ID_NUMBER", "_IP", "IP_ADDRESS"), "ID_NUMBER"),
        (("ORG", "COMPANY", "DEPARTMENT", "SCHOOL", "EMPLOYER"), "ORG"),
        (("PERSON", "NAME", "PER"), "PERSON"),
    ]
    for keys, lab in rules:
        if any(k in l for k in keys):
            return lab
    if l in ("ID", "USERID"):
        return "USERNAME"
    return None


REGEX = [
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("URL", r"https?://\S+|www\.\S+"),
    ("ID_NUMBER", r"\b\d{6}[-\s]?[1-8]\d{6}\b"),
    ("FINANCIAL", r"\b(?:\d{4}[-\s]?){3}\d{4}\b|\b\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,3})?\b"),
    ("PHONE", r"(?:\+\d{1,3}[\s-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}\b"),
]


def regex_predict(text):
    out, taken = [], []
    for lab, pat in REGEX:
        for m in re.finditer(pat, text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append({"start": m.start(), "end": m.end(), "label": lab})
    return out


def strip_bio(raw: str) -> str:
    return re.sub(r"^[BIES]-", "", raw)


def merge_adjacent(spans, text):
    """Merges same-label spans separated only by whitespace (Rampart's GIVEN_NAME + SURNAME -> a single PERSON)."""
    out = []
    for sp in sorted(spans, key=lambda x: x["start"]):
        if out and out[-1]["label"] == sp["label"] and not text[out[-1]["end"]:sp["start"]].strip():
            out[-1]["end"] = max(out[-1]["end"], sp["end"])
        else:
            out.append(dict(sp))
    return out


def score(rows, preds):
    agg = defaultdict(lambda: {"pii": 0, "leak": 0, "non": 0, "over": 0, "tp": 0, "fp": 0, "fn": 0})
    lab_agg = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    exact = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})   # exact boundaries + label (the overlap F1 above accepts ≥ 50% overlap)
    ent = defaultdict(lambda: [0, 0, 0])   # gold entities, missed (< 50% of characters covered by any prediction), missed by same label
    lab_leak = defaultdict(lambda: [0, 0])  # gold label -> [PII chars, missed chars] — used to compare models with different label schemes
    strict = defaultdict(lambda: [0, 0])    # gold label -> [PII chars, chars not covered by a same-label prediction] — what leaks for a user masking only that label
    ex_lab = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})   # exact boundaries + label, per label
    lev = defaultdict(lambda: defaultdict(int))   # gold PII level -> chars, leak, strict, entities, entity misses, span hits, exact hits
    for r, p in zip(rows, preds):
        n = len(r["text"])
        gold_mask, pred_mask, ign = [False] * n, [False] * n, [False] * n
        for s in r.get("ignore", []):   # external sets: spans of types outside our scheme (company, date, city alone …) — neither PII nor over-masking
            for i in range(max(0, s["start"]), min(s["end"], n)):
                ign[i] = True
        for s in r["spans"]:
            for i in range(s["start"], min(s["end"], n)):
                gold_mask[i] = not r["text"][i].isspace()
        for s in p:
            for i in range(max(0, s["start"]), min(s["end"], n)):
                pred_mask[i] = True
        by_lab = defaultdict(set)
        for ps in p:
            by_lab[ps["label"]].update(range(max(0, ps["start"]), min(ps["end"], n)))
        for s in r["spans"]:
            cs = [i for i in range(s["start"], min(s["end"], n)) if not r["text"][i].isspace()]
            lab_leak[s["label"]][0] += len(cs)
            lab_leak[s["label"]][1] += sum(not pred_mask[i] for i in cs)
            strict[s["label"]][0] += len(cs)
            strict[s["label"]][1] += sum(i not in by_lab[s["label"]] for i in cs)
        matched = set()
        tp = fp = 0
        for ps in p:
            hit = None
            for gi, gs in enumerate(r["spans"]):
                ov = min(ps["end"], gs["end"]) - max(ps["start"], gs["start"])
                if gi not in matched and gs["label"] == ps["label"] and ov >= 0.5 * (gs["end"] - gs["start"]):
                    hit = gi
                    break
            if hit is None:
                width = max(1, min(ps["end"], n) - max(0, ps["start"]))
                if sum(ign[i] for i in range(max(0, ps["start"]), min(ps["end"], n))) >= 0.5 * width:
                    continue   # mostly on an ignored span — not a false positive
                fp += 1
                lab_agg[ps["label"]]["fp"] += 1
            else:
                tp += 1
                matched.add(hit)
                lab_agg[ps["label"]]["tp"] += 1
        for gi, gs in enumerate(r["spans"]):
            if gi not in matched:
                lab_agg[gs["label"]]["fn"] += 1
        fn = len(r["spans"]) - len(matched)
        gold_set = {(s["start"], s["end"], s["label"]) for s in r["spans"]}
        pred_set = {(s["start"], s["end"], s["label"]) for s in p}
        # a prediction mostly on an ignored span is no false positive for exact F1 either, as in over-masking and the
        # overlap F1
        on_ign = lambda t: sum(ign[i] for i in range(max(0, t[0]), min(t[1], n))) >= 0.5 * max(1, min(t[1], n) - max(0, t[0]))
        pred_set = {t for t in pred_set if t in gold_set or not on_ign(t)}
        ex = (len(gold_set & pred_set), len(pred_set - gold_set), len(gold_set - pred_set))
        for t in gold_set & pred_set:
            ex_lab[t[2]]["tp"] += 1
        for t in pred_set - gold_set:
            ex_lab[t[2]]["fp"] += 1
        for t in gold_set - pred_set:
            ex_lab[t[2]]["fn"] += 1
        for gi, s in enumerate(r["spans"]):
            cs = [i for i in range(s["start"], min(s["end"], n)) if not r["text"][i].isspace()]
            if not cs:
                continue
            L = lev[f"L{level(s['label'], s.get('subtype'))}"]
            L["chars"] += len(cs)
            L["leak"] += sum(not pred_mask[i] for i in cs)
            L["strict"] += sum(i not in by_lab[s["label"]] for i in cs)
            L["ent"] += 1
            L["ent_miss"] += sum(pred_mask[i] for i in cs) < 0.5 * len(cs)
            L["hit"] += gi in matched
            L["exact"] += (s["start"], s["end"], s["label"]) in pred_set
        ent_n = ent_miss = ent_miss_strict = 0
        for s in r["spans"]:
            cs = [i for i in range(s["start"], min(s["end"], n)) if not r["text"][i].isspace()]
            if not cs:
                continue
            ent_n += 1
            ent_miss += sum(pred_mask[i] for i in cs) < 0.5 * len(cs)
            ent_miss_strict += sum(i in by_lab[s["label"]] for i in cs) < 0.5 * len(cs)
        spec = r.get("spec") or {}
        keys = ["ALL", f"lang:{r['lang']}", f"src:{r.get('source', '')}"] + ([f"gen:{r['gen']}"] if r.get("gen") else [])
        if r.get("domain") or spec.get("domain"):   # benchmark breakdown by domain and text format as well as language (2026-10-28)
            keys.append(f"domain:{r.get('domain') or spec.get('domain')}")
        if spec.get("format") or r.get("doc_type"):
            keys.append(f"format:{spec.get('format') or r.get('doc_type')}")
        for k in keys:
            a = agg[k]
            a["pii"] += sum(gold_mask)
            a["leak"] += sum(g and not q for g, q in zip(gold_mask, pred_mask))
            a["non"] += sum((not g) and not ign[i] and not r["text"][i].isspace() for i, g in enumerate(gold_mask))
            a["over"] += sum((not g) and q and not ign[i] and not r["text"][i].isspace() for i, (g, q) in enumerate(zip(gold_mask, pred_mask)))
            a["tp"] += tp; a["fp"] += fp; a["fn"] += fn
            x = exact[k]; x["tp"] += ex[0]; x["fp"] += ex[1]; x["fn"] += ex[2]
            e = ent[k]; e[0] += ent_n; e[1] += ent_miss; e[2] += ent_miss_strict

    def f1(d):
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else 0
        rc = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else 0
        return round(2 * p * rc / (p + rc), 3) if p + rc else 0.0
    summary = {k: {"leak": round(a["leak"] / a["pii"], 4) if a["pii"] else None,
                   "overmask": round(a["over"] / a["non"], 4) if a["non"] else None,
                   "span_f1": f1(a), "exact_f1": f1(exact[k]),
                   "entity_leak": round(ent[k][1] / ent[k][0], 4) if ent[k][0] else None,
                   "entity_leak_strict": round(ent[k][2] / ent[k][0], 4) if ent[k][0] else None} for k, a in sorted(agg.items())}
    summary["labels"] = {k: f1(v) for k, v in sorted(lab_agg.items())}
    summary["exact_by_label"] = {k: f1(v) for k, v in sorted(ex_lab.items())}
    rd = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    summary["levels"] = {k: {"chars": v["chars"], "leak": rd(v["leak"], v["chars"]), "strict_leak": rd(v["strict"], v["chars"]),
                             "entities": v["ent"], "entity_leak": rd(v["ent_miss"], v["ent"]), "recall": rd(v["hit"], v["ent"]),
                             "exact_recall": rd(v["exact"], v["ent"])} for k, v in sorted(lev.items())}
    summary["leak_by_label"] = {k: round(v[1] / v[0], 4) for k, v in sorted(lab_leak.items()) if v[0]}
    summary["strict_leak_by_label"] = {k: round(v[1] / v[0], 4) for k, v in sorted(strict.items()) if v[0]}
    tot = [sum(v[0] for v in strict.values()), sum(v[1] for v in strict.values())]
    summary["ALL"]["strict_leak"] = round(tot[1] / tot[0], 4) if tot[0] else None
    return summary


def trim_spans(text: str, spans: list) -> list:
    """Strip whitespace at span edges (exact-boundary F1 would otherwise count ' Maria' ≠ 'Maria'); drop spans left empty."""
    out = []
    for s in spans:
        a, b = max(0, s["start"]), min(len(text), s["end"])
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            out.append({**s, "start": a, "end": b})
    return out


def needs_native(pred):
    """True if the predictor outputs v1 level-2 labels (our v1 models) — then the 13-label gold is scored as is."""
    return bool({"PERSON_L2", "URL_L2"} & set(getattr(pred, "labels", None) or ()))


def collapse_gold(rows):
    """Gold spans relabelled to public-detector granularity (PERSON_L2 → PERSON, URL_L2 → URL); subtype kept for levels."""
    return [{**r, "spans": [{**s, "label": collapse_label(s["label"])} for s in r["spans"]]} for r in rows]
