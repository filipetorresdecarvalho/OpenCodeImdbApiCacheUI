import hashlib
import json
import time
import uuid
import logging
from typing import Optional, Any
from datetime import datetime, timezone, timedelta

import requests

from config.settings import Settings
from core.db_manager import DatabaseManager
from core.api_client import ApiClient
from core.storage.database import DatabaseStorage
from core.storage.filesystem import FileSystemStorage
from utils.logger import logger


class CacheManager:
    def __init__(
        self,
        settings: Settings,
        db_manager: DatabaseManager,
        api_client: ApiClient,
    ):
        self.settings = settings
        self.db = db_manager
        self.api = api_client
        self.db_storage = DatabaseStorage(db_manager, settings)
        self.fs_storage = FileSystemStorage(settings)
        self._in_flight: dict[str, Any] = {}

    def _make_key(self, endpoint: str, resource_id: str = "", params: Optional[dict] = None) -> str:
        param_str = json.dumps(params or {}, sort_keys=True)
        param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]
        return f"{endpoint}::{resource_id}::{param_hash}"

    def _is_expired(self, cached: Optional[dict]) -> bool:
        if not cached:
            return True
        meta = cached.get("_cache_meta", {})
        expires_at = meta.get("expires_at")
        if not expires_at:
            return False
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > exp_dt
        except (ValueError, TypeError):
            return False

    def get(
        self,
        endpoint: str,
        resource_id: str = "",
        query: str = "",
        params: Optional[dict] = None,
        force_refresh: bool = False,
    ) -> tuple[dict, str]:
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        if not force_refresh:
            key = self._make_key(endpoint, resource_id, params)

            if key in self._in_flight:
                logger.info(f"Request coalescing for {key} (request_id={request_id})")
                return self._in_flight[key], "coalesced"

            cached = self.db_storage.load(key)
            if cached and not self._is_expired(cached):
                latency_ms = (time.time() - start_time) * 1000
                logger.info(
                    f"Cache HIT: {key} ({latency_ms:.0f}ms, request_id={request_id})",
                    extra={"request_id": request_id, "cache_status": "hit", "latency_ms": latency_ms},
                )
                return cached, "hit"

            if self.settings.storage_strategy == "hybrid":
                fs_cached = self.fs_storage.load(key)
                if fs_cached and not self._is_expired(fs_cached):
                    latency_ms = (time.time() - start_time) * 1000
                    logger.info(
                        f"FS Cache HIT: {key} ({latency_ms:.0f}ms, request_id={request_id})",
                        extra={"request_id": request_id, "cache_status": "fs_hit", "latency_ms": latency_ms},
                    )
                    return fs_cached, "fs_hit"

        self._in_flight[key] = None

        try:
            response = self.api.fetch(endpoint, resource_id, query, params)

            if response.get("error") == "not_found":
                del self._in_flight[key]
                return response, "not_found"

            image_paths = []
            if self.settings.storage_strategy in ("hybrid", "filesystem"):
                image_paths = self._extract_and_save_images(endpoint, resource_id, response)

            ttl = self.settings.cache_ttl_seconds
            metadata = {
                "endpoint": endpoint,
                "resource_id": resource_id,
                "params_hash": self._make_key(endpoint, resource_id, params).split("::")[2],
                "image_paths": image_paths,
                "ttl_seconds": ttl,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=ttl)
                ).isoformat(),
            }

            self.db_storage.save(key, response, metadata)

            if self.settings.storage_strategy in ("hybrid", "filesystem"):
                self.fs_storage.save(key, response, metadata)

            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                f"Cache MISS (fetched from API): {key} ({latency_ms:.0f}ms, request_id={request_id})",
                extra={"request_id": request_id, "cache_status": "miss", "latency_ms": latency_ms},
            )

            result = dict(response)
            result["_cache_meta"] = {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": metadata["expires_at"],
                "image_paths": image_paths,
                "cache_status": "fresh",
            }

            return result, "miss"

        except Exception as e:
            logger.error(f"Failed to fetch from API for {key}: {e}")
            del self._in_flight[key]
            raise
        finally:
            if key in self._in_flight:
                del self._in_flight[key]

    def invalidate(self, endpoint: str, resource_id: str = "", params: Optional[dict] = None) -> int:
        key = self._make_key(endpoint, resource_id, params)
        deleted = 0
        deleted += self.db_storage.delete(key)
        if self.settings.storage_strategy in ("hybrid", "filesystem"):
            self.fs_storage.delete(key)
        logger.info(f"Cache invalidated: {key}")
        return deleted

    def invalidate_endpoint(self, endpoint: str) -> int:
        return self.db_storage.invalidate_by_endpoint(endpoint)

    def get_stats(self) -> dict:
        db_stats = self.db_storage.get_stats()
        return db_stats

    def _extract_and_save_images(
        self, endpoint: str, resource_id: str, data: Any
    ) -> list[str]:
        image_paths = []
        if not isinstance(data, dict):
            return image_paths

        urls = self._find_image_urls(data)
        for url in urls:
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                path = self.fs_storage.save_image(
                    endpoint, resource_id, url, resp.content
                )
                if path:
                    image_paths.append(path)
            except Exception as e:
                logger.warning(f"Failed to download image {url}: {e}")

        return image_paths

    @staticmethod
    def _find_image_urls(data: Any, depth: int = 0) -> list[str]:
        if depth > 10:
            return []
        urls = []
        if isinstance(data, dict):
            for key, value in data.items():
                if key in ("image", "imageUrl", "url", "poster") and isinstance(value, str):
                    if value.startswith(("http://", "https://")) and any(
                        value.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")
                    ):
                        urls.append(value)
                elif isinstance(value, (dict, list)):
                    urls.extend(CacheManager._find_image_urls(value, depth + 1))
        elif isinstance(data, list):
            for item in data:
                urls.extend(CacheManager._find_image_urls(item, depth + 1))
        return urls
