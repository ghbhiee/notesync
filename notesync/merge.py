"""3-way merge via `git merge-file` (DESIGN.md #7-8), and the rebase that
carries an edit made in a lossy endpoint copy back onto the canonical text.

Non-overlapping edits merge cleanly; on a same-line conflict the *ours* side
(callers pass the newer version) wins that hunk.
"""
import os
import re
import subprocess
import tempfile
from difflib import SequenceMatcher


def merge3(base: str, ours: str, theirs: str):
    """-> (merged_text, had_conflict). `ours` is favored on conflicting hunks."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            paths.append(p)
        po, pb, pt = paths
        r = subprocess.run(["git", "merge-file", "-p", po, pb, pt],
                           capture_output=True, encoding="utf-8")
        if r.returncode == 0:
            return r.stdout, False
        r2 = subprocess.run(["git", "merge-file", "-p", "--ours", po, pb, pt],
                            capture_output=True, encoding="utf-8")
        return r2.stdout, True


def _key(line):
    return " ".join(line.split())


def _collapsed(line):
    """What an HTML round trip does to a line: runs of spaces become one,
    leading indentation is dropped."""
    return re.sub(r" {2,}", " ", line).lstrip(" ")


def rebase(shadow: str, now: str, canon: str):
    """Carry an endpoint edit onto the canonical text.

    `shadow` is what the endpoint held after our last write -- `canon`
    itself, or a lossy copy of it (collapsed whitespace, a dropped
    formatting mark...). `now` is what it holds after the user edited it.
    Returns (text, kept_lossy): canon with exactly the user's changes
    applied. Lines the user did not touch
    keep their canonical form, even right next to an edited line (a textual
    3-way merge would call that a conflict). Only where an edit lands inside
    a stretch that differs from canon beyond whitespace is the endpoint's
    own text kept for that stretch (kept_lossy=True) -- the user's edit is
    never dropped."""
    S, N, C = shadow.split("\n"), now.split("\n"), canon.split("\n")

    # A line that only lost whitespace (collapsed runs, stripped indent) is
    # not an edit: that is what an HTML round trip does to text, never what
    # a person does to code. Such lines count as unchanged.
    ws = SequenceMatcher(a=[_key(x) for x in S], b=[_key(x) for x in N],
                         autojunk=False)
    N = list(N)
    for tag, i1, i2, j1, j2 in ws.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                if N[j1 + k] != S[i1 + k] and N[j1 + k] == _collapsed(S[i1 + k]):
                    N[j1 + k] = S[i1 + k]

    # edits the user made: shadow -> now, exact
    op_of = {}          # S line -> index of the edit op covering it
    ins = {}            # S position -> N lines inserted before it
    ops = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=S, b=N, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:
            ins.setdefault(i1, []).extend(N[j1:j2])
            continue
        ops.append((i1, i2, N[j1:j2]))
        for k in range(i1, i2):
            op_of[k] = len(ops) - 1

    def inside_op(pos):  # strictly inside an edit op's range
        return any(i1 < pos < i2 for i1, i2, _ in ops)

    out, done_ops, kept_lossy = [], set(), False

    def emit_s(k, fallback):
        """S line k: the user's replacement if edited, else `fallback`."""
        out.extend(ins.pop(k, []))
        if k in op_of:
            o = op_of[k]
            if o not in done_ops:
                done_ops.add(o)
                i1, i2, repl = ops[o]
                if i2 - i1 == 1 and len(repl) == 1 and fallback is not None:
                    repl = [_reindent(S[k], repl[0], fallback)]
                out.extend(repl)
        elif fallback is not None:
            out.append(fallback)

    # how shadow lines correspond to canonical lines, ignoring whitespace
    sm = SequenceMatcher(a=[_key(x) for x in S], b=[_key(x) for x in C],
                         autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                emit_s(i1 + k, C[j1 + k])
        elif i1 == i2:  # canonical-only lines (the endpoint dropped them)
            if not inside_op(i1):
                out.extend(C[j1:j2])
        else:           # a stretch that differs beyond whitespace
            owned = (any(k in op_of for k in range(i1, i2))
                     or any(k in ins for k in range(i1 + 1, i2)))
            if not owned:
                out.extend(ins.pop(i1, []))
                out.extend(C[j1:j2])
            else:
                kept_lossy = True
                for k in range(i1, i2):
                    if k in op_of:
                        emit_s(k, None)
                    else:
                        out.extend(ins.pop(k, []))
                        out.append(S[k])
    out.extend(ins.pop(len(S), []))
    return "\n".join(out), kept_lossy


def _reindent(old, new, canon_line):
    """A one-line edit of a line whose indentation the endpoint lost gets
    the canonical indentation back."""
    lead = len(canon_line) - len(canon_line.lstrip(" "))
    if lead and not old.startswith(" ") and not new.startswith(" "):
        return " " * lead + new
    return new
