"""SQLAlchemy models and DB session for the meetup bot.

Three tables: employees, cafes, ratings.
Connection string is read from the DATABASE_URL environment variable.
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check your .env file.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Employee(Base):
    __tablename__ = "employees"

    slack_user_id = Column(String, primary_key=True)
    display_name = Column(String)
    zip_code = Column(String, nullable=True)
    address = Column(String, nullable=True)
    lat = Column(Float)
    lng = Column(Float)
    neighborhood = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Cafe(Base):
    __tablename__ = "cafes"

    place_id = Column(String, primary_key=True)
    name = Column(String)
    address = Column(String)
    lat = Column(Float)
    lng = Column(Float)
    google_rating = Column(Float, nullable=True)
    total_votes = Column(Integer, default=0)
    avg_internal_rating = Column(Float, nullable=True)


class Rating(Base):
    __tablename__ = "ratings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slack_user_id = Column(String)
    place_id = Column(String)
    score = Column(Integer)  # 1–5
    comment = Column(Text, nullable=True)
    rated_at = Column(DateTime, default=datetime.utcnow)


def create_tables() -> None:
    """Create all tables defined on Base. Safe to call repeatedly."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI-style dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
