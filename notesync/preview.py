"""HTML preview of a note for the menu bar app's browser.

The preview is the Evernote rendering (the richest profile) in a page
shell, so there is exactly one markdown implementation: the one that
writes to the endpoints.
"""
from .normalize import normalize, split_fm
from .render import ENML_PROFILE, render

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.7 -apple-system, 'PingFang SC', sans-serif; margin: 0;
       padding: 16px 20px; color: CanvasText; background: Canvas; max-width: 46em; }
h1 { font-size: 1.5em; margin: .6em 0 .3em; } h2 { font-size: 1.25em; margin: .6em 0 .3em; }
h3 { font-size: 1.1em; margin: .5em 0 .3em; }
div { margin: 0; } ul, ol { margin: .2em 0; padding-left: 1.6em; }
table { border-collapse: collapse; margin: .5em 0; font-size: .95em; }
th, td { border: 1px solid rgba(127,127,127,.45); padding: 4px 9px; text-align: left; }
th { background: rgba(127,127,127,.12); }
code { font-family: ui-monospace, Menlo, monospace; font-size: .9em;
       background: rgba(127,127,127,.15); border-radius: 4px; padding: 1px 5px; }
div[style*="en-codeblock"] { color: CanvasText !important;
       background: rgba(127,127,127,.10) !important; margin: .4em 0; }
blockquote { margin: .4em 0; padding: 0 0 0 .9em; border-left: 3px solid rgba(127,127,127,.45);
       color: color-mix(in srgb, CanvasText 75%, Canvas); }
hr { border: 0; border-top: 1px solid rgba(127,127,127,.45); margin: .8em 0; }
a { color: -apple-system-blue; }
"""


def page(text):
    """Note text (front matter allowed) -> standalone HTML page."""
    _, body = split_fm(text)
    return ("<!doctype html><meta charset=utf-8><style>" + CSS + "</style><body>"
            + render(normalize(body), ENML_PROFILE) + "</body>")
