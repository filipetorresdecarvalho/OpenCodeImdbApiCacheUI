"""
Enhanced logger with JSON error tracking and structured logging.

All log messages are written to both console (for visibility) and files:
- app.log: all logs in JSON format with request IDs and latency
- errors.json: only errors and warnings, structured for analysis

The logger is thread-safe and supports rotating file handlers to prevent
log files from growing indefinitely.
"""
import sys
import json
import logging
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


class JsonFormatter(logging.Formatter):
    """Custom formatter that converts log records to JSON.

    Includes timestamps, log level, module info, exception traces, and
    custom fields like request_id, cache_status, latency_ms.
    """

    def format(self, record):
        """Convert a log record to a JSON string."""
        try:
            log_obj = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }

            # Include exception traceback if present
            if record.exc_info and record.exc_info[0] is not None:
                log_obj["exception"] = self.formatException(record.exc_info)

            # Include custom fields from logger.extra
            if hasattr(record, "request_id"):
                log_obj["request_id"] = record.request_id
            if hasattr(record, "cache_status"):
                log_obj["cache_status"] = record.cache_status
            if hasattr(record, "latency_ms"):
                log_obj["latency_ms"] = record.latency_ms
            if hasattr(record, "error_code"):
                log_obj["error_code"] = record.error_code
            if hasattr(record, "error_details"):
                log_obj["error_details"] = record.error_details

            return json.dumps(log_obj, ensure_ascii=False, default=str)
        except Exception as e:
            # Fallback if JSON serialization fails
            return f'{{"timestamp": "{datetime.utcnow().isoformat()}", "error": "failed to serialize log", "message": "{str(e)}"}}'


class ErrorFileHandler(RotatingFileHandler):
    """Custom file handler that only writes ERROR and WARNING level logs.

    Helps with error debugging by keeping a separate error log file.
    """

    def emit(self, record):
        """Only emit ERROR and WARNING records."""
        try:
            if record.levelno >= logging.WARNING:
                super().emit(record)
        except Exception:
            self.handleError(record)


def setup_logger(
    name: str = "imdb_cache",
    log_file: str = "logs/app.log",
    error_file: str = "logs/errors.json",
    level: str = "INFO",
    json_format: bool = True,
) -> logging.Logger:
    """Initialize and configure the application logger.

    Args:
        name: Logger name/module name
        log_file: Path to main log file (all messages)
        error_file: Path to error log file (WARNING and ERROR only)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Whether to use JSON format (True) or plain text (False)

    Returns:
        Configured logger instance ready for use
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers to avoid duplicates
    logger.handlers.clear()

    try:
        # Create log directories
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(error_file).parent.mkdir(parents=True, exist_ok=True)

        # Main log file handler (all levels)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=10,  # Keep 10 rotated files
        )

        # Error log file handler (WARNING and ERROR only)
        error_handler = ErrorFileHandler(
            error_file,
            maxBytes=20 * 1024 * 1024,  # 20MB
            backupCount=5,  # Keep 5 rotated error files
        )

        # Console handler for immediate visibility
        console_handler = logging.StreamHandler(sys.stdout)

        # Choose formatter based on json_format flag
        if json_format:
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        # Apply formatter to all handlers
        file_handler.setFormatter(formatter)
        error_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Add handlers to logger
        logger.addHandler(file_handler)
        logger.addHandler(error_handler)
        logger.addHandler(console_handler)

        logger.info(f"Logger initialized: {name} (level={level}, json={json_format})")

    except Exception as e:
        print(f"ERROR: Failed to setup logger: {e}", file=sys.stderr)
        # Fallback: at least setup console logging
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")
        )
        logger.addHandler(console_handler)

    return logger


# Global logger instance used throughout the app
logger = setup_logger()
