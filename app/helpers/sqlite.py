import asyncio
import logging
import time

from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, delete, Boolean, Column, DateTime, Integer, Text
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()


class AdsBlockDomain(Base):
    __tablename__ = "adsblock_domains"
    id = Column(Integer, primary_key=True, autoincrement=True)

    domain = Column(Text, index=True)
    type = Column(Text, index=True)
    count = Column(Integer, default=0)

    created_on = Column(DateTime, default=datetime.now(tz=timezone.utc))
    updated_on = Column(
        DateTime,
        default=datetime.now(tz=timezone.utc),
        onupdate=datetime.now(tz=timezone.utc),
    )


class AdsBlockList(Base):
    __tablename__ = "adsblock_lists"
    id = Column(Integer, primary_key=True, autoincrement=True)

    url = Column(Text, unique=True, index=True)
    contents = Column(Text)
    count = Column(Integer, default=0)
    is_active = Column(Boolean)
    status = Column(Text)

    created_on = Column(DateTime, default=datetime.now(tz=timezone.utc))
    updated_on = Column(
        DateTime,
        default=datetime.now(tz=timezone.utc),
        onupdate=datetime.now(tz=timezone.utc),
    )


class Log(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, autoincrement=True)

    module = Column(Text, index=True)
    key = Column(Text, index=True)
    value = Column(Text)

    created_on = Column(DateTime, default=datetime.now(tz=timezone.utc))
    updated_on = Column(
        DateTime,
        default=datetime.now(tz=timezone.utc),
        onupdate=datetime.now(tz=timezone.utc),
    )


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, autoincrement=True)

    key = Column(Text, unique=True, index=True)
    value = Column(Text)

    created_on = Column(DateTime, default=datetime.now(tz=timezone.utc))
    updated_on = Column(
        DateTime,
        default=datetime.now(tz=timezone.utc),
        onupdate=datetime.now(tz=timezone.utc),
    )


class SQLite:
    def __init__(self, config):
        self.engine = create_engine(config.sqlite.uri, echo=config.sqlite.echo)
        self.Session = sessionmaker(bind=self.engine)
        self.lock = asyncio.Lock()

        # apply sqlite concurrency tuning
        pragmas = {
            "journal_mode": "WAL",  # enable Write-Ahead Logging
            "synchronous": "NORMAL",  # reduce sync overhead
            "cache_size": -16000,  # set cache size (negative for KB)
            "temp_store": "MEMORY",  # use memory for temporary tables
            "locking_mode": "NORMAL",  # avoid exclusive locking
        }

        with self.engine.begin() as conn:
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

            logging.debug(
                f"flushed {len(inserts)} inserts, {len(updates)} updates,"
                f" in {time.time() - tic:.3f} seconds."
            )

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")
            session.rollback()

        finally:
            session.close()

    def insert(self, data):
        self.inserts.append(data)

    def parse(self, session, data):
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
            threshold = datetime.now(tz=timezone.utc) - timedelta(days=self.retention)
            count = session.query(Log).filter(Log.updated_on < threshold).count()

            if count > 0:
                logging.debug(
                    f"purge logs earlier than {threshold.strftime('%Y-%m-%d %H:%M:%S')} ..."
                )

                dele = delete(Log).where(Log.updated_on < threshold)
                session.execute(dele)
                session.commit()
                logging.debug(f"... done, {count} purged!")

        finally:
            session.close()

    def update(self, data):
        self.updates.append(data)

    async def close(self):
        self.running = False
        await self.flush()
        logging.debug("listener is shutting down!")

    async def flush(self):
        async with self.lock:
            self.flushB()

    async def listen(self):
        logging.debug("listener is up and running.")

        while self.running:
            await self.flush()
            await asyncio.sleep(60)  # run every minute


class SQLiteHandler(logging.Handler):
    def __init__(self, sqlite):
        super().__init__()
        self.sqlite = sqlite

    def close(self):
        self.sqlite.flushB()
        self.sqlite.engine.dispose()
        super().close()

    def emit(self, message):
        now = datetime.fromtimestamp(message.created, timezone.utc)
        try:
            row = Log(
                module=message.module,
                key=message.levelname.lower(),
                value=message.getMessage(),
                created_on=now,
                updated_on=now,
            )
            self.sqlite.insert(row)

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}")
