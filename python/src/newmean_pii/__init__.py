"""newmean-pii: detect and mask personal information in text on this machine (54 languages), with the
newmean-pii-<size> models. Python package of the npm package @newmean-ai/pii; same models, same spans.

    from newmean_pii import create_redactor

    redactor = create_redactor(size="medium")
    redactor.redact("제 메일은 kim@example.com, 전화는 010-1234-5678입니다").text
    # '제 메일은 [EMAIL], 전화는 [PHONE]입니다'
"""
from ._models import (
    DEFAULT_SIZE,
    DEFAULT_VARIANT,
    MODEL_VERSION,
    SIZES,
    VARIANTS,
    FilePin,
    ModelFileError,
    default_cache_dir,
    fetch_model,
    model_file,
)
from ._redactor import LABELS, LEVEL1_LABELS, TAGS, Redactor, apply_redaction, create_redactor
from ._regex import REGEX_RULES, regex_spans
from ._types import RedactResult, Span, Token

__version__ = "1.0.0"

__all__ = [
    "DEFAULT_SIZE",
    "DEFAULT_VARIANT",
    "LABELS",
    "LEVEL1_LABELS",
    "MODEL_VERSION",
    "REGEX_RULES",
    "SIZES",
    "TAGS",
    "VARIANTS",
    "FilePin",
    "ModelFileError",
    "RedactResult",
    "Redactor",
    "Span",
    "Token",
    "apply_redaction",
    "create_redactor",
    "default_cache_dir",
    "fetch_model",
    "model_file",
    "regex_spans",
]
