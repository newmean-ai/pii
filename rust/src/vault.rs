//! A vault: the local map from numbered placeholders back to the values they replace, so that masking can be undone in a
//! model's answer. It is an ordinary in-memory value; nothing in it is sent anywhere. The placeholders and the search
//! for known values are those of the npm package's `createVault()` and the Python package's `Vault` (shared cases:
//! `python/tests/vault_cases.json`); the other spellings the npm and Python middlewares also read (escapes, URL forms,
//! numbers written another way, other white space) are not read here.
//!
//! ```no_run
//! # use newmean_pii::{Redactor, Vault};
//! # let redactor = Redactor::from_dir("models/pii-medium")?;
//! let mut vault = Vault::new(); // one per user or conversation, never shared between users
//! let masked = redactor.redact_vault("Call Kim Minsu on 010-1234-5678", &mut vault)?;
//! // masked.text == "Call [PERSON_1] on [PHONE_1]"
//! assert_eq!(vault.restore("I called [PERSON_1]."), "I called Kim Minsu.");
//! # Ok::<(), newmean_pii::Error>(())
//! ```
//!
//! The same value always gets the same placeholder within a vault, and a value the vault holds is masked again wherever
//! it reappears in later text, also where the model would miss it there (spans with [`crate::Source::Vault`]): at word
//! edges, 3 characters or more (2 in scripts written without spaces, and two Hangul syllables, after which only
//! particles, 님, 씨 or the copula may follow in the same word). It is found as it is written and also: with full-width
//! ASCII (U+FF01 to U+FF5E) and U+3000 read as ASCII; with Hangul written as conjoining jamo (NFD) read as syllables; in
//! capitals (the full Unicode uppercase, `Weiß` as `WEISS`; a Greek value also without its accents, as Greek writes
//! capitals; a sharp s also as the capital sharp s); and, for an e-mail address, a user name, a URL's scheme and host
//! and a value of two or more words, in any ASCII case. A one-word value in another case is another word (`Will` and
//! `will`). The hit carries the known value, so one value keeps one placeholder however it is written, and it takes in
//! the marks, variation selectors and joiners right after it, so that it never ends inside a grapheme cluster.
use std::collections::{HashMap, HashSet};

use crate::types::{Label, Source, Span};
use crate::word_ranges::{MARK_RANGES, WORD_RANGES};

/// One placeholder and the value behind it.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct VaultEntry {
    /// The placeholder, e.g. `"[PERSON_1]"`.
    pub tag: String,
    /// Label family the placeholder was made for (`PERSON_L2` shares `PERSON`'s numbers).
    pub label: String,
    /// The original text.
    pub value: String,
}

/// Where a known value occurs: `start..end` in bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VaultHit {
    pub start: usize,
    pub end: usize,
    pub label: String,
    pub value: String,
}

/// Which placeholders a restore may put back.
#[derive(Clone, Copy, Debug, Default)]
pub enum Only<'a> {
    /// Every placeholder of the vault, with its current value.
    #[default]
    All,
    /// Only these placeholders, with their current values.
    Tags(&'a HashSet<String>),
    /// Only these placeholders, with these values (a snapshot from [`Vault::bindings`]).
    Values(&'a HashMap<String, String>),
}

/// Longest label family; a placeholder is at most `[` + 32 + `_` + 12 digits + `]` = 47 bytes.
const MAX_FAMILY: usize = 32;
const MAX_TAG: usize = MAX_FAMILY + 15;
const MAX_NUMBER: u64 = 999_999_999_999;

/// The family that goes on when the numbers of `family` are used up (which takes an imported placeholder with the
/// highest number): one more `_` at its end, `PERSON` → `PERSON_`, so that its placeholders stay apart
/// (`[PERSON__1]`). A family of full length gets its last other character turned into `_` instead, and one that is a
/// letter and underscores only goes on with the next letter (after Z, with A).
fn next_family(family: &str) -> String {
    if family.len() < MAX_FAMILY {
        return format!("{family}_");
    }
    let b = family.as_bytes();
    if let Some(i) = (1..b.len()).rev().find(|&i| b[i] != b'_') {
        return format!("{}_{}", &family[..i], &family[i + 1..]);
    }
    format!("{}{}", if b[0] < b'Z' { (b[0] + 1) as char } else { 'A' }, &family[1..])
}
/// How many families are tried after the first: at most 31 steps to a letter and underscores, then the 25 other letters.
const FAMILY_STEPS: usize = 56;
/// U+FF3B FULLWIDTH LEFT SQUARE BRACKET, which [`escape_tags`] puts in front of placeholder-like text.
const ESCAPED_BRACKET: char = '\u{ff3b}';

/// Parses a placeholder at the start of `s`. The grammar is the same in every package: `[` + family + `_` + number +
/// `]`, where the family is an ASCII capital letter followed by capitals, digits and underscores (32 characters at most)
/// and the number is 1 to 12 ASCII digits. Longer look-alikes are ordinary text: neither escaped nor restored. Returns
/// its length in bytes, the family and the number.
fn parse_tag(s: &str) -> Option<(usize, &str, &str)> {
    let b = s.as_bytes();
    if b.first() != Some(&b'[') {
        return None;
    }
    // one pass that stops at the first byte a placeholder cannot hold, and at the longest placeholder
    let mut close = None;
    for (i, &c) in b.iter().enumerate().skip(1).take(MAX_TAG) {
        if c == b']' {
            close = Some(i);
            break;
        }
        if !(c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_') {
            return None;
        }
    }
    let close = close?;
    let inner = &s[1..close];
    let us = inner.rfind('_')?;
    let (fam, num) = (&inner[..us], &inner[us + 1..]);
    let sized = fam.len() <= MAX_FAMILY && (1..=12).contains(&num.len());
    if sized && fam.as_bytes().first().is_some_and(u8::is_ascii_uppercase) && num.bytes().all(|c| c.is_ascii_digit()) {
        Some((close + 1, fam, num))
    } else {
        None
    }
}

/// `fam` without trailing `_` + digits groups, so that `[fam]` is never a numbered placeholder.
fn unnumbered(fam: &str) -> &str {
    let b = fam.as_bytes();
    let mut end = b.len();
    loop {
        let mut i = end;
        while i > 0 && b[i - 1].is_ascii_digit() {
            i -= 1;
        }
        if i > 0 && b[i - 1] == b'_' {
            end = i - 1;
        } else {
            return &fam[..end];
        }
    }
}

/// Every placeholder in `text` as (byte offset, length), in order.
fn tag_positions(text: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(off) = text[i..].find('[') {
        let at = i + off;
        match parse_tag(&text[at..]) {
            Some((len, _, _)) => {
                out.push((at, len));
                i = at + len;
            }
            None => i = at + 1,
        }
    }
    out
}

/// The vault placeholders in `text`, in order of appearance (duplicates kept).
pub fn vault_tags(text: &str) -> Vec<String> {
    tag_positions(text).into_iter().map(|(at, len)| text[at..at + len].to_string()).collect()
}

/// `text` with every placeholder-like part that was already in it (`"[PERSON_1]"`) turned into `"［PERSON_1]"`, so that a
/// model and a restore can tell it from the placeholders the SDK puts in.
pub fn escape_tags(text: &str) -> String {
    if !text.contains('[') {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len() + 8);
    let mut last = 0;
    for (at, _) in tag_positions(text) {
        out.push_str(&text[last..at]);
        out.push(ESCAPED_BRACKET);
        last = at + 1;
    }
    out.push_str(&text[last..]);
    out
}

// Scripts written without spaces between words (letters and marks of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese,
// Javanese, Han, Hiragana and Katakana), and Hangul: the same code point ranges as the npm and Python packages.
const NO_SPACE: &[(u32, u32)] = &[
    (0x0E01, 0x0E3A), (0x0E40, 0x0E4E), // Thai
    (0x0E81, 0x0ECF), // Lao
    (0x1000, 0x103F), (0x1050, 0x108F), (0x109A, 0x109D), (0xA9E0, 0xA9EF), (0xA9FA, 0xA9FE), (0xAA60, 0xAA7F), // Myanmar
    (0x1780, 0x17D3), (0x17D7, 0x17D7), (0x17DC, 0x17DD), (0x19E0, 0x19FF), // Khmer
    (0x1A20, 0x1A5E), (0x1A60, 0x1A7C), (0x1A7F, 0x1A7F), (0x1AA7, 0x1AA7), // Tai Tham
    (0x1B00, 0x1B4C), // Balinese
    (0xA980, 0xA9C0), (0xA9CF, 0xA9CF), // Javanese
    (0x2E80, 0x2FDF), (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B), // Han radicals, marks
    (0x3041, 0x3096), (0x309D, 0x309F), // Hiragana
    (0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF6F), (0xFF71, 0xFF9D), // Katakana
    (0x1B000, 0x1B16F), // kana supplement and extensions
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x3347F), // Han, to Extension J
];
// The marks among them (vowel signs, tone marks and the like of Thai, Lao, Myanmar, Khmer, Tai Tham, Balinese and
// Javanese; Unicode general category M): a mark belongs to the letter before it, so the place before one is no word edge.
const NO_SPACE_MARKS: &[(u32, u32)] = &[
    (0x0E31, 0x0E31), (0x0E34, 0x0E3A), (0x0E47, 0x0E4E), // Thai
    (0x0EB1, 0x0EB1), (0x0EB4, 0x0EBC), (0x0EC8, 0x0ECE), // Lao
    (0x102B, 0x103E), (0x1056, 0x1059), (0x105E, 0x1060), (0x1062, 0x1064), (0x1067, 0x106D), (0x1071, 0x1074),
    (0x1082, 0x108D), (0x108F, 0x108F), (0x109A, 0x109D), (0xA9E5, 0xA9E5), (0xAA7B, 0xAA7D), // Myanmar
    (0x17B4, 0x17D3), (0x17DD, 0x17DD), // Khmer
    (0x1A55, 0x1A5E), (0x1A60, 0x1A7C), (0x1A7F, 0x1A7F), // Tai Tham
    (0x1B00, 0x1B04), (0x1B34, 0x1B44), // Balinese
    (0xA980, 0xA983), (0xA9B3, 0xA9C0), // Javanese
];
const HANGUL: &[(u32, u32)] = &[(0x1100, 0x11FF), (0x3131, 0x318E), (0xA960, 0xA97F), (0xAC00, 0xD7A3), (0xD7B0, 0xD7FF), (0xFFA0, 0xFFDC)];

