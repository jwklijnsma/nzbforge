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
uvicorn app.main:app --host 0.0.0.0 --port 8000          # API + UI + header scanner (first start prints/creates the admin key)

cd ../frontend && npm install && npm run build           # UI is then served by uvicorn at :8000
# or `npm run dev` for hot reload on :5173 (proxies to :8000)
```

## Run with Podman + systemd (Quadlet)
Units for Podman 4.4+ are in [`deploy/podman`](deploy/podman): one `nzbforge` container (API + UI on :8000 and the
header scanner in the same process) with the `nzbforge-data` volume (SQLite in `/data`).

```bash
git clone https://github.com/jwklijnsma/nzbforge && cd nzbforge/deploy/podman
./install.sh                     # rootless: ~/.config/containers/systemd; use `sudo ./install.sh` for rootful
$EDITOR ~/.config/nzbforge/nzbforge.env      # NNTP_* and more, see Configuration (ADMIN_KEY is generated)
systemctl --user start nzbforge
loginctl enable-linger $USER     # start at boot / keep running after logout
podman exec nzbforge python -m app.cli add-group alt.binaries.example
journalctl --user -u nzbforge -f
```
The units carry `AutoUpdate=registry`, so `systemctl --user enable --now podman-auto-update.timer` keeps them on the latest release.
If the GHCR package is private, run `podman login ghcr.io` first.

The unit restarts the container when its health check (`/api?t=caps`) fails 5 times in a row (after a 2-minute start
period). While troubleshooting you can turn that off with `HealthOnFailure=none` in `nzbforge.container`, then
`systemctl daemon-reload`. If the web server ever stops answering for 30 seconds, nzbforge writes the stack of every
thread to the log (`podman logs nzbforge`), which shows where it is stuck.

## Configuration
All settings are environment variables. Put them in `backend/.env` (manual install) or in the env file the Podman
unit reads (`~/.config/nzbforge/nzbforge.env` rootless, `/etc/nzbforge/nzbforge.env` rootful), one `KEY=value` per line.
In the Podman env file, don't put quotes or comments after a value. After a change, restart nzbforge
(`systemctl --user restart nzbforge`, or without `--user` for rootful).

### General
| Option | Default | What it does |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./nzbforge.db` (container: `sqlite:////data/nzbforge.db`) | Database. SQLite, Postgres or MariaDB, see [Database](#database). |
| `ADMIN_KEY` | random | API key of the `admin` user. Only used on the very first start, when there are no users yet; the key is also printed in the log. Changing it later has no effect: use `python -m app.cli list`. |
| `SCANNER` | `auto` | Header scanner inside the web process. `auto`: on when `NNTP_HOST` is set. `1`: always on. `0`: off (pulling by hand from the Admin tab still works). |

### Usenet server
| Option | Default | What it does |
|---|---|---|
| `NNTP_HOST` | – | Your provider's news server, e.g. `news.example.com`. Without it nothing is pulled. |
| `NNTP_PORT` | `563` | Port. Usually 563 with SSL, 119 without. |
| `NNTP_SSL` | `1` | `1` = SSL/TLS, `0` = plain text. |
| `NNTP_USER` / `NNTP_PASS` | – | Your provider login. |
| `NNTP_CONNECTIONS` | `8` | Parallel connections for headers and NFOs. Keep the total of nzbforge **plus your downloader** under your provider's connection limit. |
| `NNTP_TIMEOUT` | `60` | Seconds a connection may stay silent before the request is retried on a fresh connection. A header range that times out twice is fetched again in smaller pieces. |
| `NNTP_RETRIES` | `4` | Tries per request before a group is skipped for this pull. From the third try on, a brand-new connection is used. |
| `NNTP_IDLE_CHECK` | `20` | A connection that sat idle this many seconds is checked (`DATE`) before reuse; providers close idle connections, and reusing a dead one gave `EOFError: connection closed`. |
| `NNTP_CONNECT_TIMEOUT` | `30` | Seconds to connect and get the server's greeting. A refused connection (e.g. *502 too many connections*) makes nzbforge continue with the connections it already has. |
| `NNTP_COMPRESS` | `1` | Ask for compressed headers (`XFEATURE COMPRESS GZIP`). Providers without it are detected automatically, and if compressed replies can't be decoded it is switched off for the rest of the pull (a dropped connection or timeout doesn't count). Set `0` to switch it off. |

