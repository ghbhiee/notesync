"""Render canonical markdown (subset) into endpoint rich markup (v2).

Inverse pair of the flattener in evernote.py: `flatten(render(md)) == md`
must hold per profile -- that is the no-oscillation invariant, enforced by
tests/test_render.py. Anything outside a profile's subset stays literal
text, which round-trips trivially and never loses content.

Calibrated per-endpoint subsets (2026-09-01, live experiments):
- ENML accepts h1-h3 / b / i / ul / a. Ordered lists are kept literal
  (Evernote renumbers, so digits would drift), checklists literal.
- Apple Notes accepts h1 / h2 (stored as bold + font-size 24/18px, which
  the flattener maps back) / b / i / ul. h3 degrades to plain <b> (would
  alias with bold -> literal), <ol> is merged into <ul> (type lost ->
  literal), <a href> is stripped to text (URL lost -> literal).
"""
import html
import re


class Profile:
    def __init__(self, headings, link, blank):
        self.headings = headings   # md prefix -> tag
        self.link = link
        self.blank = blank

ENML_PROFILE = Profile({"# ": "h1", "## ": "h2", "### ": "h3"},
                       link=True, blank="<div><br/></div>")
APPLE_PROFILE = Profile({"# ": "h1", "## ": "h2"},
                        link=False, blank="<div><br></div>")

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
_LINK = re.compile(r"\[([^\]\[]+)\]\((https?://[^\s)]+)\)")


def _esc(t):
    return html.escape(t, quote=False)


def _inline(text, profile):
    pats = [(_BOLD, "b"), (_ITAL, "i")]
    if profile.link:
        pats.append((_LINK, "a"))
    out = []
    while text:
        best = None
        for rx, kind in pats:
            m = rx.search(text)
            if m and (best is None or m.start() < best[0].start()):
                best = (m, kind)
        if best is None:
            out.append(_esc(text))
            break
        m, kind = best
        out.append(_esc(text[:m.start()]))
        if kind == "b":
            out.append(f"<b>{_esc(m.group(1))}</b>")
        elif kind == "i":
            out.append(f"<i>{_esc(m.group(1))}</i>")
        else:
            out.append(f'<a href="{html.escape(m.group(2))}">{_esc(m.group(1))}</a>')
        text = text[m.end():]
    return "".join(out)


def render(md, profile):
    """markdown body -> markup fragment for the endpoint."""
    lines = md.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out, in_ul = [], False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for ln in lines:
        if ln == "":
            close_ul()
            out.append(profile.blank)
            continue
        head = next((p for p in profile.headings if ln.startswith(p)), None)
        if head:
            close_ul()
            tag = profile.headings[head]
            out.append(f"<{tag}>{_inline(ln[len(head):], profile)}</{tag}>")
            continue
        if ln.startswith("- ") and not ln.startswith(("- [ ]", "- [x]")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(ln[2:], profile)}</li>")
            continue
        close_ul()
        out.append(f"<div>{_inline(ln, profile)}</div>")
    close_ul()
    return "".join(out)