fn in_ranges(r: &[(u32, u32)], c: char) -> bool {
    let cp = c as u32;
    r.iter().any(|&(a, b)| cp >= a && cp <= b)
}
fn no_space(c: char) -> bool {
    in_ranges(NO_SPACE, c)
}
fn no_space_mark(c: char) -> bool {
    in_ranges(NO_SPACE_MARKS, c)
}
fn hangul(c: char) -> bool {
    in_ranges(HANGUL, c)
}
/// A precomposed Hangul syllable (U+AC00 to U+D7A3).
fn syllable(c: char) -> bool {
    (0xAC00..=0xD7A3).contains(&(c as u32))
}
/// White space as Unicode has it (the same set in every package).
fn is_space(c: char) -> bool {
    matches!(c as u32, 0x09..=0x0D | 0x20 | 0x85 | 0xA0 | 0x1680 | 0x2000..=0x200A | 0x2028 | 0x2029 | 0x202F | 0x205F | 0x3000)
}

/// What a value of two Hangul syllables may have right after it in the same word, one piece after another to the end of
/// the run of syllables: particles, the honorifics 님 and 씨, and the copula. Any other syllable makes a longer word
/// ("지원금", "정수기"), and a two-syllable name is often a word too. The same list in every package.
const KO_TAIL: &[&str] = &[
    "가", "같이", "고", "과", "까지", "께", "께서", "나", "나마", "네", "는", "니까", "님", "다", "대로", "더러", "도", "든", "든지", "라고",
    "라는", "라도", "라서", "랑", "로", "로서", "로써", "를", "마다", "마저", "만", "만큼", "며", "밖에", "보다", "부터", "씨", "아", "야",
    "어", "었", "에", "에게", "에게서", "에서", "여", "였", "예요", "와", "요", "으로", "으로서", "으로써", "은", "을", "의", "이", "이면",
    "인", "인데", "입니다", "조차", "죠", "지", "지만", "처럼", "한테", "한테서",
];
/// The longest run of syllables [`ko_tail`] reads; a longer one is another word.
const KO_TAIL_MAX: usize = 8;

/// Whether the Hangul syllables at the start of `rest` (what follows a value of two syllables) are pieces of [`KO_TAIL`],
/// one after another to the end of the run.
fn ko_tail(rest: &str) -> bool {
    let run: Vec<char> = rest.chars().take_while(|&c| syllable(c)).take(KO_TAIL_MAX + 1).collect();
    if run.len() > KO_TAIL_MAX {
        return false;
    }
    // ok[i]: run[i..] is pieces of the list
    let mut ok = vec![false; run.len() + 1];
    ok[run.len()] = true;
    for i in (0..run.len()).rev() {
        ok[i] = KO_TAIL.iter().any(|p| {
            let n = p.chars().count();
            i + n <= run.len() && ok[i + n] && p.chars().eq(run[i..i + n].iter().copied())
        });
    }
    ok[0]
}

/// What joins the character before it into one grapheme cluster, so that a hit takes it in ("渡辺" and a variation
/// selector, "김민수" and a combining mark): marks (general category M), the zero width joiner and non-joiner, conjoining
/// Hangul vowels and finals, the half-width katakana sound marks, emoji modifiers and tag characters. The same set in
/// every package.
fn joins(c: char) -> bool {
    let cp = c as u32;
    matches!(cp, 0x200C | 0x200D | 0x1160..=0x11FF | 0xD7B0..=0xD7FF | 0xFF9E | 0xFF9F | 0x1F3FB..=0x1F3FF | 0xE0020..=0xE007F)
        || MARK_RANGES.binary_search_by(|&(a, b)| if cp < a { std::cmp::Ordering::Greater } else if cp > b { std::cmp::Ordering::Less } else { std::cmp::Ordering::Equal }).is_ok()
}

/// `end` moved past what joins the character before it (see [`joins`]).
fn widen(text: &str, end: usize) -> usize {
    end + text[end..].chars().take_while(|&c| joins(c)).map(char::len_utf8).sum::<usize>()
}

/// Greek capitals that carry an accent or a breathing, as ranges (first, last, the capital without it): the tonos
/// capitals, the polytonic ones and those with varia or oxia (the dialytika is kept). From the Unicode decompositions
/// (U+0300, U+0301, U+0313, U+0314, U+0342, U+0343 left out); the same table in every package.
const GREEK_BARE: &[(u32, u32, u32)] = &[
    (0x386, 0x386, 0x391), (0x388, 0x388, 0x395), (0x389, 0x389, 0x397), (0x38A, 0x38A, 0x399), (0x38C, 0x38C, 0x39F),
    (0x38E, 0x38E, 0x3A5), (0x38F, 0x38F, 0x3A9), (0x3D3, 0x3D3, 0x3D2), (0x1F08, 0x1F0F, 0x391), (0x1F18, 0x1F1D, 0x395),
    (0x1F28, 0x1F2F, 0x397), (0x1F38, 0x1F3F, 0x399), (0x1F48, 0x1F4D, 0x39F), (0x1F59, 0x1F59, 0x3A5), (0x1F5B, 0x1F5B, 0x3A5),
    (0x1F5D, 0x1F5D, 0x3A5), (0x1F5F, 0x1F5F, 0x3A5), (0x1F68, 0x1F6F, 0x3A9), (0x1FBA, 0x1FBB, 0x391), (0x1FC8, 0x1FC9, 0x395),
    (0x1FCA, 0x1FCB, 0x397), (0x1FDA, 0x1FDB, 0x399), (0x1FEA, 0x1FEB, 0x3A5), (0x1FEC, 0x1FEC, 0x3A1), (0x1FF8, 0x1FF9, 0x39F),
    (0x1FFA, 0x1FFB, 0x3A9),
];

