"""Connection pooling for concurrent request handling.

The default LiftHaul runtime uses ONE shared DB connection guarded by a global
lock (server._DB_LOCK): correct, but it serializes every transaction — only one
request touches the database at a time. That is the throughput ceiling.

When pooling is enabled, each request checks out its OWN connection, so
transactions run concurrently. This is the right-sized fix for handling many
simultaneous bookings / provider registrations across the national marketplace
(Luzon / Visayas / Mindanao) — combined with running several stateless app
instances behind the host's load balancer against one managed PostgreSQL.

Backends:
  * PostgreSQL  -> psycopg2 ThreadedConnectionPool (true concurrent transactions).
  * file SQLite -> one connection per worker thread (WAL; SQLite still serializes
                   writers, so this proves correctness/isolation locally rather
                   than write-throughput — real scaling is the Postgres path).
  * in-memory SQLite -> CANNOT be pooled (each connection is a separate database);
                   pooling is refused so the caller keeps the single shared conn.

Enablement (`should_pool`):
  * LIFTHAUL_DB_POOL=1/true/on  -> force on (refused for in-memory sqlite)
  * LIFTHAUL_DB_POOL=0/false    -> force off
  * unset                       -> auto-on for a postgres DATABASE_URL, else off
"""
from __future__ import annotations

import os
import threading


def _env_url():
    return os.environ.get("DATABASE_URL")


def _is_memory(url) -> bool:
    return (not url) or url in (":memory:", "sqlite://:memory:", "sqlite::memory:")


def _is_postgres(url) -> bool:
    return bool(url and url.startswith(("postgres://", "postgresql://")))


def should_pool(url=None) -> bool:
    url = url if url is not None else _env_url()
    flag = os.environ.get("LIFTHAUL_DB_POOL")
    if flag is not None:
        on = flag.strip().lower() in ("1", "true", "yes", "on")
        return bool(on and not _is_memory(url))
    # DEFAULT OFF — even on Postgres. Pooling removes the global DB lock and runs
    # transactions concurrently; the serialized single-connection default is
    # structurally free of double-booking/double-assignment races. Enabling pooling
    # (LIFTHAUL_DB_POOL=1) is a deliberate scale-time step that REQUIRES the atomic
    # single-winner guards tracked in docs/go_live/PERFORMANCE_AND_RELIABILITY_PLAN.md
    # (payments are already idempotent and safe either way). Safe launch default wins.
    return False


class _PgPool:
    def __init__(self, url, minconn, maxconn):
        import psycopg2.pool
        import dbconn
        self._dbconn = dbconn
        self._pool = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, dsn=url)
        self.kind = "postgres"
        self.capacity = maxconn      # max concurrent checkouts (bounds in-flight requests)

    def checkout(self):
        raw = self._pool.getconn()
        raw.autocommit = False
        return self._dbconn.PgConnection(raw)

    def release(self, conn, commit=True):
        try:
            conn.commit() if commit else conn.rollback()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        try:
            self._pool.putconn(conn._raw)
        except Exception:
            pass

    def dispose(self):
        try:
            self._pool.closeall()
        except Exception:
            pass


class _SqliteThreadPool:
    """One file-SQLite connection per worker thread, reused across that thread's
    requests (WAL + busy_timeout for concurrent readers)."""

    def __init__(self, path):
        self._path = path
        self._local = threading.local()
        self._all = []
        self._lock = threading.Lock()
        self.kind = "sqlite-file"
        # SQLite serializes writers; bound in-flight requests so a spike sheds load
        # gracefully instead of piling up thousands of contending threads.
        self.capacity = int(os.environ.get("LIFTHAUL_MAX_INFLIGHT", "16") or "16")

    def checkout(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            import db
            c = db._sqlite(self._path)
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=5000")
            except Exception:
                pass
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    def release(self, conn, commit=True):
        try:
            conn.commit() if commit else conn.rollback()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        # keep the per-thread connection open for reuse

    def dispose(self):
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except Exception:
                    pass
            self._all.clear()


def build(url=None, minconn=1, maxconn=None):
    """Construct a pool for the given DATABASE_URL. Caller should have checked
    should_pool() first."""
    url = url if url is not None else _env_url()
    if _is_postgres(url):
        maxconn = maxconn or int(os.environ.get("LIFTHAUL_DB_POOL_MAX", "10") or "10")
        return _PgPool(url, minconn, max(minconn, maxconn))
    path = url or "rgo_os.sqlite"
    for pre in ("sqlite:///", "sqlite://"):
        if path.startswith(pre):
            path = path[len(pre):]
    return _SqliteThreadPool(path)


class ConnProxy:
    """Module-global stand-in for the request connection. Under pooling it resolves
    every attribute (execute/cursor/commit/rollback/…) to the connection bound to
    the CURRENT thread for the current request; with no binding it falls back to the
    single shared connection. Handlers keep writing `_conn.execute(...)` unchanged."""

    def __init__(self, fallback):
        self._fallback = fallback
        self._ctx = threading.local()

    def _bind(self, conn):
        self._ctx.conn = conn

    def _unbind(self):
        self._ctx.conn = None

    def _resolve(self):
        return getattr(self._ctx, "conn", None) or self._fallback

    def __getattr__(self, name):
        # only called for attrs not found normally -> forward to the resolved conn
        return getattr(self._resolve(), name)
