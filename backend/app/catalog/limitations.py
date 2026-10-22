"""Known corpus notes shown in catalog / About (versioned with the code)."""

from __future__ import annotations

# Short, accurate notes — not a bug wall. Live counts (OCR, quarantine, cross-period)
# are appended by the catalog service when non-zero.
STATIC_LIMITATIONS = [
    "Numeric rankings use chapter-2 all-county tables; per-county chapter-3 detail is answered from report text and citations.",
    "County filters follow report section headings (source PDFs sometimes mislabel table captions).",
    "A few image-only pages (covers / key-highlights) are skipped until OCR is enabled.",
    "Coverage is exactly the reports ingested on this machine.",
]