/// A Greek letter (or a Coptic one of the Greek block).
fn greek(c: char) -> bool {
    matches!(c as u32, 0x370..=0x3FF | 0x1F00..=0x1FFF)
}

/// `up` (a value in capitals) as Greek writes capitals: without accents and breathings on Greek letters, the dialytika
/// kept ("ΝΊΚΟΣ" as "ΝΙΚΟΣ"); `None` when `up` has no Greek letter.
fn greek_capitals(up: &str) -> Option<String> {
    if !up.chars().any(greek) {
        return None;
    }
    let mut out = String::with_capacity(up.len());
    let mut after_greek = false; // the last letter that is not a combining mark is Greek
    for c in up.chars() {
        let mut cp = c as u32;
        if (0x300..=0x36F).contains(&cp) {
            if after_greek {
                if matches!(cp, 0x300 | 0x301 | 0x313 | 0x314 | 0x342 | 0x343) {
                    continue;
                }
                if cp == 0x344 {
                    cp = 0x308; // (dialytika and tonos: the dialytika stays)
                }
                if cp == 0x308 && (out.ends_with('\u{399}') || out.ends_with('\u{3A5}')) {
                    let base = out.pop().unwrap();
                    out.push(if base == '\u{399}' { '\u{3AA}' } else { '\u{3AB}' });
                    continue;
                }
            }
            out.extend(char::from_u32(cp));
            continue;
        }
        after_greek = greek(c);
        let bare = GREEK_BARE.iter().find(|&&(a, b, _)| cp >= a && cp <= b).map_or(c, |&(_, _, to)| char::from_u32(to).unwrap_or(c));
        out.push(bare);
    }
    Some(out)
}

/// The spellings of a value in capitals that `find` also matches, each way a language writes them: its full Unicode
/// uppercase ("WEISS" for "Weiß", "EMINE ŞAHIN"), for a value with "i" the Turkish and Azerbaijani capitals with "İ"
/// ("EMİNE ŞAHİN"), for a Greek value the capitals without accents and breathings ("ΕΛΕΝΗ" for "Ελένη"), and for a value
/// with a sharp s the capitals with the capital sharp s ("WEIẞ"). Spellings that are `read` itself, or that the caseless
/// match covers, are left out. The same spellings as the other packages.
fn capitals(read: &str, caseless: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let up = read.to_uppercase();
    let covered = caseless == read.len() && up == read.to_ascii_uppercase();
    let mut add = |s: String| {
        if s != read && !out.contains(&s) {
            out.push(s);
        }
    };
    if !covered {
        add(up.clone());
    }
    if let Some(g) = greek_capitals(&up) {
        add(g);
    }
    if read.contains('i') {
        add(read.replace('i', "\u{130}").to_uppercase());
    }
    if read.contains('\u{DF}') {
        add(read.replace('\u{DF}', "\u{1E9E}").to_uppercase());
    }
    out
}

/// Whether `c` changes in the vault's reading of a text (full-width ASCII, U+3000, a conjoining Hangul vowel or final).
fn read_differently(c: char) -> bool {
    matches!(c as u32, 0xFF01..=0xFF5E | 0x3000 | 0x1161..=0x1175 | 0x11A8..=0x11C2)
}

/// The text as the vault reads it: full-width ASCII (U+FF01 to U+FF5E) and U+3000 as ASCII, and Hangul written as
/// conjoining jamo (NFD, as macOS file names have it) composed into syllables, as NFC composes them. `map[i]` is where
/// the character that starts at byte `i` of the reading starts in the text, and `map[len]` is the text's length; there
/// is no map when the reading is the text.
struct Reading {
    text: String,
    map: Option<Vec<usize>>,
}

impl Reading {
    /// The place in the text of a place in the reading.
    fn at(&self, i: usize) -> usize {
        self.map.as_ref().map_or(i, |m| m[i])
    }
}

fn read(text: &str) -> Reading {
    if !text.chars().any(read_differently) {
        return Reading { text: text.to_string(), map: None };
    }
    let mut out = String::with_capacity(text.len());
    let mut map = Vec::with_capacity(text.len() + 1);
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let mut k = 0;
    while k < chars.len() {
        let (at, c) = chars[k];
        let cp = c as u32;
        let next = |j: usize| chars.get(k + j).map_or(0, |&(_, c)| c as u32);
        let (read, used) = match cp {
            0xFF01..=0xFF5E => (cp - 0xFEE0, 1),
            0x3000 => (0x20, 1),
            // a leading consonant and a vowel, and a final consonant after them
            0x1100..=0x1112 if (0x1161..=0x1175).contains(&next(1)) => {
                let lv = 0xAC00 + ((cp - 0x1100) * 21 + (next(1) - 0x1161)) * 28;
                if (0x11A8..=0x11C2).contains(&next(2)) {
                    (lv + next(2) - 0x11A7, 3)
                } else {
                    (lv, 2)
                }
            }
            // a syllable without a final consonant, and a final consonant after it
            0xAC00..=0xD7A3 if (cp - 0xAC00) % 28 == 0 && (0x11A8..=0x11C2).contains(&next(1)) => (cp + next(1) - 0x11A7, 2),
            _ => (cp, 1),
        };
        let r = char::from_u32(read).unwrap_or(c);
        for _ in 0..r.len_utf8() {
            map.push(at);
        }
        out.push(r);
        k += used;
    }
    map.push(text.len());
    Reading { text: out, map: Some(map) }
}

/// A value of two or more words: runs of word characters with white space between them.
fn words(v: &str) -> bool {
    v.split(is_space).filter(|w| w.chars().any(is_word)).nth(1).is_some()
}

/// How many bytes of `v` (from its start) are matched in any ASCII case: all of an e-mail address, a user name and a
/// value of two or more words; a URL's scheme and host; nothing of other values.
fn caseless(label: &str, v: &str) -> usize {
    if label == "EMAIL" || label == "USERNAME" || words(v) {
        return v.len();
    }
    if label != "URL" {
        return 0;
    }
    let b = v.as_bytes();
    let mut host = 0;
    let scheme = b.iter().position(|&c| !(c.is_ascii_alphanumeric() || matches!(c, b'+' | b'.' | b'-')));
    if let Some(i) = scheme {
        if i > 0 && b[0].is_ascii_alphabetic() && b[i..].starts_with(b"://") {
            host = i + 3;
        }
    }
    host + b[host..].iter().position(|&c| matches!(c, b'/' | b'?' | b'#')).unwrap_or(b.len() - host)
}

/// A known value as the vault searches for it.
#[derive(Clone, Debug)]
struct Known {
    /// Index of its first entry.
    entry: usize,
    /// The value as the vault reads it (see [`read`]).
    read: String,
    /// The bytes of `read`, from its start, that match in any ASCII case.
    caseless: usize,
    /// `read` in ASCII lower case, when `caseless` is not 0.
    lower: String,
    /// `read` in capitals, in each spelling that is another one and that the caseless match does not cover (see
    /// [`capitals`]).
    uppers: Vec<String>,
    findable: bool,
}

impl Known {
    fn new(entry: usize, label: &str, value: &str) -> Known {
        let read = read(value).text;
        let caseless = caseless(label, &read);
        let lower = if caseless > 0 { read.to_ascii_lowercase() } else { String::new() };
        let uppers = capitals(&read, caseless);
        let findable = findable(&read);
        Known { entry, read, caseless, lower, uppers, findable }
    }

