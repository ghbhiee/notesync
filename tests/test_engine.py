"""End-to-end engine tests: canonical store + DirEndpoint as a fake note app."""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from notesync.endpoints import DirEndpoint
from notesync.engine import sync_once
from notesync.normalize import split_fm


class EngineHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = root / "SYNC"
        self.state = root / "state"
        self.eproot = root / "epA"
        (self.store / "Notes").mkdir(parents=True)
        self.ep = DirEndpoint(self.eproot)
        self.cfg = {"store": str(self.store), "state": str(self.state),
                    "mappings": [{"name": "t", "folder": "Notes"}]}
        self.clock = time.time()

    def tearDown(self):
        self.tmp.cleanup()

    # helpers -------------------------------------------------------------
    def sync(self):
        return sync_once(self.cfg, {"t": {"A": self.ep}})

    def bump(self, path):
        """Give path a strictly newer mtime than anything before."""
        self.clock += 10
        os.utime(path, (self.clock, self.clock))

    def fpath(self, name):
        return self.store / "Notes" / name

    def fbody(self, name):
        _, body = split_fm(self.fpath(name).read_text(encoding="utf-8"))
        return body

    def write_file(self, name, body, keep_uuid=True):
        p = self.fpath(name)
        if keep_uuid and p.exists():
            uid, _ = split_fm(p.read_text(encoding="utf-8"))
            if uid:
                body = f"---\nns: {uid}\n---\n{body}"
        p.write_text(body, encoding="utf-8")
        self.bump(p)

    def write_ep(self, name, body):
        p = self.eproot / name
        p.write_text(body, encoding="utf-8")
        self.bump(p)

    def commits(self):
        env = {**os.environ, "GIT_DIR": str(self.state / "repo.git"),
               "GIT_WORK_TREE": str(self.store)}
        r = subprocess.run(["git", "rev-list", "--count", "HEAD"], env=env,
                           capture_output=True, encoding="utf-8")
        return int(r.stdout.strip()) if r.returncode == 0 else 0


