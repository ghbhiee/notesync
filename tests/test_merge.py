import unittest

from notesync.merge import merge3, rebase


class TestMerge3(unittest.TestCase):
    BASE = "l1\nl2\nl3\n"

    def test_clean_add_plus_delete(self):
        theirs = "l1\nl2\nl3\nl4\n"      # older: appended l4
        ours = "l1\nl3\n"                # newer: deleted l2
        merged, conflict = merge3(self.BASE, ours, theirs)
        self.assertFalse(conflict)
        self.assertEqual(merged, "l1\nl3\nl4\n")

    def test_same_line_conflict_newer_wins(self):
        ours = "l1\nNEW\nl3\n"
        theirs = "l1\nOLD\nl3\n"
        merged, conflict = merge3(self.BASE, ours, theirs)
        self.assertTrue(conflict)
        self.assertIn("NEW", merged)
        self.assertNotIn("OLD", merged)
        self.assertNotIn("<<<<<<<", merged)

    def test_delete_vs_edit_same_line_conflicts(self):
        ours = "l1\nEDITED\nl3\n"
        theirs = "l1\nl3\n"
        merged, conflict = merge3(self.BASE, ours, theirs)
        self.assertTrue(conflict)
        self.assertIn("EDITED", merged)

    def test_identical_changes_merge_clean(self):
        both = "l1\nX\nl3\n"
        merged, conflict = merge3(self.BASE, both, both)
        self.assertFalse(conflict)
        self.assertEqual(merged, both)


class TestRebase(unittest.TestCase):
    C = "t\n```\n  a  b\n    c\n```\nend"
    S = "t\n```\na b\nc\n```\nend"          # lossy endpoint copy

    def test_untouched_lines_keep_canonical_form(self):
        got, kept = rebase(self.S, self.S.replace("end", "end!"), self.C)
        self.assertEqual(got, self.C.replace("end", "end!"))
        self.assertFalse(kept)

    def test_edit_next_to_lossy_line(self):
        got, _ = rebase(self.S, self.S.replace("\nc\n", "\nc2\n"), self.C)
        self.assertEqual(got, self.C.replace("    c\n", "    c2\n"))  # re-indented

    def test_insert_and_delete(self):
        now = self.S.replace("a b\n", "").replace("end", "new\nend")
        got, _ = rebase(self.S, now, self.C)
        self.assertEqual(got, "t\n```\n    c\n```\nnew\nend")

    def test_dropped_canonical_line_survives(self):
        C, S = "| a |\n|---|\n\nx\ny", "| a |\n|---|\nx\ny"   # blank lost
        got, _ = rebase(S, S.replace("y", "y2"), C)
        self.assertEqual(got, "| a |\n|---|\n\nx\ny2")

    def test_edit_inside_non_whitespace_loss_keeps_edit(self):
        C, S = "*斜体*\nb", "斜体\nb"
        got, kept = rebase(S, "斜体改\nb", C)
        self.assertEqual(got, "斜体改\nb")
        self.assertTrue(kept)
        got, kept = rebase(S, "斜体\nb2", C)                    # elsewhere
        self.assertEqual(got, "*斜体*\nb2")
        self.assertFalse(kept)

    def test_whitespace_collapse_is_not_an_edit(self):
        C = "```\n  a  b\n    c\n```\nend"
        N = "```\na b\nc\n```\nend, edited"         # HTML round trip + one edit
        got, _ = rebase(C, N, C)
        self.assertEqual(got, C.replace("end", "end, edited"))

    def test_added_whitespace_is_an_edit(self):
        C = "a b\nc"
        got, _ = rebase(C, "a  b\n  c", C)
        self.assertEqual(got, "a  b\n  c")

