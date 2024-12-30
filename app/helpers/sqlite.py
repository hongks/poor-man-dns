import threading

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from .models import Base, AdsBlockDomain, AdsBlockList, AdsBlockLog, Setting


class SQLite:
    def __init__(self, uri):
        engine = create_engine(uri)

        # apply sqlite concurrency tuning
        with engine.connect() as conn:
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

        Session = scoped_session(sessionmaker(bind=engine))
        self.session = Session()

        Base.metadata.create_all(engine)
        self.running = True

    def serve_forever(self):
        session = self.Session()
        log_buffer = []

        while self.running:
            try:
                action, data = self.command_queue.get(timeout=1)
                if action == 'write':
                    table_name, log_data = data
                    table = self._get_table(table_name)
                    log_buffer.append((table, log_data))
                    if len(log_buffer) >= self.batch_size:
                        self._batch_write(session, log_buffer)
                        log_buffer.clear()
                elif action == 'read':
                    table_name, query, params, callback = data
                    result = session.execute(query, params).fetchall()
                    callback(result)
            except queue.Empty:
                continue  # Timeout reached, continue processing

        if log_buffer:
            self._batch_write(session, log_buffer)
        session.close()

    def shutdown(self):
        self.running = False

    def update(self, key, value):
        row = self.session.query(Setting).filter_by(key=key).first()
        dt = datetime.utcnow()

        if row:
            row.value = value
            row.updated_on = dt

        else:
            row = Setting(
                key=key,
                value=value,
                created_on=dt,
                updated_on=dt,
            )
            self.session.add(row)

        self.session.commit()
