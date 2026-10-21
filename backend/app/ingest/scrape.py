"""Polite scraper/downloader for the COB consolidated county BIRR reports.

- respects robots.txt, one request every 2-3 s, clear User-Agent
- resolves WordPress Download Manager /download/<slug>/ URLs to the actual PDF
- resumable downloads (.part + HTTP Range), SHA-256, %PDF magic + size sanity check
- idempotent manifest: reruns skip files already downloaded and unchanged on disk
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.ingest.cob_listing import ListingEntry, parse_listing

log = logging.getLogger("cob.scrape")

LISTING_URL = "https://cob.go.ke/reports/consolidated-county-budget-implementation-review-reports/"
USER_AGENT = "COB-Budget-Intelligence-Agent/0.1 (private research crawler; 1 request per 2-3 s)"
MIN_PDF_BYTES = 10 * 1024
SCRAPER_VERSION = 1


class Status:
    OK = "ok"
    UNAVAILABLE = "unavailable"  # listed with 0 KB, 404/410, or the download page offers no file
    FAILED = "failed"  # network error, not a PDF, truncated: worth retrying
    SCANNED = "scanned"  # downloaded fine, but has (almost) no text layer; needs OCR in Phase 2


@dataclass
class ManifestRecord:
    url: str
    slug: str
    title: str
    heading: str | None
    fiscal_year: str | None
    period: str | None
    listed_size: str | None
    status: str
    reason: str | None = None
    file: str | None = None
    size: int | None = None
    sha256: str | None = None
    file_url: str | None = None
    page_count: int | None = None
    text_page_ratio: float | None = None
    parse_notes: list[str] = field(default_factory=list)
    first_seen: str | None = None
    last_checked: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PoliteClient:
    def __init__(self, min_delay: float = 2.0, max_delay: float = 3.0, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.min_delay, self.max_delay = min_delay, max_delay
        self._last = 0.0
        self._sleep = sleep
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
            transport=transport,
        )
        self.robots: urllib.robotparser.RobotFileParser | None = None
        self.requests_made = 0

    def _wait(self) -> None:
        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.monotonic() - self._last
        if self._last and elapsed < delay:
            self._sleep(delay - elapsed)
        self._last = time.monotonic()

    def load_robots(self, site: str) -> None:
        rp = urllib.robotparser.RobotFileParser()
        self._wait()
        self.requests_made += 1
        try:
            resp = self.http.get(urljoin(site, "/robots.txt"))
            if resp.status_code in (401, 403):
                rp.disallow_all = True  # stdlib convention: an access-controlled robots.txt means keep out
            else:
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError as exc:
            log.warning("robots.txt unreachable (%s); proceeding with crawl-delay defaults", exc)
            rp.parse([])
        self.robots = rp
        if (cd := rp.crawl_delay(USER_AGENT)) and cd > self.min_delay:
            self.min_delay, self.max_delay = float(cd), float(cd) + 1

    def allowed(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(USER_AGENT, url)

    def get(self, url: str, **kw) -> httpx.Response:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        self._wait()
        self.requests_made += 1
        return self.http.get(url, **kw)

    def stream(self, url: str, **kw):
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        self._wait()
        self.requests_made += 1
        return self.http.stream("GET", url, **kw)


# ---------- resolving WPDM download pages ----------

_WPDM_ATTRS = ("data-downloadurl", "data-download-url", "data-href", "data-url")


def find_file_link(html: str, page_url: str) -> str | None:
    """Find the real file URL on a WPDM package page. Returns None if the page has no file."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for el in soup.find_all(True):
        for attr in _WPDM_ATTRS:
            if el.get(attr):
                candidates.append(el[attr])
        href = el.get("href")
        if href and ("wpdmdl=" in href or re.search(r"\.pdf(\?|$)", href, re.I)):
            candidates.append(href)
        onclick = el.get("onclick") or ""
        m = re.search(r"""['"](https?://[^'"]*(?:wpdmdl=|\.pdf)[^'"]*)['"]""", onclick)
        if m:
            candidates.append(m.group(1))
    for c in candidates:
        url = urljoin(page_url, c.replace("&amp;", "&"))
        if urlparse(url).scheme in ("http", "https"):
            return url
    return None


def _is_pdf_response(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    disp = resp.headers.get("content-disposition", "").lower()
    return "pdf" in ctype or "octet-stream" in ctype or ".pdf" in disp


# ---------- download ----------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _safe_name(entry: ListingEntry) -> str:
    """'<fy>_<period>_<slug prefix>-<8-char hash of URL>.pdf'. The slug is cut to keep paths short
    on Windows; the hash keeps names unique when two long slugs share a prefix."""
    fy = (entry.fiscal_year or "unknown-fy").replace("/", "-")
    period = entry.period or "unknown-period"
    slug = re.sub(r"[^a-z0-9-]+", "-", entry.slug.lower()).strip("-")[:80].rstrip("-")
    digest = hashlib.sha1(entry.url.encode()).hexdigest()[:8]
    return f"{fy}_{period}_{slug}-{digest}.pdf"


def _part_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".part")


