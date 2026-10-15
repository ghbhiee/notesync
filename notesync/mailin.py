"""Mail-to-note import: an inbox address whose mail becomes Apple Notes notes.

Independent of the sync mappings: notes are created in the Notes default
folder (or a configured one), never in the SYNC folder, and are not synced
afterwards.

Receiving: Mail.app already keeps the account in sync, so each cycle asks
it over Apple Events (mail_bridge.js) for the messages in INBOX addressed
to the import address -- no IMAP connection, no stored password, near-zero
cost. Messages addressed elsewhere are matched on their recipient lists
only (or by a header filter Mail evaluates itself, for Bcc); their content
is never read.

Converting: the raw source of each matching message is parsed with the
stdlib `email` package. Subject -> title; HTML (or plain text) -> body,
cleaned to the subset Notes imports well; cid: inline images, data: images
and attachments are written to a temp folder and referenced as file://
URLs, which Notes imports as real attachments in place
(notes_create.applescript explains why it must be done that way).

Bookkeeping: Message-ID -> state.db (mail_import), so nothing is imported
twice; the mail is then moved to a "NoteSync" mailbox (or marked read /
flagged) so the inbox shows what is done.
"""
import email
import email.policy
import email.utils
import fcntl
import hashlib
import html
import json
import mimetypes
import os
import re
import select
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

from .state import DB

HERE = Path(__file__).parent
MAIL_BRIDGE = HERE / "mail_bridge.js"
NOTES_CREATE = HERE / "notes_create.applescript"
MAIL_RULE = HERE / "mail_rule.applescript"

# real-time trigger: a Mail rule touches TRIGGER, the watch loop waits on it.
# Outside ~/Documents on purpose: no privacy prompt for the rule's script.
TRIGGER = Path("~/Library/Caches/NoteSync/mail.trigger").expanduser()
RULE_NAME = "NoteSync mail import"
MAIL_SCRIPTS = Path("~/Library/Application Scripts/com.apple.mail").expanduser()
RULE_SCRIPT = "NoteSync Mail Import.scpt"
_RULE_SCRIPT_SRC = """\
-- Installed by `notesync mail --install-rule`. Mail runs it when mail for the
-- NoteSync import address arrives; it only wakes the engine, which imports.
using terms from application "Mail"
\ton perform mail action with messages theMessages for rule theRule
\t\tdo shell script "{cmd}"
\tend perform mail action with messages
end using terms from
"""

DEFAULTS = {
    "enabled": False,
    "address": "",            # one address or a list
    "account": "iCloud",      # Mail account that receives it
    "mailbox": "INBOX",
    "include_bcc": True,      # also catch mail where the address was Bcc'd
    "lookback_days": 1,       # scan window; widened to cover downtime, 0 = all
    "allowed_senders": [],    # addresses or "@domain"; empty = anyone
    "notes_account": "",      # "" = the Notes default account
    "notes_folder": "",       # "" = that account's default folder
    "after_import": "move",   # move | flag | read | none
    "done_mailbox": "NoteSync",
    "max_attachment_mb": 50,
}
MAX_ATTEMPTS = 3
OSA_TIMEOUT = 120
SCAN_TIMEOUT = 60
PAUSE_AFTER_TIMEOUT = 1800  # an unanswered permission prompt blocks each call
CATCHUP_MAX_DAYS = 30
LAST_SCAN = "mail_last_scan"
AUTH_HELP = ("grant it in System Settings > Privacy & Security > Automation: "
             "under the app running the engine (NoteSyncBar), enable Mail")


class MailError(RuntimeError):
    pass


class NotAuthorized(MailError):
    pass


def settings(cfg):
    """The mail_import block of the config merged over the defaults."""
    s = dict(DEFAULTS)
    s.update(cfg.get("mail_import") or {})
    addr = s["address"]
    s["addresses"] = [a.strip().lower() for a in
                      ([addr] if isinstance(addr, str) else addr or []) if a.strip()]
    return s


