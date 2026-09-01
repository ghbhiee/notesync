"""The no-oscillation invariant of v2 rich rendering:
flatten(render(md)) == md for each endpoint profile."""
import unittest

from notesync.apple import html_to_md
from notesync.evernote import enml_to_md
from notesync.render import APPLE_PROFILE, ENML_PROFILE, render

DOC = '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'

CASES = [
    "# 标题\n\n正文\n",
    "## 二级\n**加粗** 与 *斜体* 混排\n",
    "- a\n- b\n\n尾行\n",
    "列表前\n- item **粗** 中\n- 二\n列表后\n",
    "普通 & <标签> 行\n",
    "1. one\n2. two\n",              # ordered: literal on both ends
    "- [ ] 待办\n- [x] 已办\n",       # checklist: literal
    "**a*b**\n",                      # nested markers stay inside bold
    "a * b * c\n",                    # spaced asterisks: not italics
    "# 头\n\n\n三连空行后\n",
    "`code` 与 ~~del~~ 字面\n",
    "",
]
EN_ONLY = [
    "### 三级\n",
    "[链接](https://x.y/path)\n",
    "文中 [a](https://a.b) 与 **粗** 并存\n",
]


class TestEnmlFixedPoint(unittest.TestCase):
    def rt(self, md):
        return enml_to_md(f"{DOC}<en-note>{render(md, ENML_PROFILE)}</en-note>")

    def test_cases(self):
        for md in CASES + EN_ONLY:
            self.assertEqual(self.rt(md), md, f"EN not a fixed point: {md!r}")


class TestAppleFixedPoint(unittest.TestCase):
    def rt(self, md):
        return html_to_md(render(md, APPLE_PROFILE))

    def test_cases(self):
        for md in CASES:
            self.assertEqual(self.rt(md), md, f"Apple not a fixed point: {md!r}")

    def test_h3_and_links_stay_literal(self):
        for md in ["### 三级\n", "[链接](https://x.y)\n"]:
            self.assertEqual(self.rt(md), md)

    def test_apple_rewritten_heading_form(self):
        # what Apple actually stores after we write <h1>/<h2>
        raw = ('<div><b><span style="font-size: 24px">大标题</span></b>'
               '<b><span style="font-size: 24px"><br></span></b></div>\n'
               '<div>正文<br></div>\n')
        self.assertEqual(html_to_md(raw), "# 大标题\n正文\n")

    def test_apple_rewritten_heading_with_blank(self):
        raw = ('<div><b><span style="font-size: 18px">小标题</span></b>'
               '<b><span style="font-size: 18px"><br></span></b></div>\n'
               '<div><br></div>\n<div>正文<br></div>\n')
        self.assertEqual(html_to_md(raw), "## 小标题\n\n正文\n")

    def test_apple_rewritten_list_with_font_noise(self):
        raw = ('<ul>\n<li><font face=".PingFang">一</font><br></li>\n'
               '<li><font face=".PingFang">二</font><br></li>\n</ul>\n')
        self.assertEqual(html_to_md(raw), "- 一\n- 二\n")
