// The vault: the cases every package shares (python/tests/vault_cases.json; skipped when the crate is built outside the
// repository) and the Rust API.
use std::collections::{HashMap, HashSet};
use std::path::Path;

use newmean_pii::{apply_redaction_vault, escape_tags, redact_with_vault, vault_tags, with_vault_spans, Label, Only, Source, Span, Vault, VaultEntry};
use serde_json::Value;

mod common;
use common::{assert_linear, median_time, skip_timing};

fn span(text: &str, value: &str, label: Label) -> Span {
    let start = text.find(value).unwrap();
    span_at(text, start, start + value.len(), label)
}

/// A span a model found over the bytes `start..end` of `text`.
fn span_at(text: &str, start: usize, end: usize, label: Label) -> Span {
    Span {
        start,
        end,
        char_start: text[..start].chars().count(),
        char_end: text[..end].chars().count(),
        utf16_start: text[..start].encode_utf16().count(),
        utf16_end: text[..end].encode_utf16().count(),
        label,
        source: Source::Model,
    }
}

/// hide_value of the shared cases on JSON: strings that equal a known value become its tag, other strings are hidden.
fn hide_json(v: &mut Vault, x: &Value) -> Value {
    match x {
        Value::String(s) => Value::String(v.hide_value(s)),
        Value::Array(a) => Value::Array(a.iter().map(|e| hide_json(v, e)).collect()),
        Value::Object(o) => Value::Object(o.iter().map(|(k, e)| (k.clone(), hide_json(v, e))).collect()),
        other => other.clone(),
    }
}

#[test]
fn shared_cases() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../python/tests/vault_cases.json");
    let Ok(raw) = std::fs::read_to_string(&path) else {
        eprintln!("skipped: no {}", path.display());
        return;
    };
    let cases: Value = serde_json::from_str(&raw).unwrap();
    for g in cases["groups"].as_array().unwrap() {
        let name = g["name"].as_str().unwrap();
        let mut v = Vault::new();
        let tags: Vec<String> =
            g["values"].as_array().unwrap().iter().map(|p| v.tag(p[0].as_str().unwrap(), p[1].as_str().unwrap())).collect();
        let want: Vec<&str> = g["tags"].as_array().unwrap().iter().map(|t| t.as_str().unwrap()).collect();
        assert_eq!(tags, want, "{name}: tags");
        for c in g["redact"].as_array().into_iter().flatten() {
            // spans a model found (then the vault's known values): each over the next place its value is written
            let text = c["text"].as_str().unwrap();
            let mut at = 0;
            let mut spans = Vec::new();
            for f in c["found"].as_array().unwrap() {
                let value = f[0].as_str().unwrap();
                let start = at + text[at..].find(value).unwrap();
                let end = start + value.len();
                let label: Label = f[1].as_str().unwrap().parse().unwrap();
                spans.push(span_at(text, start, end, label));
                at = end;
            }
            let (out, spans) = redact_with_vault(text, spans, &mut v);
            assert_eq!(out, c["want"].as_str().unwrap(), "{name}: redact");
            if let Some(want) = c["spans"].as_array() {
                // the final spans as covered text, label and source (Label is an enum: a span of a custom family is
                // labelled PERSON; its placeholder keeps the family)
                let got: Vec<(String, &str, &str)> =
                    spans.iter().map(|s| (text[s.start..s.end].to_string(), s.label.as_str(), s.source.as_str())).collect();
                let want: Vec<(String, &str, &str)> = want
                    .iter()
                    .map(|s| {
                        let label = s[1].as_str().unwrap();
                        (s[0].as_str().unwrap().to_string(), label.parse::<Label>().map_or("PERSON", Label::as_str), s[2].as_str().unwrap())
                    })
                    .collect();
                assert_eq!(got, want, "{name}: redact spans");
            }
        }
        if let Some(values) = g["entries"].as_array() {
            let got: Vec<&str> = v.entries().iter().map(|e| e.value.as_str()).collect();
            let want: Vec<&str> = values.iter().map(|x| x.as_str().unwrap()).collect();
            assert_eq!(got, want, "{name}: entries");
        }
        for c in g["hide"].as_array().into_iter().flatten() {
            assert_eq!(v.hide(c["text"].as_str().unwrap(), false), c["want"].as_str().unwrap(), "{name}: hide");
        }
        for c in g["hide_escaped"].as_array().into_iter().flatten() {
            assert_eq!(v.hide(c["text"].as_str().unwrap(), true), c["want"].as_str().unwrap(), "{name}: hide escaped");
        }
        for c in g["hide_value"].as_array().into_iter().flatten() {
            assert_eq!(hide_json(&mut v, &c["value"]), c["want"], "{name}: hide_value");
        }
        for c in g["restore"].as_array().into_iter().flatten() {
            let only: Option<HashSet<String>> = c["only"].as_array().map(|a| a.iter().map(|t| t.as_str().unwrap().to_string()).collect());
            let got = match &only {
                Some(set) => v.restore_with(c["text"].as_str().unwrap(), Only::Tags(set), c["json"].as_bool().unwrap_or(false)),
                None => v.restore_with(c["text"].as_str().unwrap(), Only::All, c["json"].as_bool().unwrap_or(false)),
            };
            assert_eq!(got, c["want"].as_str().unwrap(), "{name}: restore");
        }
    }
}