# -- Apple Events --------------------------------------------------------------
def _osa(args, timeout=OSA_TIMEOUT):
    try:
        r = subprocess.run(["osascript", *args], capture_output=True,
                           encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MailError("osascript timed out (a permission prompt may be waiting)")
    if r.returncode != 0:
        err = r.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            raise NotAuthorized(err[:300])
        raise MailError(err[:300])
    return r.stdout.strip()


def mail_bridge(cmd, timeout=OSA_TIMEOUT, **args):
    return json.loads(_osa(["-l", "JavaScript", str(MAIL_BRIDGE), cmd,
                            json.dumps(args)], timeout))


def create_note(account, folder, body_path, files):
    """-> new note id. Folder: "" (default), a name, or an x-coredata id."""
    return _osa([str(NOTES_CREATE), account or "", folder or "", str(body_path),
                 *[str(f) for f in files]])


def install_rule(cfg):
    """Compile the trigger script into Mail's script folder and (re)create
    the rule: any recipient (or iCloud's Original-recipient header) contains
    the import address -> run it. -> the script's path."""
    addrs = settings(cfg)["addresses"]
    if not addrs:
        raise MailError("no import address configured")
    src = rule_script_source()
    MAIL_SCRIPTS.mkdir(parents=True, exist_ok=True)
    out = MAIL_SCRIPTS / RULE_SCRIPT
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "rule.applescript"
        p.write_text(src, encoding="utf-8")
        r = subprocess.run(["osacompile", "-o", str(out), str(p)],
                           capture_output=True, encoding="utf-8")
        if r.returncode != 0:
            raise MailError(f"osacompile: {r.stderr.strip()[:300]}")
    # Mail's scripting stores `run script` as the absolute path it is given
    # (and refuses a path that does not exist), but at run time resolves it
    # INSIDE its scripts folder -- "<folder>/<absolute path>", ENOENT, logged
    # as "Failed to create an NSUserAppleScriptTask". A copy at that nested
    # path satisfies both.
    mirror = _mirror_path(out)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out, mirror)
    _osa([str(MAIL_RULE), "install", RULE_NAME, str(out), *addrs])
    return out


def rule_script_source(trigger=TRIGGER):
    """The rule's AppleScript: shell-quoted, then AppleScript-string-escaped."""
    trigger = Path(trigger)
    cmd = f"mkdir -p {shlex.quote(str(trigger.parent))} && touch {shlex.quote(str(trigger))}"
    return _RULE_SCRIPT_SRC.format(cmd=cmd.replace("\\", "\\\\").replace('"', '\\"'))


def _mirror_path(script):
    return MAIL_SCRIPTS.joinpath(*Path(script).parts[1:])


def remove_rule():
    _osa([str(MAIL_RULE), "remove", RULE_NAME])
    out = MAIL_SCRIPTS / RULE_SCRIPT
    mirror = _mirror_path(out)
    mirror.unlink(missing_ok=True)
    d = mirror.parent
    while d != MAIL_SCRIPTS and MAIL_SCRIPTS in d.parents:
        try:
            d.rmdir()  # only the now-empty folders of the mirror path
        except OSError:
            break
        d = d.parent
    out.unlink(missing_ok=True)


class TriggerWait:
    """A sleep that a touch of the trigger file cuts short: kqueue on the
    file, so waiting costs nothing and no touch is lost -- one that lands
    while a cycle runs is queued and ends the next wait at once."""

    _FFLAGS = (select.KQ_NOTE_WRITE | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_EXTEND
               | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME)

    def __init__(self, path=TRIGGER):
        self.path, self.kq, self.fd = Path(path), None, None
        try:
            self._arm()
        except OSError:
            self.close()  # plain sleeping; the poll still imports

    def _arm(self):
        self.close()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.fd = os.open(self.path, getattr(os, "O_EVTONLY", 0x8000))
        self.kq = select.kqueue()
        self.kq.control([select.kevent(self.fd, filter=select.KQ_FILTER_VNODE,
                                       flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                       fflags=self._FFLAGS)], 0)

    def close(self):
        if self.kq is not None:
            self.kq.close()
        if self.fd is not None:
            os.close(self.fd)
        self.kq = self.fd = None

    def wait(self, timeout):
        """-> True when the trigger fired, False when the time ran out."""
        timeout = max(0.0, timeout)
        if self.kq is None:
            time.sleep(timeout)
            return False
        evs = self.kq.control(None, 4, timeout)
        if any(e.fflags & (select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME) for e in evs):
            try:  # the cache folder was purged: watch the new file
                self._arm()
            except OSError:
                self.close()
        return bool(evs)


