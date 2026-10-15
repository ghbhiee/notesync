"""Endpoint rich markup -> canonical markdown (inverse of render.py).

One parser serves both dialects: Evernote ENML and the HTML Apple Notes
returns. The document is collected as lines of formatted text runs and only
serialized at the end, so fragmented formatting (Apple splits bold/italic
runs at every script change: <i>CODEX </i><i>整理</i>) merges back into
one span, and junk-only fragments (<b><br></b>) never surface as `**`.

Lines are decided per paragraph:
- code lines: inside an Evernote code block / <pre>, or an Apple paragraph
  whose text is entirely monospaced (<tt>). They are emitted verbatim; a
  run of them that does not start with a fence (a block the user made in
  the endpoint) is wrapped in ``` fences, an unclosed fence is closed.
- headings: <h1..h6>, or Apple's bold 24px / 18px, and h3 as a paragraph
  in bold + underline (older copies: 18px italic)
- list items: marker + indentation from the enclosing <ul>/<ol> stack, in
  child form (<li>a<ul>..</ul></li>) or Apple's sibling form (<ul><li>a
  </li><ul>..</ul></ul>)
- quote lines get `> ` per <blockquote> level
- tables become canonical pipe rows
Everything else is a paragraph with inline `**` / `*` / `***` / `` ` `` /
links.
"""
import re
from html.parser import HTMLParser

_H_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ",
             "h5": "##### ", "h6": "###### "}
_APPLE_H = {"24px": "# ", "18px": "## "}  # Apple stores h1/h2 as font sizes
_MONO = re.compile(r"courier|menlo|monaco|sf ?mono|consolas|andale|lucida console|"
                   r"pt mono|source code|fira (code|mono)|jetbrains|monospace", re.I)
_BLOCK = {"div", "p", "en-note", "body", "center"}
_INLINE = {"b", "strong", "i", "em", "code", "tt", "kbd", "samp", "a", "font",
           "span", "u", "s", "strike", "del", "sup", "sub", "small", "big"}
_FENCE_OPEN = re.compile(r"^ *(`{3,}|~{3,})(.*)$")
# Apple writes &amp; &lt; &gt; &quot; WITHOUT the semicolon, so a literal ';'
# that follows one of them would be swallowed by a standard parser
_APPLE_ENTITY = re.compile(r"&(amp|lt|gt|quot)(?![a-zA-Z0-9])")
_APPLE_CODEPOINT = {"amp": "&#38;", "lt": "&#60;", "gt": "&#62;", "quot": "&#34;"}


def apple_entities(raw):
    """Pin Apple's semicolon-less entities to explicit code points."""
    return _APPLE_ENTITY.sub(lambda m: _APPLE_CODEPOINT[m.group(1)], raw)


class _Line:
    __slots__ = ("runs", "head", "marker", "tt_blank")

    def __init__(self):
        self.runs = []        # [text, (bold, italic, code, tt, href, underline)]
        self.head = ""        # heading prefix
        self.marker = None    # list marker, on an item's first line
        self.tt_blank = False  # a monospaced blank paragraph

    def empty(self):
        return not self.runs


def _inline_md(runs, heading=False, plain_emphasis=False):
    """Serialize text runs. Emphasis does not nest in the canonical dialect:
    adjacent emphasized runs share the markers they have in common, and
    whitespace at a span's edge is moved outside its markers."""
    merged = []
    for text, a in runs:
        if merged and merged[-1][1] == a:
            merged[-1][0] += text
        elif text:
            merged.append([text, a])
    out = []
    k = 0
    while k < len(merged):
        text, (b, i, code, tt, href, _u) = merged[k]
        if href:
            j = k
            while j < len(merged) and merged[j][1][4] == href:
                j += 1
            inner = _inline_md([(t, a[:4] + (None,) + a[5:]) for t, a in merged[k:j]],
                               heading, plain_emphasis)
            out.append(f"[{inner}]({href})")
            k = j
            continue
        emph = not (heading or plain_emphasis) and (b or i)
        if not emph:
            out.append(_code(text) if (code or tt) else text)
            k += 1
            continue
        # maximal group of adjacent emphasized runs
        j, common = k, {"b", "i"}
        while j < len(merged) and not merged[j][1][4]:
            bb, ii = merged[j][1][0], merged[j][1][1]
            if not (bb or ii):
                break
            have = {x for x, on in (("b", bb), ("i", ii)) if on}
            if not common & have:
                break
            common &= have
            j += 1
        mark = {frozenset({"b", "i"}): "***", frozenset({"b"}): "**",
                frozenset({"i"}): "*"}[frozenset(common)]
        body = "".join(_code(t) if (a[2] or a[3]) else t for t, a in merged[k:j])
        lead = len(body) - len(body.lstrip(" "))
        trail = len(body) - len(body.rstrip(" "))
        core = body.strip(" ")
        if core:
            out.append(" " * lead + mark + core + mark + " " * trail)
        else:
            out.append(body)
        k = j
    return "".join(out)


