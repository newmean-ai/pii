//! On-device detection, masking and extraction of personal information (PII) in 54 languages.
//!
//! The models are small (2.6 to 15.5 MB) and run inside your process on the CPU: no service, no API key, nothing sent
//! anywhere. This crate compiles the same C core as the Python, Swift, Android and Flutter packages of
//! [newmean-pii](https://github.com/newmean-ai/pii) and gives the same spans as they do; it has no dependencies.
//!
//! ```no_run
//! use newmean_pii::{Label, Mode, Redactor};
//!
//! // the folder holds tokenizer.web.json and onnx/model_q4e.onnx (see `model_files`)
//! let redactor = Redactor::builder().model_dir("models/pii-medium").build()?;
//!
//! let text = "안녕하세요 이준호입니다 제 이메일은 joon@example.com입니다";
//! assert_eq!(redactor.redact(text, Mode::Tag)?.text, "안녕하세요 [PERSON]입니다 제 이메일은 [EMAIL]입니다");
//!
//! let found = redactor.extract(text)?;
//! assert_eq!(found.first(Label::Email), Some("joon@example.com"));
//! assert_eq!(found.to_json(), r#"{"person":["이준호"],"email":["joon@example.com"]}"#);
//! # Ok::<(), newmean_pii::Error>(())
//! ```
//!
//! No model finds everything: this lowers what leaves your system, it does not guarantee that nothing gets through. The
//! measured miss rates are in the [repository](https://github.com/newmean-ai/pii).
//!
//! # Model files
//!
//! The crate never downloads. Fetch the two files of a model once ([`model_files`] lists their URLs, sizes and SHA-256)
//! and pass the folder with [`Builder::model_dir`], or embed them with `include_bytes!` and [`Builder::build_from_bytes`].
//! Every file is checked against its published size and SHA-256 before it is loaded.
#![warn(missing_debug_implementations)]

mod extract;
mod ffi;
mod models;
mod redact;
mod redactor;
mod types;
mod vault;
mod word_ranges;

pub use extract::{Entity, Extracted};
pub use models::{default_cache_dir, model_files, sha256_hex, ModelFile, Size, Variant, MODEL_VERSION};
pub use redact::{apply_redaction, apply_redaction_with, Mode, Redacted};
pub use redactor::{core_version, Builder, Delimiter, Info, Redactor, TableContext, TableOptions};
pub use types::{Error, Label, ListError, ListKind, ListReason, Result, Source, Span, Token};
pub use vault::{apply_redaction_vault, escape_tags, redact_with_vault, vault_tags, with_vault_spans, Only, Vault, VaultEntry, VaultHit, VaultRestorer};

/// Version of this crate (the same number as the other newmean-pii packages of the release).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
