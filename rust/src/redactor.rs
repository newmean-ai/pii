//! The redactor: loads a model and finds, masks or extracts personal information.
use std::ffi::CStr;
use std::os::raw::{c_char, c_int};
use std::path::{Path, PathBuf};
use std::ptr;

use crate::extract::Extracted;
use crate::ffi;
use crate::models::{self, Size, Variant, MODEL_VERSION};
use crate::redact::{apply_redaction, apply_redaction_with, Mode, Redacted};
use crate::types::{Error, Label, ListError, ListKind, ListReason, Result, Source, Span, Token};

/// Column delimiter of table mode.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Delimiter {
    /// Detected from the text.
    #[default]
    Auto,
    Comma,
    Tab,
    Semicolon,
    /// A markdown pipe table.
    Markdown,
}

/// What table mode reads besides the whole text.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum TableContext {
    #[default]
    None,
    /// With a header row, each non-empty data cell of a column whose values are at least 80% distinct is also read on its
    /// own as `"<header>: <value>"`: fewer misses on name, contact and ID columns, more cells without personal information
    /// masked, one more model call per such cell.
    Header,
}

/// Options of [`Redactor::detect_table`] and [`Redactor::redact_table`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TableOptions {
    pub delimiter: Delimiter,
    /// The first row is a header row: masked where the model finds something, never voted on. Default `true`.
    pub header: bool,
    /// A column of short values in which the model finds names, e-mail addresses, phone numbers or user names in at least
    /// 80% of the cells is masked whole. Default `true`.
    pub vote: bool,
    pub context: TableContext,
}

impl Default for TableOptions {
    fn default() -> Self {
        TableOptions { delimiter: Delimiter::Auto, header: true, vote: true, context: TableContext::None }
    }
}

/// What a redactor runs, for audit records.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Info {
    /// Version of this crate and of the native core inside it.
    pub package_version: &'static str,
    pub model_version: &'static str,
    pub size: Size,
    pub variant: Variant,
    /// Where the weights and the tokenizer were read from (`None` when they were passed as bytes).
    pub model_path: Option<PathBuf>,
    pub tokenizer_path: Option<PathBuf>,
    /// Matrix kernel the native core runs: `"neon+fma"` (arm64), `"avx2"` or `"sse"` (x86-64, chosen once per process;
    /// `"avx2+fma"` with `NEWMEAN_PII_KERNEL=avx2+fma`), `"scalar"` (MSVC), ...
    pub build: String,
    pub hidden: usize,
    pub layers: usize,
    pub vocab: usize,
    pub max_tokens: usize,
    /// Entries of the allow and block lists after merging duplicates.
    pub allow: usize,
    pub block: usize,
}

/// Options of a [`Redactor`]; made by [`Redactor::builder`].
#[derive(Clone, Debug)]
pub struct Builder {
    size: Size,
    variant: Option<Variant>,
    sensitivity: f64,
    labels: Option<Vec<Label>>,
    keep_emoji: bool,
    whole_words: bool,
    strict_short_names: bool,
    allow: Vec<(String, Option<Label>)>,
    block: Vec<(String, Label)>,
    model_dir: Option<PathBuf>,
    cache_dir: Option<PathBuf>,
}

impl Default for Builder {
    fn default() -> Self {
        Builder {
            size: Size::default(),
            variant: None,
            sensitivity: 0.0,
            labels: None,
            keep_emoji: false,
            whole_words: true,
            strict_short_names: false,
            allow: Vec::new(),
            block: Vec::new(),
            model_dir: None,
            cache_dir: None,
        }
    }
}

impl Builder {
    /// Model size (default [`Size::Medium`]).
    pub fn size(mut self, size: Size) -> Self {
        self.size = size;
        self
    }

    /// Weight format (default: [`Size::default_variant`]).
    pub fn variant(mut self, variant: Variant) -> Self {
        self.variant = Some(variant);
        self
    }

    /// Above 0 masks more (fewer misses, more over-masking), below 0 masks less; about -1 to 1, default 0. Must be finite.
    pub fn sensitivity(mut self, sensitivity: f64) -> Self {
        self.sensitivity = sensitivity;
        self
    }

