from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


base = declarative_base()


class AdsBlockList(base):
    __tablename__ = "adsblock_lists"

    id = Column(Integer, primary_key=True)
    url = Column(Text, unique=True)
    is_active = Column(Boolean, index=True)
    contents = Column(Text)
    count = Column(Integer)
    created_on = Column(DateTime)
    updated_on = Column(DateTime)


class AdsBlockDomain(base):
    __tablename__ = "adsblock_domains"

    id = Column(Integer, primary_key=True)
    domain = Column(Text, unique=True)
    type = Column(Text, index=True)
    count = Column(Integer)
    created_on = Column(DateTime)
    updated_on = Column(DateTime)


class AdsBlockLog(base):
    __tablename__ = "adblock_logs"

    id = Column(Integer, primary_key=True)
    domain = Column(Text)
    type = Column(Text)
    created_on = Column(DateTime)
    updated_on = Column(DateTime)


class Setting(base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(Text)
    value = Column(Text)
    created_on = Column(DateTime)
    updated_on = Column(DateTime)


class SQLite:
    def session(self, configs):
        engine = create_engine(configs.sqlite_uri)
        base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)
        session = Session()

        # in case configs file is different
        row = session.query(Setting).filter_by(key="config_sha256").first()
        dt = datetime.utcnow()
        sha256 = configs.load()

        if row and sha256:
            if sha256 != row.value:
                row.value = sha256
                row.updated_on = dt

        else:
            row = Setting(
                key="config_sha256",
                value=sha256,
                created_on=dt,
                updated_on=dt,
            )
            session.add(row)

        session.commit()
        return session
