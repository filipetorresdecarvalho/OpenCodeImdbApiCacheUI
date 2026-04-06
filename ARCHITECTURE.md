# 🎬 IMDB Cache UI - Production-Ready IMDB API Caching Application

A **self-healing, production-grade Python web application** that caches IMDB API responses using MySQL/MariaDB with a beautiful Streamlit UI. Specifically designed for the **free IMDB API** with rate limiting (1 request/second) and comprehensive error handling.

---

## ✨ Key Features

### 🚀 **Self-Healing Startup**
- ✅ Auto-detects and installs missing Python dependencies
- ✅ Auto-creates database schema with SQLAlchemy ORM
- ✅ Auto-verifies MySQL/MariaDB connectivity
- ✅ Comprehensive health checks with actionable error messages

### 💾 **Hybrid Caching Architecture**
- **MySQL/MariaDB**: Stores JSON responses (queryable, ACID-compliant)
- **Filesystem**: Stores images (reduces database bloat)
- **TTL Management**: Automatic expiration of stale cache entries
- **Request Coalescing**: Multiple concurrent requests share single API call

### 🔄 **Rate-Limited Async Queue**
- **1 request/second**: Prevents IMDB API from blocking your IP
- **Automatic retries**: Exponential backoff on rate limits (429) and server errors (503)
- **Background worker**: Processes requests in separate thread
- **No API key required**: Works with free IMDB API tier

### 🎯 **Beautiful UI (Streamlit)**
- Search titles and people
- View detailed information (ratings, filmography, cast, etc.)
- Multiple view modes (Pretty, Table, Raw JSON)
- Cache status badges (hit/miss/expired)
- Cache management interface
- Real-time cache statistics

### 📊 **Comprehensive Logging**
- **JSON-structured logs** with request IDs and latency
- **Separate error logs** for debugging
- **Rotating file handlers** to manage log size
- All logs saved to `logs/` folder

### 🛡️ **Robust Error Handling**
- Try/catch blocks throughout codebase
- Graceful fallbacks on API failures
- User-friendly error messages
- Detailed error codes for debugging
- No unhandled exceptions

---

## 📋 Project Structure

```
imdb-cache-app/
├── config/
│   ├── settings.py              # Configuration (no API key required)
│   └── api_endpoints.json       # Endpoint registry
├── core/
│   ├── db_manager.py            # Database connection & ORM
│   ├── api_client.py            # IMDB API client with retries
│   ├── cache_manager.py         # Main caching logic
│   ├── queue.py                 # Rate-limited request queue (1 req/sec)
│   └── storage/
│       ├── base.py              # Storage strategy interface
│       ├── database.py          # MySQL caching backend
│       └── filesystem.py        # Filesystem caching backend
├── utils/
│   ├── logger.py                # JSON logging with error tracking
│   ├── health_check.py          # Startup validation
│   └── schema_mapper.py         # Endpoint registry
├── ui/
│   ├── app.py                   # Streamlit main app
│   └── components/
│       └── widgets.py           # Reusable UI components
├── tests/
│   └── test_core.py             # Unit tests
├── logs/                        # Error and debug logs (auto-created)
├── cache/                       # Filesystem cache (auto-created)
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── .env.example
└── README.md
```

---

## 🚀 Quick Start

### 1. **Clone & Setup**

```bash
git clone https://github.com/filipetorresdecarvalho/OpenCodeImdbApiCacheUI.git
cd OpenCodeImdbApiCacheUI
cp .env.example .env
```

### 2. **Configure** (`.env`)

```bash
# Database
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=yourpassword
DB_NAME=imdb_cache

# IMDB API (no key required for free tier)
IMDB_API_KEY=        # Optional - leave empty for free tier
IMDB_RATE_LIMIT=1    # 1 request per second (don't increase!)

# Caching
CACHE_TTL_SECONDS=3600          # 1 hour default
STORAGE_STRATEGY=hybrid         # hybrid (DB + FS) or database

# Logging
LOG_LEVEL=INFO
LOG_JSON=true                   # Structured JSON logs
```

### 3. **Start Database** (Pick One)

**Option A: Docker (Recommended)**
```bash
docker-compose up -d mysql
```

**Option B: Existing MySQL/MariaDB**
```bash
# Just configure DB_HOST, DB_USER, DB_PASSWORD in .env
```

### 4. **Install & Run**

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run ui/app.py
```

The app will automatically:
- Create the database if it doesn't exist
- Create all tables
- Start the rate-limited queue
- Launch Streamlit UI at http://localhost:8501

---

## 🔧 Usage Examples

### **Search for a Movie**
```
1. Go to "Search" tab
2. Select "Title"
3. Type "The Godfather"
4. Click "Search"
→ Results cached for 1 hour
```

### **Get Movie Details**
```
1. Go to "Detail Lookup" tab
2. Select "Title Detail"
3. Enter IMDB ID: tt0111161
4. Click "Lookup"
→ View as Pretty/Table/JSON
→ Shows cache hit/miss badge
```

### **Search for a Person**
```
1. Go to "Search" tab
2. Select "Name"
3. Type "Al Pacino"
4. Click "Search"
```

### **Get Actor Filmography**
```
1. Go to "Detail Lookup" tab
2. Select "Name Filmography"
3. Enter IMDB ID: nm0000199
4. Click "Lookup"
→ Shows all movies with job titles (Actor, Director, Writer, etc.)
```

---

## 📊 Rate Limiting & Queue

The app enforces **1 request per second** to avoid being blocked by IMDB:

```python
# In config/settings.py
imdb_rate_limit = 1  # requests per second