    /// The labels to report (default: all, [`Label::ALL`]). The model knows these labels only; values of other kinds go
    /// in [`Builder::block`].
    pub fn labels<I: IntoIterator<Item = Label>>(mut self, labels: I) -> Self {
        self.labels = Some(labels.into_iter().collect());
        self
    }

    /// `true`: the model also reads emoji. Default `false`: emoji are never read or masked.
    pub fn keep_emoji(mut self, keep_emoji: bool) -> Self {
        self.keep_emoji = keep_emoji;
        self
    }

    /// Default `true`: a span that starts or ends inside a word covers the whole word (`[ID_NUMBER]`, not
    /// `[ID_NUMBER]23`); Korean particles after a Hangul span stay (`[PERSON]님께`).
    pub fn whole_words(mut self, whole_words: bool) -> Self {
        self.whole_words = whole_words;
        self
    }

    /// `true`: a short text (at most 8 tokens: a survey answer, a table cell) needs stronger evidence to be read as a
    /// person's name. Fewer short values without personal information are masked, a lone name is missed a little more often.
    pub fn strict_short_names(mut self, strict: bool) -> Self {
        self.strict_short_names = strict;
        self
    }

    /// A value that is not personal information in your data: a span whose whole text is `text` is not masked, whatever
    /// its label. ASCII letters compare case-insensitively; nothing else is normalized (pass NFC text).
    pub fn allow(mut self, text: impl Into<String>) -> Self {
        self.allow.push((text.into(), None));
        self
    }

    /// As [`Builder::allow`], for spans of one label family only (`Person` covers `PersonL2`, `Url` covers `UrlL2`).
    pub fn allow_label(mut self, text: impl Into<String>, label: Label) -> Self {
        self.allow.push((text.into(), Some(label)));
        self
    }

    /// A value that is personal information in your data: every occurrence that starts and ends on a word boundary is
    /// masked with `label`, also where the model finds nothing and whatever [`Builder::labels`] enables.
    pub fn block(mut self, text: impl Into<String>, label: Label) -> Self {
        self.block.push((text.into(), label));
        self
    }

