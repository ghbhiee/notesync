"""Capability registry: the single source of truth for the NoteSync markdown
profile.

One row per markdown construct: its canonical spelling, the lenient
spellings the renderer also accepts, and what each endpoint does with it.
render.py asks `rich(endpoint, key)` before emitting rich markup,
`notesync profile` generates MARKDOWN_PROFILE.md from these rows, lint.py
takes its advice from them, and tests/test_render.py runs every example
through the fixed-point matrix -- so the document, the linter and the code
cannot drift apart.

Modes:
  rich     rendered as the endpoint's native element; flattening restores
           the canonical markdown exactly
  literal  kept as markdown source text in the endpoint (always lossless)
  frozen   the endpoint note is left alone entirely (never synced)
"""

RICH, LITERAL, FROZEN = "rich", "literal", "frozen"

ENDPOINTS = ("en", "apple")
ENDPOINT_NAMES = {"en": "Evernote", "apple": "Apple Notes"}


class Cap:
    def __init__(self, key, name, canonical, en, apple, example,
                 lenient=(), note="", advice=""):
        self.key, self.name, self.canonical = key, name, canonical
        self.modes = {"en": en, "apple": apple}
        self.example = example        # canonical md; must be a fixed point
        self.lenient = lenient        # (variant md, canonical md) pairs
        self.note = note              # endpoint behaviour worth knowing
        self.advice = advice          # what a writer should do instead


