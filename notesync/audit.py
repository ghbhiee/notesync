"""Fidelity audit: does every note survive a round trip through each endpoint?

offline  flatten(render(md)) == md per endpoint profile, over the live corpus
         (the invariant tests/test_render.py enforces on a fixed matrix).
--live   the real thing: each note is created in a probe container -- an
         Apple Notes folder / Evernote notebook outside every mapping, so the
         engine never sees it -- read back through the adapter (the same path
         the engine takes, including the endpoint's own rewriting), diffed,
         and deleted. Apple notes are read only after they settle: Apple
         rewrites a new note asynchronously, and the settled form is the one
         that lasts.

Verdicts per note x endpoint:
  ok      read-back == md
  canon   read-back == the canonical form of md (lenient syntax accepted by
          the renderer, e.g. a table separator with alignment colons);
          meaning is intact, only the spelling differs
  LOSSY   anything else -- the endpoint copy lost or changed content
"""
import difflib
import time
from pathlib import Path

from .normalize import normalize, split_fm


def _files(cfg, only=None):
    from .engine import IGNORED_FILES
    store = Path(cfg["store"])
    ignored = IGNORED_FILES | set(cfg.get("ignore_files", []))
    out = []
    for m in cfg["mappings"]:
        folder = store / m["folder"]
        for p in sorted(folder.glob("*.md")):
            if p.name in ignored:
                continue
            if only and not any(o in p.stem for o in only):
                continue
            out.append(p)
    return out


def _body(path):
    _, body = split_fm(path.read_text(encoding="utf-8"))
    return normalize(body)


def offline(md):
    """-> {endpoint: flatten(render(md))} for every profile."""
    from .apple import html_to_md, model_store
    from .evernote import enml_to_md
    from .render import APPLE_PROFILE, ENML_PROFILE, render
    return {"apple": normalize(html_to_md(model_store(render(md, APPLE_PROFILE)))),
            "en": normalize(enml_to_md(
                "<en-note>" + render(md, ENML_PROFILE) + "</en-note>"))}


def verdict(md, back):
    if back == md:
        return "ok"
    from .lint import canonical
    if back == canonical(md):
        return "canon"
    return "LOSSY"


def diff(a, b, name_a="file", name_b="endpoint"):
    return list(difflib.unified_diff(a.split("\n"), b.split("\n"), name_a,
                                     name_b, lineterm="", n=0))


class _Live:
    """Probe containers for real round trips."""

    SETTLE = 25  # seconds: Apple rewrites a new note asynchronously

    def __init__(self, cfg, apple_folder, en_notebook, which):
        self.eps = {}
        if "apple" in which and apple_folder:
            from .apple import AppleEndpoint
            self.eps["apple"] = AppleEndpoint(apple_folder)
        if "en" in which and en_notebook:
            from .evernote import EvernoteEndpoint
            self.eps["en"] = EvernoteEndpoint(
                en_notebook, stderr_path=str(Path(cfg["state"]) / "mcp-remote.log"))

    def roundtrips(self, name, items, log=lambda m: None):
        """items: [(title, md)] -> [read-back md or Exception]. Apple notes
        are all created first, read once they have settled, then deleted;
        Evernote stores verbatim, one note at a time."""
        ep = self.eps[name]
        if name != "apple":
            out = []
            for title, md in items:
                try:
                    nid = ep.create(f"ns-audit {title}"[:80], md)
                    try:
                        got = ep.fetch(nid)
                    finally:
                        ep.delete(nid)
                    out.append(normalize(got[1] or "") if got else
                               RuntimeError("probe note vanished"))
                except Exception as e:  # keep auditing the rest
                    out.append(e)
            return out
        nids = []
        for title, md in items:
            try:
                nids.append(ep.create(f"ns-audit {title}"[:80], md))
            except Exception as e:
                nids.append(e)
        log(f"(waiting {self.SETTLE}s for Apple Notes to settle)")
        time.sleep(self.SETTLE)
        out = []
        for nid in nids:
            if isinstance(nid, Exception):
                out.append(nid)
                continue
            try:
                got = ep.fetch(nid)
                out.append(normalize(got[1] or "") if got else
                           RuntimeError("probe note vanished"))
            except Exception as e:
                out.append(e)
            finally:
                try:
                    ep.delete(nid)
                except Exception:
                    pass
        return out

    def close(self):
        if "en" in self.eps:
            self.eps["en"].close()


def run(cfg, live=False, which=("apple", "en"), only=None, show_diff=False,
        apple_folder=None, en_notebook=None, out=print):
    """-> {(file, endpoint): verdict}. Prints a report through `out`."""
    results = {}
    files = _files(cfg, only)
    probe = None
    if live:
        apple_folder = apple_folder or cfg.get("probe_apple_folder")
        en_notebook = en_notebook or cfg.get("probe_en_notebook")
        if ("apple" in which and not apple_folder) or ("en" in which and not en_notebook):
            raise SystemExit("audit --live needs probe containers outside every "
                             "mapping: set probe_apple_folder / probe_en_notebook "
                             "in config.json or pass --apple-folder/--en-notebook")
        probe = _Live(cfg, apple_folder, en_notebook, which)
    try:
        mds = [(p, _body(p)) for p in files]
        live_back = {}
        if probe:
            for name in which:
                backs = probe.roundtrips(name, [(p.stem, md) for p, md in mds], out)
                for (p, _), back in zip(mds, backs):
                    live_back[(p.name, name)] = back
        for p, md in mds:
            rts = offline(md)
            cells = []
            for name in which:
                back, src = rts[name], "offline"
                if probe:
                    back, src = live_back[(p.name, name)], "live"
                    if isinstance(back, Exception):
                        results[(p.name, name)] = "ERROR"
                        cells.append(f"{name}=ERROR({str(back)[:60]})")
                        continue
                v = verdict(md, back)
                results[(p.name, name)] = v
                d = diff(md, back, "file", f"{name}:{src}") if v != "ok" else []
                changed = sum(1 for ln in d[2:] if ln[:1] in "+-")
                cells.append(f"{name}={v}" + (f"({changed})" if changed else ""))
                if show_diff and d:
                    for ln in d:
                        out("    " + ln)
            out(f"{'  '.join(cells):<40} {p.stem}")
    finally:
        if probe:
            probe.close()
    tally = {}
    for v in results.values():
        tally[v] = tally.get(v, 0) + 1
    out("summary: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    return results