def broken_images(note_id):
    """Images Notes could not read come back as `data:...;base64,(null)`."""
    from .apple import _bridge
    row = _bridge("read", json.dumps([note_id]))[0]
    return (row.get("body") or "").count("base64,(null)")


# -- MIME -> note HTML ------------------------------------------------------------
_FWD = re.compile(r"^\s*(?:(?:fwd?|fw|tr|wg|转发|轉寄)\s*[:：]\s*)+", re.I)
_SIGNATURES = {"application/pkcs7-signature", "application/x-pkcs7-signature",
               "application/pgp-signature"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "image/webp": ".webp", "image/heic": ".heic", "image/bmp": ".bmp",
        "image/tiff": ".tiff", "image/svg+xml": ".svg", "application/pdf": ".pdf",
        "text/plain": ".txt", "text/html": ".html", "text/calendar": ".ics"}


def note_title(subject, when=None):
    t = _FWD.sub("", subject or "")
    t = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", t)).strip()
    return t or time.strftime("Mail note %Y-%m-%d %H:%M", time.localtime(when))


def safe_filename(name, ctype, n):
    """A file name Notes can use as the attachment name; it infers the
    attachment type from the extension, so one is always present."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f\x7f:*?"<>|]', "", name).strip().lstrip(".")
    stem, ext = (name.rsplit(".", 1) + [""])[:2] if "." in name else (name, "")
    ext = "." + ext if ext else (_EXT.get(ctype) or mimetypes.guess_extension(ctype or "")
                                 or ".bin")
    stem = stem or f"attachment-{n}"
    while len(stem.encode()) > 150:  # file names are capped at 255 bytes
        stem = stem[:-1]
    return stem + ext[:20]


def _cid(part):
    v = part.get("Content-ID")
    return str(v).strip().strip("<>").lower() if v else None


def _payload(part):
    data = part.get_payload(decode=True)
    return data if isinstance(data, bytes) else b""


def text_to_html(text, flowed=False, delsp=False, title=None):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if flowed:  # RFC 3676: a trailing space marks a soft line break
        out, cur = [], None
        for ln in lines:
            if ln.startswith(" "):
                ln = ln[1:]  # space-stuffing
            soft = ln.endswith(" ") and ln != "-- "
            piece = ln[:-1] if soft and delsp else ln
            cur = piece if cur is None else cur + piece
            if not soft:
                out.append(cur)
                cur = None
        if cur is not None:
            out.append(cur)
        lines = out
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if title and lines and _bare(lines[0]) == _bare(title):
        lines = lines[1:]  # the body repeats the subject as its first line
        while lines and not lines[0].strip():
            lines.pop(0)
    blocks = []
    for ln in lines:
        if not ln.strip():
            blocks.append("<div><br></div>")
            continue
        esc = html.escape(ln.rstrip(), quote=False)
        # Notes collapses runs of spaces and strips indentation; nbsp survives
        esc = re.sub(r"^ +", lambda m: "&nbsp;" * len(m.group()), esc)
        esc = re.sub(r"  +", lambda m: " " + "&nbsp;" * (len(m.group()) - 1), esc)
        blocks.append(f"<div>{esc}</div>")
    return "".join(blocks)


def _bare(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


# -- HTML cleaning -----------------------------------------------------------
# A tiny tolerant DOM, then a whitelist serializer. Email HTML is messy
# (unclosed cells, layout tables, styles, tracking pixels); Notes imports a
# clean subset well and anything else unpredictably.
_VOID = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base",
         "wbr", "source", "track", "param", "embed"}
_DROP = {"head", "script", "style", "title", "template", "noscript", "iframe",
         "object", "embed", "svg", "math", "form", "button", "select",
         "textarea", "video", "audio", "canvas", "map", "xml"}
_BLOCKS = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
           "table", "thead", "tbody", "tfoot", "tr", "td", "th", "blockquote",
           "pre", "hr", "br", "center", "section", "article", "header", "footer",
           "main", "aside", "nav", "figure", "figcaption", "address", "dl", "dt",
           "dd", "body", "html", "caption"}
_INLINE = {"b": "b", "strong": "b", "i": "i", "em": "i", "cite": "i",
           "u": "u", "ins": "u", "s": "s", "strike": "s", "del": "s",
           "sub": "sub", "sup": "sup", "code": "tt", "tt": "tt", "kbd": "tt",
           "samp": "tt"}


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs or {}, [], parent


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node("#root")

    def _open(self, tag):
        return [n for n in self._stack() if n.tag == tag]

    def _stack(self):
        n, out = self.cur, []
        while n is not None:
            out.append(n)
            n = n.parent
        return out

    def _close(self, tag, stop=()):
        """Pop up to and including the innermost open `tag`, unless one of
        `stop` is met first (a cell never closes its enclosing table's row)."""
        for n in self._stack():
            if n.tag == tag:
                self.cur = n.parent
                return
            if n.tag in stop or n.tag == "#root":
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("td", "th"):
            self._close("td", ("tr", "table"))
            self._close("th", ("tr", "table"))
        elif tag == "tr":
            for t in ("td", "th", "tr"):
                self._close(t, ("table",))
        elif tag == "li":
            self._close("li", ("ul", "ol"))
        elif tag == "p":
            self._close("p", ("div", "td", "li", "blockquote", "table"))
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID:
            self._close(tag.lower())

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID:
            return
        # like a browser, a stray end tag never closes across a table cell
        if tag in ("td", "th", "tr", "thead", "tbody", "tfoot", "caption"):
            stop = ("table",)
        elif tag == "table":
            stop = ()
        else:
            stop = ("td", "th", "table")
        self._close(tag, stop)

    def handle_data(self, data):
        self.cur.children.append(data)


def _text(node):
    if isinstance(node, str):
        return node
    return "".join(_text(c) for c in node.children)


def _has(node, tags):
    return any(not isinstance(c, str) and (c.tag in tags or _has(c, tags))
               for c in node.children)


def _cells(table):
    rows = []

    def walk(n):
        for c in n.children:
            if isinstance(c, str):
                continue
            if c.tag == "tr":
                rows.append([x for x in c.children
                             if not isinstance(x, str) and x.tag in ("td", "th")])
            elif c.tag in ("thead", "tbody", "tfoot"):
                walk(c)
    walk(table)
    return rows


def _unwrap_layout_tables(root):
    """Tables used for layout (nested tables, one column, or holding images)
    become their cells' content, one block per cell. Notes has no nested
    tables and no images in table cells. Classified on the original
    structure first, then unwrapped bottom-up."""
    layout = set()
    for n in [root, *_walk_nodes(root)]:
        if n.tag == "table":
            rows = _cells(n)
            if (n.attrs.get("role") == "presentation" or _has(n, {"table", "img"})
                    or not rows or all(len(r) <= 1 for r in rows)):
                layout.add(id(n))

    def unwrap(node):
        for c in node.children:
            if not isinstance(c, str):
                unwrap(c)
        out = []
        for c in node.children:
            if not isinstance(c, str) and id(c) in layout:
                for row in _cells(c):
                    for cell in row:
                        div = _Node("div", parent=node)
                        div.children = cell.children
                        for k in div.children:
                            if not isinstance(k, str):
                                k.parent = div
                        out.append(div)
                continue
            out.append(c)
        node.children = out
    unwrap(root)


class _Html:
    """Serializes a cleaned tree; images resolved through `resolve`."""

    def __init__(self, resolve):
        self.resolve = resolve  # (src, attrs) -> html fragment
        self.out = []

    def run(self, node):
        self.children(node, pre=False)
        return "".join(self.out)

    def children(self, node, pre):
        kids = node.children
        for i, c in enumerate(kids):
            if isinstance(c, str):
                if pre:
                    self.out.append(html.escape(c, quote=False))
                elif c.strip():
                    self.out.append(html.escape(re.sub(r"\s+", " ", c), quote=False))
                else:
                    # whitespace between blocks is layout, between words a space
                    prev = kids[i - 1] if i else None
                    nxt = kids[i + 1] if i + 1 < len(kids) else None
                    if (prev is not None and nxt is not None
                            and not self._block(prev) and not self._block(nxt)):
                        self.out.append(" ")
                continue
            self.node(c, pre)

    @staticmethod
    def _block(n):
        return not isinstance(n, str) and n.tag in _BLOCKS

    def node(self, n, pre):
        t = n.tag
        if t in _DROP or t.startswith("o:") or t.startswith("v:"):
            return
        if t == "br":
            self.out.append("<br>")
        elif t == "hr":
            self.out.append("<hr>")
        elif t == "img":
            self.out.append(self.resolve(n.attrs.get("src", ""), n.attrs))
        elif t == "a":
            self.children(n, pre)
            href = n.attrs.get("href", "").strip()
            # Notes drops href on import: keep the target visible as text
            if (re.match(r"https?://", href, re.I) and not _has(n, {"img"})
                    and _bare(_text(n)) not in (_bare(href), "")):
                self.out.append(f" ({html.escape(href, quote=False)})")
        elif t in ("h1", "h2"):
            self.wrap(t, n, pre)
        elif t in ("h3", "h4", "h5", "h6"):
            self.out.append("<div><b>")
            self.children(n, pre)
            self.out.append("</b></div>")
        elif t in ("ul", "ol", "li", "tr"):
            self.wrap(t, n, pre)
        elif t == "table":
            self.out.append("<table><tbody>")
            for row in _cells(n):
                self.out.append("<tr>")
                for cell in row:
                    self.wrap("td", cell, pre)
                self.out.append("</tr>")
            self.out.append("</tbody></table>")
        elif t in ("td", "th"):
            self.wrap("td", n, pre)
        elif t == "pre":
            self.wrap("pre", n, True)
        elif t in _BLOCKS:
            self.wrap("div", n, pre)
        elif t in _INLINE:
            self.wrap(_INLINE[t], n, pre)
        else:  # span, font, and anything unknown: keep the content only
            self.children(n, pre)

    def wrap(self, tag, n, pre):
        self.out.append(f"<{tag}>")
        self.children(n, pre)
        self.out.append(f"</{tag}>")


def _drop_title_block(root, title):
    """Remove the first block of the body when it just repeats the title."""
    def first_text(n):
        for c in n.children:
            if isinstance(c, str):
                if c.strip():
                    return n
            elif c.tag not in _DROP:
                hit = first_text(c)
                if hit is not None:
                    return hit
        return None
    n = first_text(root)
    while n is not None and n.tag not in _BLOCKS and n.parent is not None:
        n = n.parent
    if (n is not None and n.parent is not None and n.tag not in ("body", "html")
            and _bare(_text(n)) == _bare(title)):
        n.parent.children.remove(n)


class Parsed:
    def __init__(self, title, body, files, skipped, sender):
        self.title, self.body, self.files = title, body, files
        self.skipped, self.sender = skipped, sender

    def html(self):
        return f"<div><h1>{html.escape(self.title, quote=False)}</h1></div>{self.body}"


class _Builder:
    def __init__(self, msg, workdir, max_bytes, title):
        self.msg, self.dir, self.max = msg, Path(workdir), max_bytes
        self.dir.mkdir(parents=True, exist_ok=True)
        self.title = title
        self.files, self.skipped, self.blocks = [], [], []
        self.saved = {}  # id(part) -> fragment
        self.cids = {}
        self.referenced = set()
        self.first_text = True
        for p in msg.walk():
            c = _cid(p)
            if c:
                self.cids.setdefault(c, p)
            if p.get_content_type() == "text/html" and p.get_content_disposition() != "attachment":
                for ref in re.findall(r"cid:([^\"'\s>)]+)", _content(p), re.I):
                    self.referenced.add(urllib.parse.unquote(ref).lower())

    def build(self):
        self.emit(self.msg)
        return "".join(self.blocks)

    def emit(self, part):
        ctype = part.get_content_type()
        if part.is_multipart():
            sub = part.get_payload()
            if ctype == "multipart/alternative":
                self.emit(_best_alternative(sub))
            elif ctype == "multipart/related":
                start = part.get_param("start")
                start = start.strip("<>").lower() if start else None
                root = next((p for p in sub if _cid(p) == start), sub[0]) if sub else None
                if root is not None:
                    self.emit(root)
                for p in sub:
                    if p is not root and _cid(p) not in self.referenced:
                        self.emit(p)
            elif ctype == "message/rfc822":
                inner = sub[0]
                head = [f"<b>{html.escape(note_title(str(inner.get('Subject', ''))), quote=False)}</b>"]
                for h in ("From", "Date"):
                    if inner.get(h):
                        head.append(html.escape(f"{h}: {inner[h]}", quote=False))
                self.blocks.append("<div><br></div>" + "".join(f"<div>{x}</div>" for x in head))
                self.emit(inner)
            else:
                for p in sub:
                    self.emit(p)
            return
        disp = part.get_content_disposition()
        if ctype in _SIGNATURES:
            return
        if ctype == "text/html" and disp != "attachment":
            self.blocks.append(self.clean_html(_content(part)))
        elif ctype == "text/plain" and disp != "attachment":
            flowed = (part.get_param("format") or "").lower() == "flowed"
            delsp = (part.get_param("delsp") or "").lower() == "yes"
            title = self.title if self.first_text else None
            self.first_text = False
            self.blocks.append(text_to_html(_content(part), flowed, delsp, title))
        elif _cid(part) in self.referenced:
            return  # shown inline by the HTML that references it
        else:
            self.blocks.append(self.attach(part))

    def save(self, data, name, ctype):
        fname = safe_filename(name, ctype, len(self.files) + 1)
        path, k = self.dir / fname, 2
        while path.exists():
            stem, _, ext = fname.rpartition(".")
            path, k = self.dir / f"{stem} ({k}).{ext}", k + 1
        path.write_bytes(data)
        self.files.append(path)
        return f'<img src="{path.as_uri()}">'

    def attach(self, part):
        if id(part) in self.saved:
            return self.saved[id(part)]
        data = _payload(part)
        name = part.get_filename() or _cid(part) or ""
        if len(data) > self.max:
            self.skipped.append((name, len(data)))
            frag = (f"<div>[{html.escape(name or 'attachment', quote=False)}: "
                    f"{len(data) / 1e6:.1f} MB, too large, not imported]</div>")
        elif not data:
            frag = ""
        else:
            frag = f"<div>{self.save(data, name, part.get_content_type())}</div>"
        self.saved[id(part)] = frag
        return frag

    def resolve_img(self, src, attrs):
        src = src.strip()
        if src.lower().startswith("cid:"):
            part = self.cids.get(urllib.parse.unquote(src[4:]).lower())
            if part is None:
                return ""
            frag = self.attach(part)
            return frag[5:-6] if frag.startswith("<div>") else frag  # inline
        m = re.match(r"data:([\w/+.-]+)?(;[^,]*)?,(.*)", src, re.S)
        if m:
            import base64
            try:
                data = (base64.b64decode(m.group(3)) if ";base64" in (m.group(2) or "")
                        else urllib.parse.unquote_to_bytes(m.group(3)))
            except Exception:
                return ""
            return self.save(data, attrs.get("alt", ""), m.group(1) or "image/png")
        if re.match(r"https?://", src, re.I):
            # remote images are not fetched (privacy, tracking pixels);
            # tiny ones are pixels, the rest stay visible as a reference
            if any(attrs.get(k, "").strip().rstrip("px") in ("0", "1", "2")
                   for k in ("width", "height")):
                return ""
            alt = attrs.get("alt", "").strip()
            label = f"[image: {alt}]" if alt else "[image]"
            return html.escape(f"{label} ({src})", quote=False)
        return ""

    def clean_html(self, text):
        tb = _TreeBuilder()
        tb.feed(text)
        tb.close()
        root = tb.root
        body = next((n for n in _walk_nodes(root) if n.tag == "body"), root)
        _unwrap_layout_tables(body)
        if self.first_text:
            _drop_title_block(body, self.title)
            self.first_text = False
        return _Html(self.resolve_img).run(body)


def _walk_nodes(n):
    for c in n.children:
        if not isinstance(c, str):
            yield c
            yield from _walk_nodes(c)


def _content(part):
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):  # unknown or lying charset
        return _payload(part).decode("utf-8", "replace")


