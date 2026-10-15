"""Render canonical markdown into endpoint rich markup (v3).

Inverse of flatten.py: `flatten(render(md)) == md` must hold per endpoint
for canonical md -- the no-oscillation invariant, enforced by
tests/test_render.py. Lenient spellings (`* ` bullets, table separators
with colons, ...) are rendered too; they flatten to the canonical spelling,
which is itself a fixed point. Anything an endpoint cannot store
reversibly stays literal text, which round-trips trivially and never loses
content. Which construct is rich where is declared in capabilities.py.

The parser is line oriented (one md line = one endpoint paragraph, GFM
breaks:true), block by block:
  fenced code  verbatim lines, fences included (no markdown inside)
  $$ math      literal lines, no markdown inside (as is inline $...$)
  table        header + separator (+ rows)
  quote        `> ` lines, rendered recursively inside <blockquote>
  list         `- ` / `1. ` items, nesting by indentation
  hr / heading / blank / paragraph

Endpoint calibration (live probes, see DEVLOG.zh.md):
- Both endpoints collapse runs of spaces and strip leading indentation
  unless they are written as &nbsp;.
- Apple: h1/h2 become bold 24/18px spans, <h3> degrades to bold, sizes
  below 18px are stripped -> h3 is written as bold + underline. <pre>
  becomes monospaced (<tt>) paragraphs; inline <tt> becomes a Courier run,
  but a paragraph that is nothing but <tt> becomes monospaced. <hr> and
  <blockquote> are dropped, <ol start> loses its start, <ul> directly followed by <ol>
  merges into one list, <li> line breaks are joined, href is stripped, and
  the first blank paragraph after a table is absorbed into the table.
  CJK text falls back to PingFang: no Courier (inline code with CJK stays
  literal) and no italic (italic spans with CJK stay literal).
  Entities come back without their semicolons (flatten.apple_entities).
- Apple rewrites a note asynchronously after a write ("settling", mod date
  unchanged): what counts is the settled form; the engine's drift handling
  absorbs the difference to the immediate read-back.
- Evernote stores ENML verbatim.
"""
import html
import re

from .capabilities import rich

# -- inline ----------------------------------------------------------------
_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_BOLDITAL = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
_LINK = re.compile(r"\[([^\]\[]+)\]\((https?://[^\s)]+)\)")
_MATH = re.compile(r"\$\$(?!\s).+?(?<!\s)\$\$|\$(?![\s$])[^$]+?(?<![\s\\])\$")
_RUN = re.compile(r" {2,}")
# CJK ideographs, kana, hangul, full-width forms and CJK punctuation: Apple
# renders them with a fallback font (PingFang ...) that has no monospace and,
# inside lists and table cells, no italic -- so those spans cannot round-trip
_CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]")


def _esc(t, lead=False):
    """Escape text and pin whitespace: runs of spaces (and leading spaces)
    become &nbsp;, which both endpoints keep; single inner spaces stay
    breakable."""
    s = html.escape(t, quote=False)
    s = _RUN.sub(lambda m: "&nbsp;" * len(m.group()), s)
    if lead and s.startswith(" "):
        n = len(s) - len(s.lstrip(" "))
        s = "&nbsp;" * n + s[n:]
    return s


def _code_spans(text, lead, apple=False):
    """Text that may contain inline code (inside bold/italic)."""
    out, pos = [], 0
    for m in _CODE.finditer(text):
        out.append(_esc(text[pos:m.start()], lead and pos == 0))
        if apple and _CJK.search(m.group(2)):
            out.append(_esc(m.group()))
        else:
            out.append(f"<code>{_esc(m.group(2))}</code>")
        pos = m.end()
    out.append(_esc(text[pos:], lead and pos == 0))
    return "".join(out)


def _pin(h):
    """Pin spaces that HTML would collapse across tag boundaries: a space
    touching another space through tags, and a space at either edge of the
    paragraph."""
    h = re.sub(r" (?=(?:<[^>]+>)+ )", "&nbsp;", h)
    h = re.sub(r"^((?:<[^>]+>)*) ", r"\1&nbsp;", h)
    return re.sub(r" ((?:</[^>]+>)*)$", r"&nbsp;\1", h)


def _inline(text, profile, lead=False, ctx="p"):
    """Inline markup of one paragraph / list item (ctx "li") / table cell
    (ctx "cell")."""
    return _pin(_inline_raw(text, profile, lead, ctx))


