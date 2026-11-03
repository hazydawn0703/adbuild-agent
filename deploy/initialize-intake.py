"""Create only the two new business tables using a deployment-only database DSN."""
import os
from pathlib import Path
from sqlalchemy import create_engine

engine=create_engine(os.environ['SCHEMA_DATABASE_URL'])
schema=(Path(__file__).resolve().parents[1]/'backend/schema.sql').read_text()
with engine.begin() as c:
    for name in ('intake_tasks','project_contexts'):
        exists=c.exec_driver_sql('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',(name,)).fetchone()
        if exists: continue
        statement=next(s.strip() for s in schema.split(';') if s.strip().startswith('CREATE TABLE '+name+' '))
        c.exec_driver_sql(statement)
        print('Created',name)
