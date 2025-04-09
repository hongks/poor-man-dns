import asyncio
import logging
import time

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, delete, Boolean, Column, DateTime, Integer, Text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker


Base = declarative_base()


class AdsBlockDomain(Base):
    __tablename__ = "adsblock_domains"
    id = Column(Integer, primary_key=True, autoincrement=True)

    domain = Column(Text, index=True)
    type = Column(Text, index=True)
    count = Column(Integer)

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
    is_active = Column(Boolean, index=True)
    contents = Column(Text)
    count = Column(Integer)

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
                conn.connection.execute(f"PRAGMA {key}={value};")

            conn.connection.execute("VACUUM")  # optimize the database

        Session = scoped_session(sessionmaker(bind=self.engine))
        self.session = Session()

        Base.metadata.create_all(self.engine)

        self.lock = asyncio.Lock()
        self.retention = config.logging.retention
        self.running = True

        # caches
        self.inserts = []
        self.updates = []

    async def close(self):
        self.running = False
        self.flush()
        self.engine.dispose()
        logging.debug("listener is shutting down!")

    def flush(self):
        # quick hack to optimize pure inserts
        tic = time.time()
        if self.inserts:
            buffers = self.inserts.copy()

            for buffer in buffers:
                self.session.add(buffer)
                self.inserts.pop(0)

            self.session.commit()
            logging.debug(
                f"flushing {len(buffers)} inserts in {time.time() - tic:.3f} seconds."
            )

        # updates ...
        tic = time.time()
        if self.updates:
            buffers = self.updates.copy()

            for buffer in buffers:
                self.parse(buffer)
                self.session.commit()
                self.updates.remove(buffer)

            logging.debug(
                f"flushing {len(buffers)} updates in {time.time() - tic:.3f} seconds."
            )

        # use a NEW, raw connection for checkpoint
        # with self.engine.raw_connection() as raw_conn:
        #     cursor = raw_conn.cursor()
        #     cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        #     cursor.close()
        #     raw_conn.commit()

        with self.engine.begin() as conn:
            conn.connection.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    def insert(self, data):
        self.inserts.append(data)

    async def listen(self):
        logging.debug("listener is up and running.")

        while self.running:
            self.flush()
            await asyncio.sleep(60)  # run every minute

    def parse(self, data):
        dt = datetime.now(tz=timezone.utc)
        row = None

        if isinstance(data, Setting):
            row = self.session.query(Setting).filter_by(key=data.key).first()
            if row:
                row.value = data.value
                row.updated_on = dt

            else:
                row = Setting(
                    key=data.key,
                    value=data.value,
                    created_on=dt,
                    updated_on=dt,
                )
                self.session.add(row)

        elif isinstance(data, AdsBlockDomain):
            row = (
                self.session.query(AdsBlockDomain)
                .filter_by(domain=data.domain, type=data.type)
                .first()
            )
            if row:
                row.count += 1
                row.updated_on = dt

            else:
                row = AdsBlockDomain(
                    domain=data.domain,
                    type=data.type,
                    count=1,
                    created_on=dt,
                    updated_on=dt,
                )
                self.session.add(row)

        elif isinstance(data, AdsBlockList):
            row = self.session.query(AdsBlockList).filter_by(url=data.url).first()
            if row:
                row.contents = data.contents
                row.count = data.count
                row.updated_on = dt

            else:
                row = AdsBlockList(
                    url=data.url,
                    is_active=True,
                    contents=data.contents,
                    count=data.count,
                    created_on=dt,
                    updated_on=dt,
                )
                self.session.add(row)

    def purge(self):
        dt = datetime.now(tz=timezone.utc) - timedelta(days=self.retention)
        count = self.session.query(Log).filter(Log.updated_on < dt).count()

        if count > 0:
            logging.debug(
                f"purge logs earlier than {dt.strftime('%Y-%m-%d %H:%M:%S')} ..."
            )

            dele = delete(Log).where(Log.updated_on < dt)
            self.session.execute(dele)
            self.session.commit()

            logging.debug(f"... done, {count} purged!")

    def update(self, data):
        self.updates.append(data)


class SQLiteHandler(logging.Handler):
    def __init__(self, sqlite):
        super().__init__()

        self.sqlite = sqlite

    def emit(self, message):
        dt = datetime.fromtimestamp(message.created, timezone.utc)

        row = Log(
            module=message.module,
            key=message.levelname.lower(),
            value=message.getMessage(),
            created_on=dt,
            updated_on=dt,
        )

        self.sqlite.insert(row)
