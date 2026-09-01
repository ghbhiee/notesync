"""Sync engine: one cycle over all mappings (DESIGN.md #7-8).

Star topology: the canonical store (md files) is the truth; endpoints are
reconciled against it via per-note 3-way merge with the last-agreed base
kept in git. Conflict policy: newer side wins the hunk, loser's full text is
saved under state/conflicts/. Deletion propagates only when no other side
changed since base; modification beats deletion (resurrect).
"""
import fcntl
import time
import uuid as uuidlib
from pathlib import Path

from .merge import merge3
from .normalize import chash, join_fm, normalize, sanitize_title, split_fm
from .state import DB
from .store import GitStore


def _new_uuid():
    return uuidlib.uuid4().hex


def sync_once(cfg, eps_by_mapping=None, log=lambda m: None):
    """Run one full cycle. -> number of notes touched."""
    store_root = Path(cfg["store"]).expanduser()
    state_dir = Path(cfg["state"]).expanduser()
    conflicts = state_dir / "conflicts"
    conflicts.mkdir(parents=True, exist_ok=True)

    IGNORED_FILES.update(cfg.get("ignore_files", []))
    lock = open(state_dir / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("[skip] another cycle is running")
        lock.close()
        return 0

    git = GitStore(state_dir / "repo.git", store_root)
    git.ensure()
    db = DB(state_dir / "state.db")
    try:
        touched = []
        for m in cfg["mappings"]:
            eps = (eps_by_mapping or {}).get(m["name"], {})
            touched += _sync_mapping(m, store_root, git, db, eps, conflicts, log)
        # db rows were written per note (kill-safe for long bootstraps);
        # the cycle commit is stamped onto them afterwards
        sha = git.commit_all(f"notesync: {len(touched)} note(s)")
        if sha:
            db.stamp_commit(touched, sha)
        if cfg.get("gdrive_mirror") and cfg.get("gdrive_enabled", True):
            try:
                _mirror(store_root, Path(cfg["gdrive_mirror"]).expanduser(), log)
            except Exception as e:
                log(f"[mirror error] {e}")
        return len(touched)
    finally:
        db.close()
        lock.close()


# md files in the store that are not notes (knowledge-base metafiles etc.);
# extendable via config "ignore_files"
IGNORED_FILES = {"AGENTS.md", "CLAUDE.md"}


def _scan_files(folder, owner_names):
    """-> uuid -> {title, body(normalized), mtime, path}. Assigns uuids to
    front-matter-less files. A duplicated uuid (user copied a file) keeps the
    identity on the db-registered filename and forks a fresh one for the copy."""
    entries = []
    for p in sorted(folder.glob("*.md")):
        if p.name in IGNORED_FILES:
            continue
        raw = p.read_text(encoding="utf-8")
        uid, body = split_fm(raw)
        entries.append([p, uid, body])
    by_uid = {}
    for e in entries:
        if e[1]:
            by_uid.setdefault(e[1], []).append(e)
    for uid, group in by_uid.items():
        if len(group) > 1:
            keeper = next((e for e in group if e[0].name == owner_names.get(uid)),
                          group[0])
            for e in group:
                if e is not keeper:
                    e[1] = None  # fork below
    out = {}
    for p, uid, body in entries:
        if uid is None:
            uid = _new_uuid()
            p.write_text(join_fm(uid, body), encoding="utf-8")
        out[uid] = dict(title=sanitize_title(p.stem), body=normalize(body),
                        mtime=p.stat().st_mtime, path=p)
    return out


def _base_body(git, note):
    if not (note and note["base_commit"] and note["rel_path"]):
        return ""
    raw = git.show(note["base_commit"], note["rel_path"])
    if raw is None:
        return ""
    _, body = split_fm(raw)
    return normalize(body)


def _sync_mapping(m, store_root, git, db, eps, conflicts, log):
    mname, folder_rel = m["name"], m["folder"]
    folder = store_root / folder_rel
    folder.mkdir(parents=True, exist_ok=True)

    owner_names = {}
    for uid in db.uuids_for_mapping(mname):
        r = db.get_note(uid)
        if r and r["rel_path"]:
            owner_names[uid] = Path(r["rel_path"]).name
    fvers = _scan_files(folder, owner_names)

    evers = {name: {} for name in eps}
    frozen_now = set()
    for name, ep in eps.items():
        for nid in ep.list():
            title, body, mtime = ep.read(nid)
            uid = db.uuid_for_native(name, nid)
            if body is None:  # media/tasks attached: frozen, hands off (#8)
                if uid:
                    frozen_now.add(uid)
                continue
            if uid is None:
                uid = db.claim_orphan(mname, name, sanitize_title(title),
                                      chash(body))
            if uid is None:
                uid = _new_uuid()
            evers[name][uid] = dict(nid=nid, title=sanitize_title(title),
                                    body=normalize(body), mtime=mtime)

    all_uuids = (set(db.uuids_for_mapping(mname)) | set(fvers)
                 | {u for d in evers.values() for u in d})
    touched = []

    for uid in sorted(all_uuids):
        note = db.get_note(uid)
        if uid in frozen_now:
            if note and note["status"] != "frozen":
                db.set_status(uid, "frozen")
                db.oplog(uid, "frozen", "endpoint note has media/tasks; skipping")
                log(f"[frozen] {note['title']}")
            continue
        active = bool(note and note["status"] == "active")
        epmaps = db.ep_rows(uid)
        base = _base_body(git, note)

        # -- collect sides -------------------------------------------------
        present, deletes = [], []
        fcur = fvers.get(uid)
        if fcur:
            present.append(dict(side="file", base=note["base_hash"] if note else None,
                                **fcur))
        elif active:
            deletes.append(dict(side="file"))
        skip_uid = False
        for name in eps:
            ecur = evers[name].get(uid)
            row = epmaps.get(name)
            if ecur:
                present.append(dict(side=name, base=row["base_hash"] if row else None,
                                    **ecur))
            elif active and row:
                # absent from list(): confirm before believing it (search
                # indexes lag; a freshly created note can be missing here)
                got = eps[name].fetch(row["native_id"])
                if got is None:
                    deletes.append(dict(side=name, nid=row["native_id"]))
                    continue
                title, body, mtime = got
                if body is None:
                    skip_uid = True  # frozen mid-flight: hands off this cycle
                    break
                present.append(dict(side=name, base=row["base_hash"],
                                    nid=row["native_id"],
                                    title=sanitize_title(title),
                                    body=normalize(body), mtime=mtime))
        if skip_uid:
            continue

        if not present:
            if active:
                db.tombstone(uid)
                db.oplog(uid, "tombstone", "gone from every side")
            continue

        changed = [s for s in present if chash(s["body"]) != (s["base"] or "")]

        if note and note["status"] in ("tombstone", "frozen") and present and not changed:
            changed = list(present)  # resurrect / unfreeze: re-sync unchanged content

        retitled = bool(note) and any(
            s.get("title") and s["title"] != note["title"] for s in present)

        if deletes and not changed and not retitled:
            # -- propagate deletion (no side modified since base) ----------
            for s in present:
                if s["side"] == "file":
                    s["path"].unlink()
                else:
                    eps[s["side"]].delete(s["nid"])
            db.tombstone(uid)
            db.oplog(uid, "delete", "propagated from " +
                     ",".join(d["side"] for d in deletes))
            log(f"[delete] {note['title'] if note else uid}")
            continue

        # an endpoint newly added to the mapping has neither content nor a
        # mapping row for this note: it still needs a copy pushed
        uncovered = [n for n in eps
                     if evers[n].get(uid) is None and n not in epmaps]
        if not changed and not retitled and not uncovered:
            continue  # everything matches base; nothing to do

        # -- canonical body: fold 3-way merges, newer side favored --------
        # (pure rename: no content change, fall through with current bodies)
        order = sorted(changed if changed else present, key=lambda s: s["mtime"])
        acc = order[0]["body"]
        for v in order[1:]:
            if v["body"] == acc:
                continue
            merged, conflict = merge3(base, v["body"], acc)
            if conflict:
                loser = conflicts / f"{uid}-{int(time.time())}.md"
                loser.write_text(acc, encoding="utf-8")
                db.oplog(uid, "conflict",
                         f"newer side ({v['side']}) won; loser saved {loser.name}")
                log(f"[conflict] {uid}: {v['side']} won, loser archived")
            acc = normalize(merged)
        canonical = acc

        # -- canonical title ----------------------------------------------
        old_title = note["title"] if note else None
        renames = [s for s in sorted(present, key=lambda s: s["mtime"], reverse=True)
                   if s.get("title") and s["title"] != old_title]
        title = renames[0]["title"] if renames else old_title

        # -- apply to file (collision-safe rename) ------------------------
        # suffixed names must be fixed points of sanitize_title (80-char cap),
        # or the title oscillates between cycles and renames never settle
        base_t = title
        desired = f"{title}.md"
        n = 2
        while (folder / desired).exists() and not (fcur and (folder / desired) == fcur["path"]):
            occupant, _ = split_fm((folder / desired).read_text(encoding="utf-8"))
            if occupant == uid:
                break
            suffix = f" ({n})"
            title = sanitize_title(base_t[:80 - len(suffix)] + suffix)
            desired = f"{title}.md"
            n += 1
        title = desired[:-3]
        path = folder / desired
        if not fcur or fcur["body"] != canonical or fcur["path"] != path:
            path.write_text(join_fm(uid, canonical), encoding="utf-8")
            if fcur and fcur["path"] != path:
                fcur["path"].unlink()

        # -- apply to endpoints, echo-suppress via read-back --------------
        # one bad note must not abort the whole cycle: isolate errors,
        # skip the note, retry next cycle
        ep_updates, ep_error = {}, None
        for name, ep in eps.items():
            try:
                ecur = evers[name].get(uid)
                if ecur and ecur["body"] == canonical and ecur["title"] == title:
                    ep_updates[name] = (ecur["nid"], chash(canonical))
                    continue
                if ecur:
                    nid = ep.update(ecur["nid"], title, canonical) or ecur["nid"]
                else:
                    nid = ep.create(title, canonical)
                _, body_rb, _ = ep.read(nid)
                rb_hash = chash(body_rb)
                if rb_hash != chash(canonical):
                    db.oplog(uid, "fidelity", f"{name} round-trip differs")
                ep_updates[name] = (nid, rb_hash)
            except Exception as e:
                ep_error = e
                db.oplog(uid, "error", f"{name}: {e}")
                log(f"[error] {title} @ {name}: {e}")
                break
        if ep_error is not None:
            continue

        rel_path = str(Path(folder_rel) / desired) if folder_rel else desired
        # per-note durable record (base_commit stamped after the cycle commit)
        db.upsert_note(uid, mname, title, rel_path, chash(canonical),
                       note["base_commit"] if note else None)
        for ep_name, (e_nid, e_hash) in ep_updates.items():
            db.map_native(uid, ep_name, e_nid, e_hash)
        touched.append(uid)
        db.oplog(uid, "sync", f"changed on {','.join(s['side'] for s in changed)}")
        log(f"[sync] {title} <- {','.join(s['side'] for s in changed)}")

    return touched


def _mirror(store_root, dst_root, log):
    """One-way copy of the canonical store into a plain folder (e.g. the
    Google Drive client folder). Not an endpoint: never merged back."""
    dst_root.mkdir(parents=True, exist_ok=True)
    src = {p.relative_to(store_root): p for p in store_root.rglob("*.md")
           if p.name not in IGNORED_FILES}
    for rel, sp in src.items():
        dp = dst_root / rel
        data = sp.read_bytes()
        if not dp.exists() or dp.read_bytes() != data:
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_bytes(data)
            log(f"[mirror] {rel}")
    for dp in dst_root.rglob("*.md"):
        if dp.relative_to(dst_root) not in src:
            dp.unlink()
            log(f"[mirror] removed {dp.relative_to(dst_root)}")