    /// The folder with `tokenizer.web.json` and `onnx/model_<variant>.onnx` (the layout of the Hugging Face repository
    /// `newmean-ai/pii-<size>`; see [`crate::model_files`]). Without it the files are read from the cache folder.
    pub fn model_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.model_dir = Some(dir.into());
        self
    }

    /// Cache folder holding `v1.1.0/pii-<size>/...`, used when no `model_dir` is given (default:
    /// [`crate::default_cache_dir`], which the Python package's `python -m newmean_pii download` fills).
    pub fn cache_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.cache_dir = Some(dir.into());
        self
    }

    /// Reads the model files from the folder, checks them against the published size and SHA-256, and loads the model.
    /// This crate never downloads: a missing file is an [`Error::ModelFile`] that names the URL to fetch.
    pub fn build(self) -> Result<Redactor> {
        let variant = self.variant.unwrap_or_else(|| self.size.default_variant());
        let lists = Lists::new(&self.allow, &self.block)?; // before any model file is read, as in every package
        check_sensitivity(self.sensitivity)?;
        let dir = match (&self.model_dir, &self.cache_dir) {
            (Some(dir), _) => dir.clone(),
            (None, Some(cache)) => models::cached_model_dir(cache, self.size),
            (None, None) => {
                let cache = models::default_cache_dir().ok_or_else(|| Error::ModelFile {
                    path: None,
                    message: "no model_dir given and no home folder to look for the cache in".into(),
                })?;
                models::cached_model_dir(&cache, self.size)
            }
        };
        let (tokenizer, tokenizer_path) = models::read_verified(&dir, self.size, "tokenizer")?;
        let (onnx, model_path) = models::read_verified(&dir, self.size, variant.as_str())?;
        self.finish(variant, lists, &onnx, &tokenizer, Some(model_path), Some(tokenizer_path))
    }

    /// As [`Builder::build`], with the bytes of the two model files instead of a folder (for `include_bytes!` or your own
    /// storage). The bytes are checked against the published size and SHA-256 of this builder's size and variant.
    pub fn build_from_bytes(self, onnx: &[u8], tokenizer: &[u8]) -> Result<Redactor> {
        let variant = self.variant.unwrap_or_else(|| self.size.default_variant());
        let lists = Lists::new(&self.allow, &self.block)?;
        check_sensitivity(self.sensitivity)?;
        for (data, file) in [(tokenizer, "tokenizer"), (onnx, variant.as_str())] {
            if !models::verify(data, self.size, file) {
                return Err(Error::ModelFile {
                    path: None,
                    message: format!("the bytes do not match the published {} {file} file (size or SHA-256 differs)", self.size),
                });
            }
        }
        self.finish(variant, lists, onnx, tokenizer, None, None)
    }

    fn finish(
        self,
        variant: Variant,
        lists: Lists,
        onnx: &[u8],
        tokenizer: &[u8],
        model_path: Option<PathBuf>,
        tokenizer_path: Option<PathBuf>,
    ) -> Result<Redactor> {
        // SAFETY: plain functions without arguments.
        let sizes_match = unsafe {
            ffi::pii_options_size() == std::mem::size_of::<ffi::pii_options>()
                && ffi::pii_table_options_size() == std::mem::size_of::<ffi::pii_table_options>()
        };
        if !sizes_match {
            return Err(Error::Core { code: -1, message: "the native core does not match this crate's declarations".into() });
        }
        let mut err = [0 as c_char; 256];
        // SAFETY: both buffers are valid for their lengths; err holds 256 bytes. The core copies what it keeps.
        let model =
            unsafe { ffi::pii_model_load(onnx.as_ptr(), onnx.len(), tokenizer.as_ptr(), tokenizer.len(), err.as_mut_ptr(), err.len()) };
        if model.is_null() {
            // SAFETY: the core wrote a NUL-terminated message (err was zeroed).
            let message = unsafe { CStr::from_ptr(err.as_ptr()) }.to_string_lossy().into_owned();
            return Err(Error::ModelFile { path: model_path, message: format!("the model failed to load: {message}") });
        }
        let mask = match &self.labels {
            None => Label::ALL.iter().fold(0u32, |m, l| m | 1 << l.id()),
            Some(labels) => labels.iter().fold(0u32, |m, l| m | 1 << l.id()),
        };
        Ok(Redactor {
            model,
            lists,
            mask,
            sensitivity: self.sensitivity,
            keep_emoji: self.keep_emoji,
            whole_words: self.whole_words,
            strict_short_names: self.strict_short_names,
            size: self.size,
            variant,
            model_path,
            tokenizer_path,
        })
    }
}

fn check_sensitivity(value: f64) -> Result<()> {
    if value.is_finite() {
        Ok(())
    } else {
        Err(Error::InvalidOption("sensitivity must be a finite number".into()))
    }
}

/// The allow and block lists as the core holds them (NULL when both are empty).
struct Lists(*mut ffi::pii_lists);

impl Lists {
    fn new(allow: &[(String, Option<Label>)], block: &[(String, Label)]) -> Result<Lists> {
        if allow.is_empty() && block.is_empty() {
            return Ok(Lists(ptr::null_mut()));
        }
        let term = |text: &String, label: c_int| ffi::pii_term { utf8: text.as_ptr() as *const c_char, len: text.len(), label };
        let a: Vec<ffi::pii_term> = allow.iter().map(|(t, l)| term(t, l.map_or(-1, Label::id))).collect();
        let b: Vec<ffi::pii_term> = block.iter().map(|(t, l)| term(t, l.id())).collect();
        let mut out = ptr::null_mut();
        let mut err = ffi::pii_list_error { list: -1, index: 0, reason: 0 };
        let ptr_or_null = |v: &Vec<ffi::pii_term>| if v.is_empty() { ptr::null() } else { v.as_ptr() };
        // SAFETY: the term arrays and the strings they point to live until the call returns; the core copies them.
        let code = unsafe { ffi::pii_lists_create(ptr_or_null(&a), a.len(), ptr_or_null(&b), b.len(), &mut out, &mut err) };
        if code != ffi::PII_OK {
            if err.reason == 0 {
                return Err(core_error(code));
            }
            return Err(Error::List(ListError {
                list: if err.list == ffi::PII_LIST_ALLOW { ListKind::Allow } else { ListKind::Block },
                index: err.index,
                reason: ListReason::from_code(err.reason),
            }));
        }
        Ok(Lists(out))
    }
}

