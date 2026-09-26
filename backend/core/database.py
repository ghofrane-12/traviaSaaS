from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import asyncpg
from core.config import settings

_db_url = settings.DATABASE_URL

if "?host=/cloudsql/" in _db_url:
    base = _db_url.replace("postgresql+asyncpg://", "")
    credentials, rest = base.split("@/")
    db_name, socket_part = rest.split("?host=")
    socket_dir = socket_part  

    _sync_url = (
        f"postgresql+psycopg2://{credentials}@/{db_name}"
        f"?host={socket_dir}"
    )
else:
    _sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

engine = create_engine(_sync_url, pool_size=5, max_overflow=10, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = settings.DATABASE_URL

        if "?host=/cloudsql/" in dsn:
            base = dsn.replace("postgresql+asyncpg://", "")
            credentials, rest = base.split("@/")
            db_name, socket_path = rest.split("?host=")
            user, password = credentials.split(":", 1)

            _pool = await asyncpg.create_pool(
                host=socket_path,        
                user=user,
                password=password,
                database=db_name,
                min_size=2,
                max_size=10,
                command_timeout=60,
                timeout=60,
            )
        else:
            dsn_clean = dsn.replace("postgresql+asyncpg://", "postgresql://")
            _pool = await asyncpg.create_pool(
                dsn_clean,
                min_size=2,
                max_size=10,
                command_timeout=60,
                timeout=60,
            )
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None