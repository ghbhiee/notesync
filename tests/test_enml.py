import unittest

from notesync.evernote import enml_inner, enml_to_md, md_to_enml

DOC = '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'


def wrap(inner):
    return f"{DOC}<en-note>{inner}</en-note>"


class TestMdToEnml(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(md_to_enml("a\n\nb\n"),
                         "<div>a</div><div><br/></div><div>b</div>")

    def test_escaping(self):
        self.assertEqual(md_to_enml("a & <b>\n"),
                         "<div>a &amp; &lt;b&gt;</div>")

    def test_empty(self):
        self.assertEqual(md_to_enml(""), "")


class TestEnmlToMd(unittest.TestCase):
    def test_own_format_roundtrip(self):
        for body in ["a\n\nb\n", "# 标题\n- item & x\n", "单行\n", ""]:
            self.assertEqual(enml_to_md(wrap(md_to_enml(body))), body)

    def test_empty_note_with_xml_prolog(self):
        raw = f'<?xml version="1.0" encoding="UTF-8"?>{DOC}<en-note></en-note>'
        self.assertEqual(enml_to_md(raw), "")

    def test_flatten_rich(self):
        inner = ("<div><b>x</b> y</div><ul><li>i1</li><li>i2</li></ul>"
                 "<h1>T</h1>")
        self.assertEqual(enml_to_md(wrap(inner)),
                         "**x** y\n- i1\n- i2\n# T\n")

    def test_flatten_link_and_em(self):
        inner = '<div><em>it</em> <a href="http://x.y">go</a></div>'
        self.assertEqual(enml_to_md(wrap(inner)), "*it* [go](http://x.y)\n")

    def test_flatten_idempotent_after_writeback(self):
        # lossy first pass, fixed point once written back as literal text
        inner = "<div><b>x</b></div><h2>t</h2>"
        md1 = enml_to_md(wrap(inner))
        md2 = enml_to_md(wrap(md_to_enml(md1)))
        self.assertEqual(md1, md2)

    def test_inner_extraction(self):
        self.assertEqual(enml_inner(wrap("<div>a</div>")), "<div>a</div>")
        self.assertEqual(enml_inner(wrap("")), "")
