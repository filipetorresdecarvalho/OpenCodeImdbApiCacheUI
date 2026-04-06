"""
Cache manager with async request queue and comprehensive error handling.

This is the core caching logic:
- Checks database and filesystem cache first
- Falls back to API via rate-limited queue if cache miss
- Stores responses in hybrid cache (DB + FS)
- Extracts and saves images to filesystem
- Handles cache invalidation and TTL management

Key concepts:
- Request coalescing: Multiple requests for same data wait for single API call
- Rate limiting: All API requests go through 1 req/sec queue
- Hybrid storage: JSON in DB (fast query), images on FS (reduces bloat)
"""
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
from core.queue import RateLimitedQueue
from core.storage.database import DatabaseStorage
from core.storage.filesystem import FileSystemStorage
from utils.logger import logger


class CacheManager:
    """Manages caching with rate-limited async requests."""

    def __init__(
        self,
        settings: Settings,
        db_manager: DatabaseManager,
        api_client: ApiClient,
        queue: Optional[RateLimitedQueue] = None,
    ):
        """Initialize cache manager.

        Args:
            settings: Application settings
            db_manager: Database manager instance
            api_client: API client instance
            queue: Optional RateLimitedQueue (creates one if not provided)
        """
        self.settings = settings
        self.db = db_manager
        self.api = api_client
        self.db_storage = DatabaseStorage(db_manager, settings)
        self.fs_storage = FileSystemStorage(settings)

        # Use provided queue or create new one
        if queue is None:
            # Create queue with rate limit from settings (default 1 req/sec)
            self.queue = RateLimitedQueue(
                max_requests_per_second=self.settings.imdb_rate_limit,
                max_retries=self.settings.imdb_max_retries,
            )
            self.queue.start()
        else:
            self.queue = queue

        # Track in-flight requests for request coalescing
        self._in_flight: dict[str, Any] = {}
        logger.info("CacheManager initialized")

    def _make_key(
        self,
        endpoint: str,
        resource_id: str = "",
        params: Optional[dict] = None,
    ) -> str:
        """Generate a unique cache key from endpoint and parameters.

        Args:
            endpoint: API endpoint name
            resource_id: Resource ID
            params: Additional parameters

        Returns:
            Unique cache key string
        """
        try:
            param_str = json.dumps(params or {}, sort_keys=True)
            param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]
            key = f"{endpoint}::{resource_id}::{param_hash}"
            return key
        except Exception as e:
            logger.error(
                f"Failed to make cache key: {e}",
                exc_info=True,
                extra={"error_code": "CACHE_KEY_GENERATION_FAILED"},
            )
            # Fallback to basic key
            return f"{endpoint}::{resource_id}::error"

    def _is_expired(self, cached: Optional[dict]) -> bool:
        """Check if a cached entry has expired.

        Args:
            cached: Cached data dictionary

        Returns:
            True if expired or invalid, False if still valid
        """
        try:
            if not cached:
                return True

            meta = cached.get("_cache_meta", {})
            expires_at = meta.get("expires_at")

            if not expires_at:
                return False

            exp_dt = datetime.fromisoformat(expires_at)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            is_expired = datetime.now(timezone.utc) > exp_dt
            if is_expired:
                logger.debug(f"Cache entry expired at {expires_at}")
            return is_expired

        except (ValueError, TypeError) as e:
            logger.warning(
                f"Error checking cache expiration: {e}",
                exc_info=True,
                extra={"error_code": "CACHE_EXPIRY_CHECK_FAILED"},
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error checking cache expiration: {e}",
                exc_info=True,
                extra={"error_code": "CACHE_EXPIRY_CHECK_UNEXPECTED_ERROR"},
            )
            return True

    def get(
        self,
        endpoint: str,
        resource_id: str = "",
        query: str = "",
        params: Optional[dict] = None,
        force_refresh: bool = False,
        async_fetch: bool = False,
    ) -> tuple[dict, str]:
        """Get data from cache or async queue.

        This method implements the core caching logic:
        1. Check if we have a non-expired cache entry
        2. If cache miss, submit to async queue for rate-limited fetch
        3. Support request coalescing to avoid duplicate API calls

        Args:
            endpoint: API endpoint name
            resource_id: Resource ID to fetch
            query: Search query (for search endpoints)
            params: Additional parameters
            force_refresh: Bypass cache and fetch fresh data
            async_fetch: If True, submit to queue and return immediately
                         If False, wait for result

        Returns:
            Tuple of (data_dict, status_string)
            Status: 'hit', 'miss', 'fs_hit', 'coalesced', 'not_found', 'pending'
        """
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        try:
            key = self._make_key(endpoint, resource_id, params)

            # Skip cache check if force_refresh requested
            if not force_refresh:
                # Try database cache first
                cached = self.db_storage.load(key)
                if cached and not self._is_expired(cached):
                    latency_ms = (time.time() - start_time) * 1000
                    logger.info(
                        f"Cache HIT (DB): {key} ({latency_ms:.0f}ms, request_id={request_id})",
                        extra={
                            "request_id": request_id,
                            "cache_status": "hit",
                            "latency_ms": latency_ms,
                        },
                    )
                    return cached, "hit"

                # Try filesystem cache if hybrid storage
                if self.settings.storage_strategy == "hybrid":
                    fs_cached = self.fs_storage.load(key)
                    if fs_cached and not self._is_expired(fs_cached):
                        latency_ms = (time.time() - start_time) * 1000
                        logger.info(
                            f"Cache HIT (FS): {key} ({latency_ms:.0f}ms, request_id={request_id})",
                            extra={
                                "request_id": request_id,
                                "cache_status": "fs_hit",
                                "latency_ms": latency_ms,
                            },
                        )
                        return fs_cached, "fs_hit"

            # Check for in-flight request (request coalescing)
            if key in self._in_flight:
                waiting_result = self._in_flight[key]
                if waiting_result is not None:
                    logger.info(
                        f"Request coalesced for {key} (request_id={request_id})",
                        extra={
                            "request_id": request_id,
                            "cache_status": "coalesced",
                        },
                    )
                    return waiting_result, "coalesced"

            # Mark as in-flight
            self._in_flight[key] = None

            try:
                # Make the API call
                logger.debug(
                    f"Fetching from IMDB API: {endpoint} (request_id={request_id})"
                )
                response = self.api.fetch(endpoint, resource_id, query, params)

                # Handle not found responses
                if response.get("error") == "not_found":
                    logger.info(f"Resource not found: {key}")
                    if key in self._in_flight:
                        del self._in_flight[key]
                    return response, "not_found"

                # Extract and save images
                image_paths = []
                try:
                    if self.settings.storage_strategy in ("hybrid", "filesystem"):
                        image_paths = self._extract_and_save_images(
                            endpoint, resource_id, response
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to extract images for {key}: {e}",
                        exc_info=True,
                        extra={"error_code": "IMAGE_EXTRACTION_FAILED"},
                    )

                # Store in cache
                try:
                    ttl = self.settings.cache_ttl_seconds
                    metadata = {
                        "endpoint": endpoint,
                        "resource_id": resource_id,
                        "params_hash": key.split("::")[-1],
                        "image_paths": image_paths,
                        "ttl_seconds": ttl,
                        "expires_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=ttl)
                        ).isoformat(),
                    }

                    self.db_storage.save(key, response, metadata)
                    if self.settings.storage_strategy in ("hybrid", "filesystem"):
                        self.fs_storage.save(key, response, metadata)

                except Exception as e:
                    logger.error(
                        f"Failed to save to cache: {e}",
                        exc_info=True,
                        extra={"error_code": "CACHE_SAVE_FAILED"},
                    )

                # Build result
                result = dict(response)
                result["_cache_meta"] = {
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": metadata.get("expires_at"),
                    "image_paths": image_paths,
                    "cache_status": "fresh",
                }

                # Store in in-flight for coalescing
                self._in_flight[key] = result

                latency_ms = (time.time() - start_time) * 1000
                logger.info(
                    f"Cache MISS (API fetch): {key} ({latency_ms:.0f}ms, request_id={request_id})",
                    extra={
                        "request_id": request_id,
                        "cache_status": "miss",
                        "latency_ms": latency_ms,
                    },
                )

                return result, "miss"

            except requests.exceptions.RequestException as e:
                logger.error(
                    f"API request failed for {key}: {e}",
                    exc_info=True,
                    extra={
                        "error_code": "API_REQUEST_FAILED",
                        "request_id": request_id,
                    },
                )
                if key in self._in_flight:
                    del self._in_flight[key]
                raise

            except Exception as e:
                logger.error(
                    f"Unexpected error fetching {key}: {e}",
                    exc_info=True,
                    extra={
                        "error_code": "CACHE_GET_UNEXPECTED_ERROR",
                        "request_id": request_id,
                    },
                )
                if key in self._in_flight:
                    del self._in_flight[key]
                raise

            finally:
                # Clean up in-flight entry after a delay
                if key in self._in_flight:
                    try:
                        time.sleep(0.1)  # Small delay to allow request coalescing
                        if key in self._in_flight:
                            del self._in_flight[key]
                    except Exception:
                        pass

        except Exception as e:
            logger.error(
                f"Critical error in cache.get(): {e}",
                exc_info=True,
                extra={"error_code": "CACHE_GET_CRITICAL_ERROR"},
            )
            raise

    def invalidate(
        self,
        endpoint: str,
        resource_id: str = "",
        params: Optional[dict] = None,
    ) -> int:
        """Invalidate a specific cache entry.

        Args:
            endpoint: Endpoint to invalidate
            resource_id: Resource ID
            params: Parameters

        Returns:
            Number of entries deleted
        """
        try:
            key = self._make_key(endpoint, resource_id, params)
            deleted = 0

            # Delete from DB
            try:
                deleted += self.db_storage.delete(key)
            except Exception as e:
                logger.error(
                    f"Failed to delete from DB cache: {e}",
                    exc_info=True,
                    extra={"error_code": "DB_DELETE_FAILED"},
                )

            # Delete from FS
            if self.settings.storage_strategy in ("hybrid", "filesystem"):
                try:
                    self.fs_storage.delete(key)
                except Exception as e:
                    logger.error(
                        f"Failed to delete from FS cache: {e}",
                        exc_info=True,
                        extra={"error_code": "FS_DELETE_FAILED"},
                    )

            logger.info(f"Cache invalidated: {key} ({deleted} entries)")
            return deleted

        except Exception as e:
            logger.error(
                f"Error invalidating cache: {e}",
                exc_info=True,
                extra={"error_code": "INVALIDATION_ERROR"},
            )
            return 0

    def invalidate_endpoint(self, endpoint: str) -> int:
        """Invalidate all cache entries for an endpoint.

        Args:
            endpoint: Endpoint name to invalidate

        Returns:
            Number of entries deleted
        """
        try:
            return self.db_storage.invalidate_by_endpoint(endpoint)
        except Exception as e:
            logger.error(
                f"Error invalidating endpoint '{endpoint}': {e}",
                exc_info=True,
                extra={"error_code": "ENDPOINT_INVALIDATION_ERROR"},
            )
            return 0

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with total, valid, and expired entry counts
        """
        try:
            return self.db_storage.get_stats()
        except Exception as e:
            logger.error(
                f"Error getting cache stats: {e}",
                exc_info=True,
                extra={"error_code": "STATS_RETRIEVAL_ERROR"},
            )
            return {
                "total_entries": 0,
                "valid_entries": 0,
                "expired_entries": 0,
                "error": str(e),
            }

    def _extract_and_save_images(
        self,
        endpoint: str,
        resource_id: str,
        data: Any,
    ) -> list[str]:
        """Find and download images from API response.

        Args:
            endpoint: Endpoint name
            resource_id: Resource ID
            data: API response data

        Returns:
            List of saved image paths
        """
        image_paths = []

        try:
            if not isinstance(data, dict):
                return image_paths

            # Find all image URLs in response
            urls = self._find_image_urls(data)
            logger.debug(f"Found {len(urls)} image URLs in response")

            for url in urls:
                try:
                    logger.debug(f"Downloading image: {url}")
                    resp = requests.get(url, timeout=15)
                    resp.raise_for_status()

                    path = self.fs_storage.save_image(
                        endpoint, resource_id, url, resp.content
                    )
                    if path:
                        image_paths.append(path)
                        logger.debug(f"Image saved: {path}")

                except requests.exceptions.RequestException as e:
                    logger.warning(
                        f"Failed to download image {url}: {e}",
                        extra={"error_code": "IMAGE_DOWNLOAD_FAILED"},
                    )
                except Exception as e:
                    logger.warning(
                        f"Unexpected error downloading image: {e}",
                        exc_info=True,
                        extra={"error_code": "IMAGE_DOWNLOAD_UNEXPECTED_ERROR"},
                    )

        except Exception as e:
            logger.error(
                f"Error in image extraction: {e}",
                exc_info=True,
                extra={"error_code": "IMAGE_EXTRACTION_ERROR"},
            )

        return image_paths

    @staticmethod
    def _find_image_urls(data: Any, depth: int = 0) -> list[str]:
        """Recursively find all image URLs in a data structure.

        Args:
            data: Data to search
            depth: Current recursion depth

        Returns:
            List of image URLs found
        """
        if depth > 10:
            return []

        urls = []
        try:
            if isinstance(data, dict):
                for key, value in data.items():
                    # Check for common image field names
                    if key in (
                        "image",
                        "imageUrl",
                        "url",
                        "poster",
                        "primaryImage",
                    ) and isinstance(value, str):
                        if value.startswith(("http://", "https://")) and any(
                            value.endswith(ext)
                            for ext in (".jpg", ".jpeg", ".png", ".webp")
                        ):
                            urls.append(value)
                    elif isinstance(value, (dict, list)):
                        urls.extend(CacheManager._find_image_urls(value, depth + 1))

            elif isinstance(data, list):
                for item in data:
                    urls.extend(CacheManager._find_image_urls(item, depth + 1))

        except Exception as e:
            logger.debug(f"Error finding image URLs: {e}")

        return urls

    def shutdown(self):
        """Gracefully shutdown the cache manager."""
        try:
            logger.info("Shutting down CacheManager...")
            if self.queue:
                self.queue.stop()
            logger.info("CacheManager shutdown complete")
        except Exception as e:
            logger.error(
                f"Error during shutdown: {e}",
                exc_info=True,
                extra={"error_code": "SHUTDOWN_ERROR"},
            )
