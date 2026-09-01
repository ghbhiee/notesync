"""Canonical text normalization N(x), content hashing, front-matter, titles.

DESIGN.md #4-5: conservative normalization -- never merge blank lines or touch
indentation; hash is computed over N(body) with front-matter stripped, so the
identity header never affects change detection.
"""
import hashlib
import re

_FM_RE = re.compile(r"\A---\nns: ([0-9a-f]{32})\n---\n")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = "\n".join(lines)
    if not out.strip():
        return ""
    return out.rstrip("\n") + "\n"


def chash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def split_fm(raw: str):
    """-> (uuid or None, body). Only our minimal `ns:` header is recognized."""
    m = _FM_RE.match(raw)
    if not m:
        return None, raw
    return m.group(1), raw[m.end():]


def join_fm(uid: str, body: str) -> str:
    return f"---\nns: {uid}\n---\n{body}"


def sanitize_title(t: str) -> str:
    t = (t or "").strip()
    for ch in "/\\:":
        t = t.replace(ch, "-")
    t = re.sub(r"\s+", " ", t).strip(". ")
    return t[:80] or "Untitled"
