"""SQLAlchemy ORM models."""

from sqlalchemy import Column, Integer, String

from src.backend.database import Base


class User(Base):
    """Application user, associated with a hospital."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hospital_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
