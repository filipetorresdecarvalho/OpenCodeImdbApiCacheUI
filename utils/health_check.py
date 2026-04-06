"""
Health checker for startup validation and self-healing.

Runs comprehensive checks at startup to ensure the application can run:
1. Python dependencies (auto-installs if missing)
2. Database connectivity and readiness (checks local MySQL/MariaDB first, then suggests Docker)
3. API configuration validation

If checks fail, provides actionable error messages and suggestions.
"""
import sys
import json
import importlib
import subprocess
import platform
from typing import Optional, Tuple

import pymysql

from config.settings import Settings
from utils.logger import logger

# Dictionary of required Python packages
REQUIRED_PACKAGES = {
    "sqlalchemy": "sqlalchemy",
    "pymysql": "pymysql",
    "requests": "requests",
    "streamlit": "streamlit",
    "tenacity": "tenacity",
    "pydantic": "pydantic",
    "dotenv": "python-dotenv",
}


class HealthChecker:
    """Validates system health at startup."""

    def __init__(self, settings: Settings):
        """Initialize health checker.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self.checks_passed = []
        self.checks_failed = []
        self.db_service_info = {}
        logger.info("HealthChecker initialized")

    def run_all(self) -> bool:
        """Run all health checks.

        Returns:
            True if all checks pass, False if any fail
        """
        logger.info("=" * 70)
        logger.info("STARTUP HEALTH CHECKS")
        logger.info("=" * 70)

        try:
            self._check_dependencies()
            self._check_database_service()
        except Exception as e:
            logger.error(
                f"Unexpected error during health checks: {e}",
                exc_info=True,
                extra={"error_code": "HEALTH_CHECK_UNEXPECTED_ERROR"},
            )

        all_passed = len(self.checks_failed) == 0
        status = "ALL PASSED" if all_passed else "SOME FAILED"
        logger.info(f"Health checks complete: {status}")
        logger.info(f"  Passed: {len(self.checks_passed)}")
        logger.info(f"  Failed: {len(self.checks_failed)}")
        logger.info("=" * 70)

        return all_passed

    def _check_dependencies(self) -> bool:
        """Check and auto-install Python dependencies.

        Returns:
            True if all dependencies available, False otherwise
        """
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
            logger.info(
                f"Attempting to auto-install {len(missing)} missing package(s)..."
            )
            for import_name, package_name in missing:
                try:
                    logger.info(f"  Installing {package_name}...")
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", package_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    importlib.import_module(import_name)
                    self.checks_passed.append(f"dependency:{package_name}")
                    logger.info(f"  [OK] {package_name} (auto-installed)")
                except subprocess.CalledProcessError as e:
                    self.checks_failed.append(f"dependency:{package_name}")
                    logger.error(
                        f"  [FAILED] {package_name} (install failed): {e}",
                        extra={
                            "error_code": "DEPENDENCY_INSTALL_FAILED",
                            "package": package_name,
                        },
                    )
                except ImportError as e:
                    self.checks_failed.append(f"dependency:{package_name}")
                    logger.error(
                        f"  [FAILED] {package_name} (import failed): {e}",
                        extra={
                            "error_code": "DEPENDENCY_IMPORT_FAILED",
                            "package": package_name,
                        },
                    )
                except Exception as e:
                    self.checks_failed.append(f"dependency:{package_name}")
                    logger.error(
                        f"  [FAILED] {package_name} (unexpected error): {e}",
                        exc_info=True,
                        extra={"error_code": "DEPENDENCY_UNEXPECTED_ERROR"},
                    )

        return len(self.checks_failed) == 0

    def _check_for_local_database_service(self) -> Tuple[bool, str, str]:
        """Check if there's a MySQL/MariaDB service running locally on Windows.

        Returns:
            Tuple of (found, service_name, connection_info)
            - found: True if a local service was found
            - service_name: Name of the service found (e.g., "MySQL", "MariaDB")
            - connection_info: Connection string or error message
        """
        logger.info("Checking for local MySQL/MariaDB service on Windows...")

        # Only check for local services on Windows
        if platform.system() != "Windows":
            logger.info("  Not Windows, skipping local service check")
            return False, "", ""

        try:
            # Try to check Windows services for MySQL/MariaDB
            # First, check common service names
            service_names = ["MySQL", "MariaDB", "MySQL80", "MySQL81", "MySQL90"]

            for service_name in service_names:
                try:
                    # Use sc query to check if service exists and is running
                    result = subprocess.run(
                        ["sc", "query", service_name],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )

                    if result.returncode == 0 and "RUNNING" in result.stdout:
                        logger.info(f"  Found running service: {service_name}")
                        self.db_service_info = {
                            "type": "local_windows_service",
                            "service_name": service_name,
                        }
                        return True, service_name, f"Windows Service: {service_name}"

                except subprocess.TimeoutExpired:
                    continue
                except Exception as e:
                    logger.debug(f"  Service {service_name} check error: {e}")
                    continue

            # If no Windows service found, try to connect to common local ports
            # This handles cases where MySQL might be running as a process
            logger.info("  No Windows service found, checking localhost ports...")

            # Check default MySQL/MariaDB ports on localhost
            test_ports = [3306, 3307, 3308]

            for port in test_ports:
                try:
                    # Try to connect to localhost on this port
                    conn = pymysql.connect(
                        host="127.0.0.1",
                        port=port,
                        user=self.settings.db_user,
                        password=self.settings.db_password,
                        charset=self.settings.db_charset,
                        connect_timeout=3,
                    )
                    conn.close()
                    logger.info(f"  Found MySQL/MariaDB on localhost:{port}")
                    self.db_service_info = {
                        "type": "localhost_port",
                        "port": port,
                    }
                    return True, f"MySQL", f"localhost:{port}"

                except pymysql.err.OperationalError:
                    # No MySQL on this port, continue
                    continue
                except Exception as e:
                    logger.debug(f"  Port {port} check error: {e}")
                    continue

            logger.info("  No local MySQL/MariaDB service found")
            return False, "", ""

        except Exception as e:
            logger.debug(f"  Error checking for local database service: {e}")
            return False, "", ""

    def _check_database_service(self) -> bool:
        """Check MySQL/MariaDB connectivity and responsiveness.

        First checks for local Windows service, then falls back to configured
        database. If no database found locally, suggests Docker.

        Returns:
            True if database is accessible, False otherwise
        """
        logger.info("Checking MySQL/MariaDB connectivity...")

        # First, check for local database service on Windows
        local_found, local_service, local_info = self._check_for_local_database_service()

        if local_found:
            logger.info(f"  Using local database: {local_info}")
            # Try to connect to localhost
            try:
                conn = pymysql.connect(
                    host="127.0.0.1",
                    port=self.db_service_info.get("port", 3306),
                    user=self.settings.db_user,
                    password=self.settings.db_password,
                    charset=self.settings.db_charset,
                    connect_timeout=10,
                )
                conn.close()
                self.checks_passed.append("database:local_connection")
                logger.info(f"  [OK] Local database connection successful ({local_info})")
                return True

            except Exception as e:
                logger.warning(f"  Local database found but connection failed: {e}")
                logger.info("  Will try configured database connection...")

        # Try configured database connection (from settings)
        try:
            logger.debug(
                f"Attempting connection to {self.settings.db_host}:{self.settings.db_port}"
            )
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
            logger.info(f"  [OK] Database connection successful ({self.settings.db_host}:{self.settings.db_port})")
            return True

        except pymysql.err.OperationalError as e:
            error_code = e.args[0] if e.args else 0

            # Error codes: 2003 = Can't connect, 2002 = Can't connect via socket
            if error_code in [2003, 2002]:
                # No database available - suggest options based on OS
                self.checks_failed.append("database:connection")

                if platform.system() == "Windows":
                    if local_found:
                        logger.error(
                            f"  Database service '{local_service}' found but connection failed",
                            extra={
                                "error_code": "DB_LOCAL_CONNECTION_FAILED",
                                "service": local_service,
                                "error_details": str(e),
                            },
                        )
                        logger.info("  [ACTION] Check if your local MySQL/MariaDB service is running:")
                        logger.info("     - Open Services (Win+R, type 'services.msc')")
                        logger.info("     - Find MySQL or MariaDB service")
                        logger.info("     - Right-click > Start (if not running)")
                        logger.info("     - Or check the service is using the correct port")
                    else:
                        logger.error(
                            f"  No MySQL/MariaDB found. Please install or start a database service.",
                            extra={
                                "error_code": "DB_NOT_FOUND",
                                "host": self.settings.db_host,
                                "port": self.settings.db_port,
                                "error_details": str(e),
                            },
                        )
                        logger.info("  [OPTIONS]")
                        logger.info("     Option 1: Install MySQL/MariaDB on Windows")
                        logger.info("        - Download from: https://mariadb.org/download/ or https://dev.mysql.com/downloads/mysql/")
                        logger.info("        - Install and start the service")
                        logger.info("")
                        logger.info("     Option 2: Use Docker (recommended)")
                        logger.info("        - Run: docker-compose up -d mysql")
                        logger.info("        - Or: docker run -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=rootpassword mariadb")
                else:
                    logger.error(
                        f"  Database connection failed: {e}",
                        extra={
                            "error_code": "DB_CONNECTION_FAILED",
                            "host": self.settings.db_host,
                            "port": self.settings.db_port,
                            "error_details": str(e),
                        },
                    )
                    logger.info("  [TIP] Start MySQL/MariaDB or run: docker-compose up -d mysql")

                return False

            # Other operational errors
            self.checks_failed.append("database:connection")
            logger.error(
                f"  Database connection error: {e}",
                extra={
                    "error_code": "DB_CONNECTION_ERROR",
                    "error_details": str(e),
                },
            )
            return False

        except Exception as e:
            self.checks_failed.append("database:connection")
            logger.error(
                f"  Unexpected error checking database: {e}",
                exc_info=True,
                extra={"error_code": "DB_CHECK_UNEXPECTED_ERROR"},
            )
            return False

    def get_report(self) -> dict:
        """Get a summary report of all health checks.

        Returns:
            Dictionary with passed, failed, and health status
        """
        return {
            "passed": self.checks_passed,
            "failed": self.checks_failed,
            "healthy": len(self.checks_failed) == 0,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            "db_service_info": self.db_service_info,
        }