class TestEngine(EngineHarness):
    def test_new_file_propagates_and_echo_suppresses(self):
        self.write_file("hello.md", "hi\n", keep_uuid=False)
        self.assertEqual(self.sync(), 1)
        uid, body = split_fm(self.fpath("hello.md").read_text(encoding="utf-8"))
        self.assertIsNotNone(uid)                       # uuid assigned
        self.assertEqual(body, "hi\n")
        self.assertEqual(self.ep.read("hello.md")[1], "hi\n")  # reached endpoint
        c = self.commits()
        self.assertEqual(self.sync(), 0)                # echo: full no-op
        self.assertEqual(self.commits(), c)

    def test_endpoint_new_note_lands_as_file(self):
        self.write_ep("from ep.md", "content\n")
        self.assertEqual(self.sync(), 1)
        self.assertEqual(self.fbody("from ep.md"), "content\n")
        self.assertEqual(self.sync(), 0)

    def test_edit_propagates_both_directions(self):
        self.write_file("n.md", "v1\n", keep_uuid=False)
        self.sync()
        self.write_ep("n.md", "v2\n")                   # edit on endpoint
        self.sync()
        self.assertEqual(self.fbody("n.md"), "v2\n")
        self.write_file("n.md", "v3\n")                 # edit on file
        self.sync()
        self.assertEqual(self.ep.read("n.md")[1], "v3\n")

    def test_concurrent_nonoverlapping_edits_merge(self):
        self.write_file("m.md", "l1\nl2\nl3\n", keep_uuid=False)
        self.sync()
        self.write_ep("m.md", "l1\nl2\nl3\nl4\n")       # ep appends l4 (older)
        uid, _ = split_fm(self.fpath("m.md").read_text(encoding="utf-8"))
        self.write_file("m.md", "l1\nl3\n")             # file deletes l2 (newer)
        self.sync()
        merged = "l1\nl3\nl4\n"
        self.assertEqual(self.fbody("m.md"), merged)
        self.assertEqual(self.ep.read("m.md")[1], merged)

    def test_same_line_conflict_newer_wins_loser_archived(self):
        self.write_file("c.md", "l1\nl2\nl3\n", keep_uuid=False)
        self.sync()
        self.write_ep("c.md", "l1\nOLD\nl3\n")          # older edit
        self.write_file("c.md", "l1\nNEW\nl3\n")        # newer edit
        self.sync()
        self.assertEqual(self.fbody("c.md"), "l1\nNEW\nl3\n")
        self.assertEqual(self.ep.read("c.md")[1], "l1\nNEW\nl3\n")
        losers = list((self.state / "conflicts").glob("*.md"))
        self.assertEqual(len(losers), 1)
        self.assertIn("OLD", losers[0].read_text(encoding="utf-8"))

    def test_delete_propagates_when_other_side_unchanged(self):
        self.write_file("d.md", "x\n", keep_uuid=False)
        self.sync()
        self.ep.delete("d.md")                          # deleted on endpoint
        self.sync()
        self.assertFalse(self.fpath("d.md").exists())   # file gone too
        self.assertEqual(self.sync(), 0)                # tombstone: stays gone

    def test_modification_beats_deletion(self):
        self.write_file("r.md", "x\n", keep_uuid=False)
        self.sync()
        self.ep.delete("r.md")
        self.write_file("r.md", "x\ny\n")               # concurrently edited
        self.sync()
        self.assertEqual(self.ep.read("r.md")[1], "x\ny\n")  # resurrected

    def test_rename_propagates(self):
        self.write_file("old.md", "body\n", keep_uuid=False)
        self.sync()
        raw = self.fpath("old.md").read_text(encoding="utf-8")
        self.fpath("old.md").unlink()
        self.fpath("new name.md").write_text(raw, encoding="utf-8")
        self.bump(self.fpath("new name.md"))
        self.sync()
        self.assertEqual(self.ep.list(), ["new name.md"])
        self.assertEqual(self.sync(), 0)

    def test_duplicate_file_forks_identity(self):
        self.write_file("a.md", "same\n", keep_uuid=False)
        self.sync()
        raw = self.fpath("a.md").read_text(encoding="utf-8")
        self.fpath("a copy.md").write_text(raw, encoding="utf-8")  # same uuid!
        self.bump(self.fpath("a copy.md"))
        self.sync()
        u1, _ = split_fm(self.fpath("a.md").read_text(encoding="utf-8"))
        u2, _ = split_fm(self.fpath("a copy.md").read_text(encoding="utf-8"))
        self.assertNotEqual(u1, u2)
        self.assertEqual(sorted(self.ep.list()), ["a copy.md", "a.md"])

    def test_mtime_touch_without_content_change_is_noop(self):
        self.write_file("t.md", "stable\n", keep_uuid=False)
        self.sync()
        self.bump(self.fpath("t.md"))                   # touch only
        self.assertEqual(self.sync(), 0)


if __name__ == "__main__":
    unittest.main()


class LaggyEndpoint(DirEndpoint):
    """Simulates Evernote's async search index: list() hides everything,
    but primary-key fetch() still sees the notes."""
    def list(self):
        return []


class TestLaggyIndex(EngineHarness):
    def test_missing_from_list_but_fetchable_is_not_deleted(self):
        self.write_file("keep.md", "content\n", keep_uuid=False)
        self.sync()                                  # note reaches endpoint
        self.assertEqual(self.ep.read("keep.md")[1], "content\n")
        laggy = LaggyEndpoint(self.eproot)           # same folder, blind list()
        n = sync_once(self.cfg, {"t": {"A": laggy}})
        self.assertTrue(self.fpath("keep.md").exists())   # file survived
        self.assertEqual(n, 0)

    def test_truly_gone_still_deletes(self):
        self.write_file("gone.md", "x\n", keep_uuid=False)
        self.sync()
        self.ep.delete("gone.md")
        self.sync()
        self.assertFalse(self.fpath("gone.md").exists())


class TestNewEndpoint(EngineHarness):
    def test_existing_note_pushed_to_newly_added_endpoint(self):
        self.write_file("n.md", "x\n", keep_uuid=False)
        self.sync()                                   # synced to endpoint A
        epB = DirEndpoint(Path(self.tmp.name) / "epB")
        n = sync_once(self.cfg, {"t": {"A": self.ep, "B": epB}})
        self.assertEqual(n, 1)
        self.assertEqual(epB.read("n.md")[1], "x\n")  # copy created on B
        n2 = sync_once(self.cfg, {"t": {"A": self.ep, "B": epB}})
        self.assertEqual(n2, 0)                       # and converges
