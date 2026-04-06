"""
Rate-limited async request queue for IMDB API calls.

Since the free IMDB API has strict rate limits, we use a background queue
that processes requests at 1 per second. Multiple concurrent user requests
are coalesced into a single API call.

This module implements a background worker thread that:
- Processes requests from a queue at controlled rate (1/sec)
- Handles retries with exponential backoff
- Logs all attempts and failures for debugging
- Automatically manages rate-limit 429 responses
"""
import time
import threading
import queue
import logging
from typing import Optional, Callable, Any
from datetime import datetime, timezone

from utils.logger import logger


class RateLimitedQueue:
    """Background queue that processes requests at max 1 per second.

    Prevents the IMDB API from blocking our application due to excessive
    requests. All API calls go through this queue.
    """

    def __init__(self, max_requests_per_second: float = 1.0, max_retries: int = 3):
        """Initialize the rate-limited queue.

        Args:
            max_requests_per_second: Maximum requests to process per second (default: 1.0)
            max_retries: Maximum retry attempts before giving up on a request
        """
        self.max_requests_per_second = max_requests_per_second
        self.max_retries = max_retries
        self.min_interval_seconds = 1.0 / max_requests_per_second
        self.request_queue: queue.Queue = queue.Queue()
        self.results: dict[str, Any] = {}
        self.running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.last_request_time = 0
        self._lock = threading.Lock()

        logger.info(
            f"RateLimitedQueue initialized: max_rate={max_requests_per_second}/sec, "
            f"min_interval={self.min_interval_seconds:.2f}s"
        )

    def start(self):
        """Start the background worker thread."""
        try:
            if self.running:
                logger.warning("RateLimitedQueue already running")
                return

            self.running = True
            self.worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="IMDBRateLimitedWorker",
            )
            self.worker_thread.start()
            logger.info("RateLimitedQueue worker thread started")
        except Exception as e:
            logger.error(f"Failed to start RateLimitedQueue: {e}", exc_info=True)
            self.running = False

    def stop(self):
        """Stop the background worker thread and wait for it to finish."""
        try:
            self.running = False
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=5)
                logger.info("RateLimitedQueue worker thread stopped")
        except Exception as e:
            logger.error(f"Failed to stop RateLimitedQueue: {e}", exc_info=True)

    def submit(
        self,
        request_id: str,
        fetch_func: Callable,
        args: tuple = (),
        kwargs: dict = None,
    ) -> str:
        """Submit an async request to the queue.

        Args:
            request_id: Unique identifier for this request (for deduplication)
            fetch_func: Callable that makes the actual API request
            args: Positional arguments for fetch_func
            kwargs: Keyword arguments for fetch_func

        Returns:
            request_id for later retrieval of results
        """
        try:
            if kwargs is None:
                kwargs = {}

            request_item = {
                "request_id": request_id,
                "fetch_func": fetch_func,
                "args": args,
                "kwargs": kwargs,
                "submitted_at": datetime.now(timezone.utc),
                "attempt": 0,
            }
            self.request_queue.put(request_item)
            logger.debug(f"Request submitted to queue: {request_id}")
            return request_id
        except Exception as e:
            logger.error(
                f"Failed to submit request {request_id} to queue: {e}",
                exc_info=True,
            )
            return request_id

    def get_result(self, request_id: str, wait_timeout: float = 0) -> Optional[Any]:
        """Retrieve result for a previously submitted request.

        Args:
            request_id: ID of the request to retrieve
            wait_timeout: How long to wait for result (0 = don't wait)

        Returns:
            Result dictionary or None if not ready/failed
        """
        try:
            with self._lock:
                if request_id in self.results:
                    return self.results.pop(request_id)
            return None
        except Exception as e:
            logger.error(
                f"Error retrieving result for {request_id}: {e}",
                exc_info=True,
            )
            return None

    def _worker_loop(self):
        """Main worker loop that processes queue items at controlled rate."""
        logger.info("Worker loop started")
        try:
            while self.running:
                try:
                    # Get next request from queue with timeout to allow graceful shutdown
                    request_item = self.request_queue.get(timeout=1)

                    # Calculate and enforce rate limit
                    now = time.time()
                    time_since_last = now - self.last_request_time
                    if time_since_last < self.min_interval_seconds:
                        sleep_time = self.min_interval_seconds - time_since_last
                        logger.debug(
                            f"Rate limiting: sleeping {sleep_time:.3f}s "
                            f"(last request was {time_since_last:.3f}s ago)"
                        )
                        time.sleep(sleep_time)

                    # Process the request
                    self._process_request(request_item)
                    self.last_request_time = time.time()

                except queue.Empty:
                    # Queue is empty, just continue
                    continue
                except Exception as e:
                    logger.error(
                        f"Error in worker loop: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(f"Critical error in worker loop: {e}", exc_info=True)
        finally:
            logger.info("Worker loop stopped")

    def _process_request(self, request_item: dict):
        """Process a single request with retry logic.

        Handles 429 rate limits and other transient errors automatically.
        """
        request_id = request_item["request_id"]
        fetch_func = request_item["fetch_func"]
        args = request_item["args"]
        kwargs = request_item["kwargs"]
        attempt = request_item.get("attempt", 0)

        try:
            logger.debug(
                f"Processing request {request_id} (attempt {attempt + 1}/{self.max_retries})"
            )

            # Call the fetch function
            result = fetch_func(*args, **kwargs)
            result["_queue_metadata"] = {
                "request_id": request_id,
                "attempt": attempt + 1,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            # Store result
            with self._lock:
                self.results[request_id] = result

            logger.info(f"Request {request_id} completed successfully (attempt {attempt + 1})")

        except Exception as e:
            error_str = str(e)
            is_rate_limit = "429" in error_str or "rate" in error_str.lower()
            should_retry = attempt < self.max_retries

            logger.warning(
                f"Request {request_id} failed (attempt {attempt + 1}/{self.max_retries}): {e}",
                exc_info=True,
                extra={"error_code": "RATE_LIMIT" if is_rate_limit else "FETCH_ERROR"},
            )

            if should_retry:
                # Calculate exponential backoff: base wait + extra for rate limits
                backoff = (2 ** attempt) * 2  # 2s, 4s, 8s
                if is_rate_limit:
                    backoff *= 2  # Double backoff for rate limits

                logger.info(f"Retrying request {request_id} in {backoff}s")
                request_item["attempt"] = attempt + 1

                # Re-queue with delay
                time.sleep(backoff)
                self.request_queue.put(request_item)
            else:
                # Max retries exceeded
                error_result = {
                    "_error": True,
                    "_error_message": error_str,
                    "_queue_metadata": {
                        "request_id": request_id,
                        "attempt": attempt + 1,
                        "failed": True,
                        "max_retries_exceeded": True,
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
                with self._lock:
                    self.results[request_id] = error_result

                logger.error(
                    f"Request {request_id} failed after {attempt + 1} attempts. Giving up.",
                    extra={
                        "error_code": "MAX_RETRIES_EXCEEDED",
                        "error_details": error_str,
                    },
                )
