"""3-way merge via `git merge-file` (DESIGN.md #7-8).

Non-overlapping edits merge cleanly; on a same-line conflict the *ours* side
(callers pass the newer version) wins that hunk.
"""
import os
import subprocess
import tempfile


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