#[test]
fn restores_a_stream_split_anywhere() {
    let mut v = Vault::new();
    v.tag("PERSON", "Kim Minsu");
    v.tag("EMAIL", "kim@example.com");
    let text = "Dear [PERSON_1], we wrote to [EMAIL_1]. [x] [";
    for size in [1, 2, 3, 5, 7, 100] {
        let mut r = v.restorer(Only::All, false);
        let chars: Vec<char> = text.chars().collect();
        let mut out = String::new();
        for chunk in chars.chunks(size) {
            out += &r.push(&chunk.iter().collect::<String>());
        }
        out += &r.flush();
        assert_eq!(out, "Dear Kim Minsu, we wrote to kim@example.com. [x] [");
    }
}

#[test]
fn snapshots_entries_generation_and_replay() {
    let mut v = Vault::new();
    v.tag("PERSON", "Alice");
    let snap = v.bindings(["[PERSON_1]", "[PERSON_9]"]);
    assert_eq!(snap, HashMap::from([("[PERSON_1]".to_string(), "Alice".to_string())]));
    v.remember("xid: 42y", "x[ID_NUMBER_1]y");
    assert_eq!(v.recall("xid: 42y"), Some("x[ID_NUMBER_1]y"));
    let saved = v.entries().to_vec();
    let g = v.generation();
    v.clear();
    assert_eq!((v.len(), v.generation(), v.recall("xid: 42y")), (0, g + 1, None));
    v.tag("PERSON", "Bob");
    assert_eq!(v.restore_with("[PERSON_1]", Only::Values(&snap), false), "Alice");
    let w = Vault::from_entries(saved).unwrap();
    assert_eq!(w.restore("[PERSON_1]"), "Alice");
    assert!(Vault::from_entries([VaultEntry { tag: "PERSON_1".into(), label: "PERSON".into(), value: "x".into() }]).is_err());
    assert!(Vault::from_entries([VaultEntry { tag: format!("[{}_1]", "Y".repeat(33)), label: "Y".into(), value: "v".into() }]).is_err());
    assert_eq!(escape_tags("a [PERSON_1] [note] [x_1]"), "a \u{ff3b}PERSON_1] [note] [x_1]");
    assert_eq!(vault_tags("[A_1] x [B_22] [c_3]"), vec!["[A_1]", "[B_22]"]);
    assert_eq!(v.restore_with("{\"to\":\"[PERSON_1]\"}", Only::Values(&HashMap::from([("[PERSON_1]".into(), "A \"B\"".into())])), true), "{\"to\":\"A \\\"B\\\"\"}");
}

