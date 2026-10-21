"""Parse the COB consolidated county BIRR listing page: entries, fiscal years, periods, sizes.

Pure functions (no network) so they can be tested against saved HTML snapshots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

# FY 2023/24, 2023/2024, 2023-24, 2023 – 2024, FY2023/24
FY_RE = re.compile(r"(?<!\d)(20\d{2})\s*[/\-–—]\s*(20\d{2}|\d{2})(?!\d)")
SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(bytes|B|KB|MB|GB)\b", re.I)
DOWNLOAD_PATH_RE = re.compile(r"^/download/([^/?#]+)/?$")

# Order matters: the first matching pattern wins. Cumulative periods are what COB reports,
# so "third quarter" means nine months and "fourth quarter" means the full year.
PERIOD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("9M", re.compile(r"nine[\s-]*months?|9[\s-]*months?|third[\s-]*quarter|3rd[\s-]*quarter|\bq3\b|quarter[\s-]*three", re.I)),
    ("H1", re.compile(r"half[\s-]*year|first[\s-]*half|six[\s-]*months?|6[\s-]*months?|second[\s-]*quarter|2nd[\s-]*quarter|\bq2\b|quarter[\s-]*two|mid[\s-]*year", re.I)),
    ("FY", re.compile(r"annual|full[\s-]*year|twelve[\s-]*months?|12[\s-]*months?|fourth[\s-]*quarter|4th[\s-]*quarter|\bq4\b|quarter[\s-]*four|end[\s-]*of[\s-]*(?:the[\s-]*)?(?:financial|fiscal)[\s-]*year", re.I)),
    ("Q1", re.compile(r"first[\s-]*quarter|1st[\s-]*quarter|\bq1\b|quarter[\s-]*one|three[\s-]*months?|3[\s-]*months?", re.I)),
]

FULL_YEAR_ONLY_RE = re.compile(r"\b(?:fy|financial\s+year|fiscal\s+year)\s*(?:fy\s*)?20\d{2}\s*[/\-–—]\s*\d{2,4}\b", re.I)

_UNITS = {"b": 1, "bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}


@dataclass
class ListingEntry:
    url: str
    slug: str
    title: str
    heading: str | None
    fiscal_year: str | None
    period: str | None
    size_text: str | None
    size_bytes: int | None
    parse_notes: list[str]

    @property
    def listed_as_empty(self) -> bool:
        return self.size_bytes is not None and self.size_bytes == 0


def parse_fiscal_years(text: str | None) -> list[str]:
    """All distinct fiscal years in text, normalised to 'YYYY/YY', in order of appearance.
    Only consecutive years are accepted, to reject date ranges."""
    found: list[str] = []
    for m in FY_RE.finditer(text or ""):
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw) if len(end_raw) == 4 else (start // 100) * 100 + int(end_raw)
        fy = f"{start}/{str(end)[-2:]}"
        if end == start + 1 and fy not in found:
            found.append(fy)
    return found


def parse_fiscal_year(text: str | None) -> str | None:
    fys = parse_fiscal_years(text)
    return fys[0] if fys else None


def parse_period(text: str | None) -> str | None:
    if not text:
        return None
    for code, pat in PERIOD_PATTERNS:
        if pat.search(text):
            return code
    return None


def parse_size(text: str | None) -> int | None:
    if not text:
        return None
    m = SIZE_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return int(value * _UNITS[m.group(2).lower()])


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _download_slug(href: str, base_url: str) -> tuple[str, str] | None:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.netloc and parsed.netloc.removeprefix("www.") != urlparse(base_url).netloc.removeprefix("www."):
        return None
    m = DOWNLOAD_PATH_RE.match(parsed.path)
    if not m:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/download/{m.group(1)}/", m.group(1)


def _entry_container(a: Tag) -> Tag:
    """Smallest ancestor that looks like one listing item (holds exactly one download link,
    and no section heading)."""
    best: Tag = a
    for parent in a.parents:
        if not isinstance(parent, Tag) or parent.name in ("body", "html", "[document]", "main"):
            break
        hrefs = {
            x.get("href", "").rstrip("/")
            for x in parent.find_all("a", href=True)
            if "/download/" in x.get("href", "")
        }
        if len(hrefs) > 1 or any(h not in a.parents for h in parent.find_all(list(HEADING_TAGS))):
            break
        best = parent
        if parent.name in ("li", "tr", "article") or "wpdm" in " ".join(parent.get("class", [])):
            break
    return best


HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _title_for(a: Tag, container: Tag, slug: str) -> str:
    candidates = [_clean(a.get_text(" ")), _clean(a.get("title", ""))]
    for sel in (".package-title", "[class*=title]", "h1", "h2", "h3", "h4", "h5", "h6", "strong"):
        for el in container.select(sel):
            candidates.append(_clean(el.get_text(" ")))
    generic = re.compile(r"^(download|view|open|click here|pdf|\d.*(kb|mb))$", re.I)
    for c in candidates:
        if c and not generic.match(c) and len(c) > 8:
            return c
    return slug.replace("-", " ")


def parse_listing(html: str, base_url: str) -> list[ListingEntry]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("main, article, #content, .entry-content, .elementor") or soup

    entries: dict[str, ListingEntry] = {}
    current_heading: str | None = None
    current_heading_fy: str | None = None

    # Walk elements in document order so each link is attributed to the nearest FY heading above it.
    for el in root.find_all(True):
        # On the live page each entry's title is an <h3> wrapping its own download link, so a
        # section heading is one that does not contain a download link.
        has_download = any("/download/" in x.get("href", "") for x in el.find_all("a", href=True))
        if not has_download and (
            el.name in HEADING_TAGS
            or (el.name in ("p", "strong", "span", "div", "button", "summary") and len(el.get_text(strip=True)) < 120)
        ):
            text = _clean(el.get_text(" "))
            fys = parse_fiscal_years(text)
            if fys and (el.name in HEADING_TAGS or re.search(r"\b(fy|financial|fiscal)\b", text, re.I) or text == fys[0].split("/")[0]):
                # The live accordion groups several years under one title ("FY 2021/2022, ...,
                # FY 2025/2026"); such a heading cannot supply a single fallback year.
                current_heading = text
                current_heading_fy = fys[0] if len(fys) == 1 else None
            continue

        if el.name != "a" or not el.get("href"):
            continue
        hit = _download_slug(el["href"], base_url)
        if not hit:
            continue
        url, slug = hit
        if url in entries:
            continue

        container = _entry_container(el)
        title = _title_for(el, container, slug)
        container_text = _clean(container.get_text(" "))
        size_text_match = SIZE_RE.search(container_text)
        size_text = size_text_match.group(0) if size_text_match else None

        notes: list[str] = []
        # Title first, then slug, then the heading above. The heading is the fallback because
        # titles occasionally mention a prior year for comparison.
        fy = parse_fiscal_year(title) or parse_fiscal_year(slug)
        if not fy:
            fy = current_heading_fy
            if fy:
                notes.append("fiscal_year from heading")
        elif current_heading_fy and fy != current_heading_fy:
            notes.append(f"title FY {fy} != heading FY {current_heading_fy}")
        period = parse_period(title) or parse_period(slug.replace("-", " "))
        if not period and fy and FULL_YEAR_ONLY_RE.search(title):
            # "... REPORT FOR THE FINANCIAL YEAR 2025/26" / "... REPORT FY 2024/25": a title
            # naming only the year, with no quarter/half/nine-month wording, is the annual report.
            period = "FY"
            notes.append("period FY inferred: title names the year with no sub-period")
        if not period:
            notes.append("period not recognised")

        entries[url] = ListingEntry(
            url=url,
            slug=slug,
            title=title,
            heading=current_heading,
            fiscal_year=fy,
            period=period,
            size_text=size_text,
            size_bytes=parse_size(size_text),
            parse_notes=notes,
        )
    return list(entries.values())
