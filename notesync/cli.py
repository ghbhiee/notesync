"""notesync CLI: init / status / sync / watch / mail / rerender / audit /
lint / profile / preview (DESIGN.md #10, #12).

M1 scope: file endpoint only -- versioned canonical store with history.
Note-app endpoints arrive in M2 (Evernote) and M3 (Apple Notes).
"""
import argparse
import json
import sys
import time
from pathlib import Path

from .engine import sync_once
from .state import DB
from .store import GitStore

ICLOUD = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
DEFAULT_STATE = Path("~/Documents/NoteSync").expanduser()
DEFAULT_CONFIG = DEFAULT_STATE / "config.json"
DEFAULT_CFG = {
    "store": "iCloud Drive/SYNC",
    "state": "~/Documents/NoteSync",
    "poll_apple_seconds": 120,
    "poll_evernote_seconds": 300,
    "gdrive_mirror": None,
    "gdrive_enabled": True,
    "mappings": [{"name": "sync", "folder": "", "apple_folder": "SYNC",
                  "apple_enabled": True, "en_notebook": "SYNC",
                  "en_enabled": True}],
    # mail-to-note import (mailin.py); every other key has a default there
    "mail_import": {"enabled": False, "address": ""},
}


def tlog(msg):
    print(time.strftime("[%m-%d %H:%M:%S]"), msg, flush=True)


def load_cfg(path):
    cfg = dict(DEFAULT_CFG)
    cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    store = cfg["store"]
    if store.startswith("iCloud Drive/"):
        store = str(ICLOUD / store[len("iCloud Drive/"):])
    cfg["store"] = str(Path(store).expanduser())
    cfg["state"] = str(Path(cfg["state"]).expanduser())
    return cfg


def cmd_init(args):
    cfg_path = Path(args.config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULT_CFG, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"wrote {cfg_path}")
    cfg = load_cfg(cfg_path)
    git = GitStore(Path(cfg["state"]) / "repo.git", cfg["store"])
    git.ensure()
    DB(Path(cfg["state"]) / "state.db").close()
    print(f"store : {cfg['store']}")
    print(f"state : {cfg['state']}")
    print("initialized. reminder: Finder > right-click state folder > Keep Downloaded")


def build_eps(cfg):
    """-> (eps_by_mapping, closer). Endpoints per mapping; one shared
    mcp-remote child serves every Evernote mapping."""
    eps_by_mapping, clients = {}, []
    shared = None
    for m in cfg["mappings"]:
        eps = {}
        if m.get("apple_folder") and m.get("apple_enabled", True):
            from .apple import AppleEndpoint
            eps["apple"] = AppleEndpoint(m["apple_folder"],
                                         m.get("apple_account", "iCloud"))
        if m.get("en_notebook") and m.get("en_enabled", True):
            from .evernote import MCP_CMD, EvernoteEndpoint
            from .mcpclient import McpStdioClient
            if shared is None:
                shared = McpStdioClient(
                    MCP_CMD,
                    stderr_path=str(Path(cfg["state"]) / "mcp-remote.log"))
                clients.append(shared)
            eps["en"] = EvernoteEndpoint(m["en_notebook"], client=shared)
        if eps:
            eps_by_mapping[m["name"]] = eps

    def closer():
        for c in clients:
            c.close()
    return eps_by_mapping, closer


def mail_step(cfg):
    """The mail-to-note import, after a sync cycle. Independent of the
    sync mappings, and a failure here never disturbs the sync loop."""
    try:
        from .mailin import run_once
        return run_once(cfg, log=tlog)
    except Exception as e:
        tlog(f"[mail error] {e}")
        return 0


def cmd_sync(args):
    cfg = load_cfg(args.config)
    eps, closer = build_eps(cfg)
    try:
        n = sync_once(cfg, eps, log=tlog)
    finally:
        closer()
    tlog(f"cycle done: {n} note(s) touched")
    mail_step(cfg)


def cmd_watch(args):
    cfg = load_cfg(args.config)
    interval = args.interval or min(cfg["poll_apple_seconds"], cfg["poll_evernote_seconds"])
    tlog(f"watching every {interval}s (ctrl-c to stop)")
    waiter = None
    if (cfg.get("mail_import") or {}).get("enabled"):
        # the Mail rule (notesync mail --install-rule) touches a trigger file
        from .mailin import TriggerWait
        waiter = TriggerWait()
        if waiter.kq is not None:
            tlog("mail import: real-time trigger armed")
    eps, closer = None, None
    while True:
        try:
            if eps is None:
                eps, closer = build_eps(cfg)
            n = sync_once(cfg, eps, log=tlog)
            if n:
                tlog(f"cycle: {n} note(s)")
        except Exception as e:  # keep the daemon alive; rebuild endpoints
            tlog(f"[error] {e}")
            if closer:
                closer()
            eps, closer = None, None
        mail_step(cfg)
        if waiter is None:
            time.sleep(interval)
            continue
        # between cycles a Mail rule may wake us: import right away, while
        # the sync cadence stays as it is
        deadline = time.time() + interval
        while waiter.wait(deadline - time.time()):
            time.sleep(2)  # let Mail finish filing the message
            if not mail_step(cfg):
                time.sleep(10)  # the rule can run before the message is listed
                mail_step(cfg)


