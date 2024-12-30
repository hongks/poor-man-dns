from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, Text
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class AdsBlockDomain(Base):
    __tablename__ = "adsblock_domains"
    id = Column(Integer, primary_key=True)

    domain = Column(Text, unique=True)
    type = Column(Text, index=True)
    count = Column(Integer)

    created_on = Column(DateTime, default=datetime.utcnow())
    updated_on = Column(DateTime, default=datetime.utcnow())


class AdsBlockList(Base):
    __tablename__ = "adsblock_lists"
    id = Column(Integer, primary_key=True)

    url = Column(Text, unique=True)
    is_active = Column(Boolean, index=True)
    contents = Column(Text)
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