impl Drop for Lists {
    fn drop(&mut self) {
        // SAFETY: the handle came from pii_lists_create (or is NULL, which the core accepts) and is freed once.
        unsafe { ffi::pii_lists_free(self.0) }
    }
}

fn core_error(code: c_int) -> Error {
    // SAFETY: pii_strerror returns a static string for every code.
    let message = unsafe { CStr::from_ptr(ffi::pii_strerror(code)) }.to_string_lossy().into_owned();
    Error::Core { code, message }
}

/// Finds, masks and extracts personal information with a newmean-pii model, inside this process.
///
/// A redactor can be shared between threads (`&Redactor` is `Sync`): the model is immutable and every call uses its own
/// working memory, so calls from several threads run in parallel, one core each.
///
/// ```no_run
/// use newmean_pii::{Mode, Redactor};
///
/// let redactor = Redactor::builder().model_dir("models/pii-medium").build()?;
/// let masked = redactor.redact("제 메일은 minji@example.com 입니다", Mode::Tag)?;
/// assert_eq!(masked.text, "제 메일은 [EMAIL] 입니다");
/// # Ok::<(), newmean_pii::Error>(())
/// ```
pub struct Redactor {
    model: *mut ffi::pii_model,
    lists: Lists,
    mask: u32,
    sensitivity: f64,
    keep_emoji: bool,
    whole_words: bool,
    strict_short_names: bool,
    size: Size,
    variant: Variant,
    model_path: Option<PathBuf>,
    tokenizer_path: Option<PathBuf>,
}

// SAFETY: the model and the lists are immutable after they are created, the core keeps no global state, and every call
// allocates its own scratch memory (pii.h, "Threads").
unsafe impl Send for Redactor {}
unsafe impl Sync for Redactor {}

impl Drop for Redactor {
    fn drop(&mut self) {
        // SAFETY: the model came from pii_model_load and is freed once; no call can be running (drop has `&mut self`).
        unsafe { ffi::pii_model_free(self.model) }
    }
}

impl std::fmt::Debug for Redactor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Redactor")
            .field("size", &self.size)
            .field("variant", &self.variant)
            .field("sensitivity", &self.sensitivity)
            .field("labels", &self.labels())
            .field("keep_emoji", &self.keep_emoji)
            .field("whole_words", &self.whole_words)
            .field("strict_short_names", &self.strict_short_names)
            .finish()
    }
}

impl Redactor {
    /// Options with their defaults: the medium model, all labels, sensitivity 0.
    pub fn builder() -> Builder {
        Builder::default()
    }

    /// The default redactor (medium model) from a folder with the model files.
    pub fn from_dir(dir: impl AsRef<Path>) -> Result<Redactor> {
        Builder::default().model_dir(dir.as_ref()).build()
    }

    pub fn size(&self) -> Size {
        self.size
    }

    pub fn variant(&self) -> Variant {
        self.variant
    }

    /// The labels this redactor reports.
    pub fn labels(&self) -> Vec<Label> {
        Label::ALL.into_iter().filter(|l| self.mask >> l.id() & 1 == 1).collect()
    }

    pub fn sensitivity(&self) -> f64 {
        self.sensitivity
    }

    /// Changes the threshold without reloading the model. An error (and the old value stays) unless `value` is finite.
    pub fn set_sensitivity(&mut self, value: f64) -> Result<()> {
        check_sensitivity(value)?;
        self.sensitivity = value;
        Ok(())
    }

    fn options(&self) -> ffi::pii_options {
        let mut o = std::mem::MaybeUninit::<ffi::pii_options>::zeroed();
        // SAFETY: pii_options_init writes every field of the struct; all-zero is a valid value of each field before that.
        let mut o = unsafe {
            ffi::pii_options_init(o.as_mut_ptr());
            o.assume_init()
        };
        o.sensitivity = self.sensitivity;
        o.labels = self.mask;
        o.keep_emoji = self.keep_emoji as c_int;
        o.whole_words = self.whole_words as c_int;
        o.strict_short_names = self.strict_short_names as c_int;
        o.lists = self.lists.0;
        o
    }

