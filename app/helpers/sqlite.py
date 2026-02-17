import asyncio
import logging
import time

from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, delete, Boolean, DateTime, Integer, Text
from sqlalchemy.orm import (
    declarative_base,
    mapped_column,
    sessionmaker,
    Mapped,
    Session,
)
from sqlalchemy.pool import StaticPool


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config


# ################################################################################
# models


Base: Any = declarative_base()


class AdsBlockDomain(Base):
    __tablename__ = "adsblock_domains"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    domain: Mapped[str] = mapped_column(Text, index=True)
    type: Mapped[str] = mapped_column(Text, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)

    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
    )
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
        onupdate=lambda: datetime.now(tz=timezone.utc),
    )


class AdsBlockList(Base):
    __tablename__ = "adsblock_lists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    contents: Mapped[str] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text)

    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
    )
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
        onupdate=lambda: datetime.now(tz=timezone.utc),
    )


class Log(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    module: Mapped[str] = mapped_column(Text, index=True)
    key: Mapped[str] = mapped_column(Text, index=True)
    value: Mapped[str] = mapped_column(Text)

    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
    )
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
        onupdate=lambda: datetime.now(tz=timezone.utc),
    )


class Setting(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    key: Mapped[str] = mapped_column(Text, unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)

    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(tz=timezone.utc)
    )
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(tz=timezone.utc),
        onupdate=lambda: datetime.now(tz=timezone.utc),
    )


# ################################################################################
# sqlite wrapper class


class SQLite:
    def __init__(self, config: "Config"):
        self.engine = create_engine(
            config.sqlite.uri,
            echo=config.sqlite.echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,  # thread safe connection sharing
            pool_pre_ping=True,
        )
        self.Session = sessionmaker(bind=self.engine)
        self.lock = asyncio.Lock()

        # apply sqlite concurrency tuning
        pragmas = {
            "journal_mode": "WAL",  # enable Write-Ahead Logging
            "synchronous": "NORMAL",  # reduce sync overhead
            "cache_size": -64000,  # set cache size (negative for KB)
            "temp_store": "MEMORY",  # use memory for temporary tables
            "locking_mode": "NORMAL",  # avoid exclusive locking
            "busy_timeout": 30000,  # avoid database locked
            "mmap_size": 268435456,  #  memory map the database file
            "foreign_keys": "ON",  # use foreign keys
            "wal_autocheckpoint": 1000,
            "journal_size_limit": 67108864,
            "analysis_limit": 400,
        }

        with self.engine.connect() as conn:
            for key, value in pragmas.items():
                conn.exec_driver_sql(f"PRAGMA {key}={value};")

            conn.exec_driver_sql("VACUUM")  # optimize the database

        Base.metadata.create_all(self.engine)

        # configs
        self.retention = config.logging.retention
        self.running = True

        # caches
        self.inserts = deque()
        self.updates = deque()

    def flushB(self):
        tic = time.time()
        inserts = list(self.inserts)
        updates = list(self.updates)

        if not inserts and not updates:
            return

        session = self.Session()
        try:
            for obj in inserts:
                session.add(obj)

            self.inserts.clear()

            for obj in updates:
                self.parse(session, obj)

            self.updates.clear()
            session.commit()

            with self.engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.exec_driver_sql("PRAGMA optimize")

            logging.debug(
                f"flushed {len(inserts)} inserts, {len(updates)} updates,"
                f" in {time.time() - tic:.3f} seconds."
            )

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")
            session.rollback()

        finally:
            session.close()

    def insert(self, data: Any):
        self.inserts.append(data)

    def parse(self, session: Session, data: Any):
        now = datetime.now(tz=timezone.utc)
        row = None

        if isinstance(data, Setting):
            row = session.query(Setting).filter_by(key=data.key).first()
            if not row:
                row = Setting(key=data.key, created_on=now)
                session.add(row)

            row.value = data.value
            row.updated_on = now

        elif isinstance(data, AdsBlockDomain):
            row = (
                session.query(AdsBlockDomain)
                .filter_by(domain=data.domain, type=data.type)
                .first()
            )
            if not row:
                row = AdsBlockDomain(domain=data.domain, type=data.type, created_on=now)
                session.add(row)

            row.count = (row.count or 0) + 1
            row.updated_on = now

        elif isinstance(data, AdsBlockList):
            row = session.query(AdsBlockList).filter_by(url=data.url).first()
            if not row:
                row = AdsBlockList(url=data.url, is_active=True, created_on=now)
                session.add(row)

            if data.status == "success":
                row.contents = data.contents
                row.count = data.count

            row.status = data.status
            row.updated_on = now

    def purge(self):
        session = self.Session()
        try:
            tables = [
                {"table": Log, "name": "logs", "retention": self.retention},
                {
                    "table": AdsBlockDomain,
                    "name": "blocked domain logs",
                    "retention": 99,
                },
            ]

            for table in tables:
                threshold = datetime.now(tz=timezone.utc) - timedelta(
                    days=table["retention"]
                )
                count = (
                    session.query(table["table"])
                    .filter(table["table"].updated_on < threshold)
                    .count()
                )

                if count > 0:
                    logging.debug(
                        f"purge {table['name']} earlier than {threshold.strftime('%Y-%m-%d %H:%M:%S')} ..."
                    )

                    dele = delete(table["table"]).where(
                        table["table"].updated_on < threshold
                    )
                    session.execute(dele)
                    session.commit()
                    logging.debug(f"... done, {count} purged!")

        finally:
            session.close()

    def update(self, data: Any):
        self.updates.append(data)

    async def close(self):
        self.running = False
        await self.flush()
        logging.info("listener is shutting down!")

    async def flush(self):
        async with self.lock:
            self.flushB()

    async def listen(self):
        logging.info("listener is up and running.")

        while self.running:
            await self.flush()

            # await asyncio.sleep(30)
            try:
                await asyncio.wait_for(asyncio.Event().wait(), timeout=30)
            except asyncio.TimeoutError:
                pass


# ################################################################################
# sqlite logging handler


class SQLiteHandler(logging.Handler):
    def __init__(self, sqlite: SQLite):
        super().__init__()
        self.sqlite = sqlite

    def close(self):
        self.sqlite.flushB()
        self.sqlite.engine.dispose()
        super().close()

    def emit(self, record: logging.LogRecord):
        now = datetime.fromtimestamp(record.created, timezone.utc)
        try:
            row = Log(
                module=record.module,
                key=record.levelname.lower(),
                value=record.getMessage(),
                created_on=now,
                updated_on=now,
            )
            self.sqlite.insert(row)

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")
