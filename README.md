# IMDB Cache UI

A production-ready, self-healing Python web application that caches IMDB API responses using MySQL/MariaDB, with a beautiful Streamlit UI.

## Features

- **Self-healing startup**: Auto-detects missing dependencies, verifies DB connectivity, auto-creates schema
- **Hybrid caching strategy**: JSON responses in MySQL, images on filesystem
- **Dynamic endpoint mapping**: Registry-based system that maps any IMDB API endpoint to cache tables
- **Beautiful UI**: Streamlit-based interface with search, cache indicators, manual invalidation, and multiple view modes
- **Structured logging**: JSON logs with request ID, cache status, and latency tracking
- **Docker-ready**: docker-compose.yml for one-command deployment

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url>
cd OpenCodeImdbApiCacheUI

# 2. Configure
cp .env.example .env
# Edit .env with your IMDB API key and DB credentials

# 3. Start DB (or use existing MySQL/MariaDB)
docker-compose up -d mysql

# 4. Run the app
pip install -r requirements.txt
streamlit run ui/app.py
```

## Architecture

See the project README.md for detailed architecture diagrams, decision logs, and API documentation.

## License

MIT
