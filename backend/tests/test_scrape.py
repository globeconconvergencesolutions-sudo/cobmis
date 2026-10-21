"""End-to-end scraper tests against an in-process fake of cob.go.ke (no network)."""

import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from app.ingest.scrape import PoliteClient, find_file_link, run

FIXTURE = Path(__file__).parent / "fixtures" / "cob_listing_synthetic.html"
BASE = "https://cob.go.ke"
LISTING = f"{BASE}/reports/consolidated-county-budget-implementation-review-reports/"


def make_pdf(text_pages: int, blank_pages: int = 0) -> bytes:
    doc = pymupdf.open()
    for i in range(text_pages):
        page = doc.new_page()
        page.insert_text((50, 72), f"County Governments Budget Implementation Review page {i}. " * 3)
        page.insert_text((50, 100), "Absorption rate of development expenditure was 45.2 per cent. " * 3)
        page.insert_text((50, 128), "Pending bills amounted to Kshs.164.76 billion. " * 3)
    for _ in range(blank_pages):
        doc.new_page()
    # Pad so the file clears the 10 KB sanity floor.
    doc.set_metadata({"subject": "x" * 12000})
    return doc.tobytes()


TEXT_PDF = make_pdf(4)
SCANNED_PDF = make_pdf(0, blank_pages=4)


class DroppedStream(httpx.SyncByteStream):
    """Sends some bytes, then the connection drops."""

    def __init__(self, data: bytes):
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")