    /// Whether `s` (a reading) is this value in a spelling the vault matches.
    fn is(&self, s: &str) -> bool {
        let n = self.caseless;
        s == self.read
            || self.uppers.iter().any(|u| u == s)
            || (n > 0 && s.len() == self.read.len() && s.is_char_boundary(n) && s[..n].eq_ignore_ascii_case(&self.read[..n]) && s[n..] == self.read[n..])
    }
}
/// Letters, numbers, marks and `_`: Unicode general categories L, N and M, as in the other packages (the table is
/// generated, see tool/gen_word_ranges.py).
fn is_word(c: char) -> bool {
    let cp = c as u32;
    c == '_' || WORD_RANGES.binary_search_by(|&(a, b)| if cp < a { std::cmp::Ordering::Greater } else if cp > b { std::cmp::Ordering::Less } else { std::cmp::Ordering::Equal }).is_ok()
}

/// Long enough to be found again at word edges: 3 characters, or 2 when one is in a script written without spaces or
/// both are Hangul syllables (Korean particles may follow them).
fn findable(v: &str) -> bool {
    let n = v.chars().count();
    n >= 3 || (n == 2 && (v.chars().any(no_space) || v.chars().all(syllable)))
}

// What a span's edges can hold that is not part of the value (all in the basic plane): white space (Unicode
// White_Space); sentence punctuation (. , ; : ! ? and the ellipsis, ideographic and full-width forms); brackets and
// quotation marks, each with its partner. As in the npm and Python packages.
const EDGE_SPACE: [u32; 25] = [
    0x9, 0xA, 0xB, 0xC, 0xD, 0x20, 0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008,
    0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
];
const EDGE_STOP: [u32; 15] = [0x2E, 0x2C, 0x3B, 0x3A, 0x21, 0x3F, 0x2026, 0x3001, 0x3002, 0xFF01, 0xFF0C, 0xFF0E, 0xFF1A, 0xFF1B, 0xFF1F];
const BRACKETS: [(u32, u32); 17] = [
    (0x28, 0x29), (0x5B, 0x5D), (0x7B, 0x7D), (0x3C, 0x3E), (0x201C, 0x201D), (0x2018, 0x2019), (0xAB, 0xBB), (0x300C, 0x300D),
    (0x300E, 0x300F), (0xFF08, 0xFF09), (0xFF3B, 0xFF3D), (0xFF5B, 0xFF5D), (0x3008, 0x3009), (0x300A, 0x300B), (0x3010, 0x3011),
    (0xFF1C, 0xFF1E), (0x3014, 0x3015),
];
const EDGE_QUOTE: [u32; 3] = [0x22, 0x27, 0x60]; // " ' and `, their own partners

/// Whether `c` is an opening bracket.
fn opens(c: char) -> bool {
    BRACKETS.iter().any(|p| p.0 == c as u32)
}

/// For a closing bracket, its opening one.
fn opener_of(c: char) -> Option<char> {
    BRACKETS.iter().find(|p| p.1 == c as u32).and_then(|p| char::from_u32(p.0))
}

/// A date as ISO 8601 writes it, followed by "T" and a digit: the start of an ISO timestamp (`v` from its start).
fn stamp(v: &[u8]) -> bool {
    let d = |i: usize| v[i].is_ascii_digit();
    v.len() >= 12 && (0..4).all(d) && v[4] == b'-' && d(5) && d(6) && v[7] == b'-' && d(8) && d(9) && matches!(v[10], b'T' | b't') && d(11)
}

/// `[start, end)` (bytes) of `text`, a span a detector found with `label`, without what its edges hold that is not part
/// of the value: a model can mark "Kim Minsu." at the end of a sentence, an address with the " (" after it, a name with
/// the quotation marks around it or a bytes literal's "b'", and the vault would learn that as the value. At either edge
/// white space goes. Then, except in a SECRET (a password can end with "!" or hold brackets): sentence punctuation (at
/// the start not a ".", and at the end of an ORG not its ".": "Tiffany & Co."); a bracket whose partner is not in the
/// span, a bracket that opens at the end or closes at the start; a straight quotation mark that is the only one of its
/// kind left in the span; a pair of brackets, or the only two of one straight quotation mark, around all the rest; and
/// the "b'" of a Python bytes literal at the start. A date of birth that goes on into the time of its ISO timestamp
/// ("1990-01-02T00") is cut at the date, the edge where the vault finds a known date in a timestamp. A bracket whose
/// partner is inside stays ("(02) 1234-5678"). `None` when nothing but white space and punctuation is left; the span
/// without its white space when what is left is too short to be found again ([`findable`]). One pass over the span (the
/// bracket partners in a vector): time in proportion to its length. The same in every package.
fn value_edges(text: &str, start: usize, end: usize, label: Label) -> Option<(usize, usize)> {
    // the partner of each bracket in the span (one pass: a stack for each kind of bracket), and how many of each straight
    // quotation mark the span holds, kept as its edges go
    let mut partner = vec![usize::MAX; end - start]; // (at i - start; usize::MAX: none)
    let mut opened: HashMap<char, Vec<usize>> = HashMap::new();
    let mut quotes: HashMap<char, usize> = HashMap::new();
    for (i, c) in text[start..end].char_indices() {
        let i = start + i;
        if opens(c) {
            opened.entry(c).or_default().push(i);
        } else if let Some(o) = opener_of(c) {
            if let Some(j) = opened.get_mut(&o).and_then(|stack| stack.pop()) {
                partner[i - start] = j;
                partner[j - start] = i;
            }
        } else if EDGE_QUOTE.contains(&(c as u32)) {
            *quotes.entry(c).or_default() += 1;
        }
    }
    let quote = |quotes: &HashMap<char, usize>, c: char| if EDGE_QUOTE.contains(&(c as u32)) { quotes.get(&c).copied().unwrap_or(0) } else { 0 };
    let space = |c: &char| EDGE_SPACE.contains(&(*c as u32));
    let (mut a, mut b) = (start, end);
    while let Some(c) = text[a..b].chars().next_back().filter(space) {
        b -= c.len_utf8();
    }
    while let Some(c) = text[a..b].chars().next().filter(space) {
        a += c.len_utf8();
    }
    let (ws, we) = (a, b);
    while a < b && label != Label::Secret {
        let last = text[a..b].chars().next_back().unwrap_or(' ');
        let at_last = b - last.len_utf8();
        if space(&last)
            || (EDGE_STOP.contains(&(last as u32)) && !(last == '.' && label == Label::Org))
            || opens(last) // a bracket that opens at the end
            || (opener_of(last).is_some() && !(partner[at_last - start] != usize::MAX && partner[at_last - start] >= a)) // one that closes what the span does not open
            || quote(&quotes, last) == 1
        {
            quotes.remove(&last);
            b = at_last;
            continue;
        }
        let first = text[a..b].chars().next().unwrap_or(' ');
        if space(&first)
            || (EDGE_STOP.contains(&(first as u32)) && first != '.')
            || opener_of(first).is_some() // a bracket that closes at the start
            || (opens(first) && partner[a - start] >= b) // one that opens what the span does not close (usize::MAX: none)
            || quote(&quotes, first) == 1
        {
            quotes.remove(&first);
            a += first.len_utf8();
            continue;
        }
        // a pair around all the rest
        if at_last > a && ((opens(first) && partner[a - start] == at_last) || (last == first && quote(&quotes, first) == 2)) {
            quotes.remove(&first);
            a += first.len_utf8();
            b = at_last;
            continue;
        }
        // the "b'" of a Python bytes literal (not a "b" at the end of a word: "Bob'")
        let q = text[a..b].chars().nth(1).filter(|q| EDGE_QUOTE.contains(&(*q as u32)));
        if let (Some(q), 'b') = (q, first) {
            if !text[..a].chars().next_back().is_some_and(is_word) {
                if let Some(k) = quotes.get_mut(&q) {
                    *k -= 1;
                }
                a += 2;
                continue;
            }
        }
        break;
    }
    if label == Label::DateOfBirth && b - a > 11 && stamp(&text.as_bytes()[a..b]) {
        b = a + 10;
    }
    if a >= b {
        return None;
    }
    if (a, b) == (ws, we) || findable(&text[a..b]) {
        Some((a, b))
    } else {
        Some((ws, we))
    }
}

