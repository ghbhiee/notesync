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


def _split_row(ln):
    """Parse one canonical pipe-table row ("| a | b |") -> cells, or None.
    Only the exact canonical form renders; anything looser stays literal so
    the flattener's rebuild is guaranteed byte-identical (fixed point)."""
    if not (ln.startswith("| ") and ln.endswith(" |")) or len(ln) < 4:
        return None
    cells = ln[2:-2].split(" | ")
    if any("|" in c for c in cells):
        return None
    if "| " + " | ".join(cells) + " |" != ln:
        return None
    return cells


def _try_table(lines, i, profile):
    """-> (html, lines_consumed) or None. Requires canonical header +
    separator ("|---|" per column); data rows must match the column count."""
    header = _split_row(lines[i])
    if header is None or i + 1 >= len(lines):
        return None
    if lines[i + 1] != "|" + "---|" * len(header):
        return None
    rows, j = [], i + 2
    while j < len(lines):
        cells = _split_row(lines[j])
        if cells is None or len(cells) != len(header):
            break
        rows.append(cells)
        j += 1
    out = ["<table><tr>"]
    out += [f"<th>{_inline(c, profile)}</th>" for c in header]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{_inline(c, profile)}</td>" for c in r)
                   + "</tr>")
    out.append("</table>")
    return "".join(out), j - i


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

    i = 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        if ln.startswith("| "):
            t = _try_table(lines, i - 1, profile)
            if t:
                close_ul()
                out.append(t[0])
                i += t[1] - 1
                continue
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
