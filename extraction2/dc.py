# -*- coding: utf-8 -*-
"""DC corpus connection: a Postgres DB with documents(document_id, url, fetched_at, html, ...).
Set DC_DSN, e.g. DC_DSN="postgresql://user:pass@host:5432/dbname". A real dc.py in corpus/ takes precedence."""
import os
import psycopg


def connect():
    dsn = os.environ.get("DC_DSN")
    if not dsn:
        raise RuntimeError("DC_DSN is not set")
    return psycopg.connect(dsn, autocommit=True)                 # read-only use: no idle open transaction
