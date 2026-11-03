from contextlib import contextmanager
import json
from sqlalchemy import create_engine
from pymysql.constants import FIELD_TYPE
from pymysql.converters import conversions
from pymysql.cursors import DictCursor
from .settings import settings

_engine = None

def engine():
    global _engine
    if _engine is None:
        decoders = conversions.copy()
        decoders[FIELD_TYPE.JSON] = json.loads
        _engine = create_engine(settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5,
            connect_args={"conv":decoders,"charset":"utf8mb4"})
    return _engine

class Connection:
    def __init__(self, raw): self.raw=raw
    def execute(self, sql, params=()):
        cursor=self.raw.cursor(DictCursor)
        cursor.execute(sql,params)
        return cursor

@contextmanager
def transaction():
    raw=engine().raw_connection()
    try:
        raw.begin()
        yield Connection(raw)
        raw.commit()
    except BaseException as error:
        raw.rollback()
        from pymysql.err import OperationalError
        if isinstance(error,OperationalError) and error.args[0] in (1205,1213):
            from fastapi import HTTPException
            raise HTTPException(409,'并发操作发生冲突，请刷新任务和草稿后重试') from None
        raise
    finally:
        raw.close()

def json_value(model):
    return model.model_dump_json() if hasattr(model,"model_dump_json") else json.dumps(model,ensure_ascii=False)
