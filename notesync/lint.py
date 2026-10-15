"""notesync lint: does a markdown note stay within the NoteSync profile?

Two kinds of findings:
- fix   the note uses a lenient spelling (table separator with colons,
        `* ` bullets, `**` in a heading, ...). It renders fine, but the
        endpoints hand back the canonical spelling, so the file and the
        endpoint copies differ in form. `lint --fix` rewrites the file to
        the canonical spelling -- the exact text a round trip through the
        richest endpoint produces, so fixing never changes meaning.
- warn  the note uses something that will not render (h4, raw HTML, an
        unclosed fence, ASCII art outside a code block, ...). Advice comes
        from the capability registry; a human or agent decides.

The canonical form is defined by the renderer + flattener pair itself
(`canonical(md) = flatten(render(md, Evernote))`), so lint can never
disagree with what sync does.
"""
import re

from .capabilities import BY_KEY

_BOX = re.compile(r"[┌┐└┘│─├┤┬┴┼▼▲►◄]|\+-{3,}\+")
_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^>]*)?/?>")
_FENCE = re.compile(r"^ *(`{3,}|~{3,})(.*)$")


def canonical(md):
    from .flatten import flatten
    from .render import ENML_PROFILE, render
    return flatten(render(md, ENML_PROFILE))


def _title_h1(md, title):
    """Line index of a leading `# <title>` that repeats the note title."""
    if not title:
        return None
    lines = md.split("\n")
    for k, ln in enumerate(lines):
        if not ln.strip():
            continue
        m = re.match(r"^# +(.+?)\s*$", ln)
        if m and _key(m.group(1)) == _key(title):
            return k
        return None
    return None


def _key(s):
    return re.sub(r"[\s*_`#:：\-—()（）]+", "", s).lower()


def fix(md, title=None):
    """Canonical spelling; with `title`, also drop a leading H1 that repeats
    the title (endpoints show the title already)."""
    out = canonical(md)
    k = _title_h1(out, title)
    if k is not None:
        lines = out.split("\n")
        del lines[k]
        while k < len(lines) and lines[k] == "":
            del lines[k]
        out = "\n".join(lines)
    return out


def _kind(before, after):
    """Name the canonicalization that turned `before` into `after`."""
    b = before.strip()
    if re.match(r"^\|?\s*:?-+", b) and "|" in b:
        return "table separator"
    if b.startswith("|"):
        return "table row spacing" if "**" not in b or "**" in after else "table header emphasis"
    if b.startswith("#"):
        return "heading"
    if re.match(r"^\* ", b):
        return "bullet marker"
    if re.match(r"^([-*_] *){3,}$", b):
        return "horizontal rule"
    if b.startswith(">"):
        return "quote marker"
    if re.match(r"^( *)([-*]|\d+\.) ", before):
        return "list indentation / marker spacing"
    return "spelling"


def check(md, title=None):
    """-> [(line_no, severity, rule, message)], 1-based line numbers."""
    out = []
    lines = md.split("\n")
    canon = canonical(md)
    clines = canon.split("\n")
    if canon != md:
        import difflib
        sm = difflib.SequenceMatcher(a=lines, b=clines, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            for k in range(i1, i2):
                after = clines[j1 + (k - i1)] if j1 + (k - i1) < j2 else ""
                out.append((k + 1, "fix", _kind(lines[k], after),
                            f"canonical: {after!r}" if after else "canonical: (removed)"))
    h1 = _title_h1(md, title)
    if h1 is not None:
        out.append((h1 + 1, "fix", "title heading",
                    "repeats the note title; endpoints already show the title"))

    fence = None
    math = False
    prev = ""
    for n, ln in enumerate(lines, 1):
        m = _FENCE.match(ln)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not m.group(2).strip():
                fence = None
            prev = ln
            continue
        if m:
            fence = m.group(1)
            if not m.group(2).strip():
                out.append((n, "warn", "fence language",
                            "give the code block a language (`text` for plain text)"))
            prev = ln
            continue
        if ln.strip().startswith("$$") and not (len(ln.strip()) > 2 and ln.strip().endswith("$$")):
            math = not math
        if re.match(r"^#{4,6} ", ln):
            out.append((n, "warn", "heading4", BY_KEY["heading4"].advice))
        if _BOX.search(ln) and not math:
            out.append((n, "warn", "ascii art",
                        "diagram outside a code block renders in a proportional "
                        "font; use ```mermaid (or at least a fenced block)"))
        if _TAG.search(re.sub(r"`[^`]*`", "", ln)):
            out.append((n, "warn", "raw html", BY_KEY["html"].advice))
        if "\t" in ln:
            out.append((n, "warn", "tab", BY_KEY["whitespace"].advice))
        if re.match(r"^ {0,3}-{3,} *$", ln) and prev.strip() and not prev.startswith("|"):
            out.append((n, "warn", "setext", BY_KEY["hr"].advice))
        if re.search(r"(^|\W)__[^_\s][^_]*__(\W|$)|(^|[\s(])_[^_\s][^_]*_([\s).,;:!?]|$)", ln) \
                and "$" not in ln:
            out.append((n, "warn", "underscore emphasis", BY_KEY["emphasis"].advice))
        if re.search(r"!\[[^\]]*\]\([^)]+\)", ln):
            out.append((n, "info", "image", BY_KEY["image"].note))
        prev = ln
    if fence is not None:
        out.append((len(lines), "warn", "unclosed fence",
                    "a code fence is never closed; the rest of the note stays "
                    "literal (and is code in other markdown apps)"))
    return sorted(out)


def coalesce(findings):
    """Merge runs of the same finding on consecutive lines:
    -> [(first_line, last_line, severity, rule, message)]."""
    out = []
    for line, sev, rule, msg in findings:
        if out and out[-1][2:] == (sev, rule, msg) and line == out[-1][1] + 1:
            out[-1] = (out[-1][0], line) + out[-1][2:]
        else:
            out.append((line, line, sev, rule, msg))
    return out


def lint_file(path, write=False):
    """-> (findings, changed); line numbers refer to the file as stored.
    Keeps the `ns:` front matter."""
    from pathlib import Path
    from .normalize import join_fm, normalize, split_fm
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    uid, body = split_fm(raw)
    md = normalize(body)
    offset = raw[:len(raw) - len(body)].count("\n")
    for ln in body.split("\n"):
        if ln.strip():
            break
        offset += 1  # leading blank lines are dropped by normalize
    findings = [(f[0] + offset,) + tuple(f[1:]) for f in check(md, p.stem)]
    if re.match(r"\A---\n.*?\n---\n", md, re.S):
        findings.insert(0, (1 + offset, "warn", "front matter",
                            "YAML front matter is synced as note text; the file "
                            "name is the title"))
    changed = False
    if write:
        new = fix(md, p.stem)
        if new != md:
            p.write_text(join_fm(uid, new) if uid else new, encoding="utf-8")
            changed = True
    return findings, changed
