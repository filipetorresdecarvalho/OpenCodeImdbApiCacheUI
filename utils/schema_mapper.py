import json
from pathlib import Path
from typing import Optional

from utils.logger import logger


class EndpointRegistry:
    def __init__(self, config_path: str = "config/api_endpoints.json"):
        self.config_path = Path(config_path)
        self._endpoints: dict = {}
        self._load_config()

    def _load_config(self):
        if not self.config_path.exists():
            logger.error(f"Endpoint config not found: {self.config_path}")
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            self._endpoints = config.get("endpoints", {})
            logger.info(f"Loaded {len(self._endpoints)} endpoint definitions")
        except Exception as e:
            logger.error(f"Failed to load endpoint config: {e}")

    def get_endpoint(self, name: str) -> Optional[dict]:
        return self._endpoints.get(name)

    def get_table_name(self, endpoint_name: str) -> Optional[str]:
        ep = self._endpoints.get(endpoint_name)
        return ep.get("table_name") if ep else None

    def get_schema_hints(self, endpoint_name: str) -> Optional[dict]:
        ep = self._endpoints.get(endpoint_name)
        return ep.get("schema_hints") if ep else None

    def get_ttl(self, endpoint_name: str) -> Optional[int]:
        ep = self._endpoints.get(endpoint_name)
        return ep.get("ttl_override") if ep else None

    def should_cache_images(self, endpoint_name: str) -> bool:
        ep = self._endpoints.get(endpoint_name)
        return ep.get("cache_images", False) if ep else False

    def list_endpoints(self) -> list[dict]:
        result = []
        for name, config in self._endpoints.items():
            result.append({
                "name": name,
                "path": config.get("path", ""),
                "table_name": config.get("table_name", ""),
                "cache_response": config.get("cache_response", True),
                "cache_images": config.get("cache_images", False),
            })
        return result

    def match_by_path(self, path: str) -> Optional[str]:
        for name, config in self._endpoints.items():
            template = config.get("path", "")
            if self._paths_match(template, path):
                return name
        return None

    @staticmethod
    def _paths_match(template: str, actual: str) -> bool:
        template_parts = template.strip("/").split("/")
        actual_parts = actual.strip("/").split("/")
        if len(template_parts) != len(actual_parts):
            return False
        for t, a in zip(template_parts, actual_parts):
            if t.startswith("{") and t.endswith("}"):
                continue
            if t != a:
                return False
        return True
