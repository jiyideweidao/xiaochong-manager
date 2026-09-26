import sqlite3
import threading
import time

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  ext TEXT DEFAULT '',
  orig_name TEXT DEFAULT '',
  source_type TEXT NOT NULL,
  source_path TEXT NOT NULL,
  inner_path TEXT DEFAULT '',
  origin TEXT DEFAULT '',
  folder TEXT DEFAULT '',
  group_key TEXT DEFAULT '',
  cover_key TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  mtime REAL DEFAULT 0,
  category TEXT DEFAULT '',
  style TEXT DEFAULT '',
  tags TEXT DEFAULT '',
  favorite INTEGER DEFAULT 0,
  fav_at REAL DEFAULT 0,
  render_id INTEGER DEFAULT 0,
  thumb_key TEXT DEFAULT '',
  thumb_status TEXT DEFAULT 'pending',
  thumb_msg TEXT DEFAULT '',
  added_at REAL DEFAULT 0,
  UNIQUE(source_path, inner_path)
);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_cat ON assets(category);
CREATE INDEX IF NOT EXISTS idx_assets_style ON assets(style);
CREATE INDEX IF NOT EXISTS idx_assets_group ON assets(group_key);
CREATE INDEX IF NOT EXISTS idx_assets_cover ON assets(cover_key);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(thumb_status);
CREATE INDEX IF NOT EXISTS idx_assets_fav ON assets(favorite);
CREATE INDEX IF NOT EXISTS idx_assets_src ON assets(source_path);

CREATE TABLE IF NOT EXISTS roots (
  path TEXT PRIMARY KEY,
  enabled INTEGER DEFAULT 1,
  added_at REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS archive_meta (
  path TEXT PRIMARY KEY,
  mtime REAL,
  size INTEGER,
  listed_at REAL
);

CREATE TABLE IF NOT EXISTS rename_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch TEXT,
  ts REAL,
  scope TEXT,
  container TEXT,
  old_name TEXT,
  new_name TEXT,
  ok INTEGER,
  msg TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  title TEXT,
  status TEXT,
  total INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0,
  msg TEXT DEFAULT '',
  started_at REAL,
  ended_at REAL
);
"""


# 给老库补新列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加字段
_MIGRATIONS = (("assets", "fav_at", "REAL DEFAULT 0"),)


def _migrate(c) -> None:
    """给老版本的库补上新加的列 / 索引（补完照样能用，不会丢数据）。"""
    for table, col, decl in _MIGRATIONS:
        cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % table)]
        if col not in cols:
            c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
    # 索引必须等列补齐之后再建，否则老库会报 "no such column: fav_at"
    c.execute("CREATE INDEX IF NOT EXISTS idx_assets_fav_at ON assets(fav_at)")
    c.commit()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        config.ensure_dirs()
        c = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        _migrate(c)
        c.commit()
        _local.c = c
    return c


def q(sql, args=(), one=False):
    cur = conn().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def ex(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur


def exmany(sql, rows):
    c = conn()
    cur = c.executemany(sql, rows)
    c.commit()
    cur.close()


def count(sql, args=()) -> int:
    r = q(sql, args, one=True)
    return int(r[0]) if r else 0


def now() -> float:
    return time.time()