# In core/queue.py
# Background worker thread processes requests at this rate
# Multiple concurrent user requests are queued and processed serially
```

**Why 1 request/second?**
- IMDB free API has strict rate limits
- Prevents IP banning
- More reliable than hitting limits and retrying

**Request Flow:**
```
User Click
    ↓
Check Cache (DB + FS)
    ├─ HIT: Return cached data
    └─ MISS: Submit to Queue
          ↓
      [Rate-Limited Queue]
      (waits 1 second between requests)
          ↓
      Call IMDB API
          ├─ Success: Store in cache (DB + FS)
          ├─ 429 Rate Limit: Retry with backoff
          └─ 503 Unavailable: Retry with backoff
          ↓
      Return to user
```

---

## 📝 Error Handling & Logging

### **Log Files**

All logs are saved to `logs/` folder:

```
logs/
├── app.log           # All messages (JSON format)
└── errors.json       # Only WARNING and ERROR (JSON format)
```

### **Error Codes**

Common error codes you might see in logs:

| Code | Meaning |
|------|---------|
| `RATE_LIMIT_429` | IMDB API rate-limited - auto-retried |
| `SERVICE_UNAVAILABLE_503` | IMDB API down - auto-retried |
| `REQUEST_TIMEOUT` | Network timeout - retried |
| `CONNECTION_ERROR` | Network unreachable - retried |
| `DB_CONNECTION_FAILED` | MySQL/MariaDB unreachable |
| `CACHE_SAVE_FAILED` | Failed to save to cache |
| `MAX_RETRIES_EXCEEDED` | Gave up after 3 retry attempts |

### **Log Example**

```json
{
  "timestamp": "2024-04-06T15:23:45.123456",
  "level": "INFO",
  "logger": "imdb_cache",
  "message": "Cache HIT (DB): titles_detail::tt0111161::abc123def456",
  "module": "cache_manager",
  "function": "get",
  "line": 142,
  "request_id": "a3f5b9c2",
  "cache_status": "hit",
  "latency_ms": 12.5
}
```

---

## 🗄️ Database Schema

### **Main Cache Table: `imdb_cache_entries`**

```sql
CREATE TABLE imdb_cache_entries (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    endpoint VARCHAR(100) NOT NULL,
    resource_id VARCHAR(100) NOT NULL,
    params_hash VARCHAR(64) NOT NULL,
    response_json LONGTEXT NOT NULL,
    image_paths JSON,
    cached_at DATETIME,
    expires_at DATETIME NOT NULL,
    created_at DATETIME,
    updated_at DATETIME,
    INDEX (endpoint, resource_id, params_hash),
    INDEX (expires_at)
);
```

### **Cache Query Examples**

```python
# In cache_manager.py
from core.cache_manager import CacheManager

# Get data (auto-uses cache)
result, status = cache_manager.get(
    endpoint="titles_detail",
    resource_id="tt0111161"
)
# status = "hit", "miss", "fs_hit", "coalesced", "not_found"

# Force refresh (bypass cache)
result, status = cache_manager.get(
    endpoint="titles_detail",
    resource_id="tt0111161",
    force_refresh=True
)

# Invalidate specific entry
cache_manager.invalidate(endpoint="titles_detail", resource_id="tt0111161")

# Invalidate all entries for endpoint
cache_manager.invalidate_endpoint("titles_detail")

# Get statistics
stats = cache_manager.get_stats()
# {
#     "total_entries": 1523,
#     "valid_entries": 1456,
#     "expired_entries": 67
# }
```

---

## 🔧 API Endpoints (Supported by Registry)

Defined in `config/api_endpoints.json`:

| Endpoint | Path | Description |
|----------|------|-------------|
| `titles_detail` | `/titles/{id}` | Get movie/title information |
| `titles_rating` | `/titles/{id}/ratings` | Get ratings from multiple sources |
| `names_detail` | `/names/{id}` | Get actor/person information |
| `names_filmography` | `/names/{id}/filmography` | Get all movies for person |
| `search_title` | `/search/title/{query}` | Search for titles |
| `search_name` | `/search/name/{query}` | Search for people |

To add new endpoints, edit `config/api_endpoints.json` and add schema hints for dynamic table creation.

---

## 🧪 Testing

Run the test suite:

```bash
pip install pytest
pytest tests/ -v
```

**Test Coverage:**
- ✅ Settings configuration
- ✅ Cache manager (hit/miss/coalesce)
- ✅ Database operations
- ✅ Endpoint registry
- ✅ Health checks

---

## 🐳 Docker Deployment

### **Using Docker Compose (Recommended)**

```bash
docker-compose up
```

This starts:
- MariaDB container on port 3306
- Streamlit app on port 8501

### **Using Docker Directly**

```bash
# Build image
docker build -t imdb-cache-ui .