def _best_alternative(parts):
    rich = [p for p in parts if p.get_content_type() == "text/html"
            or p.get_content_type() in ("multipart/related", "multipart/mixed")]
    plain = [p for p in parts if p.get_content_type() == "text/plain"]
    return (rich or plain or parts)[-1]


def parse_message(raw, workdir, max_bytes=50 * 1024 * 1024, when=None):
    """Raw RFC 822 bytes -> Parsed (title, body HTML, files written to workdir)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    title = note_title(str(msg.get("Subject", "")), when)
    b = _Builder(msg, workdir, max_bytes, title)
    # mail clients pad with empty paragraphs; they are noise under a title
    body = re.sub(r"^(?:<div><br></div>|<br>)+|(?:<div><br></div>|<br>)+$", "", b.build())
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    return Parsed(title, body, b.files, b.skipped, sender)


# -- the cycle step ----------------------------------------------------------
_said = {}
_paused_until = [0.0]


def _say(key, msg, log, every=3600):
    """Log a standing condition once an hour, not every cycle."""
    now = time.time()
    if key not in _said or now - _said[key] >= every:
        _said[key] = now
        log(msg)


def scan_window(lookback_days, last_scan, now):
    """Days to look back. Mail's received date is the server's, so a fixed
    short window would miss mail that arrived while this Mac was asleep or
    off: the window also reaches back to the last successful scan (+1h),
    capped at CATCHUP_MAX_DAYS. 0 = no limit."""
    if lookback_days <= 0:
        return 0
    if last_scan is None:
        return lookback_days
    gap = (now - float(last_scan) + 3600) / 86400
    return max(lookback_days, min(gap, CATCHUP_MAX_DAYS))


def message_key(hit):
    mid = (hit.get("messageId") or "").strip().strip("<>")
    if mid:
        return mid
    h = hashlib.sha1(f"{hit.get('subject')}\0{hit.get('sender')}".encode()).hexdigest()
    return f"noid:{int(hit.get('received') or 0)}:{h[:16]}"


def sender_allowed(sender, allowed):
    if not allowed:
        return True
    addr = email.utils.parseaddr(sender or "")[1].lower()
    for a in allowed:
        a = a.strip().lower()
        if a and (addr == a or (a.startswith("@") and addr.endswith(a))):
            return True
    return False


def run_once(cfg, log=lambda m: None, dry_run=False, overrides=None, force=False):
    """Import new mail sent to the import address. -> notes created.
    overrides: mail_import keys for this run only; force: run even when
    the config has it disabled (the one-shot CLI command)."""
    mc = dict(cfg.get("mail_import") or {})
    mc.update(overrides or {})
    s = settings({"mail_import": mc})
    if not (s["enabled"] or force) or not s["addresses"]:
        return 0
    if time.time() < _paused_until[0] and not force:
        return 0
    state = Path(cfg["state"]).expanduser()
    lock = open(state / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return 0  # a sync cycle holds it; next time
    db = DB(state / "state.db")
    try:
        started = time.time()
        try:
            scan = mail_bridge("scan", timeout=SCAN_TIMEOUT, account=s["account"],
                               mailbox=s["mailbox"], addresses=s["addresses"],
                               lookbackDays=scan_window(s["lookback_days"],
                                                        db.meta_get(LAST_SCAN), started),
                               bcc=bool(s["include_bcc"]))
        except NotAuthorized:
            _say("auth", f"[mail] not allowed to read Mail -- {AUTH_HELP}", log)
            return 0
        except MailError as e:
            if "timed out" in str(e):
                _paused_until[0] = time.time() + PAUSE_AFTER_TIMEOUT
                log(f"[mail] Mail did not answer within {SCAN_TIMEOUT}s (a permission "
                    f"prompt for Mail may be waiting on screen); retrying in "
                    f"{PAUSE_AFTER_TIMEOUT // 60} min")
                return 0
            raise
        if not scan.get("running"):
            _say("running", "[mail] Mail.app is not running; mail import waits for it", log)
            return 0
        n = 0
        for hit in scan["hits"]:
            n += _handle(hit, s, db, state, log, dry_run)
        if not dry_run and not overrides:  # only the configured scan moves the marker
            db.meta_set(LAST_SCAN, started)
        return n
    finally:
        db.close()
        lock.close()


def _handle(hit, s, db, state, log, dry_run):
    key = message_key(hit)
    row = db.mail_get(key)
    subject = hit.get("subject", "")
    if row and row["status"] == "imported":
        if not dry_run and s["after_import"] == "move":
            _mark(hit, s, log)  # still in the inbox: the earlier move failed
        return 0
    if row and row["status"] == "failed" and row["attempts"] >= MAX_ATTEMPTS:
        return 0
    if not sender_allowed(hit.get("sender"), s["allowed_senders"]):
        # not recorded: allowing the sender later picks it up
        _say(f"ignored:{key}", f"[mail] ignored (sender not in allowed_senders): "
             f"{subject}", log, every=float("inf"))
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="notesync-mail-"))
    try:
        src = tmp / "source.eml"
        mail_bridge("source", account=s["account"], mailbox=s["mailbox"],
                    id=hit["id"], path=str(src))
        p = parse_message(src.read_bytes(), tmp / "files",
                          int(s["max_attachment_mb"] * 1024 * 1024), hit.get("received"))
        if dry_run:
            log(f"[mail] would import: {p.title} ({len(p.files)} file(s)"
                + (f", {len(p.skipped)} too large" if p.skipped else "") + ")")
            return 0
        body = tmp / "note.html"
        body.write_text(p.html(), encoding="utf-8")
        nid = create_note(s["notes_account"], s["notes_folder"], body, p.files)
        # the note exists from here on: nothing below may fail the import
        # (a retry would create it twice)
        try:
            broken = broken_images(nid) if p.files else 0
        except Exception:
            broken = 0
    except NotAuthorized as e:
        _say("auth-notes", f"[mail] not allowed to write Notes or read Mail ({e}) -- "
             f"{AUTH_HELP}", log)
        return 0
    except Exception as e:
        db.mail_record(key, subject, hit.get("sender"), None, "failed", str(e)[:300])
        log(f"[mail error] {subject}: {e}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    db.mail_record(key, subject, hit.get("sender"), nid, "imported", None)
    extra = (f"; {len(p.files)} file(s)" if p.files else "") + \
            (f"; {broken} image(s) EMPTY" if broken else "") + \
            (f"; {len(p.skipped)} too large, skipped" if p.skipped else "")
    db.oplog(None, "mailin", f"{p.title}{extra}")
    log(f"[mail] imported: {p.title}{extra}")
    _mark(hit, s, log)
    return 1


def _mark(hit, s, log):
    if s["after_import"] == "none":
        return
    try:
        mail_bridge("mark", account=s["account"], mailbox=s["mailbox"], id=hit["id"],
                    action=s["after_import"], done=s["done_mailbox"])
    except Exception as e:  # the note exists; the marker is cosmetic
        _say(f"mark:{hit['id']}", f"[mail] could not mark '{hit.get('subject')}' "
             f"({s['after_import']}): {e}", log)
