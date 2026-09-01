"""Evernote endpoint adapter (DESIGN.md #9, M2).

Calibrated against the live official MCP (2026-08-30):
- create_note takes NO body; content is added via edit_note.
- edit_note has no whole-body mode; full replace = get_note, then
  replace(find=<entire inner ENML>, content=<new fragment>).
- get_note content: `<!DOCTYPE ...><en-note>INNER</en-note>`; an untouched
  empty note carries an extra `<?xml ...?>` prolog. Tolerate both.
- delete_note = trash (matches our delete-propagation semantics).
- search_notes hit `updatedAt` lags reality -- unusable as a change
  watermark; we get_note each listed note and let the engine's hash
  comparison decide (pilot-scale; optimize later via USN bookkeeping).
- Notes with resources (attachments/images) are FROZEN: read() returns
  body None and the engine must leave the note completely alone.
"""
import html
import re
import time
from html.parser import HTMLParser

from .mcpclient import McpError, McpStdioClient

MCP_CMD = ["npx", "-y", "mcp-remote", "https://mcp.evernote.com/mcp"]

_ENML_INNER = re.compile(r"<en-note[^>]*>(.*)</en-note>", re.S)


# ---------------------------------------------------------------------------
# md (literal text) -> ENML fragment
# ---------------------------------------------------------------------------
def md_to_enml(body: str) -> str:
    from .render import ENML_PROFILE, render
    return render(body, ENML_PROFILE)


# ---------------------------------------------------------------------------
# ENML -> md lines (flatten per DESIGN.md #4)
# ---------------------------------------------------------------------------
_BLOCK = {"div", "p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
          "ul", "ol", "table", "tr", "blockquote", "en-note"}
_H_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### "}


_APPLE_H = {"24px": "# ", "18px": "## "}  # Apple stores h1/h2 as font sizes


