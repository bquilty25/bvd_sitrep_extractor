#!/usr/bin/env python3
"""
SitRep MVE RDC — PDF Fetcher
==============================
Discovers and downloads INSP DRC MVE SitRep PDFs to a local archive.

Scrapes the INSP website for PDF links and downloads any not already present.
A manifest.json in the archive directory tracks what has been downloaded and when.

Usage:
  python3 fetch_sitreps.py                    # scan default pages, download to ./pdfs/
  python3 fetch_sitreps.py --pdf-dir archive/ # use a custom archive directory
  python3 fetch_sitreps.py --pages https://insp.cd/ebola/ https://insp.cd/category/sitrep/

Daily workflow (run both together):
  python3 fetch_sitreps.py && python3 extract_sitrep.py --update
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

# Pages to scrape for PDF links by default
DEFAULT_PAGES = [
    "https://insp.cd/ebola/",
    "https://insp.cd/category/sitrep/",
]

# A PDF URL must contain at least one of these tokens to be considered relevant
RELEVANCE_TOKENS = ["mve", "sitrep", "ebola", "marburg"]

# Only download PDFs uploaded in this month or later (YYYY/MM as it appears in the URL)
DEFAULT_SINCE = "2026/05"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_relevant(url: str) -> bool:
    """Return True if the PDF URL looks like a SitRep/MVE/Ebola document."""
    low = url.lower()
    return any(tok in low for tok in RELEVANCE_TOKENS)


def _is_recent_enough(url: str, min_ym: str) -> bool:
    """
    Return True if the wp-content upload date in the URL is >= min_ym.
    min_ym should be in 'YYYY/MM' format (e.g. '2026/05').
    URLs without a recognisable date are included by default.
    """
    m = re.search(r'/wp-content/uploads/(\d{4})/(\d{2})/', url)
    if m:
        return f"{m.group(1)}/{m.group(2)}" >= min_ym
    return True


def _title_from_filename(filename: str) -> str:
    """Derive a human-readable title from a SitRep PDF filename."""
    stem = Path(filename).stem
    title = re.sub(r'(?i)^draft[_\-\s]+', '', stem)
    title = re.sub(r'[_\-]+', ' ', title)
    title = re.sub(r'(?i)\s+vf\d*$', '', title)
    return title.strip()


def _parse_last_modified(header_value: str) -> str:
    """Parse an HTTP Last-Modified header to an ISO-8601 string, or return ''."""
    if not header_value:
        return ""
    try:
        return parsedate_to_datetime(header_value).isoformat()
    except Exception:
        return ""


# Regex to follow SitRep blog-post links found on category/listing pages
_POST_SLUG_RE = re.compile(
    r'href=["\']((https?://insp\.cd/)?sitrep-mve[^"\']+)["\']',
    re.IGNORECASE,
)


def discover_pdf_urls(pages: list, min_ym: str = DEFAULT_SINCE) -> list:
    """
    Scrape each page for wp-content PDF links, following SitRep blog-post links
    one level deep to pick up PDFs embedded in individual post pages.
    Returns a deduplicated list of dicts: {url, title, source_page}.
    """
    found: dict = {}
    a_pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']*wp-content/uploads/[^"\']*\.pdf)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    session = requests.Session()
    session.headers["User-Agent"] = "MSF-Epicentre-SitRep-Fetcher/1.0"

    def _scan(html: str, source_page: str, default_title: str = "") -> None:
        """Extract PDF links from html and add new, relevant ones to found."""
        for m in a_pattern.finditer(html):
            pdf_url = urljoin(source_page, m.group(1))
            if not (_is_relevant(pdf_url) and _is_recent_enough(pdf_url, min_ym)):
                continue
            if pdf_url in found:
                continue
            raw_text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            filename  = unquote(Path(urlparse(pdf_url).path).name)
            title     = raw_text or default_title or _title_from_filename(filename)
            found[pdf_url] = {"url": pdf_url, "title": title, "source_page": source_page}

    for page_url in pages:
        try:
            resp = session.get(page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  Warning: could not fetch {page_url}: {exc}", file=sys.stderr)
            continue

        html = resp.text

        # Level 1: direct PDF links embedded on the page
        _scan(html, page_url)

        # Level 2: follow SitRep blog-post links and scan each post for PDFs
        raw_post_urls = [m.group(1) for m in _POST_SLUG_RE.finditer(html)]
        post_urls = list(dict.fromkeys(
            u if u.startswith("http") else f"https://insp.cd/{u.lstrip('/')}"
            for u in raw_post_urls
        ))
        for post_url in post_urls:
            try:
                post_resp = session.get(post_url, timeout=30)
                post_resp.raise_for_status()
                post_html = post_resp.text
            except requests.RequestException as exc:
                print(f"  Warning: could not fetch {post_url}: {exc}", file=sys.stderr)
                continue
            h1 = re.search(r'<h1[^>]*>(.*?)</h1>', post_html, re.IGNORECASE | re.DOTALL)
            page_title = re.sub(r'<[^>]+>', '', h1.group(1)).strip() if h1 else ""
            _scan(post_html, post_url, default_title=page_title)

    return list(found.values())


def load_manifest(path: Path) -> dict:
    """Load the download manifest from disk, or return an empty dict."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_manifest(path: Path, manifest: dict) -> None:
    """Write the download manifest to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def download_pdf(url: str, pdf_dir: Path, manifest: dict,
                 title: str = "", source_page: str = "") -> tuple:
    """
    Download the PDF at url to pdf_dir if not already in manifest.
    Returns (is_new, local_path).
    """
    filename = unquote(Path(urlparse(url).path).name)
    dest = pdf_dir / filename

    if url in manifest:
        return False, dest

    print(f"  ↓  {filename}")
    if title:
        print(f"     {title}")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"     Warning: download failed — {exc}", file=sys.stderr)
        return False, dest

    pdf_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    manifest[url] = {
        "filename": filename,
        "title": title or _title_from_filename(filename),
        "source_page": source_page,
        "uploaded_at": _parse_last_modified(resp.headers.get("Last-Modified", "")),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": len(resp.content),
    }
    return True, dest


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover and download INSP DRC MVE SitRep PDFs to a local archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Daily workflow:\n"
            "  python3 fetch_sitreps.py && python3 extract_sitrep.py --update\n"
        ),
    )
    parser.add_argument(
        "--pdf-dir",
        default="pdfs",
        metavar="DIR",
        help="Local archive directory for downloaded PDFs (default: ./pdfs).",
    )
    parser.add_argument(
        "--pages",
        nargs="+",
        metavar="URL",
        help=(
            "Pages to scrape for PDF links "
            "(default: insp.cd/ebola/ and insp.cd/category/sitrep/)."
        ),
    )
    parser.add_argument(
        "--since",
        default=DEFAULT_SINCE.replace("/", "-"),
        metavar="YYYY-MM",
        help=(
            f"Only download PDFs uploaded in this month or later "
            f"(default: {DEFAULT_SINCE.replace('/', '-')})."
        ),
    )
    args = parser.parse_args()

    # Normalise --since to the YYYY/MM format used in wp-content URLs
    min_ym = args.since.replace("-", "/")

    pdf_dir       = Path(args.pdf_dir).expanduser().resolve()
    pages         = args.pages or DEFAULT_PAGES
    manifest_path = pdf_dir / "manifest.json"

    print(f"Scanning {len(pages)} page(s) for SitRep PDFs (since {min_ym}) …")
    urls = discover_pdf_urls(pages, min_ym)
    print(f"Found {len(urls)} relevant PDF link(s) on INSP website.\n")

    manifest  = load_manifest(manifest_path)
    new_count = 0
    for entry in urls:
        is_new, _ = download_pdf(
            entry["url"], pdf_dir, manifest,
            title=entry["title"], source_page=entry["source_page"],
        )
        if is_new:
            new_count += 1

    save_manifest(manifest_path, manifest)

    print()
    if new_count:
        print(f"  {new_count} new PDF(s) downloaded → {pdf_dir}/")
        print(f"  Total archived : {len(manifest)} PDF(s)")
        print(f"\nRun extraction to update the master linelist:")
        print(f"  python3 extract_sitrep.py --update --pdf-dir {pdf_dir}")
    else:
        print(f"  No new PDFs — archive is up to date ({len(manifest)} PDF(s) total).")


if __name__ == "__main__":
    main()