#[test]
fn spans_merge_partial_detections_and_escape_gaps() {
    let mut v = Vault::new();
    v.tag("PERSON", "Kim Minsu");
    let t = "Ask Kim Minsu and Kim Minsuk.";
    let spans = with_vault_spans(t, vec![span(t, "Kim", Label::Person)], &v);
    assert_eq!(spans.len(), 1);
    assert_eq!((spans[0].text(t), spans[0].source), ("Kim Minsu", Source::Vault));
    assert_eq!((spans[0].char_start, spans[0].char_end), (4, 13));
    assert_eq!(apply_redaction_vault(t, &spans, &mut v), "Ask [PERSON_1] and Kim Minsuk.");
    let u = "account [REF_1] cobalt, see [REF_1]";
    let block = Span { label: Label::Secret, source: Source::Block, ..span(u, "account [REF_1] cobalt", Label::Secret) };
    assert_eq!(apply_redaction_vault(u, &[block], &mut v), "[SECRET_1], see \u{ff3b}REF_1]");
    assert_eq!(v.value("[SECRET_1]"), Some("account [REF_1] cobalt"));
    let long = v.tag(&"X".repeat(50), "value one");
    assert_eq!(long, format!("[{}_1]", "X".repeat(32)));
}

#[test]
fn fourth_review_fixes() {
    if skip_timing("fourth_review_fixes") {
        return;
    }
    // every occurrence is tried, overlapping ones too
    let mut v = Vault::new();
    v.tag("PERSON", "Ann Ann");
    assert_eq!(v.hide("xAnn Ann Ann", false), "xAnn [PERSON_1]");
    // word characters are Unicode L, N and M: a circled letter (So) is an edge, a combining mark (Mn) is not
    let mut w = Vault::new();
    w.tag("PERSON", "Alice");
    assert_eq!(w.hide("\u{24B6}Alice", false), "\u{24B6}[PERSON_1]");
    assert_eq!(w.hide("Alice\u{301}", false), "Alice\u{301}");
    // an empty value has no placeholder; a family whose numbers are used up goes on in the next one
    let mut x = Vault::from_entries([VaultEntry { tag: "[PERSON_999999999999]".into(), label: "PERSON".into(), value: "Last One".into() }]).unwrap();
    assert_eq!(x.tag("PERSON", ""), "");
    assert_eq!(x.tag("PERSON", "One More"), "[PERSON__1]");
    assert_eq!(x.tag("PERSON", "And Another"), "[PERSON__2]");
    assert_eq!(x.restore("[PERSON__1] [PERSON_999999999999]"), "One More Last One");
    assert_eq!(x.hide("One More and Last One", false), "[PERSON__1] and [PERSON_999999999999]");
    let mut again = Vault::from_entries(x.entries().to_vec()).unwrap(); // its own entries load again
    assert_eq!(again.tag("PERSON", "A Fourth"), "[PERSON__3]");
    let full = "P".repeat(32);
    let mut w = Vault::from_entries([VaultEntry { tag: format!("[{full}_999999999999]"), label: full.clone(), value: "x".into() }]).unwrap();
    assert_eq!(w.tag(&full, "y"), format!("[{}__1]", "P".repeat(31)));
    let last = format!("A{}", "_".repeat(31)); // a letter and underscores only: on to the next letter
    let mut z = Vault::from_entries([VaultEntry { tag: format!("[{last}_999999999999]"), label: last.clone(), value: "one".into() }]).unwrap();
    let next = format!("[B{}_1]", "_".repeat(31));
    assert_eq!(z.tag(&last, "two"), next);
    assert_eq!(z.restore(&next), "two");
    // after Z it goes on with A; with all 26 used up the value is masked and not put back
    let full = |c: char| format!("{c}{}", "_".repeat(31));
    let mut w = Vault::from_entries([VaultEntry { tag: format!("[{}_999999999999]", full('Z')), label: full('Z'), value: "one".into() }]).unwrap();
    let first = format!("[{}_1]", full('A'));
    assert_eq!(w.tag(&full('Z'), "two"), first);
    assert_eq!(w.restore(&first), "two");
    let all = ('A'..='Z').map(|c| VaultEntry { tag: format!("[{}_999999999999]", full(c)), label: full(c), value: format!("v{c}") });
    let mut none = Vault::from_entries(all).unwrap();
    assert_eq!(none.tag(&full('Q'), "more"), "[Q]");
    assert_eq!(none.restore("[Q]"), "[Q]");
    assert!(Vault::from_entries([VaultEntry { tag: "[PERSON_1000000000000]".into(), label: "PERSON".into(), value: "x".into() }]).is_err());
    // scanning for placeholders is linear in the text
    let brackets = "[".repeat(200_000) + &"[A".repeat(100_000);
    let t = std::time::Instant::now();
    assert!(vault_tags(&brackets).is_empty());
    assert_eq!(escape_tags(&brackets).len(), brackets.len());
    assert!(t.elapsed().as_millis() < 2_000, "tag scan took {:?}", t.elapsed());
}

