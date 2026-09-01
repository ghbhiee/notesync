"""notesync CLI: init / status / sync / watch (DESIGN.md #10, #12).

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


def cmd_sync(args):
    cfg = load_cfg(args.config)
    eps, closer = build_eps(cfg)
    try:
        n = sync_once(cfg, eps, log=tlog)
    finally:
        closer()
    tlog(f"cycle done: {n} note(s) touched")


def cmd_watch(args):
    cfg = load_cfg(args.config)
    interval = args.interval or min(cfg["poll_apple_seconds"], cfg["poll_evernote_seconds"])
    tlog(f"watching every {interval}s (ctrl-c to stop)")
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
        time.sleep(interval)


def cmd_rerender(args):
    """One-shot: re-push every synced note so endpoints pick up the current
    rich rendering. Byte-level comparison lives in the adapters, so literal
    copies get rewritten while already-rich ones are left alone; the md-level
    hashes don't change, so no sync cycle is triggered."""
    from .normalize import normalize, split_fm
    from .state import DB
    cfg = load_cfg(args.config)
    eps_by_mapping, closer = build_eps(cfg)
    db = DB(Path(cfg["state"]) / "state.db")
    store = Path(cfg["store"])
    try:
        for m in cfg["mappings"]:
            eps = eps_by_mapping.get(m["name"], {})
            for uid in db.uuids_for_mapping(m["name"]):
                n = db.get_note(uid)
                if not n or n["status"] != "active" or not n["rel_path"]:
                    continue
                path = store / n["rel_path"]
                if not path.exists():
                    continue
                _, body = split_fm(path.read_text(encoding="utf-8"))
                body = normalize(body)
                for name, row in db.ep_rows(uid).items():
                    if name not in eps:
                        continue
                    try:
                        eps[name].update(row["native_id"], n["title"], body)
                        print(f"[rerender] {n['title']} @ {name}")
                    except Exception as e:  # dead mapping: leave to sync cycle
                        print(f"[rerender-skip] {n['title']} @ {name}: {e}")
    finally:
        db.close()
        closer()


def cmd_status(args):
    cfg = load_cfg(args.config)
    db = DB(Path(cfg["state"]) / "state.db")
    try:
        rows = db.con.execute(
            "SELECT status, COUNT(*) c FROM notes GROUP BY status").fetchall()
        print(f"store: {cfg['store']}")
        for r in rows:
            print(f"  {r['status']}: {r['c']}")
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
                     ("watch", cmd_watch), ("status", cmd_status),
                     ("rerender", cmd_rerender)]:
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name == "watch":
            p.add_argument("--interval", type=int)
    args = ap.parse_args(argv)
    args.fn(args)
