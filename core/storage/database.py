"""
Database storage implementation for cache entries.

Stores JSON API responses in MySQL/MariaDB with:
- TTL-based expiration
- Query by endpoint/resource/params
- Bulk invalidation
- Statistics tracking

Uses SQLAlchemy ORM for type safety and query building.
"""
import json
import logging
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

from sqlalchemy import Column, String, Text, DateTime, Index, BigInteger
from sqlalchemy.exc import SQLAlchemyError

from core.db_manager import Base, DatabaseManager
from core.storage.base import StorageStrategy
from config.settings import Settings
from utils.logger import logger


class CacheEntry(Base):
    """ORM model for cache entries in database.

    Each entry stores:
    - endpoint: Which API endpoint was called
    - resource_id: Which resource was fetched
    - params_hash: Hash of parameters for deduplication
    - response_json: The full JSON response
    - image_paths: List of saved image file paths
    - cached_at: When this was cached
    - expires_at: When this entry expires (for TTL)
    """

    __tablename__ = "imdb_cache_entries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    endpoint = Column(String(100), nullable=False, index=True)
    resource_id = Column(String(100), nullable=False, index=True)
    params_hash = Column(String(64), nullable=False, index=True)
    response_json = Column(Text, nullable=False)
    image_paths = Column(Text, nullable=True)
    cached_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Composite index for fast lookups by endpoint + resource + params
    __table_args__ = (
        Index("idx_endpoint_resource_params", "endpoint", "resource_id", "params_hash"),
        Index("idx_expires_at", "expires_at"),
    )


