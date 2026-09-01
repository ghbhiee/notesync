import unittest

from notesync.normalize import chash, join_fm, normalize, sanitize_title, split_fm


class TestNormalize(unittest.TestCase):
    def test_line_endings_and_nbsp(self):
        self.assertEqual(normalize("a\r\nb\rc d"), "a\nb\nc d\n")

    def test_trailing_whitespace_and_final_newline(self):
        self.assertEqual(normalize("a  \nb\t\n\n\n"), "a\nb\n")
        self.assertEqual(normalize("a"), "a\n")

    def test_blank_lines_and_indent_preserved(self):
        self.assertEqual(normalize("a\n\n  code\n"), "a\n\n  code\n")

    def test_empty(self):
        self.assertEqual(normalize("   \n \n"), "")

    def test_idempotent(self):
        for s in ["a\r\n b \n", "", "x y", "a\n\n\nb"]:
            self.assertEqual(normalize(normalize(s)), normalize(s))

    def test_hash_ignores_cosmetics(self):
        self.assertEqual(chash("a \r\nb"), chash("a\nb\n"))
        self.assertNotEqual(chash("a\nb"), chash("a\nc"))


class TestFrontMatter(unittest.TestCase):
    def test_roundtrip(self):
        uid = "0" * 32
        raw = join_fm(uid, "body\n")
        self.assertEqual(split_fm(raw), (uid, "body\n"))

    def test_no_fm(self):
        self.assertEqual(split_fm("plain\n"), (None, "plain\n"))

    def test_foreign_fm_untouched(self):
        raw = "---\ntitle: x\n---\nbody\n"
        self.assertEqual(split_fm(raw), (None, raw))


class TestTitle(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(sanitize_title(" a/b:c\\d "), "a-b-c-d")
        self.assertEqual(sanitize_title("  "), "Untitled")
        self.assertEqual(len(sanitize_title("x" * 200)), 80)
        self.assertEqual(sanitize_title("a\n b"), "a b")


if __name__ == "__main__":
    unittest.main()
