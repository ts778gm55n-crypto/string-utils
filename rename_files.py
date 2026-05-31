"""
Rename image files using the convention: YYYY_ArtistName_AlbumName.ext

Filename format expected: R-<releaseID>-<...>.jpg  (Discogs release images)

Resolution order:
  1. EXIF metadata embedded in the file
  2. Discogs API lookup using the release ID in the filename
  3. Fallback: not_found_YYYY-MM-DD_HHMMSS (file last-modified timestamp)

Setup:
  pip install requests Pillow
  Set env var DISCOGS_TOKEN with your personal token from
  https://www.discogs.com/settings/developers
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: run  pip install requests")

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
except ImportError:
    sys.exit("Missing dependency: run  pip install Pillow")


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "")
DISCOGS_API = "https://api.discogs.com/releases/{release_id}"


# ── helpers ───────────────────────────────────────────────────────────────────

def _clean(value: str) -> str:
    """Strip characters that are unsafe in filenames."""
    value = value.strip()
    value = re.sub(r'[\\/:*?"<>|]', "", value)
    value = re.sub(r"\s+", "_", value)
    return value


# ── step 1: EXIF metadata ─────────────────────────────────────────────────────

def read_metadata(path: Path) -> dict:
    """Return {'year': ..., 'artist': ..., 'album': ...} from EXIF, or {}."""
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
            album = meta.get("album") or meta.get("Album")
            if not year:
                year = str(meta.get("year") or meta.get("Year") or "")
        except Exception:
            pass

    if year and artist and album:
        return {"year": _clean(year), "artist": _clean(artist), "album": _clean(album)}
    return {}


# ── step 2: Discogs API ───────────────────────────────────────────────────────

def extract_release_id(filename: str) -> str | None:
    """Extract Discogs release ID from filenames like R-13804935-..."""
    match = re.match(r"R-(\d+)-", filename)
    return match.group(1) if match else None


def lookup_discogs(release_id: str) -> dict:
    """Query Discogs API and return {'year', 'artist', 'album'} or {}."""
    headers = {"User-Agent": "AlbumRenamer/1.0"}
    if DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"

    try:
        url = DISCOGS_API.format(release_id=release_id)
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 401:
            print("    [Discogs] Auth required — set DISCOGS_TOKEN env var")
            return {}
        if response.status_code == 404:
            print(f"    [Discogs] Release {release_id} not found")
            return {}
        if response.status_code == 429:
            print("    [Discogs] Rate limited — waiting 10s...")
            time.sleep(10)
            response = requests.get(url, headers=headers, timeout=10)

        response.raise_for_status()
        data = response.json()

        year = str(data.get("year", "")).strip()
        album = data.get("title", "").strip()
        artists = data.get("artists", [])
        artist = artists[0].get("name", "").strip() if artists else ""

        # Discogs appends " (2)" etc. to disambiguate artists — strip it
        artist = re.sub(r"\s*\(\d+\)$", "", artist).strip()

        result = {}
        if year and year.isdigit():
            result["year"] = _clean(year)
        if artist:
            result["artist"] = _clean(artist)
        if album:
            result["album"] = _clean(album)

        return result

    except Exception as e:
        print(f"    [Discogs error] {e}")
        return {}


# ── step 3: fallback ──────────────────────────────────────────────────────────

def fallback_name(path: Path) -> str:
    mtime = os.path.getmtime(path)
    ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d_%H%M%S")
    return f"not_found_{ts}"


# ── rename logic ──────────────────────────────────────────────────────────────

def build_new_stem(path: Path) -> tuple[str, str]:
    """Return (new_stem, source) where source describes how it was resolved."""

    # Step 1: EXIF
    meta = read_metadata(path)
    if meta.get("year") and meta.get("artist") and meta.get("album"):
        stem = f"{meta['year']}_{meta['artist']}_{meta['album']}"
        return stem, "metadata"

    # Step 2: Discogs
    release_id = extract_release_id(path.name)
    if release_id:
        print(f"  -> querying Discogs for release {release_id}...")
        discogs = lookup_discogs(release_id)
        if discogs.get("year") and discogs.get("artist") and discogs.get("album"):
            stem = f"{discogs['year']}_{discogs['artist']}_{discogs['album']}"
            return stem, "discogs"

    # Step 3: fallback
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

        # avoid collisions by appending a counter
        counter = 1
        while new_path.exists() and new_path != path:
            new_name = f"{new_stem}_{counter}{path.suffix.lower()}"
            new_path = path.parent / new_name
            counter += 1

        print(f"    [{source}] -> {new_name}")

        if not dry_run:
            path.rename(new_path)

        # Discogs rate limit: max 60 requests/min unauthenticated
        if source == "discogs" or "discogs" in source:
            time.sleep(1)

    print("\nDone." if not dry_run else "\nDry run complete -- no files changed. Run without --dry-run to apply.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rename image files by album metadata.")
    parser.add_argument("folder", help="Path to the folder containing images")
    parser.add_argument("--dry-run", action="store_true", help="Preview renames without changing anything")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")

    if not DISCOGS_TOKEN:
        print("Tip: set DISCOGS_TOKEN env var for higher API rate limits.")
        print("Get a free token at https://www.discogs.com/settings/developers\n")

    rename_folder(folder, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
