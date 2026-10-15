"""The no-oscillation invariant of rich rendering, per endpoint:
    flatten(render(md)) == md                    for canonical md
    flatten(render(x))  == canonical(x)          for lenient spellings
    and every result is itself a fixed point.

Apple is exercised through `apple_store`, a model of what Apple Notes
actually stores after we write HTML (every rule below was captured from a
live probe); the flattener sees what it would see in production."""
import unittest

from notesync.apple import html_to_md, model_store as apple_store
from notesync.capabilities import CAPS, rich
from notesync.evernote import enml_to_md
from notesync.render import APPLE_PROFILE, ENML_PROFILE, render

DOC = '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'


def rt_en(md):
    return enml_to_md(f"{DOC}<en-note>{render(md, ENML_PROFILE)}</en-note>")


def rt_apple(md):
    return html_to_md(apple_store(render(md, APPLE_PROFILE)))


RT = {"en": rt_en, "apple": rt_apple}

# canonical md: a fixed point on BOTH endpoints (rich or literal)
FIXED = [
    "# 标题\n\n正文\n",
    "### 三级\n正文\n",
    "## 二级\n**加粗** 与 *斜体* 混排\n",
    "- a\n- b\n\n尾行\n",
    "列表前\n- item **粗** 中\n- 二\n列表后\n",
    "普通 & <标签> 行\n",
    "- [ ] 待办\n- [x] 已办\n",       # checklist: literal
    "**a*b**\n",                      # nested markers stay inside bold
    "a * b * c\n",                    # spaced asterisks: not italics
    "# 头\n\n\n三连空行后\n",
    "`code` 与 ~~del~~ 字面\n",
    "",
    # whitespace (P0): leading indentation and runs survive
    "  两格缩进\n    四格\na    b 中间四格\n",
    "   ├─ 系统提示词              ~1,000\n",
    # entities next to semicolons (Apple drops entity semicolons)
    'q ";" &; <; >; a&b &amp; &#59; x\n',
    # code blocks: verbatim, no inline parsing, blank lines inside
    "```bash\n# comment, not a heading\n  echo  \"a  b\" *not italic* <x> &\n\n```\n",
    "```\n+--------+\n| 中文 box |\n+--------+\n        │\n        ▼\n```\n",
    "前\n```js\nconst a = 1;  // x\n```\n后\n",
    "```\na\n```\n```text\nb\n```\n",          # adjacent blocks
    "````md\n```js\nx\n```\n````\n",             # nested fence
    "```\nunclosed fence stays *literal*\n",     # unclosed: literal text
    "~~~\ntilde\n~~~\n",
    # inline code
    "run `ls -la` here\n",
    "**bold `code` tail**\n",
    "`only code`\n",                             # Apple: literal line
    "- item `code`\n",
    "use ``a`b`` ok\n",
    # quotes
    "> 引用 **粗**\n>\n> - 列表\n\n正文\n",
    "> 一行\n",
    # hr
    "上\n\n---\n\n下\n",
    # ordered / nested lists
    "1. one\n2. two\n3. three\n",
    "1. **交易侧**\n   - 建立\n   - 对\n2. **登录侧**\n",
    "- a\n  - a1\n    - a1x\n  - a2\n- b\n",
    "- a\n  1. x\n  2. y\n- b\n",
    "1. a\n\n2. b\n\n3. c\n",                     # loose: EN start=, Apple literal
    "1. a\n   续行段落\n2. b\n",
    "3. starts at three\n4. four\n",
    "- a\n1. b\n",                               # type switch
    "10. ten\n11. eleven\n    - child\n",
    # tables
    "| a | b |\n|---|---|\n| 1 | 2 |\n",
    "前文\n| 元素 | 状态 |\n|---|---|\n| 标题 **粗** | 渲染 |\n| 空 | |\n后文\n",
    "| 单列 |\n|---|\n",
    "| a | b |\n|---|---|\n非表格行\n",
    "| a |\n|---|\n| 1 |\n\n表后空一行\n",       # Apple absorbs one blank
    "| a |\n|---|\n| 1 |\n\n\n表后空两行\n",
    "| a | b |\n没有分隔行\n",
    "| `x|y` | c |\n",
    "| a \\| b | c |\n|---|---|\n",
    # Apple: CJK cannot be monospaced or (in lists / cells) italic
    "装软件：`sudo pacman -S 包名`；看 `docs/架构设计.md`\n",
    "- 发送：“*这帧我画好了*” 与 *italic en*\n",
    "| 表头 | head |\n|---|---|\n| *单元格斜体* | *cell italic* |\n",
    "按 `` ` ``（Esc）弹出\n",
    "| 键 | 作用 |\n|---|---|\n| `` ` `` | 视角圆形菜单 |\n",
    "段落 *中文斜体* 与 *en italic* 与 ***粗斜中文***\n",
    "### 中文三级标题\n### 2.1 `code` 开头 ASCII\n",
    "末尾 `x `\n",
]
EN_ONLY = [
    "[链接](https://x.y/path)\n",
    "文中 [a](https://a.b) 与 **粗** 并存\n",
]
# lenient spelling -> canonical spelling (where the construct is rich)
CANON = [
    ("## 二级\n### 三级 **粗**\n\n段落\n", "## 二级\n### 三级 粗\n\n段落\n", "heading"),
    ("| a | b |\n|:--|--:|\n| 1 | 2 |\n", "| a | b |\n|---|---|\n| 1 | 2 |\n", "table"),
    ("|a|b|\n|---|---|\n", "| a | b |\n|---|---|\n", "table"),
    ("| x | y |\n| --- | :---: |\n|1|2|\n", "| x | y |\n|---|---|\n| 1 | 2 |\n", "table"),
    ("***\n", "---\n", "hr"),
    ("- a\n    - four\n", "- a\n  - four\n", "nested"),
    ("| a |  |\n|---|---|\n", "| a | |\n|---|---|\n", "table"),
    ("| **粗** | *斜* | *中文* |\n|---|---|---|\n", "| 粗 | 斜 | 中文 |\n|---|---|---|\n",
     "table"),
]
for cap in CAPS:
    FIXED.append(cap.example)
    for variant, canon in cap.lenient:
        CANON.append((variant, canon, cap.key))