#[test]
fn fifth_review_fixes() {
    // a value the model found once is masked wherever it occurs again in the same text, on the first call
    let mut v = Vault::new();
    let t = "I met Alice yesterday. Use the word Alice twice, not Alicette.";
    let (out, spans) = redact_with_vault(t, vec![span(t, "Alice", Label::Person)], &mut v);
    assert_eq!(out, "I met [PERSON_1] yesterday. Use the word [PERSON_1] twice, not Alicette.");
    assert_eq!(spans.iter().map(|s| (s.start, s.source)).collect::<Vec<_>>(), vec![(6, Source::Model), (36, Source::Vault)]);
    // what the model found inside a larger merged span is masked where it stands alone
    let mut w = Vault::new();
    w.tag("PERSON", "Alex Kim");
    let u = "Alex Kim Lee wrote. Kim Lee signed. Alex Kim Lee left.";
    let found = Span { start: 5, end: 12, char_start: 5, char_end: 12, utf16_start: 5, utf16_end: 12, label: Label::Person, source: Source::Model };
    let (merged, _) = redact_with_vault(u, vec![found], &mut w);
    assert!(!merged.contains("Alex") && !merged.contains("Kim") && !merged.contains("Lee"), "{merged}");
    assert_eq!(w.restore(&merged), u);
    // a family with digits in its name stays apart from the numbers of another
    let mut x = Vault::from_entries([
        VaultEntry { tag: "[PERSON_1]".into(), label: "PERSON".into(), value: "Alice".into() },
        VaultEntry { tag: "[PERSON_1_99999999999]".into(), label: "PERSON_1".into(), value: "Last".into() },
    ])
    .unwrap();
    assert_eq!(x.tag("PERSON_1", "Next"), "[PERSON_1_100000000000]");
    assert_eq!(x.restore("[PERSON_1] [PERSON_1_100000000000]"), "Alice Next");
    // masking goes on until nothing new is found, however many rounds that takes
    let mut c = Vault::new();
    c.tag("PERSON", "W00");
    let words: Vec<String> = (0..10).map(|i| format!("W{i:02}")).collect();
    let tail = |line: &str| {
        let (a, b) = (line.len() - 5, line.len());
        vec![Span { start: a, end: b, char_start: a, char_end: b, utf16_start: a, utf16_end: b, label: Label::Person, source: Source::Model }]
    };
    let mut line = String::new();
    for n in 2..=words.len() {
        line = words[..n].join(" ");
        assert!(!redact_with_vault(&line, tail(&line), &mut c).0.contains("W0"));
    }
    let (again, _) = redact_with_vault(&line, tail(&line), &mut c);
    assert!(!again.contains("W0"), "{again}");
    assert_eq!(c.restore(&again), line);
}

/// A line with seven hits of [`natives_known`]: "지원님께서" (and "지원금", "지원했다" kept), a Greek name in capitals
/// without accents, a German one with SS and with the capital sharp s, "渡辺" with a variation selector, "김민수" with a
/// combining mark, and two custom values that merge.
fn natives_line() -> String {
    [
        "\u{C9C0}\u{C6D0}\u{B2D8}\u{AED8}\u{C11C} \u{C9C0}\u{C6D0}\u{AE08}\u{C744} \u{C9C0}\u{C6D0}\u{D588}\u{B2E4}; ",
        "\u{39D}\u{399}\u{39A}\u{39F}\u{3A3} \u{3A0}\u{391}\u{3A0}\u{391}\u{394}\u{39F}\u{3A0}\u{39F}\u{3A5}\u{39B}\u{39F}\u{3A3}, ",
        "ANNA WEISS and ANNA WEI\u{1E9E}; \u{6E21}\u{8FBA}\u{E0100}\u{3055}\u{3093}, \u{AE40}\u{BBFC}\u{C218}\u{301}\u{B2D8}, AB-1234-CD. ",
    ]
    .concat()
}

