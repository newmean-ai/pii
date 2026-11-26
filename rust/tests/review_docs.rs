//! The edges of a span the model found (review round of SDK 1.7.1, L2-B2): what they hold that is not part of the
//! value is left out before the vault learns it, as in the npm and Python packages (js/test/review-docs.test.ts,
//! python/tests/test_review_docs.py hold the same cases).

use newmean_pii::{apply_redaction_vault, redact_with_vault, Label, Source, Span, Vault};

mod common;
use common::{assert_linear, median_time, skip_timing};

fn marked(text: &str, value: &str, source: Source) -> Span {
    let start = text.find(value).unwrap();
    let end = start + value.len();
    Span {
        start,
        end,
        char_start: text[..start].chars().count(),
        char_end: text[..end].chars().count(),
        utf16_start: text[..start].encode_utf16().count(),
        utf16_end: text[..end].encode_utf16().count(),
        label: Label::Person,
        source,
    }
}

// The cases every package shares are in python/tests/vault_cases.json ("redact"); these check what is Rust's own: the
// offsets of the spans left, a block-list span and apply_redaction_vault.

#[test]
fn a_span_left_has_its_char_and_utf16_offsets() {
    for (text, mark, value) in [
        ("담당자 「홍길동」、 다음", "「홍길동」、", "홍길동"),
        ("Contact 서울특별시 강남구 테헤란로 123 (also", "서울특별시 강남구 테헤란로 123 (", "서울특별시 강남구 테헤란로 123"),
        ("𠀀 says \u{201c}Kim Minsu\u{201d}.", "\u{201c}Kim Minsu\u{201d}.", "Kim Minsu"),
    ] {
        let mut vault = Vault::new();
        let (out, spans) = redact_with_vault(text, vec![marked(text, mark, Source::Model)], &mut vault);
        assert_eq!(out, text.replacen(value, "[PERSON_1]", 1), "{text}");
        let s = &spans[0];
        assert_eq!(&text[s.start..s.end], value, "{text}");
        assert_eq!((s.char_start, s.char_end), (text[..s.start].chars().count(), text[..s.end].chars().count()), "{text}");
        assert_eq!((s.utf16_start, s.utf16_end), (text[..s.start].encode_utf16().count(), text[..s.end].encode_utf16().count()), "{text}");
    }
}

#[test]
fn a_block_list_span_keeps_its_edges() {
    let text = "Tiffany & Co. sent it";
    let mut vault = Vault::new();
    let (out, _) = redact_with_vault(text, vec![marked(text, "Tiffany & Co.", Source::Block)], &mut vault);
    assert_eq!(out, "[PERSON_1] sent it");
}

#[test]
fn apply_redaction_vault_learns_the_value_too() {
    let text = "Please look up Kim Minsu.";
    let mut vault = Vault::new();
    assert_eq!(apply_redaction_vault(text, &[marked(text, "Kim Minsu.", Source::Model)], &mut vault), "Please look up [PERSON_1].");
    assert_eq!(vault.entries()[0].value, "Kim Minsu");
}

/// A name and the text of size n.
type Shape = (&'static str, fn(usize) -> String);

// Doubling test (run alone by tool/test-all.sh: "growth" in the name): leaving out what a span's edges hold takes time in
// proportion to the span, however many brackets, quotation marks, stops or spaces it has to look past.
#[test]
fn span_edges_growth() {
    if skip_timing("span_edges_growth") {
        return;
    }
    let shapes: [Shape; 6] = [
        ("closing brackets after the value", |n| format!("Kim Minsu{}", ")".repeat(n))),
        ("opening brackets before the value", |n| format!("{}Kim Minsu", "(".repeat(n))),
        ("pairs of brackets around the value", |n| format!("{}Kim Minsu{}", "(".repeat(n), ")".repeat(n))),
        ("quotation marks around the value", |n| format!("{}Kim Minsu{}", '"'.to_string().repeat(n), "'".repeat(n))),
        // (commas before the value: a "." at the start of a value stays)
        ("stops and spaces around the value", |n| format!("{}Kim Minsu{}", " ,".repeat(n), ", ".repeat(n))),
        ("brackets that are not one pair", |n| format!("({}Kim Minsu{})", ")(".repeat(n), ")(".repeat(n))),
    ];
    for (name, make) in shapes {
        assert_linear(&format!("span edges, {name}"), &[50_000, 100_000, 200_000], |n| {
            let text = make(n);
            let found = vec![Span {
                start: 0,
                end: text.len(),
                char_start: 0,
                char_end: text.chars().count(),
                utf16_start: 0,
                utf16_end: text.encode_utf16().count(),
                label: Label::Person,
                source: Source::Model,
            }];
            median_time(|| {
                let mut vault = Vault::new();
                assert!(redact_with_vault(&text, found.clone(), &mut vault).0.contains("[PERSON_1]"));
            })
        });
    }
}
