"""Miss % and over-masking % of the shipped v1.1 models per language and per label, for every size (release 1.7.2).

  python scripts/results_by_language.py [--lang en|ko] [--json ../site/public/results/data.json] [--training DIR] [--few 100]

Standard library only, no model run. It reads what evaluate.py left behind for the shipped students (s-<size>-v11tt, the
default file per size, model only: no pattern layer, no SDK post-processing), measured once on 2026-11-01:
  reports/eval_v11pub-s-<size>-v11tt-<variant>.json   public test 1.1 (pmt_v11: 17,058 rows, 54 languages)
  reports/eval_v11rel-<size>-<variant>.json           KDPII (ko), OpenPII (30 languages), Nemotron-PII (en), Gretel finance (7)
  reports/preds/<tag>/<set>.jsonl                      the per-row predictions those reports were scored from
  data/eval/<set>.jsonl                                the gold rows (evaluate.py's input)
The reports hold the rates; the predictions and gold rows give the counts (rows, gold spans, PII characters) and the
over-masking per label. Every rate is scored again from the predictions and must equal the report to the fourth decimal,
or the script stops; the totals are then compared with the README table, the technical report (Section 6) and
docs/results/models-v1.1-public-test.md (SDK defaults, so a few tenths apart; printed, not an error).

Definitions (evaluate.py, as in docs/results): miss % = share of PII characters (not spaces) left unmasked, any label
counting as masked; over-masking % = share of PII-free characters (not spaces, not `ignore` spans of the external sets)
masked. By label, miss % is per gold label; over-masking % is split by the label the model masked with (level-2 labels
folded into PERSON and URL), as a share of all PII-free characters of the set, so the label rows add up to the set's
over-masking. Percentages have one decimal, from the report's four-decimal rates (as the README). A language or label
with fewer than --few gold spans is flagged.

Prints the Markdown tables of docs/results/models-v1.1-by-language-label(.ko).md to stdout (--lang ko: Korean headers)
and, with --json, writes the data of the results page (site/public/results/).
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # training/
MEASURED = "2026-11-01"
SIZES = [("large", "q4e"), ("medium", "q4e"), ("small", "q4"), ("nano", "q4")]   # docs order; the page orders nano → large
MB = {"nano": 2.6, "small": 7.1, "medium": 11.3, "large": 15.5}
# id, evaluate.py set name, report family, display name
SETS = [
    ("public", "pmt_v11", "pub", "newmean-pii-dataset (public test)"),
    ("openpii", "openpii_val", "rel", "OpenPII"),
    ("gretel", "gretel_fin_test", "rel", "Gretel finance"),
    ("nemotron", "nemotron_test", "rel", "Nemotron-PII"),
    ("kdpii", "kdpii_test", "rel", "KDPII"),
]
LANG_NAMES = {
    "ar": "Arabic", "az": "Azerbaijani", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan", "cs": "Czech", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "ga": "Irish", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "id": "Indonesian",
    "is": "Icelandic", "it": "Italian", "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer", "ko": "Korean",
    "lt": "Lithuanian", "lv": "Latvian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "nl": "Dutch", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian",
    "sr": "Serbian", "sv": "Swedish", "sw": "Swahili", "te": "Telugu", "th": "Thai", "tl": "Tagalog", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "zh": "Chinese",
}
HEAD = {
    "en": {"set": "set", "languages": "languages", "rows": "rows", "spans": "gold spans", "chars": "PII characters",
           "language": "language", "label": "label", "few": "fewer than {n} gold spans: a few spans move the rate by a point or more",
           "none": "not labelled in this set"},
    "ko": {"set": "데이터", "languages": "언어", "rows": "행", "spans": "정답 구간", "chars": "PII 글자 수",
           "language": "언어", "label": "라벨", "few": "정답 구간이 {n}개 미만: 구간 몇 개로도 비율이 1%p 넘게 움직입니다",
           "none": "이 데이터에는 없는 라벨", "names": {"public": "newmean-pii-dataset 공개 테스트"}},
}


def fold(label):
    """Level-2 model labels count with their family (PERSON_L2 → PERSON, URL_L2 → URL)."""
    return label[:-3] if label.endswith("_L2") else label


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def tally(rows, preds):
    """Counts behind evaluate.py's leak and overmask, per language and per label (same character rules)."""
    groups = defaultdict(Counter)   # "ALL" / "lang:xx" → rows, spans, pii, miss, non, over
    labels = defaultdict(Counter)   # label → spans, pii, miss (by gold label), over (by predicted label)
    overlaps = 0   # characters under two predictions (a few, where windows meet): counted once, for the longer span
    for r, p in zip(rows, preds, strict=True):
        text = r["text"]
        n = len(text)
        gold, ign, pred = [False] * n, [False] * n, [None] * n
        for s in r.get("ignore", []):
            for i in range(max(0, s["start"]), min(s["end"], n)):
                ign[i] = True
        for s in r["spans"]:
            for i in range(s["start"], min(s["end"], n)):
                gold[i] = not text[i].isspace()
        for s in sorted(p, key=lambda s: s["start"] - s["end"]):   # longest first (stable: file order on ties)
            for i in range(max(0, s["start"]), min(s["end"], n)):
                overlaps += pred[i] is not None
                pred[i] = pred[i] or fold(s["label"])
        spans = 0
        for s in r["spans"]:
            cs = [i for i in range(s["start"], min(s["end"], n)) if not text[i].isspace()]
            lab = labels[s["label"]]
            lab["pii"] += len(cs)
            lab["miss"] += sum(pred[i] is None for i in cs)
            if cs:
                lab["spans"] += 1
                spans += 1
        c = Counter(rows=1, spans=spans)
        for i in range(n):
            if gold[i]:
                c["pii"] += 1
                c["miss"] += pred[i] is None
            elif not ign[i] and not text[i].isspace():
                c["non"] += 1
                if pred[i] is not None:
                    c["over"] += 1
                    labels[pred[i]]["over"] += 1
        for key in ("ALL", f"lang:{r['lang']}"):
            groups[key].update(c)
    return groups, labels, overlaps


