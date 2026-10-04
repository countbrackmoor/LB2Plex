#!/usr/bin/env python3
"""
lb_to_plex.py — Copy a Letterboxd list into a Plex collection or playlist,
including only the films that already exist in your Plex library.

Primary input is a Letterboxd CSV export (reliable, no scraping). An optional
--url scraper mode is included but is best-effort and more fragile.

Requires: plexapi   (pip install plexapi)
Optional: requests, beautifulsoup4   (only for --url mode)

Secrets: pass --plex-token or set PLEX_TOKEN in the environment.
         pass --plex-url   or set PLEX_URL   in the environment.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass

try:
    from plexapi.server import PlexServer
except ImportError:
    sys.exit("plexapi is not installed. Run: pip install plexapi")


# ----------------------------- data model -----------------------------------

@dataclass
class Film:
    title: str
    year: int | None  # may be None if the source didn't supply one


# ----------------------------- normalization --------------------------------

_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Lowercase, drop a leading article, strip punctuation/whitespace.

    This is deliberately aggressive so "The Lord of the Rings: The Fellowship
    of the Ring" and "Lord of the Rings The Fellowship of the Ring" collide.
    It can occasionally over-merge (e.g. "Up" vs "Us" do NOT collide, but very
    short titles are riskier) — year matching is the safety net.
    """
    t = title.strip().lower()
    # Handle "Title, The" style that some exports use.
    m = re.match(r"^(.*),\s*(the|a|an)$", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}"
    t = _ARTICLE_RE.sub("", t)
    t = _NONALNUM_RE.sub("", t)
    return t


# ----------------------------- Letterboxd input -----------------------------

def parse_letterboxd_csv(path: str) -> list[Film]:
    """Parse a Letterboxd CSV export.

    Handles both the list export format (Position,Name,Year,URL,Description)
    and the films/watchlist format (Date,Name,Year,Letterboxd URI) by locating
    the header row dynamically and reading the Name/Year columns by name.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    # Find the header row: the first row containing both a "Name" and a "Year"
    # cell. Letterboxd list exports prepend a few metadata lines before it.
    header_idx = None
    for i, row in enumerate(rows):
        lowered = [c.strip().lower() for c in row]
        if "name" in lowered and "year" in lowered:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Could not find a header row with 'Name' and 'Year' columns. "
            "Is this a Letterboxd CSV export?"
        )

    header = [c.strip().lower() for c in rows[header_idx]]
    name_i = header.index("name")
    year_i = header.index("year")

    films: list[Film] = []
    for row in rows[header_idx + 1:]:
        if not row or len(row) <= max(name_i, year_i):
            continue
        name = row[name_i].strip()
        if not name:
            continue
        year_raw = row[year_i].strip()
        year = int(year_raw) if year_raw.isdigit() else None
        films.append(Film(title=name, year=year))
    return films


def scrape_letterboxd_list(url: str) -> list[Film]:
    """Best-effort scrape of a public Letterboxd list page (all pages).

    NOTE: This parses undocumented HTML and WILL break if Letterboxd changes
    their markup. Prefer the CSV export. Requires requests + beautifulsoup4.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("--url mode needs: pip install requests beautifulsoup4")

    headers = {"User-Agent": "Mozilla/5.0 (lb_to_plex)"}
    films: list[Film] = []
    seen_slugs: set[str] = set()
    page = 1
    base = url.rstrip("/")

    while True:
        page_url = base if page == 1 else f"{base}/page/{page}/"
        resp = requests.get(page_url, headers=headers, timeout=20)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Film posters carry data attributes. The exact attribute names have
        # changed over time; try a few. Title lives in the <img alt=...>.
        posters = soup.select("[data-film-slug], [data-target-link], li.poster-container div")
        new_on_page = 0
        for p in posters:
            slug = p.get("data-film-slug") or p.get("data-item-slug")
            img = p.find("img")
            title = (img.get("alt").strip() if img and img.get("alt") else None)
            if not title:
                continue
            key = slug or title
            if key in seen_slugs:
                continue
            seen_slugs.add(key)
            # Year is generally NOT on the list page without an extra request.
            films.append(Film(title=title, year=None))
            new_on_page += 1

        if new_on_page == 0:
            break
        page += 1
        if page > 100:  # safety stop
            break

    if not films:
        sys.exit(
            "Scraper found no films. Letterboxd markup may have changed — "
            "use the CSV export instead (--csv)."
        )
    return films


# ----------------------------- Plex matching --------------------------------

def match_in_plex(section, films: list[Film], year_tolerance: int, verbose: bool):
    """Return (matched_movie_objects, unmatched_films).

    For each film: server-side title search, then pick the best candidate by
    normalized-title equality, preferring a year within tolerance.
    """
    matched = []
    unmatched: list[Film] = []
    matched_keys: set[str] = set()

    for film in films:
        norm_target = normalize_title(film.title)
        try:
            candidates = section.search(title=film.title, libtype="movie")
        except Exception as e:
            if verbose:
                print(f"  ! search error for {film.title!r}: {e}")
            candidates = []

        exact = [c for c in candidates if normalize_title(c.title) == norm_target]

        chosen = None
        if film.year is not None:
            for c in exact:
                cy = getattr(c, "year", None)
                if cy is not None and abs(cy - film.year) <= year_tolerance:
                    chosen = c
                    break
        if chosen is None and exact:
            chosen = exact[0]  # title-only fallback

        if chosen is not None:
            if chosen.ratingKey not in matched_keys:
                matched_keys.add(chosen.ratingKey)
                matched.append(chosen)
            if verbose:
                yr = getattr(chosen, "year", "?")
                print(f"  ✓ {film.title} ({film.year}) -> {chosen.title} ({yr})")
        else:
            unmatched.append(film)
            if verbose:
                print(f"  ✗ {film.title} ({film.year}) — no match in library")

    return matched, unmatched