def _inline_raw(text, profile, lead, ctx):
    apple = profile.name == "apple"
    pats = [(_MATH, "math"), (_BOLDITAL, "bi"), (_BOLD, "b"), (_ITAL, "i")]
    if profile.code:
        pats.insert(0, (_CODE, "code"))
    if profile.link:
        pats.append((_LINK, "a"))
    out, first = [], True
    while text:
        best = None
        for rx, kind in pats:
            m = rx.search(text)
            if m and (best is None or m.start() < best[0].start()):
                best = (m, kind)
        if best is None:
            out.append(_esc(text, lead and first))
            break
        m, kind = best
        out.append(_esc(text[:m.start()], lead and first))
        first = False
        if kind == "code" and apple and (
                _CJK.search(m.group(2)) or (ctx == "cell" and m.group(2) != m.group(2).strip())):
            # Courier cannot hold CJK; Apple trims spaces at a cell's edges
            out.append(_esc(m.group()))
        elif kind == "code":
            out.append(f"<code>{_esc(m.group(2))}</code>")
        elif kind in ("i", "bi") and apple and _CJK.search(m.group(1)):
            out.append(_esc(m.group()))  # Apple drops italic on CJK: literal
        elif kind == "math":
            out.append(_esc(m.group()))  # literal: `*` in TeX is not emphasis
        elif kind == "a":
            out.append(f'<a href="{html.escape(m.group(2))}">{_esc(m.group(1))}</a>')
        else:
            body = (_code_spans(m.group(1), False, apple) if profile.code
                    else _esc(m.group(1)))
            op, cl = {"b": ("<b>", "</b>"), "i": ("<i>", "</i>"),
                      "bi": ("<b><i>", "</i></b>")}[kind]
            out.append(op + body + cl)
        text = text[m.end():]
    return "".join(out)


def _only_code(text):
    """True if a line is nothing but inline code spans (and spaces)."""
    return bool(_CODE.search(text)) and not _CODE.sub("", text).strip()


# -- block grammar -----------------------------------------------------------
_FENCE = re.compile(r"^( *)(`{3,}|~{3,})(.*)$")
_HR = re.compile(r"^ {0,3}([-*_])( *\1){2,} *$")
_ITEM = re.compile(r"^( *)([-*]|\d{1,9}\.)( {1,4})(\S.*)?$")
_TASK = re.compile(r"^\[[ xX]\]( |$)")
_SEP_CELL = re.compile(r"^ *:?-+:? *$")
_QUOTE = re.compile(r"^>( ?)(.*)$")


def _fence_end(lines, i):
    """Index of the closing fence for an opener at lines[i], or None."""
    m = _FENCE.match(lines[i])
    if not m:
        return None
    ch, n = m.group(2)[0], len(m.group(2))
    if ch == "`" and "`" in m.group(3):
        return None  # backticks in the info string: not a fence
    close = re.compile(r"^ *" + re.escape(ch) + "{" + str(n) + r",} *$")
    for j in range(i + 1, len(lines)):
        if close.match(lines[j]):
            return j
    return None  # unclosed: stays literal (lint warns)


def _math_end(lines, i):
    """Index of the line closing a `$$` block opened at lines[i], or None."""
    ln = lines[i].strip()
    if not ln.startswith("$$") or (len(ln) > 2 and ln.endswith("$$")):
        return None
    for j in range(i + 1, len(lines)):
        if lines[j].rstrip().endswith("$$"):
            return j
    return None


def _cells(ln):
    """Split a table row on unescaped pipes; None if not row-like."""
    s = ln.strip()
    if "|" not in s:
        return None
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _table(lines, i):
    """-> (header, rows, consumed) or None. Separator cells may carry
    alignment colons and any spacing (alignment is not kept)."""
    if i + 1 >= len(lines):
        return None
    header = _cells(lines[i])
    sep = _cells(lines[i + 1])
    if not header or not sep or len(sep) != len(header) or "|" not in lines[i + 1]:
        return None
    if not all(_SEP_CELL.match(c) for c in sep):
        return None
    rows, j = [], i + 2
    while j < len(lines):
        cells = _cells(lines[j])
        if cells is None or len(cells) != len(header) or _FENCE.match(lines[j]):
            break
        rows.append(cells)
        j += 1
    return header, rows, j - i


def _list_items(lines, i):
    """Parse a run of list item lines starting at lines[i] (indent 0).
    -> (items, consumed) with items = [(depth, ordered, number, text)], or
    None. A child sits at [parent content offset, +3]; numbers must run on
    by one inside every list; a type switch within one level ends the run."""
    items, stack, j = [], [], i  # stack: [indent, offset, ordered, next_no]
    while j < len(lines):
        m = _ITEM.match(lines[j])
        if not m or _HR.match(lines[j]):
            break
        ind, marker, text = len(m.group(1)), m.group(2), m.group(4) or ""
        if not text or _TASK.match(text):
            break
        ordered = marker[-1] == "."
        num = int(marker[:-1]) if ordered else 0
        while stack and ind < stack[-1][0]:
            stack.pop()
        if not stack:
            if ind != 0:
                break
            stack.append([0, 0, ordered, num])
        elif stack[-1][1] <= ind <= stack[-1][1] + 3:
            stack.append([ind, 0, ordered, num])   # child of the last item
        elif not stack[-1][0] <= ind < stack[-1][1]:
            break                                  # neither sibling nor child
        top = stack[-1]
        if top[2] != ordered or (ordered and top[3] != num):
            break
        top[1] = top[0] + len(marker) + 1
        top[3] = num + 1
        items.append((len(stack) - 1, ordered, num, text))
        j += 1
    if not items:
        return None
    return items, j - i