class _Flattener(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = [""]
        self.prefix = []          # pending line prefix (list bullet, heading)
        self.fmt = []             # open inline markers to close
        self.href = None

    def _in_heading(self):
        cur = "".join(self.prefix) or self.lines[-1]
        return cur.startswith("#")

    def _newline(self):
        # balance inline markers across the break: a just-opened empty marker
        # is retracted; one with content gets closed here and reopened below
        for m in reversed(self.fmt):
            if self.lines[-1].endswith(m):
                self.lines[-1] = self.lines[-1][:-len(m)]
            else:
                self.lines[-1] += m
        self.lines.append("")
        self.prefix = []  # a residual heading/bullet prefix dies with its line
        for m in self.fmt:
            self._emit(m)

    def _emit(self, text):
        if self.lines[-1] == "" and self.prefix:
            self.lines[-1] = "".join(self.prefix)
            self.prefix = []
        self.lines[-1] += text

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self._newline()
        elif tag == "span":
            # Apple rewrites our h1/h2 into <b><span font-size>; the <b>
            # arrives first, so the line may already hold its just-opened
            # markers -- retract them and switch to a heading prefix
            style = dict(attrs).get("style", "")
            m = re.search(r"font-size:\s*(\d+px)", style)
            if m and m.group(1) in _APPLE_H and not self.prefix:
                cur = self.lines[-1]
                if cur == "" or (self.fmt and cur == "".join(self.fmt)):
                    self.lines[-1] = ""
                    self.fmt.clear()
                    self.prefix = [_APPLE_H[m.group(1)]]
        elif tag in ("b", "strong"):
            if self._in_heading():
                return  # heading lines are implicitly bold on Apple
            self._emit("**"); self.fmt.append("**")
        elif tag in ("i", "em"):
            if self._in_heading():
                return
            self._emit("*"); self.fmt.append("*")
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self._emit("[")
        elif tag in _H_PREFIX:
            self._flush_block()
            self.prefix = [_H_PREFIX[tag]]
        elif tag == "li":
            self._flush_block()
            self.prefix = ["- "]
        elif tag in _BLOCK:
            self._flush_block()

    def handle_endtag(self, tag):
        if tag in ("b", "strong", "i", "em"):
            if self._in_heading():
                return
            if self.fmt:
                self._emit(self.fmt.pop())
        elif tag == "a":
            self._emit(f"]({self.href})" if self.href else "]")
            self.href = None
        elif tag in _BLOCK:
            self._flush_block()

    def _flush_block(self):
        self.prefix = []
        if self.lines[-1] != "":
            self.lines.append("")

    def handle_data(self, data):
        if "\n" in data and not data.strip():
            return  # whitespace between tags (Apple pretty-prints), not content
        parts = data.split("\n")
        for i, part in enumerate(parts):
            if i:
                self._newline()
            if part:
                self._emit(part)

    def result(self):
        # scrub fragmented-formatting junk (Apple emits runs like
        # <b>a</b><b>b</b> and <b><br></b>). Junk-only lines are DROPPED,
        # not blanked -- intentional blank lines must survive verbatim so
        # flatten(render(md)) stays a fixed point.
        out = []
        for ln in self.lines:
            ln = ln.replace("****", "")
            if ln.strip() in ("*", "**", "***"):
                continue
            out.append(ln)
        while out and out[-1] == "":
            out.pop()
        return ("\n".join(out) + "\n") if out else ""


def enml_to_md(content: str) -> str:
    m = _ENML_INNER.search(content)
    inner = m.group(1) if m else content
    # our own writes: pure <div>line</div> / <div><br/></div> sequences --
    # fast path that is exactly inverse to md_to_enml
    if re.fullmatch(r"(<div>(<br/>|[^<>]*)</div>)*", inner):
        lines = []
        for piece in re.findall(r"<div>(.*?)</div>", inner, re.S):
            lines.append("" if piece == "<br/>" else html.unescape(piece))
        return ("\n".join(lines) + "\n") if lines else ""
    f = _Flattener()
    f.feed(inner)
    return f.result()


def enml_inner(content: str) -> str:
    m = _ENML_INNER.search(content)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# endpoint adapter
# ---------------------------------------------------------------------------
class EvernoteEndpoint:
    """Implements the endpoint contract from endpoints.py against one notebook."""

    def __init__(self, notebook, client=None, stderr_path=None):
        self.client = client or McpStdioClient(MCP_CMD, stderr_path=stderr_path)
        self.notebook = notebook
        self.nb_id = self._notebook_id(notebook)
        self._cache = {}  # nid -> full get_note payload (across cycles)
        self._seen = {}   # nid -> search-hit updatedAt when cached

    def _call(self, tool, args):
        """tools/call with rate-limit backoff (server asks for ~60s)."""
        for attempt in (1, 2, 3):
            try:
                return self.client.call(tool, args)
            except McpError as e:
                if "Rate limit" not in str(e) or attempt == 3:
                    raise
                time.sleep(65)

    def _notebook_id(self, name):
        res = self._call("search_notebooks", {"query": name, "maxResults": 100})
        for hit in res.get("hits", res.get("notebooks", [])):
            if (hit.get("label") or hit.get("name")) == name:
                return hit["notebookId"]
        made = self._call("create_notebook", {"name": name})
        return made["notebookId"]

    # -- contract --------------------------------------------------------
    def list(self):
        hits, start = [], 0
        while True:
            res = self._call("search_notes", {
                "query": f'nbGuid:"{self.nb_id}"',
                "maxResults": 100, "startIndex": start})
            page = res.get("hits", [])
            hits += page
            if res.get("isLastPage", True) or not page:
                break
            start += len(page)
        # Incremental: re-fetch a note only when its search-hit updatedAt
        # moved (or it is unknown). The search index lags both ways -- ghosts
        # of trashed notes are dropped via the primary-key active check, and
        # notes we just wrote get one extra confirming get_note next cycle.
        out = []
        for h in hits:
            nid, seen = h["noteId"], h.get("updatedAt") or ""
            if self._seen.get(nid) != seen or nid not in self._cache:
                n = self._call("get_note", {"noteId": nid})
                if n.get("deleted") or n.get("active") is False:
                    self._cache.pop(nid, None)
                    self._seen.pop(nid, None)
                    continue
                self._cache[nid] = n
                self._seen[nid] = seen
            out.append(nid)
        return out

    def _fetch(self, nid):
        if nid not in self._cache:
            self._cache[nid] = self._call("get_note", {"noteId": nid})
        return self._cache[nid]

    def read(self, nid):
        n = self._fetch(nid)
        frozen = bool(n.get("resources")) or bool(n.get("tasks"))
        body = None if frozen else enml_to_md(n.get("content", ""))
        mtime = _parse_ts(n.get("updated") or n.get("created"))
        return n.get("title", ""), body, mtime

    def create(self, title, body):
        made = self._call("create_note",
                                {"title": title, "notebookId": self.nb_id})
        nid = made["noteId"]
        if body:
            try:
                self._call("edit_note", {"noteId": nid, "mode": "append",
                                               "content": md_to_enml(body)})
            except Exception:
                # half-created note would resurface as a bogus new note next
                # cycle: roll it back before propagating the failure
                try:
                    self._call("delete_note", {"noteId": nid})
                except Exception:
                    pass
                raise
        self._cache.pop(nid, None)
        self._seen.pop(nid, None)
        return nid

    def _invalidate(self, nid):
        self._cache.pop(nid, None)
        self._seen.pop(nid, None)

    def update(self, nid, title, body):
        n = self._fetch(nid)
        inner = enml_inner(n.get("content", ""))
        new_inner = md_to_enml(body)
        if inner != new_inner:
            args = {"noteId": nid}
            if inner:
                args.update(mode="replace", find=inner, content=new_inner)
            elif new_inner:
                args.update(mode="append", content=new_inner)
            if n.get("title") != title:
                args["title"] = title
            if "mode" in args or "title" in args:
                self._call("edit_note", args)
        elif n.get("title") != title:
            self._call("edit_note", {"noteId": nid, "title": title})
        self._invalidate(nid)
        return None  # GUID is stable

    def fetch(self, nid):
        """Primary-key read, bypassing the laggy search index. None = truly
        gone (trashed or nonexistent)."""
        from .mcpclient import McpError
        try:
            n = self._call("get_note", {"noteId": nid})
        except McpError:
            return None
        if n.get("deleted") or n.get("active") is False:
            return None
        self._cache[nid] = n
        return self.read(nid)

    def delete(self, nid):
        self._call("delete_note", {"noteId": nid})
        self._invalidate(nid)

    def close(self):
        self.client.close()


def _parse_ts(iso):
    if not iso:
        return 0.0
    import datetime
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
