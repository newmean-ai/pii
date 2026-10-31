"""Per-character NFKC for tokenizers without a Unicode normalizer (mDeBERTa's SentencePiece: full-width punctuation and digits,
Thai ำ, compatibility jamo became UNK — zh 6.4%, th 1.7%, ja 1.5%, ko 0.7%).

normalize(text) → (norm, back): back[j] = index in `text` of normalized character j (len(norm)+1 entries; the last = len(text)).
Characters are normalized one at a time, so a token's normalized offsets (s, e) map back to original (back[s], back[e-1] + 1)
even when a character expands (ำ → ํ + า) — labels and predictions stay on the original text.
"""
import unicodedata


def normalize(text: str):
    out, back = [], []
    for i, c in enumerate(text):
        n = unicodedata.normalize("NFKC", c) or c
        out.append(n)
        back.extend([i] * len(n))
    back.append(len(text))
    return "".join(out), back


def to_original(offsets, back):
    """Token offsets in normalized text → offsets in the original text (special tokens (0, 0) stay as they are)."""
    return [(back[s], back[e - 1] + 1) if e > s else (s, e) for s, e in offsets]


def needs(base: str) -> bool:
    return "mdeberta" in base.lower()
