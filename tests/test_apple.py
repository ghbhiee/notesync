import unittest

from notesync.apple import AppleEndpoint, _bare, html_to_md, md_to_html


class TestAppleConversion(unittest.TestCase):
    def test_div_newline_noise_ignored(self):
        self.assertEqual(html_to_md("<div>a</div>\n<div>b</div>\n"), "a\nb\n")

    def test_br_jitter_stable(self):
        a = html_to_md("<div>x</div>\n<div>y</div>\n")
        b = html_to_md("<div>x<br></div>\n<div>y<br></div>\n")
        self.assertEqual(a, b)

    def test_fragmented_bold_scrubbed(self):
        raw = "<div><b>Mac/linux</b><b>自启动</b><b><br></b></div>\n<div><b><br></b></div>\n"
        self.assertEqual(html_to_md(raw), "**Mac/linux自启动**\n")

    def test_semicolonless_entities(self):
        self.assertEqual(html_to_md("<div>a &amp b &lttag&gt</div>"),
                         "a & b <tag>\n")

    def test_empty_line_roundtrip(self):
        html = md_to_html("T", "a\n\nb\n")
        self.assertEqual(html,
                         "<div>T</div><div>a</div><div><br></div><div>b</div>")

    def test_md_to_html_escapes(self):
        self.assertIn("&amp;", md_to_html("T", "a & b\n"))

    def test_bare_key(self):
        self.assertEqual(_bare("**Mac/linux 自启动**"), _bare("Mac/linux自启动"))


class FakeBridgeEndpoint(AppleEndpoint):
    """AppleEndpoint with _parse tested directly on captured payloads."""


class TestParse(unittest.TestCase):
    def parse(self, row):
        return AppleEndpoint._parse(AppleEndpoint("x"), row)

    def test_title_stripped(self):
        row = {"name": "标题", "mod": 1.0,
               "body": "<div>标题</div>\n<div><br></div>\n<div>正文</div>\n"}
        self.assertEqual(self.parse(row), ("标题", "正文\n", 1.0))

    def test_formatted_title_line_stripped(self):
        row = {"name": "Mac自启动", "mod": 1.0,
               "body": "<div><b>Mac</b><b>自启动</b><b><br></b></div>\n<div>x</div>\n"}
        self.assertEqual(self.parse(row), ("Mac自启动", "x\n", 1.0))

    def test_media_frozen(self):
        row = {"name": "t", "mod": 1.0,
               "body": '<div>t</div><div><img src="data:image/png;base64,x"></div>'}
        self.assertEqual(self.parse(row)[1], None)

    def test_locked_frozen(self):
        row = {"name": "t", "mod": 1.0, "locked": True, "body": "<div>t</div>"}
        self.assertEqual(self.parse(row)[1], None)

    def test_title_only_note(self):
        row = {"name": "只有标题", "mod": 1.0, "body": "<div>只有标题</div>\n"}
        self.assertEqual(self.parse(row), ("只有标题", "", 1.0))