def rate(num, den):
    return round(num / den, 4) if den else None


def pct(x):
    """One decimal from a four-decimal rate, as the README and docs/results print it."""
    return None if x is None else float(f"{100 * x:.1f}")


def load_set(training, set_name, family, size, variant):
    if family == "pub":
        tag = f"v11pub-s-{size}-v11tt-{variant}"
    else:
        tag = f"v11rel-{size}-{variant}"
    report = json.loads((training / f"reports/eval_{tag}.json").read_text())[set_name]
    preds = [x["spans"] for x in sorted(read_jsonl(training / f"reports/preds/{tag}/{set_name}.jsonl"), key=lambda x: x["i"])]
    return report, preds


def build(training, few):
    """One entry per set: counts, and per size the rates of the whole set, each language and each label."""
    out = []
    for sid, set_name, family, name in SETS:
        rows = read_jsonl(training / "data" / "eval" / f"{set_name}.jsonl")
        entry = {"id": sid, "name": name, "rows": len(rows)}
        per_size = {}
        for size, variant in SIZES:
            report, preds = load_set(training, set_name, family, size, variant)
            if len(preds) != len(rows):
                sys.exit(f"{set_name} {size}: {len(preds)} predictions for {len(rows)} rows")
            groups, labels, overlaps = tally(rows, preds)
            if overlaps:
                print(f"{set_name} {size}: {overlaps} characters under two predictions, counted once for the longer span",
                      file=sys.stderr)
            bad = []
            for key, c in groups.items():
                got = (rate(c["miss"], c["pii"]), rate(c["over"], c["non"]))
                want = (report[key]["leak"], report[key]["overmask"])
                if got != want:
                    bad.append(f"{key}: scored {got}, report {want}")
            for lab, c in labels.items():
                if c["pii"] and rate(c["miss"], c["pii"]) != report["leak_by_label"].get(lab):
                    bad.append(f"label {lab}: scored {rate(c['miss'], c['pii'])}, report {report['leak_by_label'].get(lab)}")
            if bad:
                sys.exit(f"{set_name} {size}: the predictions do not give the report's numbers:\n  " + "\n  ".join(bad))
            per_size[size] = (report, groups, labels)
        _, groups, labels = per_size["large"]   # counts of gold do not depend on the size
        langs = sorted(k[5:] for k in groups if k.startswith("lang:"))
        entry["languages"] = len(langs)
        entry["all"] = {"rows": groups["ALL"]["rows"], "spans": groups["ALL"]["spans"], "pii": groups["ALL"]["pii"]}
        entry["by_language"] = [{"code": code, "name": LANG_NAMES.get(code, code), "rows": groups[f"lang:{code}"]["rows"],
                                 "spans": groups[f"lang:{code}"]["spans"], "pii": groups[f"lang:{code}"]["pii"]} for code in langs]
        all_labels = sorted({lab for _, _, labs in per_size.values() for lab in labs},
                            key=lambda lab: (-labels[lab]["pii"], lab))
        entry["by_label"] = [{"label": lab, "spans": labels[lab]["spans"], "pii": labels[lab]["pii"]} for lab in all_labels]
        for size, _ in SIZES:
            report, groups, labs = per_size[size]
            non = groups["ALL"]["non"]
            entry["all"][size] = [pct(report["ALL"]["leak"]), pct(report["ALL"]["overmask"])]
            for row in entry["by_language"]:
                x = report[f"lang:{row['code']}"]
                row[size] = [pct(x["leak"]), pct(x["overmask"])]
            for row in entry["by_label"]:
                miss = report["leak_by_label"].get(row["label"]) if row["pii"] else None
                row[size] = [pct(miss), pct(rate(labs[row["label"]]["over"], non))]
        for row in entry["by_language"] + entry["by_label"]:
            row["few"] = row["spans"] < few
        out.append(entry)
    return out


