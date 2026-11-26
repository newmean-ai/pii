//! Labels, spans, tokens and errors.
use std::fmt;
use std::ops::Range;
use std::path::PathBuf;
use std::str::FromStr;

/// What a span is. `PersonL2` and `UrlL2` are level 2 labels for indirect identifiers: a surname with a title, a nickname
/// or initials, and public profile or page URLs.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[non_exhaustive]
pub enum Label {
    Person,
    PersonL2,
    Email,
    Phone,
    Address,
    IdNumber,
    Financial,
    Org,
    Username,
    Url,
    UrlL2,
    DateOfBirth,
    Secret,
}

const NAMES: [&str; 13] = [
    "PERSON", "PERSON_L2", "EMAIL", "PHONE", "ADDRESS", "ID_NUMBER", "FINANCIAL", "ORG", "USERNAME", "URL", "URL_L2", "DATE_OF_BIRTH",
    "SECRET",
];
const KEYS: [&str; 13] = [
    "person", "person", "email", "phone", "address", "id_number", "financial", "org", "username", "url", "url", "date_of_birth", "secret",
];

impl Label {
    /// Every label, in the order of the model's label ids (the default of [`crate::Builder::labels`]).
    pub const ALL: [Label; 13] = [
        Label::Person,
        Label::PersonL2,
        Label::Email,
        Label::Phone,
        Label::Address,
        Label::IdNumber,
        Label::Financial,
        Label::Org,
        Label::Username,
        Label::Url,
        Label::UrlL2,
        Label::DateOfBirth,
        Label::Secret,
    ];
    /// Every label but the two level 2 labels: pass it to [`crate::Builder::labels`] to leave indirect identifiers visible.
    pub const LEVEL1: [Label; 11] = [
        Label::Person,
        Label::Email,
        Label::Phone,
        Label::Address,
        Label::IdNumber,
        Label::Financial,
        Label::Org,
        Label::Username,
        Label::Url,
        Label::DateOfBirth,
        Label::Secret,
    ];

    pub(crate) fn id(self) -> i32 {
        self as i32
    }

    pub(crate) fn from_id(id: i32) -> Option<Label> {
        usize::try_from(id).ok().and_then(|i| Label::ALL.get(i).copied())
    }

    /// The name every newmean-pii package uses: `"PERSON"`, `"PERSON_L2"`, `"ID_NUMBER"`, ...
    pub fn as_str(self) -> &'static str {
        NAMES[self as usize]
    }

    /// The label without its level: `PersonL2` gives `Person`, `UrlL2` gives `Url`, every other label itself.
    pub fn family(self) -> Label {
        match self {
            Label::PersonL2 => Label::Person,
            Label::UrlL2 => Label::Url,
            other => other,
        }
    }

    /// The family name in lower case, as [`crate::Extracted`] uses it for keys: `"person"`, `"email"`, `"id_number"`, ...
    pub fn key(self) -> &'static str {
        KEYS[self as usize]
    }
}

impl fmt::Display for Label {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.pad(self.as_str())
    }
}

impl FromStr for Label {
    type Err = Error;

    /// Reads a label name as [`Label::as_str`] writes it (`"PERSON"`, `"ID_NUMBER"`); upper case only.
    fn from_str(s: &str) -> Result<Label> {
        NAMES
            .iter()
            .position(|n| *n == s)
            .map(|i| Label::ALL[i])
            .ok_or_else(|| Error::InvalidOption(format!("unknown label {s:?} (labels: {})", NAMES.join(", "))))
    }
}

/// Where a span comes from.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Source {
    /// Found by the model.
    Model,
    /// Propagated down a column by table mode ([`crate::Redactor::detect_table`]).
    Table,
    /// An entry of the block list ([`crate::Builder::block`]).
    Block,
    /// A value a [`crate::Vault`] already holds, found again by [`crate::Redactor::redact_vault`] (since 1.7.2).
    Vault,
}

impl Source {
    /// The name every newmean-pii package uses: `"model"`, `"table"`, `"block"` or `"vault"`.
    pub fn as_str(self) -> &'static str {
        match self {
            Source::Model => "model",
            Source::Table => "table",
            Source::Block => "block",
            Source::Vault => "vault",
        }
    }
}