def cmd_mail(args):
    """One-shot mail-to-note import (runs even if disabled in the config);
    flags override the config for this run only."""
    from .mailin import install_rule, remove_rule, run_once, settings
    cfg = load_cfg(args.config)
    if args.remove_rule:
        remove_rule()
        tlog("Mail rule removed")
        return
    if args.install_rule:
        path = install_rule(cfg)
        tlog(f"Mail rule installed (runs {path.name}); restart the watcher to arm it")
        return
    over = {k: v for k, v in (("address", args.address), ("notes_folder", args.folder),
                              ("after_import", args.after)) if v is not None}
    if not settings({"mail_import": {**(cfg.get("mail_import") or {}), **over}})["addresses"]:
        sys.exit('no import address: set "mail_import": {"address": ...} in the config '
                 "or pass --address")
    n = run_once(cfg, log=tlog, dry_run=args.dry_run, overrides=over, force=True)
    tlog(f"mail: {n} note(s) created" + (" (dry run)" if args.dry_run else ""))


def cmd_rerender(args):
    """One-shot: re-push every synced note so endpoints pick up the current
    rendering, and re-baseline each endpoint from its read-back.

    Safe only for notes that are in sync: a note whose file changed since
    the last cycle, or whose endpoint copy was edited (modification stamp
    moved), is skipped -- run `sync` first. Endpoint rows recorded before
    stamps existed cannot be checked; they are pushed only with
    --assume-synced, meant for right after a clean `sync` with the watcher
    stopped. Holds the cycle lock, so it never races a running cycle."""
    import fcntl
    from .engine import _rev, _shadow
    from .normalize import chash, chash_v2, normalize, split_fm
    cfg = load_cfg(args.config)
    state = Path(cfg["state"])
    lock = open(state / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("a sync cycle is running; try again in a moment")
    eps_by_mapping, closer = build_eps(cfg)
    db = DB(state / "state.db")
    store = Path(cfg["store"])
    only = set(args.notes or [])
    try:
        for m in cfg["mappings"]:
            eps = eps_by_mapping.get(m["name"], {})
            for uid in db.uuids_for_mapping(m["name"]):
                n = db.get_note(uid)
                if not n or n["status"] != "active" or not n["rel_path"]:
                    continue
                if only and not any(o in n["title"] for o in only):
                    continue
                path = store / n["rel_path"]
                if not path.exists():
                    continue
                _, raw_body = split_fm(path.read_text(encoding="utf-8"))
                body = normalize(raw_body)
                if chash(body) != n["base_hash"]:
                    if chash_v2(raw_body) != n["base_hash"]:
                        print(f"[rerender-skip] {n['title']}: file changed since "
                              "the last cycle (run sync first)")
                        continue
                    if not args.dry_run:  # same text, older normalization
                        db.set_base_hash(uid, chash(body))
                for name, row in db.ep_rows(uid).items():
                    if name not in eps:
                        continue
                    ep = eps[name]
                    try:
                        got = ep.fetch(row["native_id"])
                        if got is None or got[1] is None:
                            print(f"[rerender-skip] {n['title']} @ {name}: gone or frozen")
                            continue
                        cur_rev = _rev(got[2])
                        if row["seen_rev"] is None and not args.assume_synced:
                            print(f"[rerender-skip] {n['title']} @ {name}: no stamp "
                                  "yet (after a clean sync: --assume-synced)")
                            continue
                        if row["seen_rev"] is not None and row["seen_rev"] != cur_rev \
                                and chash(got[1]) != row["base_hash"]:
                            print(f"[rerender-skip] {n['title']} @ {name}: edited in "
                                  "the endpoint (run sync first)")
                            continue
                        if args.dry_run:
                            print(f"[rerender] {n['title']} @ {name} (dry run)")
                            continue
                        ep.update(row["native_id"], n["title"], body)
                        _, rb, mt = ep.read(row["native_id"])
                        shadow = _shadow(rb, body)
                        db.map_native(uid, name, row["native_id"], chash(rb),
                                      _rev(mt), shadow)
                        print(f"[rerender] {n['title']} @ {name}"
                              + (" (read-back differs)" if shadow is not None else ""))
                    except Exception as e:  # dead mapping: leave to sync cycle
                        print(f"[rerender-skip] {n['title']} @ {name}: {e}")
    finally:
        db.close()
        closer()
        lock.close()


def cmd_audit(args):
    from .audit import run
    cfg = load_cfg(args.config)
    which = tuple(args.only_ep.split(",")) if args.only_ep else ("apple", "en")
    res = run(cfg, live=args.live, which=which, only=args.notes,
              show_diff=args.diff, apple_folder=args.apple_folder,
              en_notebook=args.en_notebook)
    if any(v in ("LOSSY", "ERROR") for v in res.values()):
        sys.exit(1)


def cmd_lint(args):
    """Lint markdown files against the NoteSync profile (--fix rewrites
    lenient spellings to canonical ones)."""
    from .lint import coalesce, lint_file
    paths = [Path(p).expanduser() for p in args.paths]
    if not paths:
        from .audit import _files
        files = _files(load_cfg(args.config))
    else:
        files = []
        for p in paths:
            files += sorted(p.glob("*.md")) if p.is_dir() else [p]
        from .engine import IGNORED_FILES
        files = [f for f in files if f.name not in IGNORED_FILES]
    n_warn = n_fix = fixed = 0
    for f in files:
        findings, changed = lint_file(f, write=args.fix)
        fixed += changed
        shown = [x for x in findings if not (args.fix and x[1] == "fix")]
        for a, b, sev, rule, msg in coalesce(shown):
            if sev == "info" and not args.verbose:
                continue
            where = f"{a}" if a == b else f"{a}-{b}"
            print(f"{f.name}:{where}: {sev}: [{rule}] {msg}")
        n_warn += sum(1 for x in shown if x[1] == "warn")
        n_fix += sum(1 for x in shown if x[1] == "fix")
        if changed:
            print(f"{f.name}: fixed")
    print(f"{len(files)} file(s): {n_fix} fixable, {n_warn} warning(s)"
          + (f", {fixed} file(s) rewritten" if args.fix else ""))
    if n_fix or n_warn:
        sys.exit(1)


def cmd_preview(args):
    """HTML preview of a note (a file, or stdin) -- used by the menu bar app."""
    from .preview import page
    text = (Path(args.file).read_text(encoding="utf-8") if args.file
            else sys.stdin.read())
    sys.stdout.write(page(text))


def cmd_profile(args):
    from .profiledoc import generate
    text = generate()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text, end="")