/// `spans` of `text` with [`value_edges`], for the vault to learn; a span with nothing left is left out. A block-list
/// span (its entry is exact) and a known value's span stay as they are.
fn value_spans(text: &str, spans: Vec<Span>) -> Vec<Span> {
    spans
        .into_iter()
        .filter_map(|s| {
            if matches!(s.source, Source::Block | Source::Vault) || s.start >= s.end || text.get(s.start..s.end).is_none() {
                return Some(s);
            }
            let (a, b) = value_edges(text, s.start, s.end, s.label)?;
            if (a, b) == (s.start, s.end) {
                return Some(s);
            }
            let (head, tail) = (&text[s.start..a], &text[b..s.end]);
            Some(Span {
                start: a,
                end: b,
                char_start: s.char_start + head.chars().count(),
                char_end: s.char_end - tail.chars().count(),
                utf16_start: s.utf16_start + head.encode_utf16().count(),
                utf16_end: s.utf16_end - tail.encode_utf16().count(),
                ..s
            })
        })
        .collect()
}

/// A number as JSON writes one, with a sign in front or without ("4524", "-4.524e3", "+8210"; not "04524"): what an
/// exponent can be.
fn json_number(v: &str) -> bool {
    let b = v.as_bytes();
    let mut i = usize::from(matches!(b.first(), Some(b'+' | b'-')));
    let digits = |i: &mut usize| {
        let from = *i;
        while *i < b.len() && b[*i].is_ascii_digit() {
            *i += 1;
        }
        *i > from
    };
    match b.get(i) {
        Some(b'0') => i += 1,
        Some(b'1'..=b'9') => {
            digits(&mut i);
        }
        _ => return false,
    }
    if b.get(i) == Some(&b'.') {
        i += 1;
        if !digits(&mut i) {
            return false;
        }
    }
    if matches!(b.get(i), Some(b'e' | b'E')) {
        i += 1;
        if matches!(b.get(i), Some(b'+' | b'-')) {
            i += 1;
        }
        if !digits(&mut i) {
            return false;
        }
    }
    i == b.len()
}

/// Word edges: a value is not found inside a longer word. A letter of a script written without spaces (Han, kana, Thai,
/// ...) on either side of the boundary is an edge ("注文ZX-88213-Kは", "CEO王伟来了"), but not a mark of such a script
/// after a value that ends in one (a Thai vowel sign: "สม" is not found in "สมัคร"). Where Hangul
/// meets other letters or digits is an edge, Korean particles may follow a Hangul value, and Hangul right before a Hangul
/// value is not an edge. After a value of two Hangul syllables, more syllables are an edge only as particles, 님, 씨 or
/// the copula ([`KO_TAIL`]): "지원님께" and "지원이" are the name, "지원금" is another word.
fn at_edges(text: &str, start: usize, end: usize, v: &str) -> bool {
    let before = text[..start].chars().next_back();
    let after = text[end..].chars().next();
    let (first, last) = (v.chars().next().unwrap(), v.chars().next_back().unwrap());
    let ok_before = match before {
        None => true,
        Some(b) => !is_word(b) || !is_word(first) || no_space(first) || no_space(b) || hangul(b) != hangul(first),
    };
    // (two characters: asked without counting all of them, at every place where a long value is found)
    let two_syllables = || v.chars().nth(2).is_none() && v.chars().nth(1).is_some() && v.chars().all(syllable);
    let ok_after = match after {
        None => true,
        Some(a) if syllable(a) && two_syllables() => ko_tail(&text[end..]),
        // (a mark of a script written without spaces belongs to the letter before it: the word goes on)
        Some(a) => {
            let mark_after = no_space(last) && no_space_mark(a);
            !is_word(a) || !is_word(last) || (!mark_after && (no_space(last) || no_space(a))) || hangul(a) || hangul(last)
        }
    };
    // a dot between two digits joins them ("4524" is not found in "4524.7" or in "1.4524"), and so does the sign of an
    // exponent before a number ("1e+4524"); a digit is any number character, as in a word. An exponent is a number: a
    // value that is not one (an address, a phone number with spaces, "04524") is not joined to "<digit>e+" before it
    let mut back = text[..start].chars().rev();
    let (second, third) = (back.nth(1), back.next());
    let number = |c: Option<char>| c.is_some_and(char::is_numeric);
    // (whether the value is a number is asked last: reading it costs its length, at every place where it is found)
    let is_json_number = || json_number(v);
    // (the value may start with the sign itself: "-4524" in "1e-4524")
    let signed = matches!(first, '+' | '-') && number(v.chars().nth(1)) && matches!(before, Some('e' | 'E')) && number(second) && is_json_number();
    let exponent = matches!(before, Some('+' | '-')) && matches!(second, Some('e' | 'E')) && number(third) && is_json_number();
    let joined_before = (first.is_numeric() && ((before == Some('.') && number(second)) || exponent)) || signed;
    let joined_after = last.is_numeric() && after == Some('.') && number(text[end..].chars().nth(1));
    ok_before && ok_after && !joined_before && !joined_after
}

fn family(label: &str) -> String {
    let up = label.to_uppercase();
    let fam = up.strip_suffix("_L2").unwrap_or(&up);
    let cleaned: String = fam.chars().map(|c| if c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_' { c } else { '_' }).collect();
    let capped: String = cleaned.chars().take(MAX_FAMILY).collect();
    let trimmed = capped.trim_start_matches(|c: char| !c.is_ascii_uppercase());
    if trimmed.is_empty() {
        "PII".to_string()
    } else {
        trimmed.to_string()
    }
}

/// Numbered placeholders that can be undone. Build it with [`Vault::new`] or, to continue a saved one, with
/// [`Vault::from_entries`] (the entries hold the original values: store them only where the original text may be stored).
#[derive(Clone, Debug, Default)]
pub struct Vault {
    entries: Vec<VaultEntry>,
    by_tag: HashMap<String, usize>,
    by_value: HashMap<String, usize>,
    // each value once, in the order it was added (its place is its age), and by the spellings `whole` takes
    known: Vec<Known>,
    by_read: HashMap<String, usize>,
    by_upper: HashMap<String, usize>,
    by_lower: HashMap<String, Vec<usize>>,
    counters: HashMap<String, u64>,
    // the values a model found that are shorter than the vault searches for (`tag_found`): held to be restored, not
    // found as a whole value elsewhere
    local: HashSet<String>,
    generation: u64,
    replays: HashMap<String, String>,
    replay_order: std::collections::VecDeque<String>,
}

/// Undoes masking in text that arrives in pieces (a streamed answer). A placeholder split across pieces is held back
/// until it is complete, so [`VaultRestorer::push`] may return less than it was given; [`VaultRestorer::flush`] returns
/// what is left.
#[derive(Debug)]
pub struct VaultRestorer<'a> {
    vault: &'a Vault,
    only: Only<'a>,
    json: bool,
    held: String,
}

impl VaultRestorer<'_> {
    pub fn push(&mut self, chunk: &str) -> String {
        self.held.push_str(chunk);
        let cut = held_back(&self.held);
        let out = self.vault.restore_with(&self.held[..cut], self.only, self.json);
        self.held.drain(..cut);
        out
    }

    pub fn flush(&mut self) -> String {
        let out = self.vault.restore_with(&self.held, self.only, self.json);
        self.held.clear();
        out
    }
}

