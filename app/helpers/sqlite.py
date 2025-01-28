import asyncio
import logging
import time

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, Boolean, Column, DateTime, Integer, Text
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
        self.engine = create_engine(config.sqlite.uri, echo=False)

        # apply sqlite concurrency tuning
        with self.engine.connect() as conn:
            conn.connection.execute(
                "PRAGMA journal_mode=WAL;"
            )  # enable Write-Ahead Logging
            conn.connection.execute(
                "PRAGMA synchronous=NORMAL;"
            )  # reduce sync overhead
            conn.connection.execute(
                "PRAGMA cache_size=-16000;"
            )  # set cache size (negative for KB)
            conn.connection.execute(
                "PRAGMA temp_store=MEMORY;"
            )  # use memory for temporary tables
            conn.connection.execute(
                "PRAGMA locking_mode=NORMAL;"
            )  # avoid exclusive locking

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
        start = time.time()
        buffers = self.inserts.copy()
        counts = len(buffers)

        if counts > 0:
            for buffer in buffers:
                self.session.add(buffer)
                self.inserts.pop(0)

            self.session.commit()
            logging.debug(
                f"flushing {counts} inserts in {time.time()- start:.3f} seconds."
            )

        # updates ...
        start = time.time()
        buffers = self.updates.copy()
        counts = len(buffers)

        if counts > 0:
            for buffer in buffers:
                self.parse(buffer)
                self.session.commit()

                self.updates.remove(buffer)

            logging.debug(
                f"flushing {counts} updates in {time.time()- start:.3f} seconds."
            )

    def insert(self, data):
        self.inserts.append(data)

    async def listen(self):
        logging.debug("listener is up and running.")

        while self.running:
            self.flush()
            await asyncio.sleep(60)  # run every minute

    async def purge(self):
        dt = datetime.now(tz=timezone.utc) + timedelta(days=self.retention)
        logging.debug(f"purge logs older than {dt} ...")

        counts = 0
        with self.session as s:
            rows = s.query(Log).filter(Log.updated_on > dt).all()
            if rows:
                counts = len(counts)
                await s.delete(rows)
                await s.commit()

        logging.debug(f"... done, {counts} purged!")

    def update(self, data):
        self.updates.append(data)

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