# -- profiles ----------------------------------------------------------------
class Profile:
    """Endpoint dialect. Capability flags come from capabilities.py."""

    def __init__(self, name, headings, blank, hr, code_block):
        self.name = name
        self.headings = headings      # md prefix -> (open, close)
        self.blank = blank
        self.hr = hr
        self.code_block = code_block  # fenced lines -> markup
        self.link = rich(name, "link")
        self.code = rich(name, "code_inline")
        self.quote = rich(name, "blockquote")
        self.ordered = rich(name, "ordered")

    def heading(self, prefix, text):
        op, cl = self.headings[prefix]
        return f"{op}{_esc(text, lead=True)}{cl}"


_EN_CODEBLOCK = ("box-sizing: border-box; padding: 8px; font-family: Monaco, "
                 "Menlo, Consolas, &quot;Courier New&quot;, monospace; "
                 "font-size: 12px; color: rgb(51, 51, 51); border-radius: 4px; "
                 "background-color: rgb(251, 250, 248); border: 1px solid "
                 "rgba(0, 0, 0, 0.15); -en-codeblock: true; --en-codeblock: true;")


def _en_code(lines):
    body = "".join(f"<div>{_esc(ln, lead=True)}</div>" if ln else "<div><br/></div>"
                   for ln in lines)
    return f'<div style="{_EN_CODEBLOCK}">{body}</div>'


def _apple_code(lines):
    # <pre> keeps every space and tab; Apple turns it into one monospaced
    # paragraph per line
    return "<pre>" + html.escape("\n".join(lines), quote=False) + "</pre>"


ENML_PROFILE = Profile(
    "en",
    {"# ": ("<h1>", "</h1>"), "## ": ("<h2>", "</h2>"), "### ": ("<h3>", "</h3>")},
    blank="<div><br/></div>", hr="<hr/>", code_block=_en_code)
# Apple: <h3> degrades to bare <b> (ambiguous with bold) and font sizes below
# 18px are stripped. Italic is no marker either: Apple drops it from CJK
# text. So h3 is a paragraph in bold + underline -- attributes Apple keeps
# on any script, and a combination markdown never produces otherwise.
APPLE_PROFILE = Profile(
    "apple",
    {"# ": ("<h1>", "</h1>"), "## ": ("<h2>", "</h2>"),
     "### ": ("<div><b><u>", "</u></b></div>")},
    blank="<div><br></div>", hr=None, code_block=_apple_code)
PROFILES = {"en": ENML_PROFILE, "apple": APPLE_PROFILE}


# -- block rendering -----------------------------------------------------------
def _heading_text(t):
    # heading lines are implicitly bold on both endpoints: `**` is a no-op
    return _BOLD.sub(r"\1", t.strip())


def _header_cell(t):
    # header cells are bold on both endpoints (Apple forces it) and carry
    # no other emphasis back
    return _ITAL.sub(r"\1", _BOLDITAL.sub(r"\1", _BOLD.sub(r"\1", t)))


def _para(ln, profile):
    if profile.name == "apple" and _only_code(ln):
        return f"<div>{_esc(ln, lead=True)}</div>"  # would become monospaced
    return f"<div>{_inline(ln, profile, lead=True)}</div>"


def _render_list(items, profile):
    """Nested lists in child form: <ul><li>a<ul><li>a1</li></ul></li></ul>
    (Apple rewrites it to sibling form; the flattener reads both)."""
    out, stack = [], []  # open list tags; each level has an open <li>
    for depth, ordered, num, text in items:
        while len(stack) > depth + 1:
            out.append(f"</li></{stack.pop()}>")
        if len(stack) == depth + 1:
            out.append("</li>")
        else:
            tag = "ol" if ordered else "ul"
            start = f' start="{num}"' if ordered and num != 1 else ""
            out.append(f"<{tag}{start}>")
            stack.append(tag)
        out.append(f"<li>{_inline(text, profile, ctx='li')}")
    while stack:
        out.append(f"</li></{stack.pop()}>")
    return "".join(out)