# ----------------------------- Plex write -----------------------------------

def push_to_plex(server, section, target_type: str, target_name: str,
                 movies, mode: str, dry_run: bool):
    target_type = target_type.lower()
    if target_type == "collection":
        existing = next((c for c in section.collections()
                         if c.title.lower() == target_name.lower()), None)
    elif target_type == "playlist":
        existing = next((p for p in server.playlists()
                         if p.title.lower() == target_name.lower()), None)
    else:
        sys.exit("target-type must be 'collection' or 'playlist'")

    if existing and mode == "replace":
        print(f"Target {target_name!r} exists; 'replace' mode -> clearing it.")
        if not dry_run:
            try:
                existing.removeItems(existing.items())
            except Exception as e:
                print(f"  ! could not clear existing items: {e}")

    # Skip items already present (append mode).
    if existing:
        present = {m.ratingKey for m in existing.items()}
        to_add = [m for m in movies if m.ratingKey not in present]
    else:
        to_add = movies

    if dry_run:
        verb = "would add"
        print(f"[dry-run] {verb} {len(to_add)} film(s) to "
              f"{target_type} {target_name!r} "
              f"({'exists' if existing else 'would be created'}).")
        return

    if not to_add and existing:
        print("Nothing new to add — all matched films already in target.")
        return
    if not to_add and not existing:
        print("No films to add; target not created.")
        return

    if existing:
        existing.addItems(to_add)
        print(f"Added {len(to_add)} film(s) to existing {target_type} {target_name!r}.")
    else:
        if target_type == "collection":
            section.createCollection(title=target_name, items=to_add)
        else:
            server.createPlaylist(title=target_name, items=to_add)
        print(f"Created {target_type} {target_name!r} with {len(to_add)} film(s).")


# ----------------------------- main -----------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="Path to a Letterboxd CSV export (recommended)")
    src.add_argument("--url", help="Public Letterboxd list URL (best-effort scrape)")

    ap.add_argument("--plex-url", default=os.environ.get("PLEX_URL"),
                    help="e.g. http://192.168.1.10:32400  (or set PLEX_URL)")
    ap.add_argument("--plex-token", default=os.environ.get("PLEX_TOKEN"),
                    help="X-Plex-Token (or set PLEX_TOKEN)")
    ap.add_argument("--library", default="Movies", help="Movie library name")
    ap.add_argument("--target-name", required=True, help="Collection/playlist name to write")
    ap.add_argument("--target-type", default="collection",
                    choices=["collection", "playlist"])
    ap.add_argument("--mode", default="append", choices=["append", "replace"],
                    help="If the target exists: add to it, or clear then add")
    ap.add_argument("--year-tolerance", type=int, default=1,
                    help="Allowed +/- year drift when matching (default 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Match and report, but write nothing to Plex")
    ap.add_argument("--quiet", action="store_true", help="Less per-film output")
    args = ap.parse_args()

    if not args.plex_url or not args.plex_token:
        sys.exit("Missing Plex URL/token. Set --plex-url/--plex-token or "
                 "PLEX_URL/PLEX_TOKEN.")

    verbose = not args.quiet

    # 1. Read the Letterboxd list.
    if args.csv:
        films = parse_letterboxd_csv(args.csv)
        print(f"Parsed {len(films)} film(s) from CSV.")
    else:
        films = scrape_letterboxd_list(args.url)
        print(f"Scraped {len(films)} film(s) from list URL "
              f"(no years available — matching on title only).")

    if not films:
        sys.exit("No films found in the source.")

    # 2. Connect to Plex.
    print(f"Connecting to Plex at {args.plex_url} ...")
    server = PlexServer(args.plex_url, args.plex_token)
    try:
        section = server.library.section(args.library)
    except Exception:
        names = [s.title for s in server.library.sections()]
        sys.exit(f"Library {args.library!r} not found. Available: {names}")

    # 3. Match.
    print(f"Matching against library {args.library!r} ...")
    matched, unmatched = match_in_plex(section, films, args.year_tolerance, verbose)
    print(f"\nMatched {len(matched)} / {len(films)}. "
          f"Unmatched: {len(unmatched)}.")

    # 4. Write.
    push_to_plex(server, section, args.target_type, args.target_name,
                 matched, args.mode, args.dry_run)

    # 5. Report misses so you can fix titles or add films.
    if unmatched:
        print("\nNot found in your library:")
        for f in unmatched:
            yr = f.year if f.year is not None else "----"
            print(f"  - {f.title} ({yr})")


if __name__ == "__main__":
    main()
