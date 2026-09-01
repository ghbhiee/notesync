"""Endpoint adapter contract + DirEndpoint (plain md folder).

Contract (used by engine.py, implemented by future Apple/Evernote adapters):
  list()                       -> iterable of native_id
  read(nid)                    -> (title, body, mtime)
  create(title, body)          -> native_id
  update(nid, title, body)     -> new native_id, or None if unchanged
  delete(nid)
  fetch(nid)                   -> (title, body, mtime) or None if truly gone.
                                  Deletion confirmation: a note missing from
                                  list() is only treated as deleted after
                                  fetch() also returns None (Evernote's search
                                  index lags behind reality; primary-key reads
                                  do not).

DirEndpoint models a note app whose note identity is its title (a rename is
delete+create) -- the worst case the engine must survive. It doubles as the
M1 test double and a generic folder endpoint.
"""
from pathlib import Path


class DirEndpoint:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self):
        return [p.name for p in sorted(self.root.glob("*.md"))]

    def read(self, nid):
        p = self.root / nid
        return Path(nid).stem, p.read_text(encoding="utf-8"), p.stat().st_mtime

    def create(self, title, body):
        nid = f"{title}.md"
        n = 2
        while (self.root / nid).exists():
            nid = f"{title} ({n}).md"
            n += 1
        (self.root / nid).write_text(body, encoding="utf-8")
        return nid

    def update(self, nid, title, body):
        p = self.root / nid
        if title != Path(nid).stem:
            new = self.create(title, body)
            p.unlink()
            return new
        p.write_text(body, encoding="utf-8")
        return None

    def delete(self, nid):
        p = self.root / nid
        if p.exists():
            p.unlink()

    def fetch(self, nid):
        return self.read(nid) if (self.root / nid).exists() else None
