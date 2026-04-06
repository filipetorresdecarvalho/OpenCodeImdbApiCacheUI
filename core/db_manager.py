import time
import logging
from contextlib import contextmanager
from typing import Optional, Generator

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.exc import OperationalError, ProgrammingError

from config.settings import Settings
from utils.logger import logger

Base = declarative_base()


class DatabaseManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.SessionLocal = None
        self._initialized = False

    def ensure_database_exists(self) -> bool:
        logger.info("Checking if database exists...")
        try:
            temp_engine = create_engine(
                self.settings.db_url_no_db,
                pool_pre_ping=True,
                echo=False,
            )
            with temp_engine.connect() as conn:
                conn.execute(text("COMMIT"))
                conn.execute(
                    text(
                        f"CREATE DATABASE IF NOT EXISTS `{self.settings.db_name}` "
                        f"CHARACTER SET {self.settings.db_charset} "
                        f"COLLATE {self.settings.db_charset}_unicode_ci"
                    )
                )
                conn.commit()
            temp_engine.dispose()
            logger.info(f"Database '{self.settings.db_name}' ensured to exist")
            return True
        except OperationalError as e:
            logger.error(f"Failed to ensure database exists: {e}")
            return False

    def initialize_engine(self) -> bool:
        logger.info("Initializing database engine...")
        try:
            self.engine = create_engine(
                self.settings.db_url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
                pool_recycle=3600,
                echo=False,
            )
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine,
            )
            self._initialized = True
            logger.info("Database engine initialized")
            return True
        except OperationalError as e:
            logger.error(f"Failed to initialize engine: {e}")
            return False

    def test_connection(self) -> bool:
        if not self.engine:
            logger.error("Engine not initialized")
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection test passed")
            return True
        except OperationalError as e:
            logger.error(f"Database connection test failed: {e}")
            return False

    def create_tables(self) -> bool:
        if not self._initialized:
            logger.error("Engine not initialized, cannot create tables")
            return False
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("All ORM tables created")
            return True
        except Exception as e:
            logger.error(f"Failed to create tables: {e}")
            return False

    def create_dynamic_table(
        self,
        table_name: str,
        columns: dict[str, str],
        primary_key: str = "id",
    ) -> bool:
        if not self._initialized:
            return False
        try:
            col_defs = []
            for col_name, col_type in columns.items():
                nullable = "NULL" if col_name != primary_key else "NOT NULL"
                col_defs.append(f"`{col_name}` {col_type} {nullable}")

            pk_clause = f"PRIMARY KEY (`{primary_key}`)"
            create_stmt = (
                f"CREATE TABLE IF NOT EXISTS `{table_name}` ("
                f"{', '.join(col_defs)}, "
                f"{pk_clause}"
                f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )

            with self.engine.connect() as conn:
                conn.execute(text(create_stmt))
                conn.commit()

            logger.info(f"Dynamic table '{table_name}' created/verified")
            return True
        except Exception as e:
            logger.error(f"Failed to create dynamic table '{table_name}': {e}")
            return False

    def table_exists(self, table_name: str) -> bool:
        if not self.engine:
            return False
        inspector = inspect(self.engine)
        return table_name in inspector.get_table_names()

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        if not self.SessionLocal:
            raise RuntimeError("Session not initialized. Call initialize_engine() first.")
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self):
        if self.engine:
            self.engine.dispose()
            logger.info("Database engine disposed")