def _code(text):
    n = max((len(r) for r in re.findall(r"`+", text)), default=0)
    return "`" * (n + 1) + text + "`" * (n + 1)


class Flattener(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # finished lines: (prefix, text, is_code)
        self.cur = _Line()
        self.fx = []           # open inline tags: (tag, effect)
        self.quote = 0
        self.lists = []        # [{"ordered", "n", "indent", "mlen"}]
        self.divs = []         # per open <div>: True if it is a code block
        self.code = 0          # inside an Evernote code block / <pre>
        self.pre = 0
        self.table = None      # {"row", "cells", "cell"} while in a table
        self.swallow_br = False

    # -- attribute state ------------------------------------------------
    def _attrs(self):
        b = i = code = tt = u = False
        href = None
        for _, e in self.fx:
            if e == "b":
                b = True
            elif e == "i":
                i = True
            elif e == "code":
                code = True
            elif e == "tt":
                tt = True
            elif e == "u":
                u = True
            elif isinstance(e, tuple):
                href = e[1]
        return (b, i, code, tt, href, u)

    def _prefix(self, line):
        p = "> " * self.quote
        if self.lists:
            lst = self.lists[-1]
            p += " " * lst["indent"]
            p += line.marker if line.marker is not None else " " * lst["mlen"]
        return p

    # -- line lifecycle ---------------------------------------------------
    def _finish(self, force=False):
        line = self.cur
        if line.empty() and not force:
            return  # nothing to end (an item still waiting for its text)
        in_item = bool(self.lists)
        texts = [a for t, a in line.runs if t.strip()]
        if (not line.head and not in_item and not self.code and texts
                and all(a[0] and a[5] for a in texts)):
            line.head = "### "  # Apple h3: a paragraph in bold + underline
        prefix = self._prefix(line)
        tt_only = (line.runs and all(a[3] for t, a in line.runs if t.strip())
                   and any(t.strip() for t, _ in line.runs))
        if self.code or ((tt_only or line.tt_blank) and not line.head
                         and not in_item):
            text = "".join(t for t, _ in line.runs)
            self.out.append(("> " * self.quote, text, True))
        elif line.head:
            self.out.append((prefix, line.head + _inline_md(line.runs, heading=True)
                             .strip(), False))
        else:
            self.out.append((prefix, _inline_md(line.runs), False))
        self.cur = _Line()

    def _block(self):
        """A block boundary: end the current line if it holds anything."""
        if not self.cur.empty():
            self._finish()

    # -- tables -------------------------------------------------------------
    def _table_start(self, tag, attrs):
        if tag == "table":
            self._block()
            self.table = {"row": 0, "cells": [], "cell": None}
            return True
        if self.table is None:
            return False
        if tag == "tr":
            self.table["cells"] = []
        elif tag in ("td", "th"):
            self.table["cell"] = []
        elif tag in _INLINE:
            return False  # formatting inside cells is tracked normally
        return True       # wrappers (tbody, object, div, br, ...) swallowed

    def _table_end(self, tag):
        t = self.table
        if t is None:
            return False
        if tag in ("td", "th"):
            runs = t["cell"] or []
            text = _inline_md(runs, plain_emphasis=t["row"] == 0).strip()
            t["cells"].append(re.sub(r"(?<!\\)\|", r"\\|", text))
            t["cell"] = None
        elif tag == "tr":
            cells = t["cells"]
            p = self._prefix(_Line())
            row = "|" + "|".join(f" {c} " if c else " " for c in cells) + "|"
            self.out.append((p, row, False))  # an empty cell is "| |"
            if t["row"] == 0:
                self.out.append((p, "|" + "---|" * len(cells), False))
            t["row"] += 1
        elif tag == "table":
            self.table = None
            self.swallow_br = True  # Apple: <object><table>..</table></object><br>
        elif tag in _INLINE:
            return False
        return True

    # -- parser callbacks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag == "br" and self.swallow_br and self.cur.empty():
            self.swallow_br = False
            return  # the <br> Apple appends to the div wrapping a table
        self.swallow_br = False
        a = dict(attrs)
        if self._table_start(tag, a):
            return
        if tag in _INLINE:
            self._inline_start(tag, a)
        elif tag == "br":
            if self.cur.empty() and self._attrs()[3]:
                self.cur.tt_blank = True
            self._finish(force=True)
        elif tag == "div":
            self._block()
            cb = "en-codeblock" in (a.get("style") or "")
            self.divs.append(cb)
            self.code += cb
        elif tag in _BLOCK:
            self._block()
        elif tag == "pre":
            self._block()
            self.pre += 1
            self.code += 1
        elif tag in _H_PREFIX:
            self._block()
            self.cur.head = _H_PREFIX[tag]
        elif tag in ("ul", "ol"):
            self._block()
            parent = self.lists[-1] if self.lists else None
            indent = parent["indent"] + parent["mlen"] if parent else 0
            try:
                start = int(a.get("start") or 1)
            except ValueError:
                start = 1
            self.lists.append({"ordered": tag == "ol", "n": start - 1,
                               "indent": indent, "mlen": 2})
        elif tag == "li":
            self._block()
            if not self.lists:
                self.lists.append({"ordered": False, "n": 0, "indent": 0,
                                   "mlen": 2, "stray": True})
            lst = self.lists[-1]
            lst["n"] += 1
            marker = f"{lst['n']}. " if lst["ordered"] else "- "
            lst["mlen"] = len(marker)
            self.cur.marker = marker
        elif tag == "blockquote":
            self._block()
            self.quote += 1
        elif tag == "hr":
            self._block()
            self.out.append(("> " * self.quote, "---", False))
        elif tag == "en-todo":
            box = "[x] " if a.get("checked") == "true" else "[ ] "
            self._data("- " + box if not self.lists else box)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "hr", "en-todo", "img", "col"):
            self.handle_endtag(tag)

    def _inline_start(self, tag, a):
        eff = None
        if tag in ("b", "strong"):
            eff = "b"
        elif tag in ("i", "em"):
            eff = "i"
        elif tag in ("code", "kbd", "samp"):
            eff = "code"
        elif tag == "tt":
            eff = "tt"
        elif tag == "u":
            eff = "u"
        elif tag == "a":
            eff = ("href", a.get("href"))
        elif tag == "font":
            if _MONO.search(a.get("face") or ""):
                eff = "code"
        elif tag == "span":
            style = a.get("style") or ""
            m = re.search(r"font-size:\s*(\d+px)", style)
            if (m and m.group(1) in _APPLE_H and self.cur.empty()
                    and not self.cur.head and not self.lists and not self.table
                    and not self.code):
                # Apple rewrites our h1/h2 into <b><span font-size>; 18px
                # opened together with <i> is our h3 encoding
                p = _APPLE_H[m.group(1)]
                if m.group(1) == "18px" and self._attrs()[1]:
                    p = "### "
                self.cur.head = p
            if re.search(r"font-family:[^;]*(" + _MONO.pattern + ")", style, re.I):
                eff = "code"
        self.fx.append((tag, eff))

    def handle_endtag(self, tag):
        if self._table_end(tag):
            return
        if tag in _INLINE:
            for k in range(len(self.fx) - 1, -1, -1):
                if self.fx[k][0] == tag:
                    del self.fx[k]
                    break
        elif tag == "div":
            self._block()
            if self.divs and self.divs.pop():
                self.code -= 1
        elif tag in _BLOCK or tag in _H_PREFIX:
            self._block()
        elif tag == "pre":
            self._block()
            self.pre -= 1
            self.code -= 1
        elif tag in ("ul", "ol"):
            self._block()
            if self.lists:
                self.lists.pop()
        elif tag == "li":
            self._block()
            if self.lists and self.lists[-1].get("stray"):
                self.lists.pop()
        elif tag == "blockquote":
            self._block()
            self.quote = max(0, self.quote - 1)

    def handle_data(self, data):
        if not self.pre and "\n" in data and not data.strip():
            return  # whitespace between tags (Apple pretty-prints), not content
        self.swallow_br = False
        self._data(data.replace("\xa0", " "))

    def _data(self, data):
        if self.table is not None:
            if self.table["cell"] is not None:
                self.table["cell"].append((data.replace("\n", " "), self._attrs()))
            return
        attrs = self._attrs()
        for k, part in enumerate(data.split("\n")):
            if k:
                self._finish(force=True)
            if part:
                self.cur.runs.append((part, attrs))

    # -- result ---------------------------------------------------------------
    def result(self):
        self._finish()
        lines, k, out = self.out, 0, []
        while k < len(lines):
            prefix, text, is_code = lines[k]
            if not is_code:
                out.append((prefix + text).rstrip())
                k += 1
                continue
            j = k
            while j < len(lines) and lines[j][2] and lines[j][0] == prefix:
                j += 1
            out += [(prefix + t).rstrip() for t in _fence_run([t for _, t, _ in lines[k:j]])]
            k = j
        while out and out[-1] == "":
            out.pop()
        return ("\n".join(out) + "\n") if out else ""


def _fence_run(texts):
    """A run of verbatim code lines -> well-formed fenced blocks."""
    out, k = [], 0
    while k < len(texts):
        m = _FENCE_OPEN.match(texts[k])
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            ch, n = m.group(1)[0], len(m.group(1))
            close = re.compile(r"^ *" + re.escape(ch) + "{" + str(n) + r",} *$")
            j = next((j for j in range(k + 1, len(texts)) if close.match(texts[j])),
                     None)
            if j is None:
                out += texts[k:] + [ch * n]
                break
            out += texts[k:j + 1]
            k = j + 1
            continue
        j = k
        while j < len(texts) and not _FENCE_OPEN.match(texts[j]):
            j += 1
        chunk = texts[k:j]
        if any(t.strip() for t in chunk):
            out += ["```"] + chunk + ["```"]
        else:
            out += chunk  # stray monospaced blank lines are just blank lines
        k = j
    return out


def flatten(markup):
    f = Flattener()
    f.feed(markup)
    f.close()
    return f.result()
