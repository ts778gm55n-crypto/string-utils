"""
Rename image files using the convention: YYYY_ArtistName_AlbumName.ext

Resolution order:
  1. EXIF metadata embedded in the file
  2. Tesseract OCR -- reads text directly from the image (no data sent anywhere)
  3. Claude validates / fills gaps in the OCR result (sends OCR text only, not the image)
  4. Fallback: not_found_YYYY-MM-DD_HHMMSS (file last-modified timestamp)

Missing fields are marked XXX in the filename so they are easy to spot and fix manually.

Setup:
  pip install pytesseract Pillow anthropic
  Tesseract binary: https://github.com/UB-Mannheim/tesseract/wiki
  Set env var ANTHROPIC_API_KEY with your key from https://console.anthropic.com
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
except ImportError:
    sys.exit("Missing dependency: run  pip install Pillow")

try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except ImportError:
    sys.exit("Missing dependency: run  pip install pytesseract")

try:
    import anthropic
    claude = anthropic.Anthropic()
except ImportError:
    sys.exit("Missing dependency: run  pip install anthropic")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _clean(value: str) -> str:
    value = value.strip()
    value = re.sub(r'[\\/:*?"<>|]', "", value)
    value = re.sub(r"\s+", "_", value)
    return value


# ── step 1: EXIF metadata ─────────────────────────────────────────────────────

def read_metadata(path: Path) -> dict:
    try:
        img = Image.open(path)
        exif_data = img._getexif() or {}
    except Exception:
        return {}

    tags = {TAGS.get(k, k): v for k, v in exif_data.items()}
    year = artist = album = None

    for date_tag in ("DateTimeOriginal", "DateTime"):
        if date_tag in tags:
            try:
                year = str(tags[date_tag])[:4]
                if year.isdigit():
                    break
            except Exception:
                pass

    desc = tags.get("ImageDescription", "")
    if isinstance(desc, str):
        try:
            meta = json.loads(desc)
            artist = meta.get("artist") or meta.get("Artist")
            album  = meta.get("album")  or meta.get("Album")
            if not year:
                year = str(meta.get("year") or meta.get("Year") or "")
        except Exception:
            pass

    if year and artist and album:
        return {"year": _clean(year), "artist": _clean(artist), "album": _clean(album)}
    return {}


# ── step 2: OCR ───────────────────────────────────────────────────────────────

def ocr_image(path: Path) -> str:
    """Return all text found in the image via Tesseract."""
    try:
        img = Image.open(path)
        text = pytesseract.image_to_string(img, config="--psm 3")
        return text.strip()
    except Exception as e:
        print(f"    [OCR error] {e}")
        return ""


def parse_ocr_text(text: str) -> dict:
    """
    Try to extract year, artist, album from raw OCR text.

    Strategy:
    - Year: first 4-digit number between 1900-2099
    - Lines are ranked by length; longest non-year lines are
      treated as artist (first) and album (second)
    """
    if not text:
        return {}

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # find year
    year = None
    for line in lines:
        match = re.search(r"\b(19|20)\d{2}\b", line)
        if match:
            year = match.group(0)
            break

    # remove very short lines and lines that are just numbers/symbols
    candidates = [l for l in lines if len(l) > 2 and not re.fullmatch(r"[\d\W]+", l)]

    # sort by length descending -- longer lines are usually the main title/artist
    candidates.sort(key=len, reverse=True)

    artist = candidates[0] if len(candidates) > 0 else None
    album  = candidates[1] if len(candidates) > 1 else None

    result = {}
    if year:
        result["year"] = _clean(year)
    if artist:
        result["artist"] = _clean(artist)
    if album:
        result["album"] = _clean(album)

    return result


# ── step 3: Claude validation ────────────────────────────────────────────────

def validate_with_claude(ocr_text: str, current: dict) -> dict:
    """
    Send OCR text to Claude to validate and fill any missing fields.
    Returns a dict with 'year', 'artist', 'album' — missing fields are 'XXX'.
    Does NOT send any images; only the raw OCR text is transmitted.
    """
    if not ANTHROPIC_API_KEY:
        print("    [Claude] ANTHROPIC_API_KEY not set -- skipping validation")
        return current

    year   = current.get("year")
    artist = current.get("artist")
    album  = current.get("album")

    # nothing for Claude to improve
    if year and artist and album:
        return current

    missing = [f for f, v in [("year", year), ("artist", artist), ("album", album)] if not v]
    print(f"    [Claude] asking Claude to fill missing fields: {missing}...")

    prompt = (
        f"The following text was extracted by OCR from a music album cover image:\n\n"
        f"---\n{ocr_text or '(no text found)'}\n---\n\n"
        f"Already identified: year={year or 'unknown'}, artist={artist or 'unknown'}, album={album or 'unknown'}.\n\n"
        f"Based on the OCR text, fill in the missing fields if you can determine them with reasonable confidence. "
        f"Reply ONLY with a JSON object, no markdown: "
        f'{{\"year\": \"YYYY or null\", \"artist\": \"name or null\", \"album\": \"title or null\"}}. '
        f"Use null for any field you cannot confidently determine."
    )

    try:
        message = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(message.content[0].text.strip())

        def pick(existing, new_val):
            if existing:
                return existing
            if new_val and str(new_val).lower() not in ("null", "none", ""):
                return _clean(str(new_val))
            return None

        return {
            "year":   pick(year,   data.get("year")),
            "artist": pick(artist, data.get("artist")),
            "album":  pick(album,  data.get("album")),
        }
    except Exception as e:
        print(f"    [Claude error] {e}")
        return current


# ── step 4: fallback ──────────────────────────────────────────────────────────

def fallback_name(path: Path) -> str:
    mtime = os.path.getmtime(path)
    ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d_%H%M%S")
    return f"not_found_{ts}"


# ── rename logic ──────────────────────────────────────────────────────────────

def build_new_stem(path: Path) -> tuple[str, str]:
    """Return (new_stem, source). Missing fields are marked XXX in the stem."""
    # Step 1: EXIF
    meta = read_metadata(path)
    if meta.get("year") and meta.get("artist") and meta.get("album"):
        return f"{meta['year']}_{meta['artist']}_{meta['album']}", "metadata"

    # Step 2: OCR
    print(f"  -> running OCR...")
    raw_text = ocr_image(path)
    if raw_text:
        print(f"    [OCR text] {repr(raw_text[:120])}")
    ocr = parse_ocr_text(raw_text)

    # Step 3: Claude validates and fills gaps
    validated = validate_with_claude(raw_text, ocr)

    year   = validated.get("year")   or "XXX"
    artist = validated.get("artist") or "XXX"
    album  = validated.get("album")  or "XXX"

    # if Claude filled at least one field, use what we have (XXX marks the rest)
    if any(v != "XXX" for v in [year, artist, album]):
        source = "ocr+claude" if ocr else "claude"
        return f"{year}_{artist}_{album}", source

    # Step 4: fallback
    return fallback_name(path), "fallback"


def rename_folder(folder: Path, dry_run: bool):
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]

    if not files:
        print("No image files found.")
        return

    print(f"{'DRY RUN -- ' if dry_run else ''}Processing {len(files)} file(s) in {folder}\n")

    for path in sorted(files):
        print(f"  {path.name}")
        new_stem, source = build_new_stem(path)
        new_name = f"{new_stem}{path.suffix.lower()}"
        new_path = path.parent / new_name

        counter = 1
        while new_path.exists() and new_path != path:
            new_name = f"{new_stem}_{counter}{path.suffix.lower()}"
            new_path = path.parent / new_name
            counter += 1

        print(f"    [{source}] -> {new_name}")

        if not dry_run:
            path.rename(new_path)

    print("\nDone." if not dry_run else "\nDry run complete -- no files changed. Run without --dry-run to apply.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rename image files using OCR.")
    parser.add_argument("folder", help="Path to the folder containing images")
    parser.add_argument("--dry-run", action="store_true", help="Preview renames without changing anything")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    rename_folder(folder, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
