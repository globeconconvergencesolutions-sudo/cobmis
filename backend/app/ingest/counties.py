"""The 47 counties: official code, canonical name, former province (region), aliases."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class County:
    code: int
    name: str
    region: str


_COUNTIES = [
    (1, "Mombasa", "Coast"), (2, "Kwale", "Coast"), (3, "Kilifi", "Coast"), (4, "Tana River", "Coast"),
    (5, "Lamu", "Coast"), (6, "Taita-Taveta", "Coast"), (7, "Garissa", "North Eastern"),
    (8, "Wajir", "North Eastern"), (9, "Mandera", "North Eastern"), (10, "Marsabit", "Eastern"),
    (11, "Isiolo", "Eastern"), (12, "Meru", "Eastern"), (13, "Tharaka-Nithi", "Eastern"),
    (14, "Embu", "Eastern"), (15, "Kitui", "Eastern"), (16, "Machakos", "Eastern"),
    (17, "Makueni", "Eastern"), (18, "Nyandarua", "Central"), (19, "Nyeri", "Central"),
    (20, "Kirinyaga", "Central"), (21, "Murang'a", "Central"), (22, "Kiambu", "Central"),
    (23, "Turkana", "Rift Valley"), (24, "West Pokot", "Rift Valley"), (25, "Samburu", "Rift Valley"),
    (26, "Trans Nzoia", "Rift Valley"), (27, "Uasin Gishu", "Rift Valley"),
    (28, "Elgeyo-Marakwet", "Rift Valley"), (29, "Nandi", "Rift Valley"), (30, "Baringo", "Rift Valley"),
    (31, "Laikipia", "Rift Valley"), (32, "Nakuru", "Rift Valley"), (33, "Narok", "Rift Valley"),
    (34, "Kajiado", "Rift Valley"), (35, "Kericho", "Rift Valley"), (36, "Bomet", "Rift Valley"),
    (37, "Kakamega", "Western"), (38, "Vihiga", "Western"), (39, "Bungoma", "Western"),
    (40, "Busia", "Western"), (41, "Siaya", "Nyanza"), (42, "Kisumu", "Nyanza"),
    (43, "Homa Bay", "Nyanza"), (44, "Migori", "Nyanza"), (45, "Kisii", "Nyanza"),
    (46, "Nyamira", "Nyanza"), (47, "Nairobi City", "Nairobi"),
]
COUNTIES: list[County] = [County(*c) for c in _COUNTIES]
BY_CODE = {c.code: c for c in COUNTIES}

# Extra spellings seen in reports, keyed by normalised form.
_ALIASES = {
    "nairobi": 47, "homabay": 43, "muranga": 21, "taita taveta": 6, "tharaka nithi": 13,
    "elgeyo marakwet": 28, "transnzoia": 26, "uasingishu": 27, "westpokot": 24, "tanariver": 4,
    "keiyo marakwet": 28,
}


def normalise(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[’'`‘]", "", s)
    s = re.sub(r"\b(county|government|of|the|city|assembly|executive)\b", " ", s)
    s = re.sub(r"[^a-z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_LOOKUP: dict[str, int] = {}
for _c in COUNTIES:
    _LOOKUP[normalise(_c.name)] = _c.code
    _LOOKUP[normalise(_c.name).replace(" ", "")] = _c.code
_LOOKUP.update(_ALIASES)


def match_county(text: str | None, fuzzy: bool = True) -> County | None:
    """Exact (after normalisation), alias, then close fuzzy match for OCR/extraction typos."""
    if not text:
        return None
    key = normalise(text)
    if not key:
        return None
    code = _LOOKUP.get(key) or _LOOKUP.get(key.replace(" ", ""))
    if code is None and fuzzy and len(key) >= 4:
        hit = difflib.get_close_matches(key.replace(" ", ""), [k for k in _LOOKUP if " " not in k], n=1, cutoff=0.85)
        code = _LOOKUP[hit[0]] if hit else None
    return BY_CODE.get(code) if code else None


_MENTION_RE = re.compile(
    r"\b(?:" + "|".join(
        sorted(
            {re.escape(c.name).replace(r"\-", r"[\s\-]?").replace(r"\ ", r"[\s\-]?").replace("'", "['’]?") for c in COUNTIES}
            | {"Nairobi", "Muranga", "Homabay"},
            key=len,
            reverse=True,
        )
    ) + r")\b",
    re.I,
)


def county_mentions(text: str) -> dict[int, int]:
    """Count mentions of each county in free text: {county_code: count}."""
    out: dict[int, int] = {}
    for m in _MENTION_RE.finditer(text):
        c = match_county(m.group(0), fuzzy=False)
        if c:
            out[c.code] = out.get(c.code, 0) + 1
    return out