### Scanning
| Option | Default | What it does |
|---|---|---|
| `SCAN_INTERVAL` | `300` | Seconds between automatic pulls. "Pull now" in the Admin tab doesn't wait for this. |
| `INITIAL_ARTICLES` | `200000` | How far back to start in a group that has never been pulled (number of headers). Lower it (e.g. `20000`) when you import many groups. |
| `SCAN_BATCH` | `10000` | Headers per XOVER request. |
| `POSTPROCESS_EVERY` | `300` | During long pulls, build releases and fetch NFOs every N seconds, so new spots appear before the pull is done. |
| `ENRICH_EVERY` | `60` | During a pull, metadata (TVmaze/TMDB/Cinemeta) is looked up on its own thread every N seconds, so the rate-limited lookups never pause the header download. |
| `MIN_COMPLETION` | `0.90` | A post becomes a release when it is complete, or at least this complete (0-1) and has had no new parts for 2 hours. |
| `EXPIRE_DAYS` | `3` | Unfinished posts older than this are dropped. |
| `NFO_PER_PASS` | `100` | NFO files downloaded per processing round. |

### Metadata (posters, descriptions, genres, ratings)
| Option | Default | What it does |
|---|---|---|
| `TMDB_API_KEY` | – | Optional. TMDB API key (free at themoviedb.org); when set, movies are looked up on TMDB first. Without it movies still get metadata from Cinemeta. |
| `CINEMETA` | `1` | Free, keyless metadata from Cinemeta (Stremio's public IMDb-based catalogue): IMDb id, poster, plot, genres, IMDb rating. Used for **movies** (or after TMDB when a key is set) and for **shows TVmaze doesn't know**. `0` = off. |
| `CINEMETA_URL` | `https://v3-cinemeta.strem.io` | Cinemeta address, e.g. for a self-hosted copy. |
| `ENRICH_PER_PASS` | `1000` | Releases looked up per processing round. |
| `ENRICH_WORKERS` | `8` | Parallel lookups. Each site's rate limit is respected anyway (TVmaze 2/s, TMDB 20/s, Cinemeta 5/s). |

### Send to downloader (NZBGet / SABnzbd)
Shows a **Send** button in the spot list and spot view.

| Option | Default | What it does |
|---|---|---|
| `DOWNLOADER` | – | `nzbget` or `sabnzbd`. Empty = no Send button. |
| `DOWNLOADER_URL` | – | e.g. `http://nzbget:6789` or `http://sabnzbd:8080`. |
| `DOWNLOADER_USER` / `DOWNLOADER_PASS` | – | NZBGet control username/password. |
| `DOWNLOADER_APIKEY` | – | SABnzbd API key. |
| `DOWNLOADER_CATEGORIES` | NZBGet: `Movies`, `Series`, `Music`, `Software`; SABnzbd: `movies`, `tv`, `audio`, `software` | Newznab category to downloader category, e.g. `2000=Movies,5000=Series,3000=Music`. A subcategory (`2045`) falls back to its parent (`2000`). |
| `DOWNLOADER_PRIORITY` | `normal` | Priority of NZBs sent with **Send**: `low`, `normal`, `high`, `veryhigh` or `force` (starts at once, even when the queue is paused). |

### Web server
These are uvicorn options, on the command line rather than in the env file (the container already uses them):

| Option | What it does |
|---|---|
| `--host 0.0.0.0 --port 8000` | Where the API and UI listen. In the Podman unit change `PublishPort=8000:8000` instead. |
| `--proxy-headers` | Behind a reverse proxy: use the `X-Forwarded-*` headers so NZB links in the API get the right host and scheme. |
| `--workers` | Keep at 1 (the default): every worker would start its own scanner. |

### Example
```ini
DATABASE_URL=sqlite:////data/nzbforge.db
NNTP_HOST=news.example.com
NNTP_PORT=563
NNTP_SSL=1
NNTP_USER=me
NNTP_PASS=secret
NNTP_CONNECTIONS=8
INITIAL_ARTICLES=20000
TMDB_API_KEY=0123456789abcdef
DOWNLOADER=nzbget
DOWNLOADER_URL=http://192.168.1.10:6789
DOWNLOADER_USER=nzbget
DOWNLOADER_PASS=tegbzn6789
```

## Command line
Run inside the container with `podman exec nzbforge python -m app.cli ...`, or from `backend/` on a manual install.

| Command | What it does |
|---|---|
| `add-group alt.binaries.example` | Add one group. |
| `import-groups [PATTERN] [--min-articles N]` | Add every group on your provider matching the pattern (default `alt.binaries.*`, at least 1 article). |
| `create-user NAME [--admin]` | Create a user with its own API key (e.g. one per Sonarr/Radarr). |
| `list` | Show groups and users with their API keys. |
| `python -m app.scanner` | Run the scanner on its own (use `SCANNER=0` for uvicorn then). |

## Database
SQLite works out of the box (WAL mode, so the UI stays fast while the scanner writes). Keep the SQLite file on a
**local disk**, not on NFS/SMB: SQLite's locking doesn't work reliably over network shares. For large indexes
(hundreds of active groups, millions of headers) use Postgres or MariaDB.

**Postgres**: `pip install "psycopg[binary]"`, then
```
DATABASE_URL=postgresql+psycopg://nzbforge:password@db-host/nzbforge
```
**MariaDB**: `pip install pymysql`, create the database and user (MySQL 8 is not supported yet: it lacks
`CREATE INDEX IF NOT EXISTS`, which the start-up migration uses):
```sql
CREATE DATABASE nzbforge CHARACTER SET utf8mb4;
CREATE USER 'nzbforge'@'%' IDENTIFIED BY 'password';
GRANT ALL PRIVILEGES ON nzbforge.* TO 'nzbforge'@'%';
```
then
```
DATABASE_URL=mysql+pymysql://nzbforge:password@db-host/nzbforge
```
Tables and new columns are created automatically on start. The container image only includes the SQLite driver;
for Postgres or MariaDB in the container, build your own image with the extra driver added to `backend/requirements.txt`.

## Add to Prowlarr
Indexers > Add > **Generic Newznab**. URL `http://<host>:8000`, API path `/api`, API key from `python -m app.cli list`.
Categories: 1000 Console, 2000 Movies (2030 SD, 2040 HD, 2045 UHD), 3000 Audio (3010 MP3, 3030 Audiobook, 3040 Lossless),
4000 PC (4030 Mac, 4050 Games), 5000 TV (5030 SD, 5040 HD, 5045 UHD), 6000 XXX, 7000 Other (7020 Ebook).
Use `python -m app.cli create-user NAME` for more keys.
Behind a reverse proxy run uvicorn with `--proxy-headers` so NZB links get the right host.

## How it works
The scanner runs as a background thread inside the uvicorn process (one process, one container). It starts
automatically when `NNTP_HOST` is set; `SCANNER=0` turns it off, `SCANNER=1` forces it on. Keep uvicorn at one
worker (the default), or each worker starts its own scanner. To run it separately anyway: `SCANNER=0` for uvicorn
plus `python -m app.scanner`.

1. `scanner.py` pulls XOVER headers from your Usenet provider, parses `"file.ext" yEnc (n/N)` subjects and groups them per poster + base name into collections.
2. When a collection is complete (or >= MIN_COMPLETION and idle 2h) it becomes a `Release`, classified into Newznab categories with season/episode parsed from the name.
3. `/api?t=get` builds the NZB on the fly from the stored message-ids.

## Speed
- **Parallel connections** (`NNTP_CONNECTIONS`, default 8): header batches are fetched on several connections at once
  and ahead of time, while the previous batch is being saved. This runs across groups: the next groups' `GROUP` and
  `XOVER` requests are already on the wire while the current one is saved, so thousands of small groups don't each
  wait for their own round trips. Keep it below your provider's connection limit
  (and leave room for your downloader).
- **Compressed headers** (`NNTP_COMPRESS=1`): providers that support `XFEATURE COMPRESS GZIP` (Highwinds/Giganews
  based ones, among others) send headers zlib-compressed, usually 5-10x less data. Others are detected and read as plain text.
- **SQLite in WAL mode**: the scanner writes while the web UI keeps reading, and commits are much cheaper.
- **Releases during long pulls**: every `POSTPROCESS_EVERY` seconds (default 300), so new spots show up without
  waiting for the last group. NFOs are fetched in parallel. Metadata lookups run on a separate thread and never
  stop the pull (before, a pull stood still for minutes while TVmaze's 2 requests/s limit was waited out, and the
  provider dropped the idle connections in the meantime).
- **Bulk inserts**: new collections (nearly every post in obfuscated groups) are written with one bulk `INSERT` per batch.
- **Self-healing connections**: idle connections are checked before reuse and dead ones replaced; slow header ranges
  are split instead of failing the group.
- **Metadata**: titles that already have metadata (earlier episodes) are copied without a web lookup, lookups are cached
  for 12 hours, and run in parallel (`ENRICH_WORKERS`) within TVmaze's and TMDB's rate limits.

On a test server with 0.4 s latency per 10,000 headers: 18,000 headers/s before, 56,000 headers/s with 8 connections.
Simulated pull of 300 mixed-size groups (40 ms round trip, 16 connections): 11,700 headers/s before the cross-group
pipeline, 38,000 headers/s after. Past that the limit is CPU (parsing + saving run in one Python process), not your line.

## Pulling groups (Admin tab)
![Pull progress](docs/img/pulling.png)

- **Pull all groups now** wakes the scanner instead of waiting for `SCAN_INTERVAL`; **Pull** on a row pulls just that group.
  While a pull runs you see the current group, a bar for its headers and one for all groups; a small progress pill in the
  top bar shows it from any page.
- **Import all from provider** runs in the background (pattern + minimum articles) with its own progress.
- Per group: on/off (only active groups are pulled automatically), headers still to pull, last pulled, last error,
  and Remove. Filter by name or show only active / off / groups with errors.
- API: `POST /web/pull` `{"groups": [...]}` (empty = all active), `GET /web/jobs` (live status),
  `PATCH/DELETE /web/groups/{name}`, `POST /web/groups/import` (now returns 202 and runs in the background).

## Spotweb-style interface
![Spot list](docs/img/spots.png)

- **Category tree** with counts (Movies, TV, Audio, PC/Games, Console, Ebooks, XXX, Other), plus quick filters for
  language (Dutch audio or NL subs), resolution, source and age, and sorting by date, title, size, rating or grabs.
- **Spot details parsed from the release name**: resolution, source (Remux/BluRay/WEB-DL/...), video codec, HDR/Dolby Vision,
  audio (TrueHD Atmos, DTS-HD MA, DD+ ...), language, subtitles, edition (Extended, Repack ...), release group.
  Genres and a website link come from TVmaze/TMDB. Existing releases are tagged automatically on start/scan.
- **Send to NZBGet or SABnzbd** from the list or the spot view: set `DOWNLOADER=nzbget|sabnzbd`, `DOWNLOADER_URL`
  and `DOWNLOADER_USER`/`DOWNLOADER_PASS` (NZBGet) or `DOWNLOADER_APIKEY` (SABnzbd). Categories default to
  Movies/Series/Music/Software (NZBGet) or movies/tv/audio/software (SABnzbd); override with
  `DOWNLOADER_CATEGORIES=2000=Movies,5000=Series`.

## Release information (Spotweb-style)
- **Details panel**: click a release for poster, description, rating, file list with part counts, and the NFO.
- **TV** metadata comes from TVmaze, **movies** from Cinemeta; neither needs a key. Shows TVmaze doesn't know fall back
  to Cinemeta. With `TMDB_API_KEY` set, movies are looked up on TMDB first.
  Credit: this product uses the TMDB API but is not endorsed or certified by TMDB. TV data courtesy of TVmaze (CC BY-SA). Movie data via Cinemeta (Stremio).
- **NFO files** are downloaded from Usenet (first segment of the `.nfo`) by the scanner.
- The API also returns `tvdbid`, `imdb`, `tmdbid`, `coverurl`, `rating` and `year`, and accepts `tvdbid`/`imdbid`/`tmdbid`
  search params, so Sonarr and Radarr can match releases by id. Releases not looked up yet still match by title.
- Existing databases are upgraded in place on start (new nullable columns are added automatically).
- Real Spotnet data (the signed posts Spotweb reads from `free.pt`) is not implemented. For that, keep running Spotweb
  and add it to Prowlarr next to this indexer.

## Known limits (next things to build)
- Obfuscated posts (random per-file names) are not grouped; needs set-marker/poster/time-window grouping.
- Real Spotnet spots (`free.pt`) are not read yet.
- Collections store parts in a JSON column. Fine for a personal index; for millions of headers a `parts` table + Postgres would scale better.
- Categories come from name heuristics; TV/movie metadata comes from TVmaze/Cinemeta/TMDB title matching (no TVDB API).
- No PAR2 inspection, passworded-release detection, or release de-duplication across groups.
- Check your Usenet provider's terms before bulk-scanning headers.

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