/// The known values of [`natives_line`].
fn natives_known() -> Vault {
    let mut v = Vault::new();
    v.tag("PERSON", "\u{C9C0}\u{C6D0}");
    v.tag("PERSON", "\u{39D}\u{3AF}\u{3BA}\u{3BF}\u{3C2} \u{3A0}\u{3B1}\u{3C0}\u{3B1}\u{3B4}\u{3CC}\u{3C0}\u{3BF}\u{3C5}\u{3BB}\u{3BF}\u{3C2}");
    v.tag("PERSON", "Anna Wei\u{DF}");
    v.tag("PERSON", "\u{6E21}\u{8FBA}");
    v.tag("PERSON", "\u{AE40}\u{BBFC}\u{C218}");
    v.tag("CUSTOMER", "AB-1234");
    v.tag("CUSTOMER", "1234-CD");
    v
}

/// `s` in full-width ASCII (U+FF01 to U+FF5E).
fn full_width(s: &str) -> String {
    s.chars().map(|c| if ('!'..='~').contains(&c) { char::from_u32(c as u32 + 0xFEE0).unwrap() } else { c }).collect()
}

/// Text from code points (conjoining Hangul jamo and the like, kept out of the source as characters).
fn points(cps: &[u32]) -> String {
    cps.iter().map(|&c| char::from_u32(c).unwrap()).collect()
}

fn model_span(start: usize, end: usize) -> Span {
    // (ASCII text in the tests that use it: bytes, code points and UTF-16 units are the same)
    Span { start, end, char_start: start, char_end: end, utf16_start: start, utf16_end: end, label: Label::Person, source: Source::Model }
}

#[test]
fn spellings_keep_one_placeholder() {
    // what the model finds in another spelling of a known value gets that value's placeholder; the vault learns no new value
    let mut v = Vault::new();
    v.tag("PERSON", "Kim Minsu");
    let t = "KIM MINSU wrote to Kim Minsu.";
    let (out, _) = redact_with_vault(t, vec![span(t, "KIM MINSU", Label::Person)], &mut v);
    assert_eq!(out, "[PERSON_1] wrote to [PERSON_1].");
    assert_eq!(v.len(), 1);
    assert_eq!(apply_redaction_vault(t, &[span(t, "KIM MINSU", Label::Person)], &mut v), "[PERSON_1] wrote to Kim Minsu.");
    // a one-word value in another case is another value
    let (out, _) = redact_with_vault("Will will", vec![model_span(5, 9)], &mut v);
    assert_eq!((out.as_str(), v.len()), ("Will [PERSON_2]", 2));
    // spans of values found in a reading point into the text as it is written: full-width letters and NFD Hangul
    let mut w = Vault::new();
    w.tag("EMAIL", "kim@example.com");
    w.tag("PERSON", &points(&[0xAE40, 0xBBFC, 0xC218]));
    let nfd_name = points(&[0x1100, 0x1175, 0x11B7, 0x1106, 0x1175, 0x11AB, 0x1109, 0x116E]);
    let nfd_particle = points(&[0x1102, 0x1175, 0x11B7]);
    let t = format!("{} and {nfd_name}{nfd_particle}", full_width("kim@example.com"));
    let spans = with_vault_spans(&t, vec![], &w);
    let got: Vec<_> = spans.iter().map(|s| (s.text(&t).to_string(), s.char_start, s.char_end, s.utf16_start, s.utf16_end, s.source)).collect();
    assert_eq!(got, vec![(full_width("kim@example.com"), 0, 15, 0, 15, Source::Vault), (nfd_name.clone(), 20, 28, 20, 28, Source::Vault)]);
    assert_eq!(apply_redaction_vault(&t, &spans, &mut w), format!("[EMAIL_1] and [PERSON_1]{nfd_particle}"));
    assert_eq!(w.len(), 2);
    // hide_value: a whole string in another spelling, whatever its length
    let mut x = Vault::new();
    x.tag("PERSON", "Al");
    assert_eq!((x.hide_value("AL"), x.hide_value("al"), x.hide_value(&full_width("Al"))), ("[PERSON_1]".into(), "al".into(), "[PERSON_1]".into()));
}

