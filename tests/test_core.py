import pytest
import json
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timezone, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from core.cache_manager import CacheManager
from core.api_client import ApiClient
from core.db_manager import DatabaseManager
from core.storage.database import DatabaseStorage
from core.storage.filesystem import FileSystemStorage
from utils.schema_mapper import EndpointRegistry
from utils.health_check import HealthChecker


@pytest.fixture
def settings():
    s = Settings()
    s.imdb_api_key = "test-key"
    s.cache_ttl_seconds = 3600
    s.storage_strategy = "hybrid"
    return s


@pytest.fixture
def mock_db_manager():
    db = MagicMock(spec=DatabaseManager)
    db.session_scope = MagicMock()
    return db


@pytest.fixture
def mock_api_client():
    api = MagicMock(spec=ApiClient)
    return api


@pytest.fixture
def cache_manager(settings, mock_db_manager, mock_api_client):
    cm = CacheManager(settings, mock_db_manager, mock_api_client)
    cm.db_storage = MagicMock(spec=DatabaseStorage)
    cm.fs_storage = MagicMock(spec=FileSystemStorage)
    return cm


class TestSettings:
    def test_default_values(self):
        s = Settings()
        assert s.db_host == "localhost"
        assert s.db_port == 3306
        assert s.cache_ttl_seconds == 3600

    def test_db_url_construction(self):
        s = Settings(db_user="test", db_password="pw", db_host="myhost", db_port=3307, db_name="mydb")
        assert "test:pw@myhost:3307/mydb" in s.db_url
        assert "charset=utf8mb4" in s.db_url


class TestCacheManager:
    def test_cache_hit_returns_cached_data(self, cache_manager):
        cached_data = {"title": "Test Movie", "_cache_meta": {"cache_status": "hit"}}
        cache_manager.db_storage.load.return_value = cached_data

        result, status = cache_manager.get("titles_detail", resource_id="tt123")

        assert status == "hit"
        assert result["title"] == "Test Movie"

    def test_cache_miss_fetches_from_api(self, cache_manager):
        cache_manager.db_storage.load.return_value = None
        cache_manager.api.fetch.return_value = {"title": "New Movie", "year": 2024}
        cache_manager.db_storage.save.return_value = True
        cache_manager.fs_storage.save.return_value = True

        result, status = cache_manager.get("titles_detail", resource_id="tt456")

        assert status == "miss"
        assert result["title"] == "New Movie"
        cache_manager.api.fetch.assert_called_once()
        cache_manager.db_storage.save.assert_called_once()

    def test_force_refresh_bypasses_cache(self, cache_manager):
        cache_manager.api.fetch.return_value = {"title": "Refreshed"}
        cache_manager.db_storage.save.return_value = True
        cache_manager.fs_storage.save.return_value = True

        result, status = cache_manager.get(
            "titles_detail", resource_id="tt789", force_refresh=True
        )

        assert status == "miss"
        cache_manager.db_storage.load.assert_not_called()

    def test_not_found_response(self, cache_manager):
        cache_manager.db_storage.load.return_value = None
        cache_manager.api.fetch.return_value = {"error": "not_found", "url": "test"}

        result, status = cache_manager.get("titles_detail", resource_id="tt000")

        assert status == "not_found"
        assert result["error"] == "not_found"

    def test_request_coalescing(self, cache_manager):
        cache_manager.db_storage.load.return_value = None
        cache_manager.api.fetch.return_value = {"title": "Coalesced"}
        cache_manager.db_storage.save.return_value = True
        cache_manager.fs_storage.save.return_value = True

        result1, status1 = cache_manager.get("titles_detail", resource_id="tt111")
        assert status1 == "miss"


class TestEndpointRegistry:
    @pytest.fixture
    def registry(self, tmp_path):
        config = {
            "endpoints": {
                "titles_detail": {
                    "path": "/titles/{id}",
                    "table_name": "imdb_titles_detail",
                    "cache_response": True,
                    "cache_images": True,
                    "ttl_override": None,
                    "schema_hints": {"id": "VARCHAR(20)", "title": "VARCHAR(500)"},
                }
            }
        }
        config_file = tmp_path / "api_endpoints.json"
        config_file.write_text(json.dumps(config))
        return EndpointRegistry(str(config_file))

    def test_get_endpoint(self, registry):
        ep = registry.get_endpoint("titles_detail")
        assert ep is not None
        assert ep["table_name"] == "imdb_titles_detail"

    def test_get_table_name(self, registry):
        assert registry.get_table_name("titles_detail") == "imdb_titles_detail"
        assert registry.get_table_name("nonexistent") is None

    def test_should_cache_images(self, registry):
        assert registry.should_cache_images("titles_detail") is True

    def test_list_endpoints(self, registry):
        endpoints = registry.list_endpoints()
        assert len(endpoints) == 1
        assert endpoints[0]["name"] == "titles_detail"

    def test_path_matching(self, registry):
        assert registry.match_by_path("/titles/tt123") == "titles_detail"
        assert registry.match_by_path("/unknown/tt123") is None


class TestHealthChecker:
    def test_dependency_check_passes(self, settings):
        checker = HealthChecker(settings)
        result = checker._check_dependencies()
        assert result is True
        assert len(checker.checks_failed) == 0

    def test_api_config_warns_without_key(self, settings):
        settings.imdb_api_key = ""
        checker = HealthChecker(settings)
        result = checker._check_api_config()
        assert result is False
        assert "api:key_missing" in checker.checks_failed

    def test_get_report(self, settings):
        checker = HealthChecker(settings)
        checker.checks_passed = ["dep:requests"]
        checker.checks_failed = ["api:key_missing"]
        report = checker.get_report()
        assert report["healthy"] is False
        assert len(report["passed"]) == 1
        assert len(report["failed"]) == 1