/// A piece of the input to mask. `start..end` are UTF-8 byte offsets, so `&text[span.start..span.end]` is the value; the
/// same span is also given in code points (Python `str` indices) and UTF-16 code units (JavaScript, Java, Swift `NSString`)
/// for systems that count that way. Spans never split a grapheme cluster.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Span {
    pub start: usize,
    pub end: usize,
    pub char_start: usize,
    pub char_end: usize,
    pub utf16_start: usize,
    pub utf16_end: usize,
    pub label: Label,
    pub source: Source,
}

impl Span {
    /// `start..end` in bytes.
    pub fn range(&self) -> Range<usize> {
        self.start..self.end
    }

    /// The value in `text` (the text the span was found in); an empty string if the span does not fit `text`.
    pub fn text<'a>(&self, text: &'a str) -> &'a str {
        text.get(self.start..self.end).unwrap_or("")
    }
}

/// A token id of the model's tokenizer and where it is in the input (bytes, code points, UTF-16 code units), without the
/// `<s>` and `</s>` the model adds.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Token {
    pub id: i32,
    pub start: usize,
    pub end: usize,
    pub char_start: usize,
    pub char_end: usize,
    pub utf16_start: usize,
    pub utf16_end: usize,
}

/// Which list an invalid entry is in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ListKind {
    Allow,
    Block,
}

/// Why a list entry is invalid.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum ListReason {
    /// A block entry without a label.
    BadShape,
    /// More than 64 code points.
    TooLong,
    /// A NUL character or a line break.
    BadText,
    /// No characters.
    Empty,
    /// White space at the start or the end (entries are never trimmed).
    EdgeSpace,
    UnknownLabel,
    /// More than 10,000 entries in one list.
    TooMany,
    /// Two block entries with the same text and different labels.
    LabelConflict,
    /// The same text in the allow list and in the block list.
    InBoth,
}

impl ListReason {
    pub(crate) fn from_code(code: i32) -> ListReason {
        match code {
            2 => ListReason::TooLong,
            3 => ListReason::BadText,
            4 => ListReason::Empty,
            5 => ListReason::EdgeSpace,
            6 => ListReason::UnknownLabel,
            7 => ListReason::TooMany,
            8 => ListReason::LabelConflict,
            9 => ListReason::InBoth,
            _ => ListReason::BadShape,
        }
    }

    /// The name every newmean-pii package uses: `"too_long"`, `"edge_space"`, `"in_both"`, ...
    pub fn as_str(self) -> &'static str {
        match self {
            ListReason::BadShape => "bad_shape",
            ListReason::TooLong => "too_long",
            ListReason::BadText => "bad_text",
            ListReason::Empty => "empty",
            ListReason::EdgeSpace => "edge_space",
            ListReason::UnknownLabel => "unknown_label",
            ListReason::TooMany => "too_many",
            ListReason::LabelConflict => "label_conflict",
            ListReason::InBoth => "in_both",
        }
    }
}

/// An invalid allow or block entry: which list, the entry's index in the order it was added, and why. The error never
/// holds the entry's text, so it is safe to log.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ListError {
    pub list: ListKind,
    pub index: usize,
    pub reason: ListReason,
}

/// Everything that can fail in this crate.
#[derive(Debug)]
#[non_exhaustive]
pub enum Error {
    /// A bad option: a sensitivity that is not finite, an unknown label name.
    InvalidOption(String),
    /// A model file is missing, cannot be read, or does not match the published size and SHA-256.
    ModelFile { path: Option<PathBuf>, message: String },
    /// An invalid allow or block entry.
    List(ListError),
    /// The native core refused a call (out of memory, a text longer than 2 GB).
    Core { code: i32, message: String },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::InvalidOption(m) => f.write_str(m),
            Error::ModelFile { path: Some(p), message } => write!(f, "{}: {message}", p.display()),
            Error::ModelFile { path: None, message } => f.write_str(message),
            Error::List(e) => {
                let list = if e.list == ListKind::Allow { "allow" } else { "block" };
                write!(f, "invalid {list} entry at index {}: {}", e.index, e.reason.as_str())
            }
            Error::Core { code, message } => write!(f, "native core error {code}: {message}"),
        }
    }
}

impl std::error::Error for Error {}

/// `Result` with this crate's [`Error`].
pub type Result<T> = std::result::Result<T, Error>;
