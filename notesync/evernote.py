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
import re
import time

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
# ENML -> md (flatten.py, shared with Apple)
# ---------------------------------------------------------------------------
def enml_to_md(content: str) -> str:
    from .flatten import flatten
    m = _ENML_INNER.search(content)
    return flatten(m.group(1) if m else content)


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