def save_response(resp: httpx.Response, dest: Path, resumed: bool = False) -> tuple[str, str | None]:
    """Write an open streaming response to dest (via .part). Returns (status, reason)."""
    part = _part_path(dest)
    if resp.status_code in (404, 410):
        return Status.UNAVAILABLE, f"HTTP {resp.status_code} for file"
    if resp.status_code == 416 and resumed:
        pass  # we already have every byte
    elif resp.status_code not in (200, 206):
        return Status.FAILED, f"HTTP {resp.status_code} for file"
    elif not _is_pdf_response(resp):
        return Status.FAILED, f"not a PDF (content-type {resp.headers.get('content-type')!r})"
    else:
        # 200 to a Range request means the server ignored it: start over.
        mode = "ab" if resp.status_code == 206 and resumed else "wb"
        expected = None if resp.headers.get("content-encoding") else resp.headers.get("content-length")
        got = 0
        with part.open(mode) as f:
            for chunk in resp.iter_bytes():  # unbuffered, so a dropped connection keeps what arrived
                f.write(chunk)
                got += len(chunk)
        if expected and got < int(expected):
            return Status.FAILED, f"truncated ({got}/{expected} bytes); rerun to resume"
    if not part.exists():
        return Status.FAILED, "no data received"
    with part.open("rb") as f:
        magic = f.read(5)
    if magic != b"%PDF-":
        part.unlink()
        return Status.FAILED, "downloaded file is not a PDF (bad magic bytes)"
    if part.stat().st_size < MIN_PDF_BYTES:
        part.unlink()
        return Status.UNAVAILABLE, f"file too small ({part.stat().st_size} bytes)"
    part.replace(dest)
    return Status.OK, None


def download_to(client: PoliteClient, url: str, dest: Path) -> tuple[str, str | None]:
    """Stream url to dest, resuming a previous partial download with HTTP Range."""
    part = _part_path(dest)
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.stream(url, headers=headers) as resp:
        return save_response(resp, dest, resumed=bool(offset))


