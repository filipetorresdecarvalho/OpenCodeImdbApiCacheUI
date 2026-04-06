"""
IMDB API client with comprehensive error handling and retry logic.

This module handles all communication with the free IMDB API. Key features:
- No API key required (uses free tier)
- Automatic retries with exponential backoff for transient errors
- Rate limiting via external queue (1 request/sec)
- Comprehensive error logging for debugging
- Support for different IMDB API endpoints
"""
import time
import logging
from typing import Optional

import requests
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
    """Client for making requests to the free IMDB API.

    Handles connection pooling, retry logic, error handling, and logging.
    """

    def __init__(self, settings: Settings):
        """Initialize the API client.

        Args:
            settings: Application settings object with API configuration
        """
        self.settings = settings
        self.session = requests.Session()

        # Set standard headers for IMDB API
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": f"IMDBCacheUI/{settings.app_version}",
        })

        # API key is optional for free tier
        if settings.imdb_api_key:
            self.session.headers["X-API-Key"] = settings.imdb_api_key
            logger.info("IMDB API key configured")
        else:
            logger.info("IMDB API key not set (using free tier)")

        logger.debug(f"API client initialized: {settings.imdb_api_base}")

    def _build_url(
        self,
        endpoint: str,
        resource_id: str = "",
        query: str = "",
    ) -> str:
        """Build the full URL for an API request.

        Args:
            endpoint: API endpoint path (e.g. '/titles/{id}')
            resource_id: Resource ID to substitute (e.g. 'tt0111161')
            query: Query parameter to substitute (e.g. 'The Godfather')

        Returns:
            Complete URL ready for HTTP request
        """
        try:
            base = self.settings.imdb_api_base.rstrip("/")
            # Replace placeholders in endpoint
            path = endpoint.format(id=resource_id, query=query)
            full_url = f"{base}{path}"
            return full_url
        except Exception as e:
            logger.error(
                f"Failed to build URL for {endpoint}: {e}",
                exc_info=True,
                extra={"error_code": "URL_BUILD_ERROR"},
            )
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((
            requests.exceptions.RequestException,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        )),
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
        """Fetch data from IMDB API with automatic retries.

        This method implements exponential backoff retry logic via tenacity.
        It handles rate limiting, timeouts, and connection errors automatically.

        Args:
            endpoint: API endpoint path
            resource_id: IMDB resource ID (e.g. actor or title ID)
            query: Search query (for search endpoints)
            params: Additional query parameters

        Returns:
            Parsed JSON response from API

        Raises:
            requests.exceptions.RequestException: On permanent failures (429, 503, etc.)
            requests.exceptions.Timeout: On request timeout
        """
        url = self._build_url(endpoint, resource_id, query)
        start = time.time()

        try:
            logger.debug(f"Fetching from IMDB API: {url}")
            response = self.session.get(url, params=params, timeout=30)
            elapsed_ms = (time.time() - start) * 1000

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "unknown")
                logger.warning(
                    f"Rate limited by IMDB API. Retry-After: {retry_after}",
                    extra={
                        "error_code": "RATE_LIMIT_429",
                        "error_details": f"Retry-After: {retry_after}",
                    },
                )
                # Raise to trigger retry logic
                raise requests.exceptions.HTTPError(
                    f"Rate limited (429). Retry-After: {retry_after}",
                    response=response,
                )

            # Handle 503 Service Unavailable
            if response.status_code == 503:
                logger.warning(
                    f"IMDB API service unavailable (503): {url}",
                    extra={"error_code": "SERVICE_UNAVAILABLE_503"},
                )
                raise requests.exceptions.HTTPError(
                    "Service Unavailable (503)",
                    response=response,
                )

            # Raise for other HTTP errors (4xx, 5xx)
            response.raise_for_status()

            # Parse JSON response
            data = response.json()

            logger.info(
                f"IMDB API response received: {url} "
                f"({elapsed_ms:.0f}ms, {len(str(data))} bytes)",
                extra={
                    "latency_ms": elapsed_ms,
                    "status_code": response.status_code,
                },
            )
            return data

        except requests.exceptions.HTTPError as e:
            elapsed_ms = (time.time() - start) * 1000
            if e.response is not None and e.response.status_code == 404:
                logger.info(f"Resource not found: {url}")
                return {"error": "not_found", "url": url}
            logger.error(
                f"HTTP error fetching {url}: {e} ({elapsed_ms:.0f}ms)",
                exc_info=True,
                extra={
                    "error_code": f"HTTP_{e.response.status_code if e.response else 'UNKNOWN'}",
                    "latency_ms": elapsed_ms,
                },
            )
            raise

        except requests.exceptions.Timeout as e:
            elapsed_ms = (time.time() - start) * 1000
            logger.error(
                f"Timeout fetching {url} ({elapsed_ms:.0f}ms): {e}",
                exc_info=True,
                extra={
                    "error_code": "REQUEST_TIMEOUT",
                    "latency_ms": elapsed_ms,
                },
            )
            raise

        except requests.exceptions.ConnectionError as e:
            logger.error(
                f"Connection error fetching {url}: {e}",
                exc_info=True,
                extra={"error_code": "CONNECTION_ERROR"},
            )
            raise

        except ValueError as e:
            logger.error(
                f"Invalid JSON response from {url}: {e}",
                exc_info=True,
                extra={"error_code": "INVALID_JSON"},
            )
            raise

        except Exception as e:
            logger.error(
                f"Unexpected error fetching {url}: {e}",
                exc_info=True,
                extra={"error_code": "UNEXPECTED_ERROR"},
            )
            raise

    def close(self):
        """Close the session and clean up resources."""
        try:
            self.session.close()
            logger.info("API client session closed")
        except Exception as e:
            logger.error(f"Error closing API client session: {e}", exc_info=True)
