# IMDB Cache UI

Working Streamlit app for searching the free IMDB API with a hybrid cache.
This version includes the basic feature set: search, detail lookup, cache
inspection, local MySQL/MariaDB detection, structured logging, and startup
health checks.

## What It Does

- Uses the free IMDB API with no API key required.
- Rate limits API traffic to 1 request per second.
- Caches JSON responses in MySQL/MariaDB.
- Stores images on disk for smaller database size.
- Detects a local Windows MySQL/MariaDB service first.
- Auto-creates the database and tables on startup.
- Writes JSON logs to `logs/app.log` and `logs/errors.json`.

## Main Features

- Search titles by text or direct IMDb ID.
- Look up title details, ratings, name details, and filmography.
- View cached data as pretty output, table view, or raw JSON.
- Invalidate cache entries by endpoint or resource ID.
- Show startup health information when initialization fails.
- Reuse a rate-limited background queue for all API calls.

## Project Layout

- `ui/app.py` - Streamlit entry point.
- `core/api_client.py` - IMDB API client and retry logic.
- `core/cache_manager.py` - Cache orchestration and request coalescing.
- `core/queue.py` - Rate-limited background queue.
- `core/db_manager.py` - Database creation and connection management.
- `core/storage/database.py` - MySQL/MariaDB cache storage.
- `core/storage/filesystem.py` - Filesystem cache and image storage.
- `utils/health_check.py` - Startup checks and dependency bootstrap.
- `utils/logger.py` - Structured JSON logging.
- `utils/schema_mapper.py` - Endpoint registry loaded from JSON.
- `config/api_endpoints.json` - Endpoint definitions and schema hints.

## Requirements

- Python 3.11+
- MySQL or MariaDB
- Streamlit
- Internet access for IMDB API requests

## Configuration

Create a `.env` file in the project root. Start from `.env.example` and update
the database values for your local setup.

Example:

```env
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your-password
DB_NAME=imdb_cache
IMDB_API_KEY=
CACHE_TTL_SECONDS=3600
STORAGE_STRATEGY=hybrid
LOG_LEVEL=INFO
LOG_FILE=logs/app.log
LOG_JSON=true
MAX_CACHE_SIZE_MB=500
```

## Quick Start

```bash
git clone https://github.com/filipetorresdecarvalho/OpenCodeImdbApiCacheUI
cd OpenCodeImdbApiCacheUI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run ui/app.py
```

## Windows + MariaDB

If MariaDB is already installed as a Windows service, this app will try to use
it before suggesting Docker. The service is detected automatically during the
startup health check.

If connection fails:

- Confirm the service is running in `services.msc`.
- Verify the `DB_USER`, `DB_PASSWORD`, and `DB_PORT` values in `.env`.
- Make sure the selected user can connect from `localhost`.

## API Endpoints

Configured endpoints include:

- `titles_detail`
- `titles_rating`
- `names_detail`
- `names_filmography`
- `search_title`
- `search_name`

Endpoint definitions live in `config/api_endpoints.json`.

## Logging

Logs are written in JSON format for easier debugging.

- `logs/app.log` - full application log
- `logs/errors.json` - warnings and errors only

## Testing

```bash
pytest
```

## Notes

- This is a working baseline with the core features implemented.
- The app is designed to fail fast with a health report when startup checks do
  not pass.
- The free IMDB API can still return rate limits, so all traffic goes through
  the queue.

## License

MIT
