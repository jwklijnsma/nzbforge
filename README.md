# nzbforge

A small Newznab-compatible Usenet indexer: Python (FastAPI + SQLAlchemy) backend, React (Vite) front end.
Prowlarr / Sonarr / Radarr talk to `/api` (caps, search, tvsearch, movie, get, details) like any Newznab indexer.

## Run it
```bash
cd backend
python -m venv .venv && source .venv/bin/activate       # Python 3.10+
pip install -r requirements.txt
cp .env.example .env && set -a && source .env && set +a

python -m app.cli add-group alt.binaries.example         # groups your provider carries
uvicorn app.main:app --host 0.0.0.0 --port 8000          # API + UI (first start prints/creates the admin key)
python -m app.scanner                                    # second process: header scanner

cd ../frontend && npm install && npm run build           # UI is then served by uvicorn at :8000
# or `npm run dev` for hot reload on :5173 (proxies to :8000)
```

## Database Setup (MySQL/MariaDB)

To use MySQL/MariaDB instead of the default SQLite:

1. **Install MySQL/MariaDB**: Ensure your server is running (e.g., `sudo systemctl start mysql`).
2. **Create Database and User**:
```sql
CREATE DATABASE nzbforge;
CREATE USER 'nzbforge'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON nzbforge.* TO 'nzbforge'@'localhost';
FLUSH PRIVILEGES;
```
3. **Update Dependencies**: Add `mysqlclient` to your `requirements.txt` file.
4. **Configure `.env`**:
```
DB_URL=mysql+pymysql://nzbforge:your_secure_password@localhost/nzbforge
```
5. **Run Setup**: Proceed with the `pip install -r requirements.txt` and subsequent steps as in the "Run it" section.

**Note**: The `DB_URL` format uses `mysql+pymysql://` for the SQLAlchemy driver. Adjust host, port (default 3306), and database name as needed.

## Add to Prowlarr
Indexers > Add > **Generic Newznab**. URL `http://<host>:8000`, API path `/api`, API key from `python -m app.cli list`.
Categories: 2000 Movies, 3000 Audio, 5000 TV, 7000 Other. Use `python -m app.cli create-user NAME` for more keys.
Behind a reverse proxy run uvicorn with `--proxy-headers` so NZB links get the right host.

## How it works
1. `scanner.py` pulls XOVER headers from your Usenet provider, parses `"file.ext" yEnc (n/N)` subjects and groups them per poster + base name into collections.
2. When a collection is complete (or >= MIN_COMPLETION and idle 2h) it becomes a `Release`, classified into Newznab categories with season/episode parsed from the name.
3. `/api?t=get` builds the NZB on the fly from the stored message-ids.

## Release information (Spotweb-style)
- **Details panel**: click a release for poster, description, rating, file list with part counts, and the NFO.
- **TV** metadata comes from TVmaze (no key needed). **Movies** use TMDB: set `TMDB_API_KEY` in `.env` (free key).
  Credit: this product uses the TMDB API but is not endorsed or certified by TMDB. TV data courtesy of TVmaze (CC BY-SA).
- **NFO files** are downloaded from Usenet (first segment of the `.nfo`) by the scanner.
- The API also returns `tvdbid`, `imdb`, `tmdbid`, `coverurl`, `rating` and `year`, and accepts `tvdbid`/`imdbid`/`tmdbid`
  search params, so Sonarr and Radarr can match releases by id. Releases not looked up yet still match by title.
- Existing databases are upgraded in place on start (new nullable columns are added automatically).
- Real Spotnet data (the signed posts Spotweb reads from `free.pt`) is not implemented. For that, keep running Spotweb
  and add it to Prowlarr next to this indexer.

## Known limits (next things to build)
- Obfuscated posts (random per-file names) are not grouped; needs set-marker/poster/time-window grouping.
- Collections store parts in a JSON column. Fine for a personal index; for millions of headers switch to a `parts` table + Postgres.
- Categories come from name heuristics only. No TVDB/TMDB/IMDB matching, so `tvdbid`/`imdbid` params are ignored (Sonarr/Radarr fall back to text `q`).
- No NFO/PAR2 inspection, passworded-release detection, or release de-duplication across groups.
- Check your Usenet provider's terms before bulk-scanning headers.

> **API Key UI Note**: The API key management interface shows a "Generate New API Key" button. When clicked, it displays a secure 64-character hex key with a "Copy" button for easy clipboard access. The interface uses a clean, minimal design with light background styling.
> **Note**: Please add the screenshot image to `screenshots/api_key_ui_screenshot.png` to display the UI image.
## Releases
Releases are automatic. Every push to `main` runs the tests and, if there is something to release, creates a
`vX.Y.Z` tag, a GitHub release with generated notes, and Docker images `ghcr.io/jwklijnsma/nzbforge:X.Y.Z` / `:X.Y` / `:latest`.
The bump follows the commit messages since the last tag:

| Commit | Bump |
|---|---|
| `feat!: ...`, `fix!: ...` or `BREAKING CHANGE` in the body | major |
| `feat: ...` | minor |
| `fix: ...` or any other message | patch |
| only `chore:` / `ci:` / `docs:` / `test:` / `style:` / `build:` (e.g. Renovate) | no release |

To force a release: Actions → **Automatic Release** → Run workflow, then pick patch/minor/major.
