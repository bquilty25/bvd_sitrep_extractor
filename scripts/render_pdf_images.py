#!/usr/bin/env python3
"""Render each PDF in data/raw/ to per-page PNG images and build a collage.

Each PDF becomes one horizontal row in the collage (pages tiled left→right).
Rows are stacked top→bottom, one per SitRep in ascending order.

Usage:
    python scripts/render_pdf_images.py

Output:
    assets/images/sitrep_pages/<name>/page_NN.png  — individual page images
    assets/images/sitrep_collage.png               — combined collage
"""

import json
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image

REPO_ROOT = Path(__file__).parent.parent
PDFS_DIR = REPO_ROOT / "data" / "raw"
ASSETS_DIR = REPO_ROOT / "assets" / "images"
PAGES_DIR = ASSETS_DIR / "sitrep_pages"
COLLAGE_PATH = ASSETS_DIR / "sitrep_collage.png"

PAGE_HEIGHT = 220   # px per page thumbnail (height); width scales with aspect ratio
GAP_COL = 4         # px gap between pages within a row
GAP_ROW = 8         # px gap between rows
BG_COLOR = (248, 248, 248)


def render_pdf(pdf_path: Path) -> list:
    """Convert a PDF file to a list of PIL Images (one per page) at 120 dpi."""
    return convert_from_path(str(pdf_path), dpi=120)


def save_page_images(name: str, pages: list) -> None:
    out_dir = PAGES_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(pages, start=1):
        out_path = out_dir / f"page_{i:02d}.png"
        page.save(str(out_path), "PNG")
        print(f"  saved {out_path.relative_to(REPO_ROOT)}")


def make_row(pages: list) -> Image.Image:
    """Tile a list of page images horizontally with consistent height."""
    thumbs = []
    for page in pages:
        aspect = page.width / page.height
        new_w = int(PAGE_HEIGHT * aspect)
        thumb = page.resize((new_w, PAGE_HEIGHT), Image.LANCZOS)
        thumbs.append(thumb)

    row_w = sum(t.width for t in thumbs) + GAP_COL * (len(thumbs) - 1)
    row = Image.new("RGB", (row_w, PAGE_HEIGHT), color=BG_COLOR)
    x = 0
    for thumb in thumbs:
        row.paste(thumb, (x, 0))
        x += thumb.width + GAP_COL
    return row


def build_collage(rows: list) -> Image.Image:
    """Stack row images vertically."""
    max_w = max(r.width for r in rows)
    total_h = sum(r.height for r in rows) + GAP_ROW * (len(rows) - 1)
    collage = Image.new("RGB", (max_w, total_h), color=(255, 255, 255))
    y = 0
    for row in rows:
        collage.paste(row, (0, y))
        y += row.height + GAP_ROW
    return collage


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    manifest_path = PDFS_DIR / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    # Deduplicate by filename; sort by sitrep_number string (zero-padded, so lex sort works)
    seen: set = set()
    entries = []
    for entry in manifest.values():
        fn = entry["filename"]
        if fn not in seen:
            seen.add(fn)
            entries.append(entry)
    entries.sort(key=lambda e: e.get("sitrep_number", "000"))

    row_images = []
    for entry in entries:
        fn = entry["filename"]
        stem = Path(fn).stem
        pdf_file = PDFS_DIR / stem / fn
        if not pdf_file.exists():
            print(f"Skipping missing: {fn}")
            continue

        name = pdf_file.stem
        print(f"Rendering {pdf_file.name} ...")
        pages = render_pdf(pdf_file)
        save_page_images(name, pages)
        row_images.append(make_row(pages))

    if not row_images:
        print("No PDFs rendered — nothing to do.")
        return

    print(f"\nBuilding collage from {len(row_images)} PDFs ...")
    collage = build_collage(row_images)
    collage.save(str(COLLAGE_PATH), "PNG")
    relative = COLLAGE_PATH.relative_to(REPO_ROOT)
    print(f"Collage saved → {relative}  ({collage.width} × {collage.height} px)")


if __name__ == "__main__":
    main()
