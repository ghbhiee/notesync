"""MARKDOWN_PROFILE.md is generated from the capability registry; it must
never drift from the code (regenerate: python3 -m notesync profile --out
MARKDOWN_PROFILE.md)."""
import unittest
from pathlib import Path

from notesync.profiledoc import generate

DOC = Path(__file__).resolve().parent.parent / "MARKDOWN_PROFILE.md"


class TestProfileDoc(unittest.TestCase):
    def test_up_to_date(self):
        self.assertEqual(DOC.read_text(encoding="utf-8"), generate(),
                         "MARKDOWN_PROFILE.md is stale: regenerate it")


if __name__ == "__main__":
    unittest.main()