# ---- checks against the published tables -------------------------------------------------------------------------------

def nums(cell):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", cell.replace("**", ""))]


def check_docs(data, repo):
    """Compare the set totals with the README table, the technical report and the docs/results tables; print each result."""
    by_id = {e["id"]: e for e in data}
    order = ["public", "kdpii", "openpii", "nemotron", "gretel"]   # column order of both tables
    lines, ok = [], True
    readme = (repo / "README.md").read_text()
    report = (repo / "docs/report/REPORT.md").read_text()
    for size, _ in SIZES:
        m = re.search(rf"^\| \*\*newmean-pii-{size}\*\* \| [^|]+ \|(.+)\|\s*$", readme, re.M)
        cells = [nums(c)[0] for c in m.group(1).split("|")] if m else []
        ours = [by_id[s]["all"][size][0] for s in order]
        same = cells == ours
        ok &= same
        lines.append(f"README miss {size}: {'equal' if same else 'DIFFERENT'} (README {cells}, here {ours})")
        m = re.search(rf"^\| \*\*ours, {size}\*\* \| [^|]+ \| [^|]+ \|(.+)$", report, re.M)
        cells = [nums(c) for c in m.group(1).split("|")[:5]] if m else []
        ours = [by_id[s]["all"][size] for s in order]
        same = cells == ours
        ok &= same
        lines.append(f"REPORT Section 6 miss / over-masking {size}: {'equal' if same else 'DIFFERENT'} (REPORT {cells}, here {ours})")
    # docs/results uses SDK defaults (wholeWords on): informative only
    res = (repo / "docs/results/models-v1.1-public-test.md").read_text()
    pub = by_id["public"]
    m = re.search(r"^\| all \| [\d,]+ \|(.+)\|\s*$", res, re.M)
    if m:
        theirs = [nums(c) for c in m.group(1).split("|")]
        ours = [pub["all"][s] for s, _ in SIZES]
        lines.append(f"docs/results all rows (SDK defaults) {theirs}, here (model only) {ours}")
    diffs = []
    for row in pub["by_language"]:
        m = re.search(rf"^\| [^|]+ \({re.escape(row['code'])}\) \| [\d,]+ \|(.+)\|\s*$", res, re.M)
        if not m:
            continue
        for (size, _), cell in zip(SIZES, m.group(1).split("|")):
            for k, (a, b) in enumerate(zip(row[size], nums(cell))):
                diffs.append((round(a - b, 1), k, size, row["code"]))
    if diffs:
        for k, what in ((0, "miss"), (1, "over-masking")):
            d = [x for x in diffs if x[1] == k]
            same = sum(x[0] == 0 for x in d)
            lo, hi = min(x[0] for x in d), max(x[0] for x in d)
            lines.append(f"docs/results by language, {what}: {same} of {len(d)} cells equal, model only minus SDK defaults "
                         f"{lo:+.1f} to {hi:+.1f} points")
    return ok, lines


