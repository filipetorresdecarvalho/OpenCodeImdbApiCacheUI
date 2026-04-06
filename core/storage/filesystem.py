"""
Filesystem storage implementation for cache entries.

Stores JSON API responses and images on disk with:
- Hierarchical directory structure
- Automatic image download and caching
- TTL-based expiration checking
- Orphaned file cleanup

Advantages:
- Free (no database cost)
- Good for large responses and images
- Easy to browse and debug
- Works without DB

Disadvantages:
- Not queryable
- Requires manual cleanup
- Not thread-safe for concurrent writes
- Backup complexity
"""
import os
import json
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone

from core.storage.base import StorageStrategy
from config.settings import Settings
from utils.logger import logger


class FileSystemStorage(StorageStrategy):
    """Caching backend using the filesystem."""

    def __init__(self, settings: Settings):
        """Initialize filesystem storage.

        Args:
            settings: Application settings with cache_dir
        """
        self.settings = settings
        self.cache_dir = Path(settings.cache_dir)

        try:
            # Create cache directory if it doesn't exist
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Filesystem storage initialized: {self.cache_dir}")
        except Exception as e:
            logger.error(
                f"Failed to create cache directory {self.cache_dir}: {e}",
                exc_info=True,
                extra={"error_code": "CACHE_DIR_CREATE_FAILED"},
            )

    def _generate_path(self, key: str) -> Path:
        """Generate filesystem path from cache key.

        Creates directory structure:
        cache/imdbapi/{endpoint}/{resource_id}/{params_hash}.json

        Args:
            key: Cache key (format: "endpoint::resource_id::params_hash")

        Returns:
            Full path to cache file
        """
        try:
            parts = key.split("::")
            endpoint = parts[0] if len(parts) > 0 else "unknown"
            resource_id = parts[1] if len(parts) > 1 else "unknown"
            params_hash = parts[2] if len(parts) > 2 else "default"

            # Sanitize names to be filesystem-safe
            safe_endpoint = self._sanitize(endpoint)
            safe_resource = self._sanitize(resource_id)

            # Create directory path
            dir_path = self.cache_dir / safe_endpoint / safe_resource
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(
                    f"Failed to create directory {dir_path}: {e}",
                    exc_info=True,
                    extra={"error_code": "CACHE_SUBDIR_CREATE_FAILED"},
                )
                return self.cache_dir / "error.json"

            return dir_path / f"{params_hash}.json"

        except Exception as e:
            logger.error(
                f"Failed to generate path for key '{key}': {e}",
                exc_info=True,
                extra={"error_code": "PATH_GENERATION_FAILED"},
            )
            return self.cache_dir / "error.json"

    @staticmethod
    def _sanitize(name: str) -> str:
        """Sanitize string to be filesystem-safe.

        Replaces invalid characters with underscores.

        Args:
            name: String to sanitize

        Returns:
            Filesystem-safe string
        """
        return "".join(
            c if c.isalnum() or c in "-_." else "_"
            for c in str(name)
        )

    def save(
        self,
        key: str,
        data: Any,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Save cache entry to filesystem.

        Args:
            key: Cache key
            data: Data to cache
            metadata: Optional metadata

        Returns:
            True if successful, False otherwise
        """
        try:
            file_path = self._generate_path(key)

            # Wrap data with metadata
            payload = {
                "data": data,
                "metadata": metadata or {},
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }

            # Write JSON to file
            try:
                file_path.write_text(
                    json.dumps(payload, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                logger.debug(f"Cache entry saved to FS: {file_path}")
                return True

            except OSError as e:
                logger.error(
                    f"OS error writing cache file {file_path}: {e}",
                    exc_info=True,
                    extra={"error_code": "FILE_WRITE_OS_ERROR"},
                )
                return False

        except Exception as e:
            logger.error(
                f"Failed to save cache to FS: {e}",
                exc_info=True,
                extra={
                    "error_code": "FS_SAVE_FAILED",
                    "key": key,
                },
            )
            return False

    def load(self, key: str) -> Optional[Any]:
        """Load cache entry from filesystem.

        Checks expiration automatically.

        Args:
            key: Cache key

        Returns:
            Cached data if found and valid, None otherwise
        """
        try:
            file_path = self._generate_path(key)

            if not file_path.exists():
                logger.debug(f"FS cache file not found: {file_path}")
                return None

            try:
                # Read and parse JSON
                payload = json.loads(file_path.read_text(encoding="utf-8"))

            except json.JSONDecodeError as e:
                logger.error(
                    f"Failed to parse FS cache file {file_path}: {e}",
                    exc_info=True,
                    extra={"error_code": "FS_JSON_PARSE_FAILED"},
                )
                return None

            # Check expiration
            expires_at = payload.get("metadata", {}).get("expires_at")
            if expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)

                    if datetime.now(timezone.utc) > exp_dt:
                        logger.info(f"FS cache entry expired: {key}")
                        return None

                except ValueError as e:
                    logger.warning(
                        f"Failed to parse expiration time: {e}",
                        extra={"error_code": "EXPIRY_PARSE_FAILED"},
                    )

            # Extract and return data
            data = payload["data"]
            if isinstance(data, dict):
                data["_cache_meta"] = {
                    "cache_status": "hit",
                    "storage": "filesystem",
                    "cached_at": payload.get("cached_at"),
                }

            logger.debug(f"FS cache entry loaded: {key}")
            return data

        except Exception as e:
            logger.error(
                f"Failed to load cache from FS: {e}",
                exc_info=True,
                extra={
                    "error_code": "FS_LOAD_FAILED",
                    "key": key,
                },
            )
            return None

    def delete(self, key: str) -> bool:
        """Delete cache entry from filesystem.

        Args:
            key: Cache key

        Returns:
            True if successful, False otherwise
        """
        try:
            file_path = self._generate_path(key)

            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.debug(f"FS cache entry deleted: {file_path}")
                except OSError as e:
                    logger.error(
                        f"OS error deleting cache file {file_path}: {e}",
                        exc_info=True,
                        extra={"error_code": "FILE_DELETE_OS_ERROR"},
                    )
                    return False

            return True

        except Exception as e:
            logger.error(
                f"Failed to delete FS cache: {e}",
                exc_info=True,
                extra={"error_code": "FS_DELETE_FAILED"},
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
                f"Error checking FS cache existence: {e}",
                extra={"error_code": "FS_EXISTS_CHECK_FAILED"},
            )
            return False

    def save_image(
        self,
        endpoint: str,
        resource_id: str,
        image_url: str,
        image_data: bytes,
    ) -> Optional[str]:
        """Download and save an image to filesystem.

        Args:
            endpoint: Endpoint name
            resource_id: Resource ID
            image_url: URL of the image
            image_data: Binary image data

        Returns:
            Relative path to saved image, or None if failed
        """
        try:
            safe_endpoint = self._sanitize(endpoint)
            safe_resource = self._sanitize(resource_id)

            # Generate filename from URL hash
            url_hash = hashlib.md5(image_url.encode()).hexdigest()[:16]

            # Detect image format from URL
            ext = "jpg"
            if image_url.endswith(".png"):
                ext = "png"
            elif image_url.endswith(".webp"):
                ext = "webp"
            elif image_url.endswith(".gif"):
                ext = "gif"

            # Create images directory
            dir_path = self.cache_dir / safe_endpoint / safe_resource / "images"
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(
                    f"Failed to create images directory {dir_path}: {e}",
                    exc_info=True,
                    extra={"error_code": "IMAGES_DIR_CREATE_FAILED"},
                )
                return None

            # Write image file
            file_path = dir_path / f"{url_hash}.{ext}"
            try:
                file_path.write_bytes(image_data)
                rel_path = str(file_path.relative_to(self.cache_dir))
                logger.debug(f"Image saved to FS: {rel_path}")
                return rel_path

            except OSError as e:
                logger.error(
                    f"OS error writing image file {file_path}: {e}",
                    exc_info=True,
                    extra={"error_code": "IMAGE_WRITE_OS_ERROR"},
                )
                return None

        except Exception as e:
            logger.error(
                f"Failed to save image to FS: {e}",
                exc_info=True,
                extra={"error_code": "IMAGE_SAVE_FAILED"},
            )
            return None

    def cleanup_orphaned(self, known_keys: set) -> int:
        """Delete cache files that don't match any known keys.

        Useful for cleanup after invalidation.

        Args:
            known_keys: Set of valid cache keys

        Returns:
            Number of files deleted
        """
        deleted = 0

        try:
            for json_file in self.cache_dir.rglob("*.json"):
                try:
                    rel = str(json_file.relative_to(self.cache_dir))
                    key_parts = rel.replace("\\", "/").replace(".json", "").split("/")

                    if len(key_parts) >= 3:
                        reconstructed = f"{key_parts[0]}::{key_parts[1]}::{key_parts[2]}"
                        if reconstructed not in known_keys:
                            try:
                                json_file.unlink()
                                deleted += 1
                            except OSError as e:
                                logger.warning(
                                    f"Failed to delete orphaned file {json_file}: {e}"
                                )

                except Exception as e:
                    logger.debug(f"Error processing file {json_file}: {e}")

            logger.info(f"Cleaned up {deleted} orphaned FS cache files")
            return deleted

        except Exception as e:
            logger.error(
                f"Error during orphaned cleanup: {e}",
                exc_info=True,
                extra={"error_code": "ORPHANED_CLEANUP_FAILED"},
            )
            return 0