# Run container
docker run -p 8501:8501 \
  -e DB_HOST=mysql-host \
  -e DB_USER=root \
  -e DB_PASSWORD=password \
  imdb-cache-ui
```

---

## 🔐 Security Considerations

- **No API Key Exposed**: Free IMDB API requires no key
- **SQL Injection Prevention**: Uses SQLAlchemy ORM with parameterized queries
- **Input Sanitization**: All user input sanitized for filesystem operations
- **Rate Limiting**: Prevents abuse and IP banning
- **Error Logging**: Sensitive info never logged (no request bodies, etc.)

---

## 📈 Performance Tuning

### **Increase Cache TTL**
```bash
CACHE_TTL_SECONDS=86400  # 24 hours instead of 1 hour
```

### **Enable Filesystem-Only Caching**
```bash
STORAGE_STRATEGY=filesystem  # Skip database for faster access
```

### **Database Connection Pooling**
```python
# In core/db_manager.py
pool_size=10          # Connections to keep open
max_overflow=20       # Additional connections allowed
pool_recycle=3600     # Recycle connections hourly
```

### **Optimize Logging**
```bash
LOG_LEVEL=WARNING      # Only log warnings/errors
LOG_JSON=false         # Use plain text instead of JSON
```

---

## 🐛 Troubleshooting

### **"Database connection failed"**
```bash
# Check MySQL/MariaDB is running
docker-compose logs mysql

# Or check existing MySQL service
mysqladmin ping -h localhost -u root
```

### **"No results found" in searches**
- Try with different search query
- Check internet connection
- Verify IMDB API is accessible: https://imdb-api.com
- Check logs for HTTP errors

### **"Rate limited (429)" in logs**
- App will auto-retry with backoff
- Wait a few minutes before searching again
- This is normal with free API tier

### **Cache not working**
```bash
# Clear cache and logs
rm -rf cache/ logs/

# Check database
mysql -u root imdb_cache -e "SELECT COUNT(*) FROM imdb_cache_entries;"

# Force refresh
Check "Force Refresh" checkbox in UI
```

### **Logs growing too fast**
```bash
# Logs rotate at 50MB automatically
# Or manually reduce log level
LOG_LEVEL=WARNING  # Only warnings and errors
```

---

## 📚 Development

### **Adding a New API Endpoint**

1. **Register in `config/api_endpoints.json`:**
```json
{
  "endpoints": {
    "my_new_endpoint": {
      "path": "/my-api/{id}",
      "table_name": "imdb_my_endpoint",
      "cache_response": true,
      "cache_images": false,
      "ttl_override": null,
      "schema_hints": { "id": "VARCHAR(20)", "data": "TEXT" }
    }
  }
}
```

2. **Use in cache manager:**
```python
result, status = cache_manager.get(
    endpoint="my_new_endpoint",
    resource_id="some_id"
)
```

### **Adding Custom Error Handling**

All modules use try/catch with error codes:

```python
try:
    # Do something
    pass
except SpecificError as e:
    logger.error(
        f"Descriptive message: {e}",
        exc_info=True,  # Include traceback
        extra={
            "error_code": "MY_ERROR_CODE",
            "custom_field": "value"
        }
    )
except Exception as e:
    # Catch-all for unexpected errors
    logger.error(f"Unexpected error: {e}", exc_info=True)
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure logs include error codes and context
5. Document any new configuration options
6. Submit a pull request

---

## 📄 License

MIT License - see LICENSE file

---

## 🎓 Learning Resources

- **SQLAlchemy ORM**: https://docs.sqlalchemy.org/
- **Streamlit Docs**: https://docs.streamlit.io/
- **IMDB API**: https://imdb-api.com
- **Python Logging**: https://docs.python.org/3/library/logging.html
- **Rate Limiting**: https://en.wikipedia.org/wiki/Rate_limiting

---

## ⭐ Key Takeaways

✅ **No API key required** - works with free IMDB API  
✅ **Self-healing** - auto-installs deps, creates DB, validates health  
✅ **Production-ready** - comprehensive error handling and logging  
✅ **Performant** - hybrid caching (DB + FS), request coalescing  
✅ **User-friendly** - beautiful Streamlit UI with multiple views  
✅ **Developer-friendly** - well-documented, extensive comments, type hints  

---

## 📞 Support

For issues or questions:
1. Check the **Troubleshooting** section above
2. Review logs in `logs/errors.json`
3. Run health checks: `python -c "from utils.health_check import HealthChecker; HealthChecker(Settings()).run_all()"`
4. Open an issue on GitHub with error codes from logs

---

**Happy caching! 🎬**
