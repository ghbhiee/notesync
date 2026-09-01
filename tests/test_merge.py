import unittest

from notesync.merge import merge3


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