/// Where a trailing incomplete placeholder ("[PERS") starts in `s`, or `s.len()`.
fn held_back(s: &str) -> usize {
    let from = s.len().saturating_sub(MAX_TAG);
    let mut from = from;
    while !s.is_char_boundary(from) {
        from += 1;
    }
    if let Some(off) = s[from..].rfind('[') {
        let at = from + off;
        let rest = &s[at + 1..];
        if rest.bytes().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_') {
            return at;
        }
    }
    s.len()
}

impl Vault {
    pub fn new() -> Vault {
        Vault::default()
    }

    /// A vault that continues `entries` (from [`Vault::entries`]). Fails on an entry that is not a placeholder, has an
    /// empty value or repeats a tag.
    pub fn from_entries<I: IntoIterator<Item = VaultEntry>>(entries: I) -> crate::Result<Vault> {
        let mut v = Vault::new();
        for e in entries {
            v.add(VaultEntry { label: family(&e.label), ..e }, false)?;
        }
        Ok(v)
    }

    /// `e` held; with `local`, a new value is held to be restored only, not searched for (`tag_found`).
    fn add(&mut self, e: VaultEntry, local: bool) -> crate::Result<()> {
        let ok = parse_tag(&e.tag).filter(|(len, fam, num)| *len == e.tag.len() && fam.len() <= MAX_FAMILY && num.len() <= 12);
        let Some((_, fam, num)) = ok else {
            return Err(crate::Error::InvalidOption(format!("not a vault entry: {:?}", e.tag)));
        };
        if e.value.is_empty() {
            return Err(crate::Error::InvalidOption(format!("not a vault entry: {:?}", e.tag)));
        }
        if self.by_tag.contains_key(&e.tag) {
            return Err(crate::Error::InvalidOption(format!("duplicate vault tag {}", e.tag)));
        }
        let n: u64 = num.parse().unwrap_or(0);
        let c = self.counters.entry(fam.to_string()).or_insert(0);
        *c = (*c).max(n);
        let i = self.entries.len();
        self.by_tag.insert(e.tag.clone(), i);
        if !self.by_value.contains_key(&e.value) {
            self.by_value.insert(e.value.clone(), i);
            if local {
                self.local.insert(e.value.clone());
            } else {
                self.index(i, &e.label, &e.value);
            }
        }
        self.entries.push(e);
        Ok(())
    }

    /// The value of entry `i` searched for: its spellings indexed.
    fn index(&mut self, i: usize, label: &str, value: &str) {
        let k = Known::new(i, label, value);
        let age = self.known.len();
        self.by_read.entry(k.read.clone()).or_insert(age);
        for u in &k.uppers {
            self.by_upper.entry(u.clone()).or_insert(age);
        }
        if k.caseless > 0 {
            self.by_lower.entry(k.lower.clone()).or_default().push(age);
        }
        self.known.push(k);
    }

    /// The known value that `value` is as a whole, in any spelling `find` matches (the one held longest), also with what
    /// joins its last character after it (a mark, a variation selector), as `find` takes that in.
    fn whole(&self, value: &str) -> Option<&VaultEntry> {
        if self.known.is_empty() || value.is_empty() {
            return None;
        }
        let r = read(value).text;
        let found = self.whole_read(&r);
        if found.is_some() {
            return found;
        }
        let cut = r.len() - r.chars().rev().take_while(|&c| joins(c)).map(char::len_utf8).sum::<usize>();
        if cut == 0 || cut == r.len() || !at_edges(&r, 0, cut, &r[..cut]) {
            return None;
        }
        self.whole_read(&r[..cut])
    }

    /// [`Vault::whole`] for a reading, as it is.
    fn whole_read(&self, r: &str) -> Option<&VaultEntry> {
        let mut best = [self.by_read.get(r), self.by_upper.get(r)].into_iter().flatten().copied().min();
        if let Some(ks) = self.by_lower.get(&r.to_ascii_lowercase()) {
            if let Some(&k) = ks.iter().find(|&&k| self.known[k].is(r)) {
                best = Some(best.map_or(k, |b| b.min(k)));
            }
        }
        best.map(|k| &self.entries[self.known[k].entry])
    }

    /// The placeholder for a value found in a text: that of the known value it is in another spelling, or its own. A new
    /// value shorter than the vault searches for at word edges (3 characters, 2 in scripts written without spaces and
    /// two Hangul syllables) is held to be restored, but not found as a whole value elsewhere ([`Vault::hide_value`]): a
    /// "0" that a model took for a date once must not turn every "0" into a placeholder. Values the application tags
    /// ([`Vault::tag`]) are found as a whole value whatever their length.
    pub(crate) fn tag_found(&mut self, label: &str, value: &str) -> String {
        if let Some(e) = self.whole(value) {
            return e.tag.clone();
        }
        if let Some(&i) = self.by_value.get(value) {
            return self.entries[i].tag.clone(); // (held already: one value, one placeholder)
        }
        if value.is_empty() || findable(&read(value).text) {
            return self.tag(label, value);
        }
        self.add_new(label, value, true)
    }

    /// Number of values in the vault.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Goes up by one on every [`Vault::clear`].
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// The placeholder for `value`, adding it under `label` (its family) when new. The same value keeps its first tag.
    /// An empty value has no placeholder (`""`). Numbers go up to 999,999,999,999; a family that has used them up (after
    /// an imported entry with that number) goes on as `[PERSON__1]`.
    pub fn tag(&mut self, label: &str, value: &str) -> String {
        if value.is_empty() {
            return String::new();
        }
        if let Some(&i) = self.by_value.get(value) {
            if self.local.remove(value) {
                // (a short value a model found is found as a whole value from now on: the application gives it)
                let e = &self.entries[i];
                let (label, value) = (e.label.clone(), e.value.clone());
                self.index(i, &label, &value);
            }
            return self.entries[i].tag.clone();
        }
        self.add_new(label, value, false)
    }

    /// A new entry for `value` under `label` (its family); `local` as in `add`.
    fn add_new(&mut self, label: &str, value: &str, local: bool) -> String {
        let fam = family(label);
        // some 10^13 values later, or a state made by hand: masked, not undone ("PERSON_1" gives "[PERSON]", never a
        // numbered placeholder)
        let plain = format!("[{}]", unnumbered(&fam));
        // a family whose numbers are used up goes on in the next one, so a value always gets a placeholder that can be undone
        let used_up = |f: &str| self.counters.get(f).copied().unwrap_or(0) >= MAX_NUMBER;
        let mut numbered = fam.clone();
        for _ in 0..FAMILY_STEPS {
            if !used_up(&numbered) {
                break;
            }
            numbered = next_family(&numbered);
        }
        if used_up(&numbered) {
            return plain; // every family on the way used up
        }
        let n = self.counters.get(&numbered).copied().unwrap_or(0) + 1;
        let e = VaultEntry { tag: format!("[{numbered}_{n}]"), label: fam, value: value.to_string() };
        let tag = e.tag.clone();
        match self.add(e, local) {
            Ok(()) => tag,
            Err(_) => plain,
        }
    }

    /// The value behind a placeholder.
    pub fn value(&self, tag: &str) -> Option<&str> {
        self.by_tag.get(tag).map(|&i| self.entries[i].value.as_str())
    }

    /// The vault's placeholders in `text` replaced with their values; other bracketed text is left as it is.
    pub fn restore(&self, text: &str) -> String {
        self.restore_with(text, Only::All, false)
    }

