import asyncio
import logging
import time

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from .models import Base, AdsBlockDomain, AdsBlockList, AdsBlockLog, Setting


class SQLite:
    def __init__(self, uri):
        self.engine = create_engine(uri)

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

        self.buffers = []
        self.lock = asyncio.Lock()
        self.running = True

    def insert(self, data):
        self.buffers.append(data)

    def flush(self):
        start = time.time()
        buffers = self.buffers.copy()

        for buffer in buffers:
            self.session.add(buffer)
            self.buffers.remove(buffer)

        self.session.commit()
        logging.debug(
            f"flushing {len(buffers)} records in {time.time()- start:.3f} seconds."
        )

    def close(self):
        self.running = False
        self.flush()
        self.engine.dispose()

    async def listen(self):
        logging.debug("listener is up and running.")

        while self.running:
            self.flush()
            await asyncio.sleep(60)

    def update(self, data):
        dt = datetime.now(tz=timezone.utc)

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
                self.insert(row)

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
                self.insert(row)

        self.session.commit()


class SQLiteHandler(logging.Handler):
    def __init__(self, sqlite):
        super().__init__()

        self.sqlite = sqlite

    def emit(self, message):
        dt = datetime.fromtimestamp(message.created, timezone.utc)

        row = AdsBlockLog(
            module=message.module,
            key=message.levelname.lower(),
            value=message.getMessage(),
            created_on=dt,
            updated_on=dt,
        )

        self.sqlite.insert(row)
