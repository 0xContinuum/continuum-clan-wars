import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

URL = os.getenv("DATABASE_URL", "sqlite:///./cyberwars.db")
if URL.startswith("postgres://"):
    URL = URL.replace("postgres://", "postgresql://", 1)

# SQLAlchemy'nin psycopg2-binary sürücüsünü kullanabilmesi için sürücü ekliyoruz
if URL.startswith("postgresql://") and "+" not in URL:
    URL = URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(URL, pool_pre_ping=True,
                       connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
