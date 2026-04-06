import time
import requests
from typing import Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config.settings import Settings
from utils.logger import logger


class ApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": f"IMDBCacheUI/{settings.app_version}",
        })
        if settings.imdb_api_key:
            self.session.headers["X-API-Key"] = settings.imdb_api_key

    def _build_url(self, endpoint: str, resource_id: str = "", query: str = "") -> str:
        base = self.settings.imdb_api_base.rstrip("/")
        path = endpoint.format(id=resource_id, query=query)
        return f"{base}{path}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=30),
        retry=retry_if_exception_type((requests.exceptions.RequestException,)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def fetch(
        self,
        endpoint: str,
        resource_id: str = "",
        query: str = "",
        params: Optional[dict] = None,
    ) -> dict:
        url = self._build_url(endpoint, resource_id, query)
        logger.info(f"Fetching from IMDB API: {url}")
        start = time.time()

        try:
            response = self.session.get(url, params=params, timeout=30)
            elapsed_ms = (time.time() - start) * 1000

            if response.status_code == 429:
                logger.warning(
                    f"Rate limited by IMDB API. Retry-After: {response.headers.get('Retry-After', 'unknown')}"
                )
                raise requests.exceptions.HTTPError(
                    f"Rate limited (429). Retry-After: {response.headers.get('Retry-After', 'unknown')}",
                    response=response,
                )

            response.raise_for_status()
            data = response.json()

            logger.info(
                f"IMDB API response received: {url} ({elapsed_ms:.0f}ms, {len(str(data))} bytes)"
            )
            return data

        except requests.exceptions.HTTPError as e:
            elapsed_ms = (time.time() - start) * 1000
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Resource not found: {url}")
                return {"error": "not_found", "url": url}
            logger.error(f"HTTP error fetching {url}: {e} ({elapsed_ms:.0f}ms)")
            raise
        except requests.exceptions.Timeout:
            elapsed_ms = (time.time() - start) * 1000
            logger.error(f"Timeout fetching {url} ({elapsed_ms:.0f}ms)")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error fetching {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching {url}: {e}")
            raise

    def close(self):
        self.session.close()
        logger.info("API client session closed")
