"""The no-oscillation invariant of v2 rich rendering:
flatten(render(md)) == md for each endpoint profile."""
import unittest

from notesync.apple import html_to_md
from notesync.evernote import enml_to_md
from notesync.render import APPLE_PROFILE, ENML_PROFILE, render

DOC = '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'

CASES = [
    "# 标题\n\n正文\n",
    "### 三级\n正文\n",
    "## 二级\n### 三级 **粗**\n\n段落\n",
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

    def test_links_stay_literal(self):
        self.assertEqual(self.rt("[链接](https://x.y)\n"), "[链接](https://x.y)\n")

    def test_apple_rewritten_h3_form(self):
        # Apple fragments our b+i+18px encoding; must still flatten to ###
        raw = ('<div><b><i><span style="font-size: 18px">三级</span></i></b>'
               '<b><i><span style="font-size: 18px">标题</span></i></b>'
               '<b><i><span style="font-size: 18px"><br></span></i></b></div>\n'
               '<div>正文<br></div>\n')
        self.assertEqual(html_to_md(raw), "### 三级标题\n正文\n")

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


TABLE_CASES = [
    "| a | b |\n|---|---|\n| 1 | 2 |\n",
    "前文\n| 元素 | 状态 |\n|---|---|\n| 标题 **粗** | 渲染 |\n| 空 |  |\n后文\n",
    "| 单列 |\n|---|\n",                       # header-only table
    "| a | b |\n|---|---|\n非表格行\n",         # table then plain line
]
TABLE_LITERAL = [
    "| a | b |\n|:--|--:|\n| 1 | 2 |\n",       # alignment colons: literal
    "|a|b|\n|---|---|\n",                       # non-canonical spacing: literal
    "| a | b |\n没有分隔行\n",                   # no separator: literal
]


class TestTableFixedPoint(unittest.TestCase):
    def rt_en(self, md):
        from notesync.render import ENML_PROFILE, render
        return enml_to_md(f"{DOC}<en-note>{render(md, ENML_PROFILE)}</en-note>")

    def rt_apple(self, md):
        from notesync.render import APPLE_PROFILE, render
        return html_to_md(render(md, APPLE_PROFILE))

    def test_tables_fixed_point(self):
        for md in TABLE_CASES + TABLE_LITERAL:
            self.assertEqual(self.rt_en(md), md, f"EN: {md!r}")
            self.assertEqual(self.rt_apple(md), md, f"Apple: {md!r}")

    def test_apple_rewritten_table_form(self):
        # what Apple actually stores after we write a <table> (captured live)
        raw = ('<div>标题行</div>\n'
               '<div><object><table cellspacing="0" style="x">\n<tbody>\n'
               '<tr><td valign="top" style="y"><div><b><font face=".P-Bold">元素</font></b></div>\n</td>'
               '<td valign="top"><div><b><font face=".P-Bold">状态</font></b></div>\n</td></tr>\n'
               '<tr><td><div><font face=".P">标题</font></div>\n</td>'
               '<td><div><font face=".P">渲染</font></div>\n</td></tr>\n'
               '</tbody>\n</table></object><br></div>\n'
               '<div>表后行<br></div>\n')
        self.assertEqual(html_to_md(raw),
                         "标题行\n| 元素 | 状态 |\n|---|---|\n| 标题 | 渲染 |\n表后行\n")