def cmd_status(args):
    cfg = load_cfg(args.config)
    db = DB(Path(cfg["state"]) / "state.db")
    try:
        rows = db.con.execute(
            "SELECT status, COUNT(*) c FROM notes GROUP BY status").fetchall()
        print(f"store: {cfg['store']}")
        for r in rows:
            print(f"  {r['status']}: {r['c']}")
        mails = db.con.execute(
            "SELECT status, COUNT(*) c FROM mail_import GROUP BY status").fetchall()
        if mails:
            print("mail import: " + ", ".join(f"{r['status']} {r['c']}" for r in mails))
        print("recent activity:")
        for r in db.con.execute(
                "SELECT ts, action, detail FROM oplog ORDER BY ts DESC LIMIT 10"):
            t = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
            print(f"  {t}  {r['action']:<9} {r['detail']}")
    finally:
        db.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="notesync")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("init", cmd_init), ("sync", cmd_sync),
                     ("watch", cmd_watch), ("mail", cmd_mail), ("status", cmd_status),
                     ("rerender", cmd_rerender), ("audit", cmd_audit),
                     ("lint", cmd_lint), ("profile", cmd_profile),
                     ("preview", cmd_preview)]:
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name == "watch":
            p.add_argument("--interval", type=int)
        if name == "mail":
            p.add_argument("--dry-run", action="store_true",
                           help="list what would be imported; create nothing")
            p.add_argument("--address", help="import address (overrides the config)")
            p.add_argument("--folder", help="Notes folder name or id (default: the "
                           "configured one, else the default folder)")
            p.add_argument("--after", choices=["move", "flag", "read", "none"],
                           help="what to do with an imported mail")
            p.add_argument("--install-rule", action="store_true",
                           help="create the Mail rule that triggers an import the "
                           "moment such mail arrives")
            p.add_argument("--remove-rule", action="store_true",
                           help="remove that Mail rule")
        if name == "rerender":
            p.add_argument("notes", nargs="*",
                           help="only notes whose title contains one of these")
            p.add_argument("--assume-synced", action="store_true",
                           help="push rows that have no modification stamp yet")
            p.add_argument("--dry-run", action="store_true")
        if name == "audit":
            p.add_argument("notes", nargs="*",
                           help="only notes whose title contains one of these")
            p.add_argument("--live", action="store_true",
                           help="real round trips through the probe containers")
            p.add_argument("--diff", action="store_true")
            p.add_argument("--only-ep", help="apple, en, or apple,en")
            p.add_argument("--apple-folder")
            p.add_argument("--en-notebook")
        if name == "lint":
            p.add_argument("paths", nargs="*", help="files or folders "
                           "(default: the synced store)")
            p.add_argument("--fix", action="store_true",
                           help="rewrite lenient spellings to canonical ones")
            p.add_argument("-v", "--verbose", action="store_true")
        if name == "preview":
            p.add_argument("file", nargs="?", help="note file (default: stdin)")
        if name == "profile":
            p.add_argument("--out", help="write to this file instead of stdout")
    args = ap.parse_args(argv)
    args.fn(args)
