"""Leak corpus cases for long known values that repeat themselves (1.7.1 gate 7, 2026-11-26): a value longer than 32
characters is searched for in one pass (Knuth-Morris-Pratt in Swift, Kotlin, Dart and Rust; the engine's search with the
value's period in npm and Python), at every place where it starts, overlapping places merged, in every spelling the vault
searches: as written, in any ASCII case, a URL whose path must be written as it is, capitals of another spelling, other
white space, and a value of long lines. The same values are shared cases of all six packages
(python/tests/vault_cases.json); here they go through the npm and Python middlewares.

Read by gen_vault_corpus.py (load_parts): add(g) calls g.case(...). Every character outside ASCII is built with chr()
(REVIEW-GUIDE rule 9).
"""

NL = chr(10)
SZ = chr(0xDF)  # sharp s
CAP_SZ = chr(0x1E9E)  # capital sharp s


def words(w, n):
    return " ".join(w[i % len(w)] for i in range(n))


def add(g):
    def case(cid, frm, text, **kw):
        """A case in text positions and, with the text under a key, in data positions."""
        kw.setdefault("data", {"note": text})
        return g.case(cid, frm, text, **kw)

    ha = ("PERSON", words(["ha"], 12))
    case("lv-overlap-written", "gate 7 (1.7.1): a value of 12 words that overlaps itself, 20 times in a row",
         "Laughing: " + "ha " * 20 + "end.", known=[ha], leak=[ha[1]], keep=["Laughing: "])
    mixed = ("PERSON", words(["Ha", "ha"], 12))
    case("lv-overlap-caseless", "gate 7 siblings: the same in other ASCII cases", "Said: " + "HA hA Ha ha " * 5 + "end.",
         known=[mixed], leak=["HA hA Ha ha HA hA"], keep=["Said: "])
    case("lv-overlap-white-space", "gate 7 siblings: the same with two spaces between the words",
         "Said: " + "Ha  ha  " * 8 + "end.", known=[mixed], leak=["Ha  ha  Ha  ha"], keep=["Said: "])
    case("lv-overlap-two-lines", "gate 7 siblings: two runs of it, the second with other white space, on two lines",
         "hA Ha " * 7 + NL + "Ha  ha  " * 7 + "end.", known=[mixed], leak=["hA Ha hA Ha", "Ha  ha  Ha  ha"])
    url = ("URL", "https://x.io/" * 4)
    case("lv-url-path", "gate 7 siblings: a URL whose path is longer than 32 characters, its host in capitals, 5 times",
         "see HTTPS://X.IO/" + "https://x.io/" * 5 + " now", known=[url], leak=["x.io/https", "X.IO/https"], keep=["see "])
    weiss = ("PERSON", words(["Wei" + SZ, "wei" + SZ], 7))
    case("lv-capitals", "gate 7 siblings: capitals of another spelling (SS and the capital sharp s), again and again",
         "WEISS " * 9 + "and " + ("WEI" + CAP_SZ + " ") * 8 + "end.", known=[weiss],
         leak=["WEISS WEISS", "WEI" + CAP_SZ + " WEI"])
    dots = ("PERSON", ". " * 16 + "Kim")
    case("lv-partial-matches", "gate 7 siblings: a value found after long partial matches", ". " * 20 + "Kim, and . . Kim",
         known=[dots], leak=[dots[1]], keep=[", and . . Kim"])
    address = ("ADDRESS", "Unit 4, 1200 Long Main Street North Building A, Springfield Heights District West")
    lines = "Unit 4" + NL + "1200 Long Main Street North Building A" + NL + "Springfield Heights District West"
    case("lv-long-lines", "gate 7 siblings: a value of lines longer than 32 characters, written over three lines",
         "Ship to:" + NL + lines + NL + "Thanks.", known=[address], leak=["Long Main Street", "Springfield Heights"],
         keep=["Ship to:", "Thanks."])