    /// Takes the spans the core allocated and frees them.
    fn take(code: c_int, out: *mut ffi::pii_span, n: usize) -> Result<Vec<Span>> {
        if code != ffi::PII_OK {
            return Err(core_error(code));
        }
        let mut spans = Vec::with_capacity(n);
        let mut bad = None;
        if !out.is_null() {
            // SAFETY: on PII_OK the core returns n spans at out.
            for s in unsafe { std::slice::from_raw_parts(out, n) } {
                let source = match s.source {
                    ffi::PII_SOURCE_MODEL => Some(Source::Model),
                    ffi::PII_SOURCE_TABLE => Some(Source::Table),
                    ffi::PII_SOURCE_BLOCK => Some(Source::Block),
                    _ => None,
                };
                match (Label::from_id(s.label), source) {
                    (Some(label), Some(source)) => spans.push(Span {
                        start: s.byte_start,
                        end: s.byte_end,
                        char_start: s.cp_start,
                        char_end: s.cp_end,
                        utf16_start: s.u16_start,
                        utf16_end: s.u16_end,
                        label,
                        source,
                    }),
                    _ => bad = Some((s.label, s.source)),
                }
            }
            // SAFETY: out came from the core and is freed once.
            unsafe { ffi::pii_spans_free(out) };
        }
        match bad {
            Some((label, source)) => {
                Err(Error::Core { code: -1, message: format!("unknown label {label} or source {source} from the native core") })
            }
            None => Ok(spans),
        }
    }

    /// The spans to mask, sorted and non-overlapping. `&text[span.start..span.end]` is the value.
    pub fn detect(&self, text: &str) -> Result<Vec<Span>> {
        let options = self.options();
        let (mut out, mut n) = (ptr::null_mut(), 0usize);
        // SAFETY: text is valid for text.len() bytes; the model and the lists outlive the call (`&self`).
        let code = unsafe { ffi::pii_detect(self.model, text.as_ptr() as *const c_char, text.len(), &options, &mut out, &mut n) };
        Self::take(code, out, n)
    }

    /// Table mode for CSV, TSV, semicolon-separated and markdown pipe tables: the whole text is read at once and every
    /// span is clipped to a cell value, so delimiters, quotes and line breaks are never masked. Text that is not a table
    /// gives [`Redactor::detect`].
    pub fn detect_table(&self, text: &str, table: &TableOptions) -> Result<Vec<Span>> {
        let options = self.options();
        let mut t = std::mem::MaybeUninit::<ffi::pii_table_options>::zeroed();
        // SAFETY: pii_table_options_init writes every field.
        let mut t = unsafe {
            ffi::pii_table_options_init(t.as_mut_ptr());
            t.assume_init()
        };
        t.delimiter = match table.delimiter {
            Delimiter::Auto => 0,
            Delimiter::Comma => b',' as c_int,
            Delimiter::Tab => b'\t' as c_int,
            Delimiter::Semicolon => b';' as c_int,
            Delimiter::Markdown => b'|' as c_int,
        };
        t.header = table.header as c_int;
        t.vote = table.vote as c_int;
        t.context = (table.context == TableContext::Header) as c_int;
        let (mut out, mut n) = (ptr::null_mut(), 0usize);
        // SAFETY: as detect; t is a valid table options struct of the library's size.
        let code =
            unsafe { ffi::pii_detect_table(self.model, text.as_ptr() as *const c_char, text.len(), &options, &t, &mut out, &mut n) };
        Self::take(code, out, n)
    }

    /// The text with every span replaced as `mode` says, and the spans.
    pub fn redact(&self, text: &str, mode: Mode) -> Result<Redacted> {
        let spans = self.detect(text)?;
        Ok(Redacted { text: apply_redaction(text, &spans, mode), spans })
    }

    /// The text with every span replaced by `replace(span, original)`: pseudonyms, hashes, your own tags.
    pub fn redact_with<F: FnMut(&Span, &str) -> String>(&self, text: &str, replace: F) -> Result<Redacted> {
        let spans = self.detect(text)?;
        Ok(Redacted { text: apply_redaction_with(text, &spans, replace), spans })
    }