# ---- Markdown ------------------------------------------------------------------------------------------------------------

def fmt_int(n):
    return f"{n:,}"


def cell(pair):
    return " / ".join("—" if v is None else f"{v:.1f}" for v in pair)


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def markdown(data, lang, few):
    h = HEAD[lang]
    sizes = [s for s, _ in SIZES]
    mark = lambda r: fmt_int(r["spans"]) + (" †" if r["few"] else "")   # noqa: E731
    parts = []
    rows = [[h.get("names", {}).get(e["id"], e["name"]), str(e["languages"]) if e["languages"] > 1 else e["by_language"][0]["code"], fmt_int(e["rows"]),
             fmt_int(e["all"]["spans"])] + [cell(e["all"][s]) for s in sizes] for e in data]
    parts.append(("all", table([h["set"], h["languages"], h["rows"], h["spans"]] + sizes, rows)))
    for e in data:
        if e["languages"] > 1:
            rows = [[f"{r['name']} ({r['code']})", fmt_int(r["rows"]), mark(r)] + [cell(r[s]) for s in sizes]
                    for r in sorted(e["by_language"], key=lambda r: r["name"])]
            parts.append((f"{e['id']}:language", table([h["language"], h["rows"], h["spans"]] + sizes, rows)))
        rows = [[r["label"], mark(r) if r["pii"] else "—", fmt_int(r["pii"]) if r["pii"] else "—"] + [cell(r[s]) for s in sizes]
                for r in e["by_label"]]
        parts.append((f"{e['id']}:label", table([h["label"], h["spans"], h["chars"]] + sizes, rows)))
    out = []
    for key, t in parts:
        out.append(f"<!-- {key} -->\n{t}")
    out.append(f"<!-- footnote -->\n† {h['few'].format(n=few)}; — {h['none']}.")
    return "\n\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--training", type=Path, default=ROOT, help="folder with reports/ and data/eval/ (default: this training/)")
    ap.add_argument("--repo", type=Path, default=ROOT.parent, help="repository root with README.md and docs/ (for the checks)")
    ap.add_argument("--lang", choices=sorted(HEAD), default="en", help="language of the table headers")
    ap.add_argument("--json", type=Path, help="write the results page data here (site/public/results/data.json)")
    ap.add_argument("--few", type=int, default=100, help="flag languages and labels with fewer gold spans than this")
    a = ap.parse_args()
    data = build(a.training, a.few)
    ok, lines = check_docs(data, a.repo)
    for line in lines:
        print(line, file=sys.stderr)
    if a.json:
        page = {"measured": MEASURED, "models": "v1.1", "few": a.few, "mb": MB,
                "sizes": ["nano", "small", "medium", "large"], "sets": data}
        a.json.write_text(json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.write(markdown(data, a.lang, a.few))
    if not ok:
        sys.exit("the totals differ from the README or the technical report (see above)")


if __name__ == "__main__":
    main()