    /// [`Vault::restore`] limited by `only`; with `json` the values are escaped for the inside of a JSON string.
    pub fn restore_with(&self, text: &str, only: Only<'_>, json: bool) -> String {
        let positions = tag_positions(text);
        if positions.is_empty() {
            return text.to_string();
        }
        let mut out = String::with_capacity(text.len());
        let mut last = 0;
        for (at, len) in positions {
            let tag = &text[at..at + len];
            let value: Option<&str> = match only {
                Only::All => self.value(tag),
                Only::Tags(set) => set.contains(tag).then(|| self.value(tag)).flatten(),
                Only::Values(map) => map.get(tag).map(String::as_str),
            };
            out.push_str(&text[last..at]);
            match value {
                Some(v) if json => out.push_str(&json_escape(v)),
                Some(v) => out.push_str(v),
                None => out.push_str(tag),
            }
            last = at + len;
        }
        out.push_str(&text[last..]);
        out
    }

    /// A restorer for a streamed text.
    pub fn restorer<'a>(&'a self, only: Only<'a>, json: bool) -> VaultRestorer<'a> {
        VaultRestorer { vault: self, only, json, held: String::new() }
    }

    /// The current values of these placeholders, as a snapshot for [`Only::Values`].
    pub fn bindings<'t, I: IntoIterator<Item = &'t str>>(&self, tags: I) -> HashMap<String, String> {
        tags.into_iter().filter_map(|t| self.value(t).map(|v| (t.to_string(), v.to_string()))).collect()
    }

    /// Where the known values occur in `text` (sorted, non-overlapping; overlapping occurrences are merged into one hit
    /// whose value is the merged text and whose label is the first one's). A hit carries the known value, also where it
    /// is written another way (see the module's notes); where two values are found on the same part of the text, the one
    /// the vault has held longer.
    pub fn find(&self, text: &str) -> Vec<VaultHit> {
        if self.known.is_empty() {
            return Vec::new();
        }
        let reading = read(text);
        let r = reading.text.as_str();
        let mut lower: Option<String> = None;
        // (start, end) in the reading and the value's age
        let mut found: Vec<(usize, usize, usize)> = Vec::new();
        for (age, k) in self.known.iter().enumerate() {
            if !k.findable {
                continue;
            }
            // (a hit takes in what joins its last character, so that it never ends inside a grapheme cluster)
            let mut take = |at: usize, end: usize| {
                if at_edges(r, at, end, &r[at..end]) {
                    found.push((at, widen(r, end), age));
                }
            };
            if k.caseless > 0 {
                // the caseless part in any ASCII case, the rest as it is written
                let low = lower.get_or_insert_with(|| r.to_ascii_lowercase());
                for at in followed_by(occurrences(low, &k.lower), r, k.caseless, &k.read[k.caseless..]) {
                    take(at, at + k.lower.len());
                }
            } else {
                for at in occurrences(r, &k.read) {
                    take(at, at + k.read.len());
                }
            }
            for u in &k.uppers {
                for at in occurrences(r, u) {
                    take(at, at + u.len());
                }
            }
        }
        found.sort_by(|a, b| a.0.cmp(&b.0).then(b.1.cmp(&a.1)).then(a.2.cmp(&b.2)));
        // overlapping known values are merged ("Alex Kim" + "Kim Lee"); (start, end, age, merged) in the text, and the
        // value of a hit is copied once it is merged: not at every place where a long value occurs
        let mut merged: Vec<(usize, usize, usize, bool)> = Vec::new();
        for (at, end, age) in found {
            let (start, end) = (reading.at(at), reading.at(end));
            match merged.last_mut() {
                Some(l) if start < l.1 => {
                    if end > l.1 {
                        l.1 = end;
                        l.3 = true;
                    }
                }
                _ => merged.push((start, end, age, false)),
            }
        }
        merged
            .into_iter()
            .map(|(start, end, age, m)| {
                let e = &self.entries[self.known[age].entry];
                VaultHit { start, end, label: e.label.clone(), value: if m { text[start..end].to_string() } else { e.value.clone() } }
            })
            .collect()
    }

    /// The vault's known values in `text` replaced with their placeholders (no model call). Overlapping known values
    /// are hidden together under a new placeholder. With `escape`, placeholder-like text in the rest of `text` is
    /// escaped as [`escape_tags`] does.
    pub fn hide(&mut self, text: &str, escape: bool) -> String {
        let esc = |t: &str| if escape { escape_tags(t) } else { t.to_string() };
        let mut out = String::with_capacity(text.len());
        let mut last = 0;
        for h in self.find(text) {
            out.push_str(&esc(&text[last..h.start]));
            let tag = match self.by_value.get(&h.value) {
                Some(&i) => self.entries[i].tag.clone(),
                None => self.tag(&h.label, &h.value),
            };
            out.push_str(&tag);
            last = h.end;
        }
        out.push_str(&esc(&text[last..]));
        out
    }

    /// [`Vault::hide`] for one argument value: a string that is as a whole a known value (in any spelling [`Vault::find`]
    /// matches) is replaced whatever its length.
    pub fn hide_value(&mut self, value: &str) -> String {
        match self.whole(value) {
            Some(e) => e.tag.clone(),
            None => self.hide(value, false),
        }
    }

    /// Remembers what the model wrote (`masked`) for a text that was restored, so that it can be sent again instead of
    /// the restored text. At most 4,096 are kept.
    pub fn remember(&mut self, key: &str, masked: &str) {
        if self.replays.insert(key.to_string(), masked.to_string()).is_some() {
            self.replay_order.retain(|k| k != key);
        }
        self.replay_order.push_back(key.to_string());
        while self.replay_order.len() > 4096 {
            if let Some(old) = self.replay_order.pop_front() {
                self.replays.remove(&old);
            }
        }
    }

    /// What [`Vault::remember`] stored for `key`.
    pub fn recall(&self, key: &str) -> Option<&str> {
        self.replays.get(key).map(String::as_str)
    }

    /// The entries in the order they were added.
    pub fn entries(&self) -> &[VaultEntry] {
        &self.entries
    }

    pub fn clear(&mut self) {
        self.entries.clear();
        self.by_tag.clear();
        self.by_value.clear();
        self.known.clear();
        self.by_read.clear();
        self.by_upper.clear();
        self.by_lower.clear();
        self.counters.clear();
        self.local.clear();
        self.replays.clear();
        self.replay_order.clear();
        self.generation += 1;
    }
}

/// A value of at most this many bytes is looked for again after each place where it starts (each step costs at most 32
/// bytes); a longer one is searched for in one pass ([`occurrences`]). The same in every package.
const SHORT_NEEDLE: usize = 32;

/// Every place where `pat` starts in `s`, overlapping ones too ("Ann Ann" in "xAnn Ann Ann"), in time linear in both. A
/// long value is searched for with Knuth-Morris-Pratt over the bytes (a place where a value written in UTF-8 starts is
/// always a character boundary), so that one that repeats itself ("ha ha ha" again and again) is not compared again
/// from its start at every place where it occurs.
fn occurrences(s: &str, pat: &str) -> Vec<usize> {
    let (t, p) = (s.as_bytes(), pat.as_bytes());
    let m = p.len();
    let mut out = Vec::new();
    if m == 0 || m > t.len() {
        return out;
    }
    if m <= SHORT_NEEDLE {
        let mut from = 0;
        while let Some(at) = s.get(from..).and_then(|rest| rest.find(pat)) {
            let at = from + at;
            out.push(at);
            from = at + s[at..].chars().next().map_or(1, char::len_utf8);
        }
        return out;
    }
    // fail[j]: the length of the longest proper prefix of p[..=j] that is also a suffix of it
    let mut fail = vec![0usize; m];
    let mut k = 0;
    for j in 1..m {
        while k > 0 && p[j] != p[k] {
            k = fail[k - 1];
        }
        if p[j] == p[k] {
            k += 1;
        }
        fail[j] = k;
    }
    k = 0;
    for (i, &c) in t.iter().enumerate() {
        while k > 0 && c != p[k] {
            k = fail[k - 1];
        }
        if c == p[k] {
            k += 1;
        }
        if k == m {
            out.push(i + 1 - m);
            k = fail[m - 1];
        }
    }
    out
}

