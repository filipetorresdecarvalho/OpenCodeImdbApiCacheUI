import json
import importlib
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pymysql

from config.settings import Settings
from utils.logger import logger

REQUIRED_PACKAGES = {
    "sqlalchemy": "sqlalchemy",
    "pymysql": "pymysql",
    "requests": "requests",
    "streamlit": "streamlit",
    "tenacity": "tenacity",
    "pydantic": "pydantic",
}


class HealthChecker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.checks_passed = []
        self.checks_failed = []

    def run_all(self) -> bool:
        logger.info("=" * 60)
        logger.info("Running startup health checks...")
        logger.info("=" * 60)

        self._check_dependencies()
        self._check_database_service()
        self._check_api_config()

        all_passed = len(self.checks_failed) == 0
        status = "ALL PASSED" if all_passed else "SOME FAILED"
        logger.info(f"Health checks complete: {status}")
        logger.info(f"  Passed: {len(self.checks_passed)}")
        logger.info(f"  Failed: {len(self.checks_failed)}")
        return all_passed

    def _check_dependencies(self) -> bool:
        logger.info("Checking Python dependencies...")
        missing = []
        for import_name, package_name in REQUIRED_PACKAGES.items():
            try:
                importlib.import_module(import_name)
                self.checks_passed.append(f"dependency:{package_name}")
                logger.info(f"  [OK] {package_name}")
            except ImportError:
                missing.append((import_name, package_name))
                logger.warning(f"  [MISSING] {package_name}")

        if missing:
            logger.info(f"Attempting to install {len(missing)} missing package(s)...")
            for import_name, package_name in missing:
                try:
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", package_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    importlib.import_module(import_name)
                    self.checks_passed.append(f"dependency:{package_name}")
                    logger.info(f"  [INSTALLED] {package_name}")
                except Exception as e:
                    self.checks_failed.append(f"dependency:{package_name}")
                    logger.error(f"  [FAILED] Could not install {package_name}: {e}")
                    return False

        return True

    def _check_database_service(self) -> bool:
        logger.info("Checking MySQL/MariaDB connectivity...")
        try:
            conn = pymysql.connect(
                host=self.settings.db_host,
                port=self.settings.db_port,
                user=self.settings.db_user,
                password=self.settings.db_password,
                charset=self.settings.db_charset,
                connect_timeout=10,
            )
            conn.close()
            self.checks_passed.append("database:connection")
            logger.info("  [OK] Database connection successful")
            return True
        except pymysql.err.OperationalError as e:
            self.checks_failed.append("database:connection")
            logger.error(f"  [FAILED] Database connection: {e}")
            logger.info("  Tip: Start MySQL/MariaDB or run: docker-compose up -d mysql")
            return False

    def _check_api_config(self) -> bool:
        logger.info("Checking IMDB API configuration...")
        if not self.settings.imdb_api_key:
            self.checks_failed.append("api:key_missing")
            logger.warning("  [WARN] IMDB_API_KEY not set. API calls will fail.")
            logger.info("  Tip: Set IMDB_API_KEY in your .env file")
            return False
        self.checks_passed.append("api:configured")
        logger.info("  [OK] API key configured")
        return True

    def get_report(self) -> dict:
        return {
            "passed": self.checks_passed,
            "failed": self.checks_failed,
            "healthy": len(self.checks_failed) == 0,
        }