def _list_renderable(items, profile):
    if not profile.ordered and any(o for _, o, _, _ in items):
        return False
    if profile.name == "apple":
        # Apple cannot store a start number: every ordered list opened in
        # the run must begin at 1
        depth_open = 0
        for d, o, n, _ in items:
            if d == depth_open and o and n != 1:
                return False
            depth_open = d + 1
    return True


def _literal(lines, profile):
    return "".join(profile.blank if ln == "" else _para(ln, profile) for ln in lines)


def _blocks(lines):
    """Split md lines into blocks: (kind, lines, data)."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln == "":
            out.append(("blank", [ln], None))
            i += 1
            continue
        end = _fence_end(lines, i) if _FENCE.match(ln) else None
        if end is not None:
            out.append(("code", lines[i:end + 1], None))
            i = end + 1
            continue
        end = _math_end(lines, i) if ln.lstrip().startswith("$$") else None
        if end is not None:
            out.append(("math", lines[i:end + 1], None))
            i = end + 1
            continue
        if "|" in ln:
            t = _table(lines, i)
            if t:
                out.append(("table", lines[i:i + t[2]], t))
                i += t[2]
                continue
        if _QUOTE.match(ln):
            j = i
            while j < len(lines) and _QUOTE.match(lines[j]):
                j += 1
            out.append(("quote", lines[i:j], None))
            i = j
            continue
        if _HR.match(ln):
            out.append(("hr", [ln], None))
            i += 1
            continue
        head = re.match(r"^(#{1,3}) +(\S.*)$", ln)
        if head:
            out.append(("heading", [ln], (head.group(1) + " ", head.group(2))))
            i += 1
            continue
        if _ITEM.match(ln):
            got = _list_items(lines, i)
            if got:
                out.append(("list", lines[i:i + got[1]], got[0]))
                i += got[1]
                continue
        out.append(("para", [ln], None))
        i += 1
    return out


def _ordered_groups(blocks):
    """Apple cannot resume numbering. An ordered list continued after a
    paragraph or blank line (`1. a` / text / `2. b`) is one group: its
    first part could render but the rest could not, so on Apple the whole
    group stays literal. -> set of block indices in multi-part groups."""
    bad, members, nxt = set(), [], None
    for k, (kind, _, data) in enumerate(blocks):
        if kind == "list":
            first = data[0]
            if first[1] and nxt is not None and first[2] == nxt and nxt != 1:
                members.append(k)
                bad.update(members)
            else:
                members = [k] if first[1] else []
            tops = [n for d, o, n, _ in data if d == 0]
            nxt = tops[-1] + 1 if first[1] else None
        elif kind in ("heading", "hr"):
            members, nxt = [], None
    return bad


def render_lines(lines, profile):
    blocks = _blocks(lines)
    apple_bad = _ordered_groups(blocks) if profile.name == "apple" else set()
    out = []
    prev = None
    for k, (kind, blines, data) in enumerate(blocks):
        if kind == "blank":
            out.append(profile.blank)
            if profile.name == "apple" and prev == "table":
                # Apple absorbs the first blank paragraph after a table
                out.append(profile.blank)
        elif kind == "code":
            if rich(profile.name, "code_block"):
                out.append(profile.code_block(blines))
            else:
                out.append(_literal(blines, profile))
        elif kind == "math":
            # TeX stays literal and unparsed (`+ x` is not a list item)
            out.append("".join(profile.blank if ln == "" else
                               f"<div>{_esc(ln, lead=True)}</div>" for ln in blines))
        elif kind == "table":
            header, rows, _ = data
            t = ["<table><tr>"]
            t += [f"<th>{_inline(_header_cell(c), profile, ctx='cell')}</th>"
                  for c in header]
            t.append("</tr>")
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{_inline(c, profile, ctx='cell')}</td>"
                                          for c in r) + "</tr>")
            t.append("</table>")
            out.append("".join(t))
        elif kind == "quote":
            if profile.quote:
                inner = [_QUOTE.match(q).group(2) for q in blines]
                out.append("<blockquote>" + render_lines(inner, profile)
                           + "</blockquote>")
            else:
                out.append(_literal(blines, profile))
        elif kind == "hr":
            out.append(profile.hr if rich(profile.name, "hr") else _literal(blines, profile))
        elif kind == "heading":
            prefix, text = data
            out.append(profile.heading(prefix, _heading_text(text)))
        elif kind == "list":
            if k in apple_bad or not _list_renderable(data, profile) or (
                    profile.name == "apple" and prev == "list"):
                out.append(_literal(blines, profile))
            else:
                out.append(_render_list(data, profile))
        else:
            out.append(_para(blines[0], profile))
        prev = kind
    return "".join(out)


def render(md, profile):
    """markdown body -> markup fragment for the endpoint."""
    lines = md.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return render_lines(lines, profile)