    /// The text with numbered placeholders that `vault` can undo (`"[PERSON_1]"`, the same value always the same tag), and
    /// the spans. The vault's known values are also masked where they occur again and the model does not find them
    /// (spans with [`crate::Source::Vault`]); placeholder-like text left unmasked is escaped (`"［PERSON_1]"`). Since 1.7.2.
    pub fn redact_vault(&self, text: &str, vault: &mut crate::Vault) -> Result<Redacted> {
        let (text, spans) = crate::vault::redact_with_vault(text, self.detect(text)?, vault);
        Ok(Redacted { text, spans })
    }

    /// [`Redactor::detect_table`] and redaction. The output is still a valid table.
    pub fn redact_table(&self, text: &str, mode: Mode, table: &TableOptions) -> Result<Redacted> {
        let spans = self.detect_table(text, table)?;
        Ok(Redacted { text: apply_redaction(text, &spans, mode), spans })
    }

    /// The values found in the text, grouped by label, as structured data instead of a masked text.
    ///
    /// ```no_run
    /// # use newmean_pii::{Label, Redactor};
    /// # let redactor = Redactor::from_dir("models/pii-medium")?;
    /// let found = redactor.extract("안녕하세요 이준호입니다 제 이메일은 joon@example.com입니다")?;
    /// assert_eq!(found.first(Label::Person), Some("이준호"));
    /// assert_eq!(found.to_json(), r#"{"person":["이준호"],"email":["joon@example.com"]}"#);
    /// # Ok::<(), newmean_pii::Error>(())
    /// ```
    pub fn extract(&self, text: &str) -> Result<Extracted> {
        Ok(Extracted::new(text, self.detect(text)?))
    }

    /// Token ids and offsets as the model's tokenizer gives them, without `<s>` and `</s>`.
    pub fn tokenize(&self, text: &str) -> Result<Vec<Token>> {
        let (mut out, mut n) = (ptr::null_mut(), 0usize);
        // SAFETY: text is valid for text.len() bytes; the model outlives the call.
        let code = unsafe { ffi::pii_tokenize(self.model, text.as_ptr() as *const c_char, text.len(), &mut out, &mut n) };
        if code != ffi::PII_OK {
            return Err(core_error(code));
        }
        let mut tokens = Vec::with_capacity(n);
        if !out.is_null() {
            // SAFETY: on PII_OK the core returns n tokens at out; freed once below.
            for t in unsafe { std::slice::from_raw_parts(out, n) } {
                tokens.push(Token {
                    id: t.id,
                    start: t.byte_start,
                    end: t.byte_end,
                    char_start: t.cp_start,
                    char_end: t.cp_end,
                    utf16_start: t.u16_start,
                    utf16_end: t.u16_end,
                });
            }
            unsafe { ffi::pii_tokens_free(out) };
        }
        Ok(tokens)
    }

    /// Versions, model files, kernel build and list sizes of this redactor.
    pub fn info(&self) -> Info {
        let mut m = ffi::pii_model_info::default();
        let (mut allow, mut block) = (0usize, 0usize);
        // SAFETY: the model and the lists handle are valid; the out pointers are valid; the build string is static.
        let build = unsafe {
            ffi::pii_model_get_info(self.model, &mut m);
            ffi::pii_lists_counts(self.lists.0, &mut allow, &mut block);
            CStr::from_ptr(ffi::pii_build_info()).to_string_lossy().into_owned()
        };
        Info {
            package_version: crate::VERSION,
            model_version: MODEL_VERSION,
            size: self.size,
            variant: self.variant,
            model_path: self.model_path.clone(),
            tokenizer_path: self.tokenizer_path.clone(),
            build,
            hidden: m.hidden as usize,
            layers: m.layers as usize,
            vocab: m.vocab as usize,
            max_tokens: m.max_tokens as usize,
            allow,
            block,
        }
    }
}

/// The version of the native core compiled into this crate (the same number as the crate's).
pub fn core_version() -> String {
    // SAFETY: a static NUL-terminated string.
    unsafe { CStr::from_ptr(ffi::pii_version()) }.to_string_lossy().into_owned()
}
