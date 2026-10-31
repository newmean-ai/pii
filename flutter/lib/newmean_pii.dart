/// Detects and masks personal information (PII) in text on the device with the newmean-pii models: a port of the npm
/// package @newmean-ai/pii over the shared native core (dart:ffi). Nothing is sent anywhere; model files are read
/// locally or downloaded once from Hugging Face and checked against pinned SHA-256 values.
library;

export 'src/loader.dart' show PiiProgress, defaultCacheDir;
export 'src/models.dart'
    show PiiModelFile, defaultSize, defaultVariant, modelFile, modelSizes, modelVersion, nativeBuildInfo, nativeVersion;
export 'src/newmean_pii.dart' show NewmeanPii;
export 'src/redaction.dart' show PiiRedaction, RedactMode, applyRedaction;
export 'src/regex.dart' show regexRules, regexSpans;
export 'src/span.dart' show PiiSpan, labelFamily, level1Labels, piiLabels;