#[test]
fn model_spans_lose_what_their_boundary_took_and_short_values_stay_where_found() {
    // (1.7.1 review, L3-B1, L3-B2 and L3-B4 in this package; the shared cases have the rest)
    let mut v = Vault::new();
    let t = "page 0 of 7";
    assert_eq!(redact_with_vault(t, vec![span_at(t, 5, 6, Label::DateOfBirth)], &mut v).0, "page [DATE_OF_BIRTH_1] of 7");
    assert_eq!(v.hide_value("0"), "0");
    // a value the application gives is found as a whole value whatever its length
    assert_eq!(v.tag("DATE_OF_BIRTH", "0"), "[DATE_OF_BIRTH_1]");
    assert_eq!(v.hide_value("0"), "[DATE_OF_BIRTH_1]");
    // the offsets of a cut span in every unit
    let u = "\u{d55c} (Kim Minsu).";
    let (out, spans) = redact_with_vault(u, vec![span(u, "(Kim Minsu).", Label::Person)], &mut v);
    assert_eq!(out, "\u{d55c} ([PERSON_1]).");
    assert_eq!((spans[0].text(u), spans[0].char_start, spans[0].char_end, spans[0].utf16_start, spans[0].utf16_end), ("Kim Minsu", 3, 12, 3, 12));
}

// ("growth" in the name: tool/test-all.sh runs it alone, after the suites, so that no other suite distorts the ratios)
#[test]
fn growth_with_vault_spans_and_redact() {
    if skip_timing("growth_with_vault_spans_and_redact") {
        return;
    }
    // n known values found again, each also found in part by the model, and n other spans: one pass over both
    let unit = "Ask Kim Minsu about Lee Jun. ";
    let input = |n: usize| {
        let text = unit.repeat(n);
        let spans: Vec<Span> =
            (0..n).flat_map(|i| [model_span(i * unit.len() + 4, i * unit.len() + 7), model_span(i * unit.len() + 20, i * unit.len() + 27)]).collect();
        (text, spans)
    };
    let mut v = Vault::new();
    v.tag("PERSON", "Kim Minsu");
    assert_linear("with_vault_spans", &[16_000, 32_000, 64_000], |n| {
        let (text, spans) = input(n);
        median_time(|| assert_eq!(with_vault_spans(&text, spans.clone(), &v).len(), 2 * n))
    });
    assert_linear("redact_with_vault", &[2_000, 4_000, 8_000], |n| {
        let (text, spans) = input(n);
        median_time(|| {
            let mut fresh = v.clone();
            assert_eq!(redact_with_vault(&text, spans.clone(), &mut fresh).1.len(), 2 * n);
        })
    });
    // one span a model found with n brackets before its value and n sentence marks and opening brackets after it: each
    // end is cut once (1.7.1 review, L3-B1)
    assert_linear("redact_with_vault, a span's ends", &[8_000, 16_000, 32_000], |n| {
        let text = format!("{}Kim Minsu{}{}", "(".repeat(n), ".,".repeat(n), " (".repeat(n));
        median_time(|| {
            let mut fresh = Vault::new();
            let spans = vec![span_at(&text, 0, text.len(), Label::Person)];
            assert_eq!(redact_with_vault(&text, spans, &mut fresh).1[0].text(&text), "Kim Minsu");
        })
    });
    // the same with the text in full-width letters (a reading with a map back to the text)
    let wide = full_width(unit);
    assert_linear("with_vault_spans, full-width", &[8_000, 16_000, 32_000], |n| {
        let text = wide.repeat(n);
        median_time(|| assert_eq!(with_vault_spans(&text, vec![], &v).len(), n))
    });
    // capitals in other spellings (full Unicode uppercase, Greek without accents, the capital sharp s), a value of two
    // Hangul syllables with particles and inside longer words, values followed by a variation selector and a combining
    // mark, and custom values that merge: seven hits a line
    let lines = |n: usize| natives_line().repeat(n);
    let known = natives_known();
    assert_linear("redact_with_vault, capitals, Hangul and clusters", &[500, 1_000, 2_000], |n| {
        let text = lines(n);
        median_time(|| assert_eq!(redact_with_vault(&text, vec![], &mut known.clone()).1.len(), 7 * n))
    });
    // a known value followed by a run of n combining marks (taken in whole), and a value of two syllables followed by n
    // syllables (another word)
    assert_linear("with_vault_spans, a run of marks", &[160_000, 320_000, 640_000], |n| {
        let text = format!("{}{}{}", "\u{6E21}\u{8FBA}", "\u{301}".repeat(n), "\u{3055}\u{3093}");
        median_time(|| assert_eq!(with_vault_spans(&text, vec![], &known)[0].end, text.len() - 6))
    });
    assert_linear("with_vault_spans, a long word after two syllables", &[10_000, 20_000, 40_000], |n| {
        let text = format!("{} {}", "\u{C9C0}\u{C6D0}".repeat(n), "\u{C9C0}\u{C6D0}\u{AE08}".repeat(n));
        median_time(|| assert_eq!(with_vault_spans(&text, vec![], &known).len(), 0))
    });
    // known values: one text, a vault that holds more and more values (each searched once)
    let text = unit.repeat(200);
    assert_linear("find, known values", &[4_000, 8_000, 16_000], |n| {
        let mut many = Vault::new();
        for i in 0..n {
            many.tag("PERSON", &format!("Person Number{i}"));
        }
        many.tag("PERSON", "Kim Minsu");
        median_time(|| assert_eq!(many.find(&text).len(), 200))
    });
}

