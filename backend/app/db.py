from contextlib import contextmanager
import json
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .settings import settings

_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings().database_url, kwargs={"row_factory":dict_row}, min_size=1,max_size=10)
    return _pool


@contextmanager
def transaction():
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def json_value(model):
    return Jsonb(json.loads(model.model_dump_json()) if hasattr(model,"model_dump_json") else model)
