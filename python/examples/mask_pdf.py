"""Mask a PDF's text before it goes to a model (newmean-pii 1.7).

The models read text, not files: extract the text first, then mask it page by page. Scanned PDFs need OCR first.

    pip install newmean-pii pypdf
    python mask_pdf.py contract.pdf > contract.masked.txt
"""
import sys

from newmean_pii import create_redactor


def masked_pages(path, redactor, mode="tag"):
    """The masked text of each page of a PDF (pypdf's text extraction)."""
    from pypdf import PdfReader

    return [redactor.redact(page.extract_text() or "", mode).text for page in PdfReader(path).pages]


if __name__ == "__main__":
    redactor = create_redactor(size="medium")
    for i, text in enumerate(masked_pages(sys.argv[1], redactor), 1):
        print(f"--- page {i} ---\n{text}")