class FakeCob:
    """Routes for the synthetic listing's 7 packages, each exercising a different code path."""

    def __init__(self):
        self.calls: list[str] = []
        self.range_headers: list[str | None] = []
        self.truncate_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        self.calls.append(path)
        pdf_headers = {"content-type": "application/pdf"}

        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /wp-admin/\n")
        if path == "/reports/consolidated-county-budget-implementation-review-reports/":
            return httpx.Response(200, text=FIXTURE.read_text())

        slug = "county-governments-budget-implementation-review-report-for-the-first-quarter-of-fy-2023-24"
        # 1) package page redirects straight to the PDF
        if path == f"/download/{slug}/":
            return httpx.Response(302, headers={"location": "/wp-content/uploads/q1-2023-24.pdf"})
        if path == "/wp-content/uploads/q1-2023-24.pdf":
            self.range_headers.append(request.headers.get("range"))
            if self.truncate_once:
                self.truncate_once = False
                return httpx.Response(200, headers=pdf_headers, stream=DroppedStream(TEXT_PDF[: len(TEXT_PDF) // 2]))
            if rng := request.headers.get("range"):
                start = int(rng.split("=")[1].rstrip("-"))
                return httpx.Response(206, headers=pdf_headers, content=TEXT_PDF[start:])
            return httpx.Response(200, headers=pdf_headers, content=TEXT_PDF)
        # 2) WPDM package page with a data-downloadurl button
        if path == "/download/cgbirr-half-year-2023-24/":
            return httpx.Response(200, text='<a class="wpdm-download-link" data-downloadurl="https://cob.go.ke/download/cgbirr-half-year-2023-24/?wpdmdl=101&amp;refresh=abc">Download</a>')
        if path == "/download/cgbirr-half-year-2023-24/?wpdmdl=101&refresh=abc":
            return httpx.Response(200, headers={"content-type": "application/octet-stream", "content-disposition": 'attachment; filename="h1.pdf"'}, content=TEXT_PDF)
        # 3) scanned PDF
        if path == "/download/cgbirr-nine-months-fy-2023-2024/":
            return httpx.Response(200, headers=pdf_headers, content=SCANNED_PDF)
        # 4) listed 0 KB, page has no file
        if path == "/download/annual-cgbirr-fy-2021-22/":
            return httpx.Response(200, text="<p>No file attached.</p>")
        # 5) 404
        if path == "/download/third-quarter-cbirr-2016-17/":
            return httpx.Response(404)
        # 6) claims PDF but serves an HTML error page
        if path == "/download/cbirr-2016-17-misc/":
            return httpx.Response(200, headers=pdf_headers, content=b"<html>error</html>" * 1000)
        # 7) server error
        if path == "/download/q4-2012-13/":
            return httpx.Response(503)
        return httpx.Response(404)


def client_for(fake: FakeCob) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(fake), sleep=lambda s: None, min_delay=0, max_delay=0)


def load(data_dir: Path) -> dict[str, dict]:
    docs = json.loads((data_dir / "manifest.json").read_text())["documents"]
    return {d["slug"]: d for d in docs}


def test_full_run_statuses(tmp_path):
    fake = FakeCob()
    summary = run(tmp_path, LISTING, client=client_for(fake))
    m = load(tmp_path)
    assert summary["entries"] == 7 and summary["downloaded"] == 7

    q1 = m["county-governments-budget-implementation-review-report-for-the-first-quarter-of-fy-2023-24"]
    assert q1["status"] == "ok" and q1["page_count"] == 4 and q1["text_page_ratio"] == 1.0
    assert (tmp_path / "raw" / q1["file"]).read_bytes() == TEXT_PDF
    assert q1["file"].startswith("2023-24_Q1_")

    # Direct-redirect PDFs are saved from the first response: one request, not two.
    assert fake.calls.count("/wp-content/uploads/q1-2023-24.pdf") == 1
    assert m["cgbirr-half-year-2023-24"]["status"] == "ok"
    assert m["cgbirr-half-year-2023-24"]["file_url"].endswith("wpdmdl=101&refresh=abc")
    assert m["cgbirr-nine-months-fy-2023-2024"]["status"] == "scanned"
    assert m["annual-cgbirr-fy-2021-22"]["status"] == "unavailable"
    assert "0 KB" in m["annual-cgbirr-fy-2021-22"]["reason"]
    assert m["third-quarter-cbirr-2016-17"]["status"] == "unavailable"
    assert m["cbirr-2016-17-misc"]["status"] == "failed" and "magic" in m["cbirr-2016-17-misc"]["reason"]
    assert m["q4-2012-13"]["status"] == "failed" and "503" in m["q4-2012-13"]["reason"]
    assert not list((tmp_path / "raw").glob("*.part"))
    assert (tmp_path / "listing_snapshot.html").exists()


def test_rerun_skips_completed_and_unavailable(tmp_path):
    fake = FakeCob()
    run(tmp_path, LISTING, client=client_for(fake))
    before = load(tmp_path)

    fake2 = FakeCob()
    summary = run(tmp_path, LISTING, client=client_for(fake2))
    # Only the failed ones (misc, q4) are retried; ok/scanned/unavailable are skipped.
    assert summary["skipped"] == 5 and summary["downloaded"] == 2
    assert not any("q1-2023-24.pdf" in c or "half-year" in c for c in fake2.calls)
    after = load(tmp_path)
    for slug in ("cgbirr-half-year-2023-24", "cgbirr-nine-months-fy-2023-2024"):
        assert after[slug]["sha256"] == before[slug]["sha256"]


def test_check_new_only_fetches_listing(tmp_path):
    run(tmp_path, LISTING, client=client_for(FakeCob()))
    fake = FakeCob()
    summary = run(tmp_path, LISTING, check_new=True, client=client_for(fake))
    assert summary["new"] == 0 and summary["downloaded"] == 0
    assert fake.calls == ["/robots.txt", "/reports/consolidated-county-budget-implementation-review-reports/"]


def test_corrupted_file_is_redownloaded(tmp_path):
    run(tmp_path, LISTING, client=client_for(FakeCob()))
    q1 = next(d for d in load(tmp_path).values() if d["slug"].endswith("first-quarter-of-fy-2023-24"))
    (tmp_path / "raw" / q1["file"]).write_bytes(b"%PDF-garbage")
    fake = FakeCob()
    run(tmp_path, LISTING, client=client_for(fake))
    assert "/wp-content/uploads/q1-2023-24.pdf" in fake.calls
    assert (tmp_path / "raw" / q1["file"]).read_bytes() == TEXT_PDF


def test_truncated_download_resumes_with_range(tmp_path):
    fake = FakeCob()
    fake.truncate_once = True
    run(tmp_path, LISTING, client=client_for(fake))
    q1 = next(d for d in load(tmp_path).values() if d["slug"].endswith("first-quarter-of-fy-2023-24"))
    assert q1["status"] == "failed" and "RemoteProtocolError" in q1["reason"]
    assert list((tmp_path / "raw").glob("*.part"))

    fake2 = FakeCob()
    run(tmp_path, LISTING, client=client_for(fake2))
    q1 = next(d for d in load(tmp_path).values() if d["slug"].endswith("first-quarter-of-fy-2023-24"))
    assert q1["status"] == "ok"
    # First hit is the package URL redirecting to the file (probe), then the ranged resume.
    assert fake2.range_headers[-1] == f"bytes={len(TEXT_PDF) // 2}-"
    assert (tmp_path / "raw" / q1["file"]).read_bytes() == TEXT_PDF


def test_robots_disallow_is_respected(tmp_path):
    fake = FakeCob()
    orig = fake.__call__

    def handler(request):
        if request.url.path == "/robots.txt":
            fake.calls.append("/robots.txt")
            return httpx.Response(200, text="User-agent: *\nDisallow: /download/\n")
        return orig(request)

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, min_delay=0, max_delay=0)
    run(tmp_path, LISTING, client=client)
    m = load(tmp_path)
    assert all(d["status"] == "failed" and "robots.txt" in d["reason"] for d in m.values())
    assert not any(c.startswith("/download/") for c in fake.calls)


def test_rate_limit_sleeps_between_requests():
    slept = []
    fake = FakeCob()
    client = PoliteClient(transport=httpx.MockTransport(fake), sleep=slept.append, min_delay=2.0, max_delay=3.0)
    client.get(f"{BASE}/robots.txt")
    client.get(f"{BASE}/robots.txt")
    assert len(slept) == 1 and 1.9 <= slept[0] <= 3.0


@pytest.mark.parametrize(
    "html,expected",
    [
        ('<a data-downloadurl="/download/x/?wpdmdl=5">Download</a>', "https://cob.go.ke/download/x/?wpdmdl=5"),
        ('<a href="https://cob.go.ke/wp-content/uploads/2024/01/Report.PDF">x</a>', "https://cob.go.ke/wp-content/uploads/2024/01/Report.PDF"),
        ("""<button onclick="location.href='https://cob.go.ke/download/x/?wpdmdl=9&ind=1'">Get</button>""", "https://cob.go.ke/download/x/?wpdmdl=9&ind=1"),
        ("<p>Nothing to download</p>", None),
    ],
)
def test_find_file_link(html, expected):
    assert find_file_link(html, "https://cob.go.ke/download/x/") == expected
