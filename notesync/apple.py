"""Apple Notes endpoint adapter (DESIGN.md #9, M3).

Calibrated against the live Notes app (2026-08-30):
- note id (`x-coredata://...`) is stable; name == first body line.
- body is HTML; Apple's serialization jitters at the byte level (trailing
  <br> appears/disappears, entity semicolons get eaten) but is stable at the
  flattened-markdown level -- all hashing happens there.
- write-back: setting body works; multi-line <div> structure survives.
- locked notes report passwordProtected; media notes carry <img>/data: URIs.
  Both are FROZEN (body None): the engine must leave them alone.

Bridge calls are batched: one `list` per cycle plus one bulk `read` for the
notes whose modification date moved. Bodies are cached across cycles inside
a watch process.
"""
import html
import json
import re
import subprocess
from pathlib import Path

from .flatten import apple_entities, flatten

BRIDGE = Path(__file__).parent / "apple_bridge.js"
MEDIA_MARKERS = ("<img", "<object", "data:image", "<attachment", "en-media")
_TABLE_OBJECT = re.compile(r"<object>\s*<table")


def _bridge(*args):
    r = subprocess.run(["osascript", "-l", "JavaScript", str(BRIDGE), *args],
                       capture_output=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"apple bridge: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


_TT = '<div><font face="Courier"><tt>{}</tt></font></div>'


def model_store(h):
    """Offline model of how Apple Notes rewrites the HTML we write (each
    rule captured from a live probe). Used by the tests and by the offline
    audit, so both see what the flattener sees in production."""
    # <pre> -> one monospaced paragraph per line
    def pre(m):
        lines = m.group(1).split("\n")
        return "".join(_TT.format(ln if ln else "<br>") for ln in lines)
    h = re.sub(r"<pre>(.*?)</pre>", pre, h, flags=re.S)
    # inline <code> -> Courier run
    h = re.sub(r"<code>(.*?)</code>",
               r'<font face="Courier"><span style="font-size: 12px">\1</span></font>', h)
    # h1/h2 -> bold font-size spans
    h = re.sub(r"<h1>(.*?)</h1>",
               r'<div><b><span style="font-size: 24px">\1</span></b></div>', h)
    h = re.sub(r"<h2>(.*?)</h2>",
               r'<div><b><span style="font-size: 18px">\1</span></b></div>', h)
    # nbsp is returned as a plain space
    h = h.replace("&nbsp;", " ")
    # tables are wrapped as objects; the first blank paragraph after one is
    # absorbed into the wrapper's trailing <br>
    h = re.sub(r"<table>(.*?)</table>(<div><br></div>)?",
               r"<div><object><table><tbody>\1</tbody></table></object><br></div>",
               h, flags=re.S)
    # nested lists come back in sibling form
    for _ in range(4):
        h = re.sub(r"<li>([^<]*(?:<(?!/?li\b|/?[uo]l\b)[^>]*>[^<]*)*)"
                   r"(<(ul|ol)>.*?</\3>)</li>", r"<li>\1</li>\2", h, flags=re.S)
    # italic is dropped from CJK text (PingFang has none)
    h = re.sub(r"<i>([^<]*[\u2e80-\u9fff\uff00-\uffef][^<]*)</i>", r"\1", h)
    # entities lose their semicolons
    return re.sub(r"&(amp|lt|gt|quot);", r"&\1", h)


def html_to_md(body: str) -> str:
    return flatten(apple_entities(body))


def md_to_html(title: str, body: str) -> str:
    from .render import APPLE_PROFILE, render
    head = f"<div>{html.escape(title, quote=False)}</div>"
    return head + render(body, APPLE_PROFILE)


def _clean_title(line: str) -> str:
    """A body first line used as the title: drop md formatting artifacts."""
    return re.sub(r"^[#\s*_]+|[\s*_]+$", "", line)


def _bare(s: str) -> str:
    """Formatting-insensitive comparison key for title-line matching."""
    return re.sub(r"[\s*#`>~_\-\[\]()]+", "", s or "")


class AppleEndpoint:
    """Endpoint contract (endpoints.py) against one Apple Notes folder."""

    def __init__(self, folder, account="iCloud"):
        self.account, self.folder = account, folder
        self._meta = {}    # nid -> list row
        self._notes = {}   # nid -> (title, body|None, mod), cached across cycles

    # -- contract --------------------------------------------------------
    def list(self):
        rows = _bridge("list", self.account, self.folder)
        self._meta = {r["id"]: r for r in rows}
        stale = [nid for nid, r in self._meta.items()
                 if nid not in self._notes or self._notes[nid][2] != r["mod"]]
        if stale:
            self._load(stale)
        for nid in list(self._notes):
            if nid not in self._meta:
                del self._notes[nid]
        return list(self._meta)

    def _load(self, nids):
        for i in range(0, len(nids), 25):  # bounded argv/stdout per call
            self._load_chunk(nids[i:i + 25])

    def _load_chunk(self, nids):
        for row in _bridge("read", json.dumps(nids)):
            if row.get("gone"):
                self._notes.pop(row["id"], None)
            else:
                self._notes[row["id"]] = self._parse(row)

    def _parse(self, row):
        name, raw = row.get("name", ""), row.get("body") or ""
        # Apple renders our <table> as <object><table>...; that's content,
        # not an attachment -- only non-table objects freeze the note
        probe = _TABLE_OBJECT.sub("<table", raw)
        if row.get("locked") or any(m in probe for m in MEDIA_MARKERS):
            return (name, None, row["mod"])  # frozen: hands off
        lines = html_to_md(raw).split("\n")
        title = name
        if lines:
            b0, bn = _bare(lines[0]), _bare(name)
            # `name` is Apple's TRUNCATED first line; compare by prefix, and
            # take the untruncated body line as the real title so a long
            # title round-trips without oscillating renames
            if b0 == bn or (bn and b0.startswith(bn)) or (b0 and bn.startswith(b0)):
                title = _clean_title(lines[0]) or name
                lines = lines[1:]
                while len(lines) > 1 and lines[0] == "":
                    lines.pop(0)
        return (title, "\n".join(lines), row["mod"])

    def read(self, nid):
        if nid not in self._notes:
            self._load([nid])
        return self._notes[nid]

    def create(self, title, body):
        return _bridge("create", self.account, self.folder,
                       md_to_html(title, body))["id"]

    def update(self, nid, title, body):
        _bridge("update", nid, md_to_html(title, body))
        self._notes.pop(nid, None)  # engine read-back must see real bytes
        return None

    def delete(self, nid):
        _bridge("delete", nid)
        self._notes.pop(nid, None)

    def fetch(self, nid):
        # byId can still read notes sitting in Recently Deleted, so a bare
        # read is NOT existence proof. Folder membership is authoritative
        # (AppleScript reads the live database -- no index lag).
        current = {r["id"] for r in _bridge("list", self.account, self.folder)}
        if nid not in current:
            return None
        rows = _bridge("read", json.dumps([nid]))
        if rows[0].get("gone"):
            return None
        self._notes[nid] = self._parse(rows[0])
        return self._notes[nid]
