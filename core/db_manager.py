"""
Database management with auto-recovery and comprehensive error handling.

This module handles all database operations:
- Creating engine and session factories
- Auto-creating database if it doesn't exist
- Auto-creating tables from ORM models
- Providing thread-safe session context manager
- Comprehensive error logging and recovery

Uses SQLAlchemy ORM for type safety and query building.
"""
import time
import logging
import platform
from contextlib import contextmanager
from typing import Optional, Generator, Tuple

from sqlalchemy import create_engine, text, inspect, event
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from config.settings import Settings
from utils.logger import logger

# Base class for all ORM models
Base = declarative_base()


class DatabaseManager:
    """Manages database connections, migrations, and session lifecycle."""

    def __init__(self, settings: Settings):
        """Initialize database manager.

        Args:
            settings: Application settings with database configuration
        """
        self.settings = settings
        self.engine = None
        self.SessionLocal = None
        self._initialized = False
        self._local_db_detected = False
        self._local_db_connection_info = None
        logger.debug("DatabaseManager initialized")

    def detect_local_database(self) -> Tuple[bool, str, str]:
        """Detect if there's a local MySQL/MariaDB service running on Windows.

        Returns:
            Tuple of (found, service_name, connection_info)
        """
        if platform.system() != "Windows":
            logger.debug("Not Windows, skipping local database detection")
            return False, "", ""

        logger.info("Detecting local MySQL/MariaDB service...")

        # Check Windows services
        service_names = ["MySQL", "MariaDB", "MySQL80", "MySQL81", "MySQL90"]

        for service_name in service_names:
            try:
                result = __import__('subprocess').run(
                    ["sc", "query", service_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and "RUNNING" in result.stdout:
                    logger.info(f"Found local database service: {service_name}")
                    self._local_db_detected = True
                    self._local_db_connection_info = ("127.0.0.1", 3306)
                    return True, service_name, "127.0.0.1:3306"
            except Exception:
                continue

        # Check localhost ports
        import pymysql
        for port in [3306, 3307, 3308]:
            try:
                conn = pymysql.connect(
                    host="127.0.0.1",
                    port=port,
                    user=self.settings.db_user,
                    password=self.settings.db_password,
                    connect_timeout=3,
                )
                conn.close()
                logger.info(f"Found local database on port: {port}")
                self._local_db_detected = True
                self._local_db_connection_info = ("127.0.0.1", port)
                return True, "MySQL/MariaDB", f"127.0.0.1:{port}"
            except Exception:
                continue

        logger.info("No local database detected")
        return False, "", ""

    def ensure_database_exists(self) -> bool:
        """Create the database if it doesn't exist.

        Connects to MySQL/MariaDB without specifying a database, then
        executes CREATE DATABASE IF NOT EXISTS.

        Returns:
            True if successful, False otherwise
        """
        logger.info("Checking if database exists...")
        temp_engine = None
        try:
            # Connect without database to create it
            temp_engine = create_engine(
                self.settings.db_url_no_db,
                pool_pre_ping=True,
                echo=False,
            )

            with temp_engine.connect() as conn:
                conn.execute(text("COMMIT"))
                create_sql = (
                    f"CREATE DATABASE IF NOT EXISTS `{self.settings.db_name}` "
                    f"CHARACTER SET {self.settings.db_charset} "
                    f"COLLATE {self.settings.db_charset}_unicode_ci"
                )
                logger.debug(f"Executing: {create_sql}")
                conn.execute(text(create_sql))
                conn.commit()

            logger.info(f"Database '{self.settings.db_name}' ensured to exist")
            return True

        except OperationalError as e:
            logger.error(
                f"Failed to ensure database exists: {e}",
                exc_info=True,
                extra={
                    "error_code": "DB_CREATE_FAILED",
                    "error_details": str(e),
                },
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error ensuring database exists: {e}",
                exc_info=True,
                extra={"error_code": "DB_UNEXPECTED_ERROR"},
            )
            return False
        finally:
            # Clean up temporary engine
            if temp_engine:
                try:
                    temp_engine.dispose()
                except Exception as e:
                    logger.warning(f"Error disposing temp engine: {e}")

    def initialize_engine(self) -> bool:
        """Initialize the main database engine and session factory.

        Returns:
            True if successful, False otherwise
        """
        logger.info("Initializing database engine...")
        try:
            self.engine = create_engine(
                self.settings.db_url,
                pool_pre_ping=True,  # Test connections before using
                pool_size=10,
                max_overflow=20,
                pool_recycle=3600,  # Recycle connections every hour
                echo=False,
            )

            # Setup connection event handlers
            @event.listens_for(self.engine, "connect")
            def receive_connect(dbapi_conn, connection_record):
                """Set connection charset to utf8mb4 on new connections."""
                try:
                    dbapi_conn.execute("SET NAMES utf8mb4")
                except Exception as e:
                    logger.warning(f"Failed to set connection charset: {e}")

            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine,
            )
            self._initialized = True
            logger.info("Database engine initialized successfully")
            return True

        except OperationalError as e:
            logger.error(
                f"Failed to initialize engine (connection error): {e}",
                exc_info=True,
                extra={"error_code": "ENGINE_INIT_CONNECTION_FAILED"},
            )
            return False
        except Exception as e:
            logger.error(
                f"Failed to initialize engine: {e}",
                exc_info=True,
                extra={"error_code": "ENGINE_INIT_FAILED"},
            )
            return False

    def test_connection(self) -> bool:
        """Test database connectivity.

        Returns:
            True if connection works, False otherwise
        """
        if not self.engine:
            logger.error("Engine not initialized, cannot test connection")
            return False

        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection test passed")
            return True
        except OperationalError as e:
            logger.error(
                f"Database connection test failed: {e}",
                exc_info=True,
                extra={"error_code": "CONNECTION_TEST_FAILED"},
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error during connection test: {e}",
                exc_info=True,
                extra={"error_code": "CONNECTION_TEST_ERROR"},
            )
            return False

    def create_tables(self) -> bool:
        """Create all ORM tables based on Base metadata.

        Returns:
            True if successful, False otherwise
        """
        if not self._initialized:
            logger.error("Engine not initialized, cannot create tables")
            return False

        try:
            logger.info("Creating ORM tables...")
            Base.metadata.create_all(bind=self.engine)
            logger.info("All ORM tables created/verified")
            return True
        except ProgrammingError as e:
            logger.error(
                f"Programming error creating tables: {e}",
                exc_info=True,
                extra={"error_code": "TABLE_CREATE_PROGRAMMING_ERROR"},
            )
            return False
        except SQLAlchemyError as e:
            logger.error(
                f"SQLAlchemy error creating tables: {e}",
                exc_info=True,
                extra={"error_code": "TABLE_CREATE_SQLALCHEMY_ERROR"},
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error creating tables: {e}",
                exc_info=True,
                extra={"error_code": "TABLE_CREATE_UNEXPECTED_ERROR"},
            )
            return False

    def create_dynamic_table(
        self,
        table_name: str,
        columns: dict[str, str],
        primary_key: str = "id",
    ) -> bool:
        """Create a table dynamically using raw SQL.

        Useful for tables not defined in ORM models.

        Args:
            table_name: Name of table to create
            columns: Dict of column_name -> column_type
            primary_key: Which column is the primary key

        Returns:
            True if successful, False otherwise
        """
        if not self._initialized:
            logger.error("Engine not initialized, cannot create dynamic table")
            return False

        try:
            logger.debug(f"Creating dynamic table: {table_name}")

            # Build column definitions
            col_defs = []
            for col_name, col_type in columns.items():
                nullable = "NULL" if col_name != primary_key else "NOT NULL"
                col_defs.append(f"`{col_name}` {col_type} {nullable}")

            # Add primary key constraint
            pk_clause = f"PRIMARY KEY (`{primary_key}`)"
            create_stmt = (
                f"CREATE TABLE IF NOT EXISTS `{table_name}` ("
                f"{', '.join(col_defs)}, "
                f"{pk_clause}"
                f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )

            logger.debug(f"SQL: {create_stmt}")

            with self.engine.connect() as conn:
                conn.execute(text(create_stmt))
                conn.commit()

            logger.info(f"Dynamic table '{table_name}' created/verified")
            return True

        except SQLAlchemyError as e:
            logger.error(
                f"Failed to create dynamic table '{table_name}': {e}",
                exc_info=True,
                extra={
                    "error_code": "DYNAMIC_TABLE_CREATE_FAILED",
                    "table_name": table_name,
                },
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error creating dynamic table '{table_name}': {e}",
                exc_info=True,
                extra={"error_code": "DYNAMIC_TABLE_CREATE_UNEXPECTED_ERROR"},
            )
            return False

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database.

        Args:
            table_name: Name of table to check

        Returns:
            True if table exists, False otherwise
        """
        try:
            if not self.engine:
                return False
            inspector = inspect(self.engine)
            exists = table_name in inspector.get_table_names()
            logger.debug(f"Table '{table_name}' exists: {exists}")
            return exists
        except Exception as e:
            logger.error(
                f"Error checking if table '{table_name}' exists: {e}",
                exc_info=True,
            )
            return False

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Context manager for database sessions.

        Usage:
            with db_manager.session_scope() as session:
                session.query(Model).all()

        Handles commit/rollback automatically.

        Yields:
            SQLAlchemy Session object

        Raises:
            RuntimeError: If SessionLocal not initialized
        """
        if not self.SessionLocal:
            raise RuntimeError(
                "Session not initialized. Call initialize_engine() first."
            )

        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(
                f"SQLAlchemy error in session (rolled back): {e}",
                exc_info=True,
                extra={"error_code": "SESSION_SQLALCHEMY_ERROR"},
            )
            raise
        except Exception as e:
            session.rollback()
            logger.error(
                f"Unexpected error in session (rolled back): {e}",
                exc_info=True,
                extra={"error_code": "SESSION_UNEXPECTED_ERROR"},
            )
            raise
        finally:
            try:
                session.close()
            except Exception as e:
                logger.warning(f"Error closing session: {e}")

    def dispose(self):
        """Close all database connections and clean up resources."""
        try:
            if self.engine:
                self.engine.dispose()
                logger.info("Database engine disposed")
        except Exception as e:
            logger.error(f"Error disposing database engine: {e}", exc_info=True)