CAPS = [
    Cap("heading", "Headings 1-3", "`# `, `## `, `### ` + text",
        RICH, RICH, "# Title\n## Section\n### Sub\nbody\n",
        lenient=[("#  Spaced\n", "# Spaced\n"),
                 ("## **Bold** head\n", "## Bold head\n")],
        note="Heading text is literal: endpoints bold whole headings, so `**` "
             "inside a heading is dropped. Apple stores h1/h2 as 24/18px bold "
             "and h3 as a bold underlined line.",
        advice="Do not put `**` inside headings."),
    Cap("heading4", "Headings 4-6", "`#### ` ...",
        LITERAL, LITERAL, "#### deep\n",
        advice="Use `###` or a bold line instead."),
    Cap("emphasis", "Bold / italic", "`**bold**`, `*italic*`, `***both***`",
        RICH, RICH, "**bold** and *italic* and ***both*** and **粗体**\n",
        note="Emphasis does not nest (no italic inside bold). Apple drops "
             "italic from CJK text, so italic spans containing CJK stay "
             "literal there.",
        advice="Use `*`, not `_`, for emphasis."),
    Cap("code_inline", "Inline code", "`` `code` ``",
        RICH, RICH, "run `ls -la` here, **see `x`**\n",
        note="Apple has no inline code style: it becomes a Courier run. On "
             "Apple, inline code stays literal when it contains CJK (Courier "
             "has no CJK glyphs) and when a line is nothing but inline code "
             "(Apple would turn it into a monospaced paragraph).",
        advice="Keep other words on a line that holds inline code."),
    Cap("link", "Links", "`[text](https://url)`",
        RICH, LITERAL, "see [docs](https://example.com/a)\n",
        note="Apple strips href on write, so links stay literal there.",
        advice="Use absolute http(s) URLs."),
    Cap("bullet", "Bullet lists", "`- item`",
        RICH, RICH, "- one\n- two\n",
        lenient=[("* star\n* two\n", "- star\n- two\n")],
        note="`+` is not a bullet here (TeX lines start with `+`).",
        advice="Use `-` for bullets."),
    Cap("ordered", "Ordered lists", "`1. item`, numbered 1..n without gaps",
        RICH, RICH, "1. one\n2. two\n3. three\n",
        lenient=[("1.  wide\n2.   gap\n", "1. wide\n2. gap\n")],
        note="Numbers must run 1, 2, 3 ... inside a list. Evernote also "
             "renders a list that resumes after a paragraph (`start`); Apple "
             "cannot store a start number, so a list that is split by "
             "paragraphs or blank lines stays literal there.",
        advice="Keep ordered lists tight (no blank lines between items)."),
    Cap("nested", "Nested lists", "indent by the parent marker width: 2 "
        "spaces under `- `, 3 under `1. `",
        RICH, RICH, "- a\n  - a1\n    - a1x\n1. b\n   - b1\n",
        lenient=[("- a\n    - four\n", "- a\n  - four\n")],
        advice="Indent children exactly under the parent's text."),
    Cap("checklist", "Checklists", "`- [ ] todo` / `- [x] done`",
        LITERAL, LITERAL, "- [ ] todo\n- [x] done\n"),
    Cap("code_block", "Code blocks", "```` ```lang ```` ... ```` ``` ````",
        RICH, RICH, "```bash\n# comment, not a heading\n  indented  *x*\n```\n",
        note="Content is verbatim: no markdown inside, whitespace kept. The "
             "fence lines stay visible inside the block (they carry the "
             "language tag and delimit the block for round trips). Evernote: "
             "code block box; Apple: monospaced paragraphs.",
        advice="Always close fences and give a language (`text` for plain "
               "text). Draw diagrams with ```` ```mermaid ````, not ASCII art."),
    Cap("blockquote", "Block quotes", "`> text`, `>` for a blank quote line",
        RICH, LITERAL, "> quoted **bold**\n>\n> - item\n",
        lenient=[(">tight\n", "> tight\n")],
        note="Apple drops `<blockquote>` on write, so quotes stay literal there."),
    Cap("hr", "Horizontal rule", "`---` on its own line",
        RICH, LITERAL, "above\n\n---\n\nbelow\n",
        lenient=[("***\n", "---\n"), ("___\n", "---\n")],
        note="Apple accepts `<hr>` but turns it into blank lines once the note "
             "settles, so rules stay literal there.",
        advice="Leave a blank line above `---` (right under text, standard "
               "markdown reads it as a heading underline)."),
    Cap("table", "Tables", "`| a | b |` then `|---|---|`, one row per line",
        RICH, RICH, "| a | b |\n|---|---|\n| 1 | 2 |\n",
        lenient=[("| a | b |\n| :--- | ---: |\n| 1 | 2 |\n",
                  "| a | b |\n|---|---|\n| 1 | 2 |\n"),
                 ("|a|b|\n|-|-|\n", "| a | b |\n|---|---|\n")],
        note="Column alignment is not kept (canonical separator is `|---|`). "
             "Apple stores tables as native table objects.",
        advice="Every row needs the same number of cells."),
    Cap("whitespace", "Indentation and space runs", "kept as typed",
        RICH, RICH, "  two leading spaces\na    gap\n",
        note="Written as non-breaking spaces so neither endpoint collapses "
             "them. Tabs are not preserved outside code blocks; trailing "
             "whitespace is always dropped.",
        advice="Indent with spaces, not tabs."),
    Cap("math", "Math", "`$...$` inline, `$$` block lines",
        LITERAL, LITERAL,
        "energy $a*b*c$ here\n$$\nE = mc^2\n+ x_{*}\n$$\n",
        note="Shown as TeX source; nothing inside is parsed as markdown. "
             "Rendering as images is planned, not implemented."),
    Cap("image", "Images / attachments", "`![alt](path)`",
        LITERAL, LITERAL, "![fig](attachments/a.png)\n",
        note="The md reference stays literal. A note that holds an image or "
             "attachment inside the endpoint is frozen (never synced)."),
    Cap("html", "Raw HTML", "-", LITERAL, LITERAL, "<b>not bold</b>\n",
        advice="Use markdown syntax; raw HTML is shown as text."),
    Cap("strike", "Strikethrough / footnotes / highlights", "-",
        LITERAL, LITERAL, "~~old~~ text[^1]\n"),
]

BY_KEY = {c.key: c for c in CAPS}


def rich(endpoint, key):
    return BY_KEY[key].modes[endpoint] == RICH
