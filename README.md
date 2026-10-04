# lb_to_plex

Copy a [Letterboxd](https://letterboxd.com) list into a **Plex** collection or
playlist — including only the films that already exist in your Plex library.

For each film in the list it searches your Plex movie library by title,
disambiguates by release year, and adds every match to a collection (default)
or playlist. Films it can't find are reported at the end so you know exactly
what was skipped.

> Letterboxd calls these **lists** (plus your **watchlist**), not "playlists."
> This tool works with any of them, since they all export the same way.

---

## Requirements

- Python 3.10+
- [`plexapi`](https://github.com/pkkid/python-plexapi) — required
- `requests` + `beautifulsoup4` — only for the optional `--url` scraper mode

```bash
pip install plexapi
# optional, only if you use --url instead of --csv:
pip install requests beautifulsoup4
```

---

## Setup

### 1. Get your Plex connection details

**Server URL** — `http://<server-ip>:32400` (e.g. `http://192.168.1.10:32400`).
Use the LAN IP of the machine running Plex.

**Token** (`X-Plex-Token`):
1. Open the Plex web app and play or view any library item.
2. Click **⋯ → Get Info → View XML**.
3. In the browser URL that opens, copy the value of `X-Plex-Token=...`.

Provide both via environment variables (recommended) or command-line flags:

```bash
export PLEX_URL="http://192.168.1.10:32400"
export PLEX_TOKEN="your-x-plex-token"
```

### 2. Get your Letterboxd list as a CSV (recommended)

- Open the list on Letterboxd → **⋯ / Export** to download it as CSV, **or**
- Settings → **Data** → *Export your data* to get everything (lists + watchlist)
  as CSVs in a ZIP.

The CSV just needs `Name` and `Year` columns — both export formats have them.

---

## Usage

Always start with a dry run — it matches and reports but writes nothing:

```bash
python3 lb_to_plex.py --csv my-list.csv --target-name "Letterboxd Faves" --dry-run
```

Then run it for real:

```bash
python3 lb_to_plex.py --csv my-list.csv --target-name "Letterboxd Faves"
```

### More examples

```bash
# Create a playlist instead of a collection:
python3 lb_to_plex.py --csv my-list.csv --target-name "LB" --target-type playlist

# Re-sync: clear the target first, then add the current matches:
python3 lb_to_plex.py --csv my-list.csv --target-name "LB" --mode replace

# Non-default library name, looser year matching:
python3 lb_to_plex.py --csv my-list.csv --target-name "LB" \
    --library "Films" --year-tolerance 2

# Scrape a public list URL instead of using a CSV (titles only, no years):
python3 lb_to_plex.py --url "https://letterboxd.com/USER/list/SLUG/" --target-name "LB"
```

---

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--csv PATH` | — | Letterboxd CSV export. **Required unless `--url` is given.** |
| `--url URL` | — | Public Letterboxd list URL (best-effort scrape). Mutually exclusive with `--csv`. |
| `--plex-url URL` | `$PLEX_URL` | Plex server base URL. |
| `--plex-token TOKEN` | `$PLEX_TOKEN` | Plex `X-Plex-Token`. |
| `--library NAME` | `Movies` | Name of your Plex movie library. |
| `--target-name NAME` | — | Collection/playlist to write to. **Required.** |
| `--target-type` | `collection` | `collection` or `playlist`. |
| `--mode` | `append` | If the target exists: `append` adds to it; `replace` clears it first. |
| `--year-tolerance N` | `1` | Allowed ± year drift when matching (covers release-date disagreements). |
| `--dry-run` | off | Match and report only; write nothing. |
| `--quiet` | off | Suppress per-film match output. |

---

## How matching works

1. The film's title is searched in Plex (server-side, substring match).
2. Candidates are filtered to those whose **normalized** title matches — titles
   are lowercased, a leading `The/A/An` is dropped (and `Title, The` is
   rewritten), and punctuation/spacing is stripped. So *"The Lord of the Rings:
   The Fellowship of the Ring"* matches *"Lord of the Rings The Fellowship of
   the Ring"*.
3. Among those, a film whose year is within `--year-tolerance` of the
   Letterboxd year wins; otherwise the first title match is used.
4. Matches are de-duplicated and films already in the target are skipped.

**Known limits:** remakes, re-releases with unusual Plex metadata, and films
stored under a different (e.g. localized) title can be missed. The
end-of-run "Not found" list surfaces exactly these so you can fix a title or
add the film. In `--url` mode there are no years, so matching is title-only and
weaker — prefer CSV.

---

## Security notes

- **Nothing identifying is hardcoded** in the script — no token, no server
  address, no personal data. It's safe to publish as-is.
- **Keep your Plex token secret.** It grants access to your account/server.
  Prefer `export PLEX_TOKEN=...` over passing `--plex-token` on the command
  line, since command-line arguments land in your shell history and are visible
  to other local users via the process list. If a token ever leaks, invalidate
  it by signing out all devices in your Plex account settings.
- **Don't paste raw run output publicly.** The console prints your server URL
  (which includes your internal IP) and your matched/unmatched film titles.
- No telemetry, analytics, or network calls beyond your Plex server and —
  in `--url` mode only — Letterboxd.

---

## Troubleshooting

- **`Library 'Movies' not found`** — the script prints the available library
  names; pass the right one with `--library`.
- **`Could not find a header row with 'Name' and 'Year'`** — the file isn't a
  Letterboxd CSV export, or is the wrong CSV. Re-export from Letterboxd.
- **Lots of unexpected misses** — try `--year-tolerance 2`, and check the
  "Not found" list for title mismatches between Letterboxd and Plex.
- **`--url` finds nothing** — Letterboxd changed their page markup; use `--csv`.
- **Connection errors** — confirm the server IP/port and that the machine
  running the script can reach Plex on your network.
