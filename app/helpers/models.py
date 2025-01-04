from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, Text
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class AdsBlockDomain(Base):
    __tablename__ = "adsblock_domains"
    id = Column(Integer, primary_key=True, autoincrement=True)

    domain = Column(Text, unique=True, index=True)
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


class AdsBlockLog(Base):
    __tablename__ = "adblock_logs"
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