def inspect_pdf(path: Path) -> tuple[int | None, float | None]:
    """Page count and share of pages with a usable text layer (a cheap scanned-PDF signal)."""
    try:
        import pymupdf
    except ImportError:
        return None, None
    try:
        with pymupdf.open(path) as doc:
            n = doc.page_count
            sample = range(0, n, max(1, n // 40))  # up to ~40 pages, spread across the file
            with_text = sum(1 for i in sample if len(doc[i].get_text().strip()) >= 200)
            return n, round(with_text / max(1, len(sample)), 3)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not open %s: %s", path.name, exc)
        return None, None


# ---------- orchestration ----------


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.records: dict[str, dict[str, Any]] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.records = {r["url"]: r for r in data.get("documents", [])}

    def save(self, source_url: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {
            "source": source_url,
            "scraper_version": SCRAPER_VERSION,
            "updated_at": _now(),
            "documents": list(self.records.values()),
        }
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def _unchanged_on_disk(rec: dict[str, Any], raw_dir: Path) -> bool:
    if rec.get("status") not in (Status.OK, Status.SCANNED) or not rec.get("file"):
        return False
    path = raw_dir / rec["file"]
    return path.exists() and path.stat().st_size == rec.get("size") and _sha256(path) == rec.get("sha256")


def process_entry(client: PoliteClient, entry: ListingEntry, raw_dir: Path) -> ManifestRecord:
    rec = ManifestRecord(
        url=entry.url, slug=entry.slug, title=entry.title, heading=entry.heading,
        fiscal_year=entry.fiscal_year, period=entry.period, listed_size=entry.size_text,
        status=Status.FAILED, parse_notes=list(entry.parse_notes), last_checked=_now(),
    )
    dest = raw_dir / _safe_name(entry)
    try:
        # Most WPDM sites redirect /download/<slug>/?wpdmdl=<id> straight to the file; the bare
        # /download/<slug>/ is the package page. Ask for the page and look for the file link.
        partial = _part_path(dest).exists()
        with client.stream(entry.url) as resp:
            if resp.status_code in (404, 410):
                rec.status, rec.reason = Status.UNAVAILABLE, f"HTTP {resp.status_code} for download page"
                return rec
            if resp.status_code != 200:
                rec.status, rec.reason = Status.FAILED, f"HTTP {resp.status_code} for download page"
                return rec
            final_url = str(resp.url)
            if _is_pdf_response(resp):
                if partial:
                    direct_resume = True  # reopen below with a Range header
                else:
                    # Redirected straight to the file: save it from this response, one request.
                    rec.file_url = final_url
                    return _record_result(rec, dest, *save_response(resp, dest))
            else:
                direct_resume = False
                body = resp.read()
        if direct_resume:
            return _finish(rec, client, final_url, dest)
        link = find_file_link(body.decode("utf-8", "replace"), final_url)
        if not link:
            rec.status = Status.UNAVAILABLE
            rec.reason = (
                "listed as 0 KB and download page offers no file"
                if entry.listed_as_empty
                else "download page has no file link (may need a browser; see Playwright fallback)"
            )
            return rec
        return _finish(rec, client, link, dest)
    except PermissionError as exc:
        rec.status, rec.reason = Status.FAILED, str(exc)
    except httpx.HTTPError as exc:
        rec.status, rec.reason = Status.FAILED, f"{type(exc).__name__}: {exc}"
    return rec


def _finish(rec: ManifestRecord, client: PoliteClient, file_url: str, dest: Path) -> ManifestRecord:
    rec.file_url = file_url
    return _record_result(rec, dest, *download_to(client, file_url, dest))


def _record_result(rec: ManifestRecord, dest: Path, status: str, reason: str | None) -> ManifestRecord:
    rec.status, rec.reason = status, reason
    if status == Status.OK:
        rec.file = dest.name
        rec.size = dest.stat().st_size
        rec.sha256 = _sha256(dest)
        rec.page_count, rec.text_page_ratio = inspect_pdf(dest)
        if rec.text_page_ratio is not None and rec.text_page_ratio < 0.5:
            rec.status, rec.reason = Status.SCANNED, f"only {rec.text_page_ratio:.0%} of sampled pages have a text layer"
    return rec


def refresh_metadata(rec: dict[str, Any], entry: ListingEntry) -> None:
    """Re-apply listing-derived fields after parser fixes, without touching the downloaded file."""
    rec.update(
        title=entry.title, heading=entry.heading, fiscal_year=entry.fiscal_year,
        period=entry.period, listed_size=entry.size_text,
    )
    kept = [n for n in rec.get("parse_notes", []) if n == "no longer on listing page"]
    rec["parse_notes"] = list(entry.parse_notes) + kept


def run(
    data_dir: Path,
    listing_url: str = LISTING_URL,
    check_new: bool = False,
    retry_unavailable: bool = False,
    limit: int | None = None,
    client: PoliteClient | None = None,
    listing_html: str | None = None,
) -> dict[str, Any]:
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(data_dir / "manifest.json")
    client = client or PoliteClient()
    site = f"{urlparse(listing_url).scheme}://{urlparse(listing_url).netloc}"

    client.load_robots(site)
    if listing_html is None:
        resp = client.get(listing_url)
        resp.raise_for_status()
        listing_html = resp.text
        snap = data_dir / "listing_snapshot.html"
        snap.write_text(listing_html, encoding="utf-8")

    entries = parse_listing(listing_html, listing_url)
    if not entries:
        raise RuntimeError("No /download/ links found on the listing page; the layout may have changed.")
    log.info("listing: %d entries", len(entries))

    actions = {"downloaded": [], "skipped": [], "new": []}
    seen = set()
    for i, entry in enumerate(entries):
        if limit is not None and len(actions["downloaded"]) >= limit:
            break
        seen.add(entry.url)
        old = manifest.records.get(entry.url)
        if old is None:
            actions["new"].append(entry.url)
        elif check_new:
            continue
        elif _unchanged_on_disk(old, raw_dir):
            refresh_metadata(old, entry)
            old["last_checked"] = _now()
            actions["skipped"].append(entry.url)
            continue
        elif old.get("status") == Status.UNAVAILABLE and not retry_unavailable:
            actions["skipped"].append(entry.url)
            continue

        log.info("[%d/%d] %s", i + 1, len(entries), entry.title)
        rec = process_entry(client, entry, raw_dir)
        rec.first_seen = (old or {}).get("first_seen") or _now()
        manifest.records[entry.url] = asdict(rec)
        actions["downloaded"].append(entry.url)
        manifest.save(listing_url)  # save after every file so an interrupted run loses nothing
        log.info("    -> %s %s", rec.status, rec.reason or "")

    gone = [u for u in manifest.records if u not in seen and limit is None]
    for u in gone:
        notes = manifest.records[u].setdefault("parse_notes", [])
        if "no longer on listing page" not in notes:
            notes.append("no longer on listing page")
    manifest.save(listing_url)
    return {"entries": len(entries), "requests": client.requests_made, **{k: len(v) for k, v in actions.items()}, "gone": gone}
