"""state.db (DESIGN.md #5). Generalized: per-endpoint columns live in ep_map
rather than fixed apple_*/en_* columns, so test doubles and future endpoints
share one code path. Connection is opened per cycle and checkpoint-closed so
the iCloud replica of ~/Documents/NoteSync stays consistent between cycles.
"""
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes(
  uuid        TEXT PRIMARY KEY,
  mapping     TEXT NOT NULL,
  title       TEXT,
  rel_path    TEXT,
  base_hash   TEXT,
  base_commit TEXT,
  status      TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS ep_map(
  uuid      TEXT NOT NULL,
  ep        TEXT NOT NULL,
  native_id TEXT NOT NULL,
  base_hash TEXT,
  seen_rev  TEXT,
  PRIMARY KEY (uuid, ep)
);
CREATE UNIQUE INDEX IF NOT EXISTS ep_native ON ep_map(ep, native_id);
CREATE TABLE IF NOT EXISTS oplog(
  ts REAL, uuid TEXT, action TEXT, detail TEXT
);
"""


class DB:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(_SCHEMA)

    # -- notes ------------------------------------------------------------
    def get_note(self, uid):
        return self.con.execute("SELECT * FROM notes WHERE uuid=?", (uid,)).fetchone()

    def uuids_for_mapping(self, mapping):
        return [r["uuid"] for r in self.con.execute(
            "SELECT uuid FROM notes WHERE mapping=?", (mapping,))]

    def upsert_note(self, uid, mapping, title, rel_path, base_hash, base_commit):
        self.con.execute(
            """INSERT INTO notes(uuid, mapping, title, rel_path, base_hash, base_commit, status)
               VALUES(?,?,?,?,?,?,'active')
               ON CONFLICT(uuid) DO UPDATE SET mapping=excluded.mapping,
                 title=excluded.title, rel_path=excluded.rel_path,
                 base_hash=excluded.base_hash, base_commit=excluded.base_commit,
                 status='active'""",
            (uid, mapping, title, rel_path, base_hash, base_commit))
        self.con.commit()

    def set_status(self, uid, status):
        self.con.execute("UPDATE notes SET status=? WHERE uuid=?", (status, uid))
        self.con.commit()

    def stamp_commit(self, uuids, sha):
        self.con.executemany("UPDATE notes SET base_commit=? WHERE uuid=?",
                             [(sha, u) for u in uuids])
        self.con.commit()

    def tombstone(self, uid):
        self.con.execute("UPDATE notes SET status='tombstone' WHERE uuid=?", (uid,))
        self.con.execute("DELETE FROM ep_map WHERE uuid=?", (uid,))
        self.con.commit()

    # -- endpoint mapping -------------------------------------------------
    def uuid_for_native(self, ep, native_id):
        r = self.con.execute("SELECT uuid FROM ep_map WHERE ep=? AND native_id=?",
                             (ep, native_id)).fetchone()
        return r["uuid"] if r else None

    def ep_rows(self, uid):
        return {r["ep"]: r for r in self.con.execute(
            "SELECT * FROM ep_map WHERE uuid=?", (uid,))}

    def claim_orphan(self, mapping, ep, title, base_hash):
        """An endpoint note with no mapping row: before minting a new uuid,
        try to claim an existing same-title same-content note that lacks a
        mapping for this endpoint (e.g. after an interrupted create)."""
        r = self.con.execute(
            """SELECT n.uuid FROM notes n
               WHERE n.mapping=? AND n.title=? AND n.base_hash=?
                 AND n.status='active'
                 AND NOT EXISTS (SELECT 1 FROM ep_map e
                                 WHERE e.uuid=n.uuid AND e.ep=?)
               LIMIT 1""", (mapping, title, base_hash, ep)).fetchone()
        return r["uuid"] if r else None

    def map_native(self, uid, ep, native_id, base_hash, seen_rev=None):
        self.con.execute(
            """INSERT INTO ep_map(uuid, ep, native_id, base_hash, seen_rev)
               VALUES(?,?,?,?,?)
               ON CONFLICT(uuid, ep) DO UPDATE SET native_id=excluded.native_id,
                 base_hash=excluded.base_hash, seen_rev=excluded.seen_rev""",
            (uid, ep, native_id, base_hash, seen_rev))
        self.con.commit()

    # -- misc -------------------------------------------------------------
    def oplog(self, uid, action, detail=""):
        self.con.execute("INSERT INTO oplog VALUES(?,?,?,?)",
                         (time.time(), uid, action, detail))
        self.con.commit()

    def close(self):
        self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.con.close()
