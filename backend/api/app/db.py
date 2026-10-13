from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def init_pool(dsn: str) -> None:
    global _pool
    _pool = ConnectionPool(
        dsn,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=True,
    )
    _pool.wait(timeout=15)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_conn():
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    with _pool.connection() as conn:
        yield conn