class DatabaseStorage(StorageStrategy):
    """Caching backend using MySQL/MariaDB.

    Advantages:
    - ACID compliance
    - Easy querying and bulk operations
    - Built-in backup/replication support
    - Good for frequently accessed data

    Disadvantages:
    - Not suitable for large binary data (images)
    - Database size grows quickly with responses
    """

    def __init__(self, db_manager: DatabaseManager, settings: Settings):
        """Initialize database storage.

        Args:
            db_manager: Database manager instance
            settings: Application settings
        """
        self.db = db_manager
        self.settings = settings
        logger.debug("DatabaseStorage initialized")

    def save(
        self,
        key: str,
        data: Any,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Save a cache entry to the database.

        Automatically handles insert-or-update logic.

        Args:
            key: Cache key (format: "endpoint::resource_id::params_hash")
            data: Data to cache (will be JSON serialized)
            metadata: Optional metadata (endpoint, resource_id, ttl_seconds, etc.)

        Returns:
            True if successful, False otherwise
        """
        metadata = metadata or {}

        try:
            with self.db.session_scope() as session:
                try:
                    # Serialize data to JSON
                    response_json = json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str,
                    )

                    # Build cache entry
                    now = datetime.now(timezone.utc)
                    entry = CacheEntry(
                        endpoint=metadata.get("endpoint", ""),
                        resource_id=metadata.get("resource_id", ""),
                        params_hash=metadata.get("params_hash", ""),
                        response_json=response_json,
                        image_paths=json.dumps(metadata.get("image_paths", [])),
                        cached_at=now,
                        expires_at=now,
                        created_at=now,
                        updated_at=now,
                    )

                    # Set expiration time
                    if "ttl_seconds" in metadata:
                        entry.expires_at = now + timedelta(
                            seconds=metadata["ttl_seconds"]
                        )
                    else:
                        entry.expires_at = now + timedelta(
                            seconds=self.settings.cache_ttl_seconds
                        )

                    # Check if entry already exists
                    existing = (
                        session.query(CacheEntry)
                        .filter_by(
                            endpoint=entry.endpoint,
                            resource_id=entry.resource_id,
                            params_hash=entry.params_hash,
                        )
                        .first()
                    )

                    if existing:
                        # Update existing entry
                        existing.response_json = entry.response_json
                        existing.image_paths = entry.image_paths
                        existing.cached_at = entry.cached_at
                        existing.expires_at = entry.expires_at
                        existing.updated_at = entry.updated_at
                        logger.debug(f"Updated cache entry in DB: {key}")
                    else:
                        # Insert new entry
                        session.add(entry)
                        logger.debug(f"Created new cache entry in DB: {key}")

                except ValueError as e:
                    logger.error(
                        f"Failed to JSON serialize cache data: {e}",
                        exc_info=True,
                        extra={
                            "error_code": "JSON_SERIALIZATION_FAILED",
                            "key": key,
                        },
                    )
                    return False

            logger.debug(f"Cache entry saved to DB: {key}")
            return True

        except SQLAlchemyError as e:
            logger.error(
                f"Database error saving cache entry: {e}",
                exc_info=True,
                extra={
                    "error_code": "DB_SAVE_FAILED",
                    "key": key,
                },
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error saving cache entry: {e}",
                exc_info=True,
                extra={
                    "error_code": "CACHE_SAVE_UNEXPECTED_ERROR",
                    "key": key,
                },
            )
            return False

    def load(self, key: str) -> Optional[dict]:
        """Load a cache entry from the database.

        Automatically checks expiration.

        Args:
            key: Cache key

        Returns:
            Cached data if found and valid, None otherwise
        """
        metadata = self._parse_key(key)

        try:
            with self.db.session_scope() as session:
                try:
                    # Query for the cache entry
                    entry = (
                        session.query(CacheEntry)
                        .filter_by(
                            endpoint=metadata.get("endpoint", ""),
                            resource_id=metadata.get("resource_id", ""),
                            params_hash=metadata.get("params_hash", ""),
                        )
                        .first()
                    )

                    if not entry:
                        logger.debug(f"Cache entry not found in DB: {key}")
                        return None

                    # Check expiration
                    now = datetime.now(timezone.utc)
                    if entry.expires_at < now:
                        logger.info(f"Cache entry expired: {key}")
                        return None

                    # Parse JSON response
                    try:
                        data = json.loads(entry.response_json)
                    except json.JSONDecodeError as e:
                        logger.error(
                            f"Failed to parse cached JSON: {e}",
                            exc_info=True,
                            extra={
                                "error_code": "JSON_PARSE_FAILED",
                                "key": key,
                            },
                        )
                        return None

                    # Add metadata
                    data["_cache_meta"] = {
                        "cached_at": entry.cached_at.isoformat(),
                        "expires_at": entry.expires_at.isoformat(),
                        "image_paths": (
                            json.loads(entry.image_paths)
                            if entry.image_paths
                            else []
                        ),
                        "cache_status": "hit",
                    }

                    logger.debug(f"Cache entry loaded from DB: {key}")
                    return data

                except json.JSONDecodeError as e:
                    logger.error(
                        f"Failed to parse image_paths JSON: {e}",
                        exc_info=True,
                        extra={"error_code": "IMAGE_PATHS_PARSE_FAILED"},
                    )
                    return None

        except SQLAlchemyError as e:
            logger.error(
                f"Database error loading cache entry: {e}",
                exc_info=True,
                extra={
                    "error_code": "DB_LOAD_FAILED",
                    "key": key,
                },
            )
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error loading cache entry: {e}",
                exc_info=True,
                extra={
                    "error_code": "CACHE_LOAD_UNEXPECTED_ERROR",
                    "key": key,
                },
            )
            return None

    def delete(self, key: str) -> bool:
        """Delete a cache entry from the database.

        Args:
            key: Cache key

        Returns:
            True if successful, False otherwise
        """
        metadata = self._parse_key(key)

        try:
            with self.db.session_scope() as session:
                try:
                    entry = (
                        session.query(CacheEntry)
                        .filter_by(
                            endpoint=metadata.get("endpoint", ""),
                            resource_id=metadata.get("resource_id", ""),
                            params_hash=metadata.get("params_hash", ""),
                        )
                        .first()
                    )

                    if entry:
                        session.delete(entry)
                        logger.debug(f"Cache entry deleted from DB: {key}")
                    else:
                        logger.debug(f"Cache entry not found to delete: {key}")

                except SQLAlchemyError as e:
                    logger.error(
                        f"Database error deleting cache entry: {e}",
                        exc_info=True,
                        extra={"error_code": "DB_DELETE_FAILED"},
                    )
                    return False

            return True

        except Exception as e:
            logger.error(
                f"Unexpected error deleting cache entry: {e}",
                exc_info=True,
                extra={"error_code": "CACHE_DELETE_UNEXPECTED_ERROR"},
            )
            return False

    def exists(self, key: str) -> bool:
        """Check if a valid cache entry exists.

        Args:
            key: Cache key

        Returns:
            True if valid cache entry exists, False otherwise
        """
        try:
            return self.load(key) is not None
        except Exception as e:
            logger.warning(
                f"Error checking cache existence: {e}",
                extra={"error_code": "CACHE_EXISTS_CHECK_FAILED"},
            )
            return False

    def invalidate_by_endpoint(self, endpoint: str) -> int:
        """Delete all cache entries for an endpoint.

        Args:
            endpoint: Endpoint name to invalidate

        Returns:
            Number of entries deleted
        """
        try:
            with self.db.session_scope() as session:
                try:
                    deleted = (
                        session.query(CacheEntry)
                        .filter_by(endpoint=endpoint)
                        .delete(synchronize_session=False)
                    )
                    logger.info(
                        f"Invalidated {deleted} cache entries for endpoint: {endpoint}"
                    )
                    return deleted

                except SQLAlchemyError as e:
                    logger.error(
                        f"Database error invalidating endpoint: {e}",
                        exc_info=True,
                        extra={"error_code": "DB_INVALIDATE_ENDPOINT_FAILED"},
                    )
                    return 0

        except Exception as e:
            logger.error(
                f"Unexpected error invalidating endpoint: {e}",
                exc_info=True,
                extra={"error_code": "INVALIDATE_ENDPOINT_UNEXPECTED_ERROR"},
            )
            return 0

    def invalidate_by_resource(self, endpoint: str, resource_id: str) -> int:
        """Delete all cache entries for a specific resource.

        Args:
            endpoint: Endpoint name
            resource_id: Resource ID

        Returns:
            Number of entries deleted
        """
        try:
            with self.db.session_scope() as session:
                try:
                    deleted = (
                        session.query(CacheEntry)
                        .filter_by(endpoint=endpoint, resource_id=resource_id)
                        .delete(synchronize_session=False)
                    )
                    logger.info(
                        f"Invalidated {deleted} entries for {endpoint}/{resource_id}"
                    )
                    return deleted

                except SQLAlchemyError as e:
                    logger.error(
                        f"Database error invalidating resource: {e}",
                        exc_info=True,
                        extra={"error_code": "DB_INVALIDATE_RESOURCE_FAILED"},
                    )
                    return 0

        except Exception as e:
            logger.error(
                f"Unexpected error invalidating resource: {e}",
                exc_info=True,
                extra={"error_code": "INVALIDATE_RESOURCE_UNEXPECTED_ERROR"},
            )
            return 0

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with entry counts
        """
        try:
            with self.db.session_scope() as session:
                try:
                    total = session.query(CacheEntry).count()
                    expired = (
                        session.query(CacheEntry)
                        .filter(
                            CacheEntry.expires_at < datetime.now(timezone.utc)
                        )
                        .count()
                    )
                    valid = total - expired

                    stats = {
                        "total_entries": total,
                        "valid_entries": valid,
                        "expired_entries": expired,
                    }
                    logger.debug(f"Cache stats: {stats}")
                    return stats

                except SQLAlchemyError as e:
                    logger.error(
                        f"Database error getting stats: {e}",
                        exc_info=True,
                        extra={"error_code": "DB_STATS_FAILED"},
                    )
                    return {
                        "total_entries": 0,
                        "valid_entries": 0,
                        "expired_entries": 0,
                        "error": str(e),
                    }

        except Exception as e:
            logger.error(
                f"Unexpected error getting stats: {e}",
                exc_info=True,
                extra={"error_code": "STATS_UNEXPECTED_ERROR"},
            )
            return {
                "total_entries": 0,
                "valid_entries": 0,
                "expired_entries": 0,
                "error": str(e),
            }

    @staticmethod
    def _parse_key(key: str) -> dict:
        """Parse cache key into components.

        Key format: "endpoint::resource_id::params_hash"

        Args:
            key: Cache key string

        Returns:
            Dictionary with parsed components
        """
        try:
            parts = key.split("::")
            return {
                "endpoint": parts[0] if len(parts) > 0 else "",
                "resource_id": parts[1] if len(parts) > 1 else "",
                "params_hash": parts[2] if len(parts) > 2 else "",
            }
        except Exception as e:
            logger.warning(f"Failed to parse cache key '{key}': {e}")
            return {"endpoint": "", "resource_id": "", "params_hash": ""}