/// The places of `at` (sorted) where `pat` also starts `offset` bytes later in `s`: one pass over both lists when `pat`
/// is long, so that checking the part of a value that must be written as it is costs no more than finding it.
fn followed_by(at: Vec<usize>, s: &str, offset: usize, pat: &str) -> Vec<usize> {
    if pat.len() <= SHORT_NEEDLE {
        return at.into_iter().filter(|&a| s.as_bytes()[a + offset..].starts_with(pat.as_bytes())).collect();
    }
    if at.is_empty() {
        return at;
    }
    let starts = occurrences(s, pat);
    let mut j = 0;
    at.into_iter()
        .filter(|&a| {
            while j < starts.len() && starts[j] < a + offset {
                j += 1;
            }
            j < starts.len() && starts[j] == a + offset
        })
        .collect()
}

fn json_escape(v: &str) -> String {
    let mut out = String::with_capacity(v.len());
    for c in v.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

/// Code point and UTF-16 offsets of byte offsets asked for in order, each counted on from the one before.
struct Offsets<'a> {
    text: &'a str,
    byte: usize,
    chars: usize,
    units: usize,
}

impl Offsets<'_> {
    fn at(&mut self, byte: usize) -> (usize, usize) {
        if byte < self.byte {
            // (not in order: counted from the start)
            (self.byte, self.chars, self.units) = (0, 0, 0);
        }
        for c in self.text[self.byte..byte].chars() {
            self.chars += 1;
            self.units += c.len_utf16();
        }
        self.byte = byte;
        (self.chars, self.units)
    }
}

/// `spans` plus the vault's known values (source [`Source::Vault`]). A known value that spans cover only in part ("Kim"
/// of a known "Kim Minsu") is masked whole: the overlapping spans are merged into one span over both, which keeps the
/// known value's placeholder when the merged span is exactly that value. One pass over the hits and the spans (sorted
/// by their start first if they are not). A span over known values has their label, and the label of a span the model
/// found when it was merged with one; a known value of a custom label ("CUSTOMER") is labelled [`Label::Person`], which
/// [`apply_redaction_vault`] does not take for its family.
pub fn with_vault_spans(text: &str, spans: Vec<Span>, vault: &Vault) -> Vec<Span> {
    let hits = vault.find(text); // in order, apart
    if hits.is_empty() {
        return spans;
    }
    let mut spans = spans;
    if !spans.windows(2).all(|w| w[0].start <= w[1].start) {
        spans.sort_by_key(|s| s.start);
    }
    let mut out: Vec<Span> = Vec::with_capacity(spans.len() + hits.len());
    let mut rest = spans.into_iter().peekable();
    let mut offsets = Offsets { text, byte: 0, chars: 0, units: 0 };
    for h in hits {
        while let Some(s) = rest.next_if(|s| s.end <= h.start) {
            out.push(s);
        }
        let mut over: Vec<Span> = Vec::new();
        if out.last().is_some_and(|s| s.end > h.start) {
            over.extend(out.pop()); // one that reaches this far (merged before)
        }
        while let Some(s) = rest.next_if(|s| s.start < h.end) {
            over.push(s);
        }
        if !over.is_empty() && over.iter().all(|s| s.start <= h.start && s.end >= h.end) {
            out.extend(over); // already masked whole
            continue;
        }
        let (at_start, at_end) = (offsets.at(h.start), offsets.at(h.end));
        // the merged span starts and ends where the hit or a span does (a span carries its own offsets)
        let first = over.iter().filter(|s| s.start < h.start).min_by_key(|s| s.start);
        let last = over.iter().filter(|s| s.end > h.end).max_by_key(|s| s.end);
        let (start, (char_start, utf16_start)) = first.map_or((h.start, at_start), |s| (s.start, (s.char_start, s.utf16_start)));
        let (end, (char_end, utf16_end)) = last.map_or((h.end, at_end), |s| (s.end, (s.char_end, s.utf16_end)));
        let label = if (start, end) == (h.start, h.end) || over.is_empty() {
            h.label.parse::<Label>().unwrap_or(Label::Person)
        } else {
            over[0].label
        };
        out.push(Span { start, end, char_start, char_end, utf16_start, utf16_end, label, source: Source::Vault });
    }
    out.extend(rest);
    out
}

/// `text` with each span replaced by the vault's numbered placeholder for its value; placeholder-like text left unmasked
/// is escaped ("［PERSON_1]"), so that only the vault's own placeholders restore. `spans` as in
/// [`crate::apply_redaction`]; the vault learns each without what its edges hold that is not part of the value, as in
/// [`redact_with_vault`]. A span of [`Source::Vault`] that is exactly where known values were found (as
/// [`with_vault_spans`] makes it) is numbered in the family of the first of them, also when that is a custom label that
/// [`Label`] cannot name (such a span is labelled [`Label::Person`]).
pub fn apply_redaction_vault(text: &str, spans: &[Span], vault: &mut Vault) -> String {
    apply_spans_vault(text, &value_spans(text, spans.to_vec()), vault)
}

/// [`apply_redaction_vault`] with the spans as they are.
fn apply_spans_vault(text: &str, spans: &[Span], vault: &mut Vault) -> String {
    // (where the vault finds known values: looked up only when a span from them is not as a whole a known value, which
    // is when known values that overlap were merged)
    let merged = spans.iter().any(|s| s.source == Source::Vault && text.get(s.start..s.end).is_some_and(|v| vault.whole(v).is_none()));
    let hits = if merged { vault.find(text) } else { Vec::new() };
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for span in spans {
        if span.start < last || span.end < span.start {
            continue;
        }
        let (Some(before), Some(original)) = (text.get(last..span.start), text.get(span.start..span.end)) else {
            continue;
        };
        out.push_str(&escape_tags(before));
        let family = match span.source {
            Source::Vault => hits.binary_search_by_key(&span.start, |h| h.start).ok().filter(|&i| hits[i].end == span.end).map(|i| hits[i].label.as_str()),
            _ => None,
        };
        // (a known value written another way keeps its placeholder)
        out.push_str(&vault.tag_found(family.unwrap_or(span.label.as_str()), original));
        last = span.end;
    }
    out.push_str(&escape_tags(&text[last..]));
    out
}

/// `text` masked with a vault: the spans the model found plus the vault's known values, and then every value this text
/// added to the vault masked wherever it occurs again in it ("I met Alice. Say Alice twice." when the model finds only
/// the first "Alice"). The found spans lose what their edges hold that is not part of the value first ("Kim Minsu." at
/// the end of a sentence is the value "Kim Minsu"). Returns the text and the final spans. This is what
/// [`crate::Redactor::redact_vault`] does.
pub fn redact_with_vault(text: &str, found: Vec<Span>, vault: &mut Vault) -> (String, Vec<Span>) {
    // (what the edges of a found span hold that is not part of the value is left out before the vault learns it)
    let found = value_spans(text, found);
    let mut spans = with_vault_spans(text, found.clone(), vault);
    let mut out = apply_spans_vault(text, &spans, vault); // the vault learns this text's values here
    // also what the model found inside a larger merged span ("Kim Lee" of "Alex Kim Lee"), so it is masked where it stands alone
    for s in &found {
        if let Some(value) = text.get(s.start..s.end) {
            if !value.trim().is_empty() {
                vault.tag_found(s.label.as_str(), value);
            }
        }
    }
    // until nothing new is found: every round either masks more of the text or merges spans, so this ends
    loop {
        let next = with_vault_spans(text, spans.clone(), vault);
        if next.len() == spans.len() && next.iter().zip(&spans).all(|(a, b)| a.start == b.start && a.end == b.end) {
            return (out, spans);
        }
        spans = next;
        out = apply_spans_vault(text, &spans, vault);
    }
}
