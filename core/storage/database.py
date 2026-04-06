import json
import logging
from typing import Any, Optional
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, Index, BigInteger
from sqlalchemy.exc import SQLAlchemyError

from core.db_manager import Base, DatabaseManager
from core.storage.base import StorageStrategy
from config.settings import Settings
from utils.logger import logger


class CacheEntry(Base):
    __tablename__ = "imdb_cache_entries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    endpoint = Column(String(100), nullable=False, index=True)
    resource_id = Column(String(100), nullable=False, index=True)
    params_hash = Column(String(64), nullable=False, index=True)
    response_json = Column(Text, nullable=False)
    image_paths = Column(Text, nullable=True)
    cached_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_endpoint_resource_params", "endpoint", "resource_id", "params_hash"),
        Index("idx_expires_at", "expires_at"),
    )


class DatabaseStorage(StorageStrategy):
    def __init__(self, db_manager: DatabaseManager, settings: Settings):
        self.db = db_manager
        self.settings = settings

    def save(self, key: str, data: Any, metadata: Optional[dict] = None) -> bool:
        metadata = metadata or {}
        try:
            with self.db.session_scope() as session:
                entry = CacheEntry(
                    endpoint=metadata.get("endpoint", ""),
                    resource_id=metadata.get("resource_id", ""),
                    params_hash=metadata.get("params_hash", ""),
                    response_json=json.dumps(data, ensure_ascii=False, default=str),
                    image_paths=json.dumps(metadata.get("image_paths", [])),
                    cached_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc).replace(
                        second=0, microsecond=0
                    ),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )

                if "ttl_seconds" in metadata:
                    from datetime import timedelta
                    entry.expires_at = entry.cached_at + timedelta(
                        seconds=metadata["ttl_seconds"]
                    )
                else:
                    from datetime import timedelta
                    entry.expires_at = entry.cached_at + timedelta(
                        seconds=self.settings.cache_ttl_seconds
                    )

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
                    existing.response_json = entry.response_json
                    existing.image_paths = entry.image_paths
                    existing.cached_at = entry.cached_at
                    existing.expires_at = entry.expires_at
                    existing.updated_at = entry.updated_at
                else:
                    session.add(entry)

            logger.debug(f"Cache entry saved to DB: {key}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Failed to save cache entry to DB: {e}")
            return False

    def load(self, key: str) -> Optional[dict]:
        metadata = self._parse_key(key)
        try:
            with self.db.session_scope() as session:
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
                    return None

                now = datetime.now(timezone.utc)
                if entry.expires_at < now:
                    logger.info(f"Cache entry expired: {key}")
                    return None

                data = json.loads(entry.response_json)
                data["_cache_meta"] = {
                    "cached_at": entry.cached_at.isoformat(),
                    "expires_at": entry.expires_at.isoformat(),
                    "image_paths": json.loads(entry.image_paths) if entry.image_paths else [],
                    "cache_status": "hit",
                }
                return data
        except SQLAlchemyError as e:
            logger.error(f"Failed to load cache entry from DB: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse cached response JSON: {e}")
            return None

    def delete(self, key: str) -> bool:
        metadata = self._parse_key(key)
        try:
            with self.db.session_scope() as session:
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
            logger.info(f"Cache entry deleted: {key}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Failed to delete cache entry: {e}")
            return False

    def exists(self, key: str) -> bool:
        return self.load(key) is not None

    def invalidate_by_endpoint(self, endpoint: str) -> int:
        try:
            with self.db.session_scope() as session:
                deleted = (
                    session.query(CacheEntry)
                    .filter_by(endpoint=endpoint)
                    .delete(synchronize_session=False)
                )
            logger.info(f"Invalidated {deleted} cache entries for endpoint: {endpoint}")
            return deleted
        except SQLAlchemyError as e:
            logger.error(f"Failed to invalidate endpoint cache: {e}")
            return 0

    def invalidate_by_resource(self, endpoint: str, resource_id: str) -> int:
        try:
            with self.db.session_scope() as session:
                deleted = (
                    session.query(CacheEntry)
                    .filter_by(endpoint=endpoint, resource_id=resource_id)
                    .delete(synchronize_session=False)
                )
            logger.info(f"Invalidated {deleted} entries for {endpoint}/{resource_id}")
            return deleted
        except SQLAlchemyError as e:
            logger.error(f"Failed to invalidate resource cache: {e}")
            return 0

    def get_stats(self) -> dict:
        try:
            with self.db.session_scope() as session:
                total = session.query(CacheEntry).count()
                expired = (
                    session.query(CacheEntry)
                    .filter(CacheEntry.expires_at < datetime.now(timezone.utc))
                    .count()
                )
                valid = total - expired
                return {"total_entries": total, "valid_entries": valid, "expired_entries": expired}
        except SQLAlchemyError as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {"total_entries": 0, "valid_entries": 0, "expired_entries": 0}

    def _parse_key(self, key: str) -> dict:
        parts = key.split("::")
        return {
            "endpoint": parts[0] if len(parts) > 0 else "",
            "resource_id": parts[1] if len(parts) > 1 else "",
            "params_hash": parts[2] if len(parts) > 2 else "",
        }
