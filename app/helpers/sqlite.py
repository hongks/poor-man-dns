from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, Text, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker


Base = declarative_base()


class AdsBlockList(Base):
    __tablename__ = "adsblock_lists"
    id = Column(Integer, primary_key=True)

    url = Column(Text, unique=True)
    is_active = Column(Boolean, index=True)
    contents = Column(Text)
    count = Column(Integer)

    created_on = Column(DateTime, default=datetime.utcnow())
    updated_on = Column(DateTime, default=datetime.utcnow())


class AdsBlockDomain(Base):
    __tablename__ = "adsblock_domains"
    id = Column(Integer, primary_key=True)

    domain = Column(Text, unique=True)
    type = Column(Text, index=True)
    count = Column(Integer)

    created_on = Column(DateTime, default=datetime.utcnow())
    updated_on = Column(DateTime, default=datetime.utcnow())


class AdsBlockLog(Base):
    __tablename__ = "adblock_logs"
    id = Column(Integer, primary_key=True)

    module = Column(Text)
    key = Column(Text)
    value = Column(Text)

    created_on = Column(DateTime, default=datetime.utcnow())
    updated_on = Column(DateTime, default=datetime.utcnow())


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)

    key = Column(Text)
    value = Column(Text)

    created_on = Column(DateTime, default=datetime.utcnow())
    updated_on = Column(DateTime, default=datetime.utcnow())


class SQLite:
    def __init__(self, uri):
        engine = create_engine(uri)

        Session = scoped_session(sessionmaker(bind=engine))
        self.session = Session()

        Base.metadata.create_all(engine)

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