class TestFixedPoint(unittest.TestCase):
    def test_canonical_md(self):
        for ep, rt in RT.items():
            for md in FIXED + (EN_ONLY if ep == "en" else []):
                with self.subTest(ep=ep, md=md):
                    self.assertEqual(rt(md), md)

    def test_lenient_md(self):
        for ep, rt in RT.items():
            for variant, canon, key in CANON:
                want = canon if rich(ep, key) else variant
                with self.subTest(ep=ep, md=variant):
                    got = rt(variant)
                    self.assertEqual(got, want)
                    self.assertEqual(rt(got), got)

    def test_links_literal_on_apple(self):
        self.assertEqual(rt_apple("[链接](https://x.y)\n"), "[链接](https://x.y)\n")


class TestRendering(unittest.TestCase):
    def test_code_fence_not_parsed(self):
        h = render("```bash\n# x *y*\n```\n", ENML_PROFILE)
        self.assertNotIn("<h1>", h)
        self.assertNotIn("<i>", h)
        self.assertIn("en-codeblock", h)

    def test_whitespace_pinned(self):
        self.assertEqual(render("  a  b c\n", APPLE_PROFILE),
                         "<div>&nbsp;&nbsp;a&nbsp;&nbsp;b c</div>")

    def test_apple_only_code_line_literal(self):
        self.assertEqual(render("`x`\n", APPLE_PROFILE), "<div>`x`</div>")
        self.assertEqual(render("`x`\n", ENML_PROFILE), "<div><code>x</code></div>")

    def test_apple_table_blank_compensation(self):
        h = render("| a |\n|---|\n\nx\n", APPLE_PROFILE)
        self.assertTrue(h.endswith("</table><div><br></div><div><br></div><div>x</div>"))

    def test_ordered_start(self):
        self.assertIn('<ol start="3">', render("3. c\n", ENML_PROFILE))
        self.assertNotIn("<ol", render("3. c\n", APPLE_PROFILE))
        # a list resumed after a paragraph: Evernote renders both parts,
        # Apple keeps the whole group literal
        md = "1. a\n\n2. b\n"
        self.assertEqual(render(md, ENML_PROFILE).count("<ol"), 2)
        self.assertNotIn("<ol", render(md, APPLE_PROFILE))

    def test_apple_list_type_switch_literal(self):
        h = render("- a\n1. b\n", APPLE_PROFILE)
        self.assertIn("<ul>", h)
        self.assertNotIn("<ol", h)


