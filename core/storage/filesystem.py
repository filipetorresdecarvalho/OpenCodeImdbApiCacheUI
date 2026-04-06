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
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = Path(settings.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _generate_path(self, key: str) -> Path:
        parts = key.split("::")
        endpoint = parts[0] if len(parts) > 0 else "unknown"
        resource_id = parts[1] if len(parts) > 1 else "unknown"
        params_hash = parts[2] if len(parts) > 2 else "default"

        safe_endpoint = self._sanitize(endpoint)
        safe_resource = self._sanitize(resource_id)

        dir_path = self.cache_dir / safe_endpoint / safe_resource
        dir_path.mkdir(parents=True, exist_ok=True)

        return dir_path / f"{params_hash}.json"

    @staticmethod
    def _sanitize(name: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)

    def save(self, key: str, data: Any, metadata: Optional[dict] = None) -> bool:
        try:
            file_path = self._generate_path(key)
            payload = {
                "data": data,
                "metadata": metadata or {},
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug(f"Cache entry saved to FS: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save cache to FS: {e}")
            return False

    def load(self, key: str) -> Optional[Any]:
        try:
            file_path = self._generate_path(key)
            if not file_path.exists():
                return None

            payload = json.loads(file_path.read_text(encoding="utf-8"))

            expires_at = payload.get("metadata", {}).get("expires_at")
            if expires_at:
                exp_dt = datetime.fromisoformat(expires_at)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp_dt:
                    logger.info(f"FS cache entry expired: {key}")
                    return None

            data = payload["data"]
            if isinstance(data, dict):
                data["_cache_meta"] = {
                    "cache_status": "hit",
                    "storage": "filesystem",
                    "cached_at": payload.get("cached_at"),
                }
            return data
        except Exception as e:
            logger.error(f"Failed to load cache from FS: {e}")
            return None

    def delete(self, key: str) -> bool:
        try:
            file_path = self._generate_path(key)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"FS cache entry deleted: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete FS cache: {e}")
            return False

    def exists(self, key: str) -> bool:
        return self.load(key) is not None

    def save_image(self, endpoint: str, resource_id: str, image_url: str, image_data: bytes) -> Optional[str]:
        try:
            safe_endpoint = self._sanitize(endpoint)
            safe_resource = self._sanitize(resource_id)
            url_hash = hashlib.md5(image_url.encode()).hexdigest()[:16]

            ext = "jpg"
            if image_url.endswith(".png"):
                ext = "png"
            elif image_url.endswith(".webp"):
                ext = "webp"

            dir_path = self.cache_dir / safe_endpoint / safe_resource / "images"
            dir_path.mkdir(parents=True, exist_ok=True)

            file_path = dir_path / f"{url_hash}.{ext}"
            file_path.write_bytes(image_data)

            rel_path = str(file_path.relative_to(self.cache_dir))
            logger.debug(f"Image saved to FS: {rel_path}")
            return rel_path
        except Exception as e:
            logger.error(f"Failed to save image to FS: {e}")
            return None

    def cleanup_orphaned(self, known_keys: set) -> int:
        deleted = 0
        for json_file in self.cache_dir.rglob("*.json"):
            rel = str(json_file.relative_to(self.cache_dir))
            key_parts = rel.replace("\\", "/").replace(".json", "").split("/")
            if len(key_parts) >= 3:
                reconstructed = f"{key_parts[0]}::{key_parts[1]}::{key_parts[2]}"
                if reconstructed not in known_keys:
                    try:
                        json_file.unlink()
                        deleted += 1
                    except Exception:
                        pass
        logger.info(f"Cleaned up {deleted} orphaned FS cache files")
        return deleted