// ("growth" in the name: tool/test-all.sh runs it alone, after the suites)
#[test]
fn growth_long_values_that_repeat_themselves() {
    if skip_timing("growth_long_values_that_repeat_themselves") {
        return;
    }
    // a known value of n words that repeats itself, in a text that repeats it: each place where it may start is looked at
    // once, in every spelling the vault searches (1.7.1 gate 7: Swift, Kotlin and Dart compared it from its start at
    // every place; Rust looked for it again from the next place after each one, and copied the value at each place). The
    // vault learns the value (its spellings) and finds it; one hit each: the places overlap and merge.
    let words = |w: &[&str], n: usize| (0..n).map(|i| w[i % w.len()]).collect::<Vec<_>>().join(" ");
    // a value learned from a model's span over the whole text (". . . .Kim Minsu"), found once after long partial matches
    assert_linear("a long value from a span, found once", &[20_000, 40_000, 80_000], |n| {
        let text = format!("{}Kim Minsu{}", " .".repeat(n), ", ".repeat(n));
        let want = format!(" [PERSON_1]{}", ", ".repeat(n));
        let spans = vec![span_at(&text, 0, text.len(), Label::Person)];
        median_time(|| assert_eq!(redact_with_vault(&text, spans.clone(), &mut Vault::new()).0, want))
    });
    type Make = Box<dyn Fn(usize) -> String>;
    let shapes: Vec<(&str, &str, Make, Make, [usize; 3])> = vec![
        ("as it is written", "PERSON", Box::new(move |n| words(&["ha"], n)), Box::new(|n| "ha ".repeat(2 * n)), [32_000, 64_000, 128_000]),
        ("in any ASCII case", "PERSON", Box::new(move |n| words(&["Ha", "ha"], n)), Box::new(|n| "hA Ha ".repeat(n)), [32_000, 64_000, 128_000]),
        (
            "a URL's path as it is written",
            "URL",
            Box::new(|n| "https://x.io/".repeat(n)),
            Box::new(|n| format!("HTTPS://X.IO/{}", "https://x.io/".repeat(2 * n - 1))),
            [8_000, 16_000, 32_000],
        ),
        ("in capitals of another spelling", "PERSON", Box::new(move |n| words(&["Wei\u{DF}"], n)), Box::new(|n| "WEISS ".repeat(2 * n)), [16_000, 32_000, 64_000]),
    ];
    for (name, label, value, text, sizes) in &shapes {
        assert_linear(&format!("a long value that overlaps itself, {name}"), sizes, |n| {
            let (known, t) = (value(n), text(n));
            median_time(|| {
                let mut v = Vault::new();
                v.tag(label, &known);
                assert_eq!(v.find(&t).len(), 1);
            })
        });
    }
}