class TestAppleCapturedForms(unittest.TestCase):
    """Raw bodies captured from Apple Notes."""

    def test_h3_form(self):
        raw = ('<div><b><i><span style="font-size: 18px">三级</span></i></b>'
               '<b><i><span style="font-size: 18px">标题</span></i></b>'
               '<b><i><span style="font-size: 18px"><br></span></i></b></div>\n'
               '<div>正文<br></div>\n')
        self.assertEqual(html_to_md(raw), "### 三级标题\n正文\n")

    def test_h3_cjk_font_form(self):
        raw = ('<div><b><i><font face=".AppleSystemUIFontBoldItalic"><span style='
               '"font-size: 18px">渲染器要点</span></font></i></b><b><i><font face='
               '".AppleSystemUIFontBoldItalic"><span style="font-size: 18px"><br>'
               '</span></font></i></b></div>\n')
        self.assertEqual(html_to_md(raw), "### 渲染器要点\n")

    def test_h3_underline_form(self):
        raw = ('<div><b><u><font face=".PingFangUITextSC-Bold">粗体下划线三级标题</font>'
               '</u></b><br></div>\n<div>正文</div>\n')
        self.assertEqual(html_to_md(raw), "### 粗体下划线三级标题\n正文\n")

    def test_heading_form(self):
        raw = ('<div><b><span style="font-size: 24px">大标题</span></b>'
               '<b><span style="font-size: 24px"><br></span></b></div>\n'
               '<div>正文<br></div>\n')
        self.assertEqual(html_to_md(raw), "# 大标题\n正文\n")

    def test_heading_with_blank(self):
        raw = ('<div><b><span style="font-size: 18px">小标题</span></b>'
               '<b><span style="font-size: 18px"><br></span></b></div>\n'
               '<div><br></div>\n<div>正文<br></div>\n')
        self.assertEqual(html_to_md(raw), "## 小标题\n\n正文\n")

    def test_list_with_font_noise(self):
        raw = ('<ul>\n<li><font face=".PingFang">一</font><br></li>\n'
               '<li><font face=".PingFang">二</font><br></li>\n</ul>\n')
        self.assertEqual(html_to_md(raw), "- 一\n- 二\n")

    def test_cjk_italic_fragments(self):
        raw = ('<div><i>CODEX </i><i>整理</i><i>｜</i><i>资料核查截至</i>'
               '<i> 2026 </i><i>年</i><br></div>\n')
        self.assertEqual(html_to_md(raw), "*CODEX 整理｜资料核查截至 2026 年*\n")

    def test_semicolonless_entities(self):
        raw = ('<div>q1 &quot;&quot q2 &amp; q3 &lt; q4 &gt; q5 a&ampb '
               'q6 &ampamp; q7 &amp#59; t &lttag&gt</div>\n')
        self.assertEqual(html_to_md(raw),
                         'q1 ";" q2 &; q3 <; q4 >; q5 a&b q6 &amp; q7 &#59; t <tag>\n')

    def test_code_paragraphs(self):
        raw = ('<div><font face="Courier"><tt>```bash</tt></font></div>\n'
               '<div><font face="Courier"><tt># A3 *not italic*</tt></font></div>\n'
               '<div><font face="Courier"><tt>  echo &quota  b&quot</tt></font></div>\n'
               '<div><font face="Courier"><tt><br></tt></font></div>\n'
               '<div><font face="Courier"><tt>| </tt></font><font face="Courier">'
               '<tt>中文</tt></font><font face="Courier"><tt> box |</tt></font></div>\n'
               '<div><font face="Courier"><tt>```</tt></font></div>\n'
               '<div>after</div>\n')
        self.assertEqual(html_to_md(raw), '```bash\n# A3 *not italic*\n  echo "a  b"\n\n'
                                          '| 中文 box |\n```\nafter\n')

    def test_user_monospace_paragraph_gets_fences(self):
        raw = ('<div><font face="Courier"><tt>A5 whole-line</tt></font>'
               '<font face=".AppleSystemUIFont"><span style="font-size: 13px"><tt><br>'
               '</tt></span></font></div>\n<div>next</div>\n')
        self.assertEqual(html_to_md(raw), "```\nA5 whole-line\n```\nnext\n")

    def test_inline_code_runs(self):
        raw = ('<div>A4 inline <font face="Courier"><span style="font-size: 12px">'
               'tt code</span></font> end</div>\n'
               '<div><b>B14 bold </b><b><font face="Courier-Bold"><span style='
               '"font-size: 12px">code</span></font></b><b> tail</b><br></div>\n')
        self.assertEqual(html_to_md(raw), "A4 inline `tt code` end\n"
                                          "**B14 bold `code` tail**\n")

    def test_sibling_nested_lists(self):
        raw = ('<ul>\n<li>A15 lvl1</li>\n<ul>\n<li>A16 lvl2</li>\n<ul>\n'
               '<li>A17 lvl3</li>\n</ul>\n</ul>\n<li>A18 lvl1</li>\n</ul>\n'
               '<div>sep</div>\n<ul>\n<li>A20 ul</li>\n<ol>\n<li>A21 nested ol</li>\n'
               '</ol>\n</ul>\n')
        self.assertEqual(html_to_md(raw),
                         "- A15 lvl1\n  - A16 lvl2\n    - A17 lvl3\n- A18 lvl1\n"
                         "sep\n- A20 ul\n  1. A21 nested ol\n")

    def test_hr(self):
        self.assertEqual(html_to_md("<div>A6</div>\n<div><hr/></div>\n<div>A7</div>\n"),
                         "A6\n---\nA7\n")

    def test_table_form(self):
        raw = ('<div>标题行</div>\n'
               '<div><object><table cellspacing="0" style="x">\n<tbody>\n'
               '<tr><td valign="top" style="y"><div><b><font face=".P-Bold">元素</font></b></div>\n</td>'
               '<td valign="top"><div><b><font face=".P-Bold">状态</font></b></div>\n</td></tr>\n'
               '<tr><td><div><font face=".P">标题</font></div>\n</td>'
               '<td><div><font face="Courier"><span style="font-size: 12px">渲染</span></font></div>\n</td></tr>\n'
               '</tbody>\n</table></object><br></div>\n'
               '<div>表后行<br></div>\n')
        self.assertEqual(html_to_md(raw),
                         "标题行\n| 元素 | 状态 |\n|---|---|\n| 标题 | `渲染` |\n表后行\n")


class TestEvernoteForms(unittest.TestCase):
    def test_editor_list_divs(self):
        inner = "<ul><li><div>a</div></li><li><div><b>b</b></div></li></ul>"
        self.assertEqual(enml_to_md(f"{DOC}<en-note>{inner}</en-note>"), "- a\n- **b**\n")

    def test_user_codeblock_gets_fences(self):
        inner = '<div style="--en-codeblock:true;"><div>x = 1</div><div>  y</div></div>'
        self.assertEqual(enml_to_md(f"{DOC}<en-note>{inner}</en-note>"),
                         "```\nx = 1\n  y\n```\n")

    def test_todo(self):
        inner = '<div><en-todo checked="true"/>done</div><div><en-todo/>open</div>'
        self.assertEqual(enml_to_md(f"{DOC}<en-note>{inner}</en-note>"),
                         "- [x] done\n- [ ] open\n")

    def test_pre_and_nbsp(self):
        inner = "<pre>  a\n\tb</pre><div>\xa0\xa0c</div>"
        self.assertEqual(enml_to_md(f"{DOC}<en-note>{inner}</en-note>"),
                         "```\n  a\n\tb\n```\n  c\n")


if __name__ == "__main__":
    unittest.main()
