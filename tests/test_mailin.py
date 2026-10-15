"""Mail-to-note import: MIME -> note HTML, and the per-cycle bookkeeping
(dedup, retries, marking) against a fake Mail/Notes bridge."""
import re
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from notesync import mailin
from notesync.state import DB

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
       b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def parse(msg, **kw):
    d = Path(tempfile.mkdtemp())
    return mailin.parse_message(msg.as_bytes(), d / "files", **kw), d


def imgs(body):
    from urllib.parse import unquote
    return [unquote(u) for u in re.findall(r'<img src="file://([^"]+)">', body)]


class ParseText(unittest.TestCase):
    def test_plain_text(self):
        m = EmailMessage()
        m["Subject"] = "Fwd: 转发: 购物 清单"
        m["From"] = "Me <me@example.com>"
        m.set_content("牛奶\n\n  缩进  两格\n<b>不是标签</b>\n")
        p, _ = parse(m)
        self.assertEqual(p.title, "购物 清单")
        self.assertEqual(p.sender, "me@example.com")
        self.assertEqual(p.body, "<div>牛奶</div><div><br></div>"
                                 "<div>&nbsp;&nbsp;缩进 &nbsp;两格</div>"
                                 "<div>&lt;b&gt;不是标签&lt;/b&gt;</div>")
        self.assertTrue(p.html().startswith("<div><h1>购物 清单</h1></div>"))

    def test_first_line_repeating_subject_is_dropped(self):
        m = EmailMessage()
        m["Subject"] = "Weekly plan"
        m.set_content("Weekly plan\n\nmonday\n")
        p, _ = parse(m)
        self.assertEqual(p.body, "<div>monday</div>")

    def test_format_flowed(self):
        self.assertEqual(
            mailin.text_to_html("a long \nline\n> q \n> r\n", flowed=True),
            "<div>a long line</div><div>&gt; q &gt; r</div>")
        self.assertEqual(mailin.text_to_html("ab \ncd\n", flowed=True, delsp=True),
                         "<div>abcd</div>")

    def test_empty_subject_falls_back_to_date(self):
        m = EmailMessage()
        m.set_content("x")
        p, _ = parse(m, when=0)
        self.assertTrue(p.title.startswith("Mail note 19"))


class ParseHtml(unittest.TestCase):
    def evernote_like(self):
        """HTML + cid inline image, the title repeated in the body, a layout
        table around it, and a separate PDF attachment."""
        m = EmailMessage()
        m["Subject"] = "FW: 旅行笔记"
        m["From"] = "someone@example.org"
        m.set_content("plain fallback")
        m.add_alternative(
            "<html><head><style>p{color:red}</style><title>t</title></head><body>"
            "<table role='presentation'><tr><td>"
            "<div><span style='font-size:20px'>旅行笔记</span></div>"
            "<div>第一天 <b>出发</b><img src='cid:pic1@x'>到达</div>"
            "<script>alert(1)</script>"
            "</td></tr></table></body></html>", subtype="html")
        m.get_payload()[1].add_related(PNG, "image", "png", cid="<pic1@x>",
                                       filename="照片.png")
        m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                         filename="行程.pdf")
        return m

    def test_inline_cid_image_in_place_and_attachment_appended(self):
        p, _ = parse(self.evernote_like())
        self.assertEqual(p.title, "旅行笔记")
        files = imgs(p.body)
        self.assertEqual([Path(f).name for f in files], ["照片.png", "行程.pdf"])
        self.assertEqual([f.name for f in p.files], ["照片.png", "行程.pdf"])
        self.assertEqual(p.files[0].read_bytes(), PNG)
        # image sits between the words it was between; the PDF comes last
        self.assertRegex(p.body, r"<div>第一天 <b>出发</b><img src=\"file://[^\"]+\.png\">到达</div>")
        self.assertRegex(p.body, r"</div><div><img src=\"file://[^\"]+\.pdf\"></div>$")
        # body repeated the title: dropped; no style/script/title text leaked
        self.assertNotIn("旅行笔记", p.body)
        for leak in ("color:red", "alert", "<table", "<span", "plain fallback"):
            self.assertNotIn(leak, p.body)

    def test_unreferenced_related_part_still_kept(self):
        m = EmailMessage()
        m["Subject"] = "s"
        m.add_alternative("<p>hi</p>", subtype="html")
        m.get_payload()[0].add_related(PNG, "image", "png", cid="<orphan@x>")
        p, _ = parse(m)
        self.assertEqual(len(p.files), 1)
        self.assertTrue(p.files[0].name.endswith(".png"))

    def body(self, html):
        m = EmailMessage()
        m["Subject"] = "subject"
        m.set_content(html, subtype="html")
        return parse(m)[0].body

    def test_empty_body_padding_trimmed(self):
        m = EmailMessage()
        m["Subject"] = "ceshi"
        m.set_content("\n")
        m.add_alternative('<div dir="ltr"><br></div>\n', subtype="html")
        self.assertEqual(parse(m)[0].body, "")
        self.assertEqual(self.body("<br><div>x</div><div><br></div><br>"), "<div>x</div>")

    def test_links_keep_their_target_visible(self):
        self.assertEqual(
            self.body('<div><a href="https://a.example/x">docs</a> and '
                      '<a href="https://b.example">https://b.example</a></div>'),
            "<div>docs (https://a.example/x) and https://b.example</div>")

    def test_remote_images_not_fetched(self):
        self.assertEqual(
            self.body('<div><img src="https://t.example/p.gif" width="1" height="1">'
                      '<img src="https://c.example/a.png" alt="chart"></div>'),
            "<div>[image: chart] (https://c.example/a.png)</div>")

    def test_data_uri_image_saved(self):
        import base64
        b = self.body('<div><img src="data:image/png;base64,%s"></div>'
                      % base64.b64encode(PNG).decode())
        self.assertEqual(len(imgs(b)), 1)
        self.assertTrue(imgs(b)[0].endswith(".png"))

    def test_data_table_kept_layout_table_unwrapped(self):
        self.assertEqual(
            self.body("<table><tr><th>a<td>b<tr><td>1<td>2</table>"),
            "<table><tbody><tr><td>a</td><td>b</td></tr>"
            "<tr><td>1</td><td>2</td></tr></tbody></table>")
        # the outer table only lays out; the inner one is data and stays
        self.assertEqual(
            self.body("<table><tr><td><table><tr><td>x</td><td>y</td></tr></table>"
                      "</td><td>z</td></tr></table>"),
            "<div><table><tbody><tr><td>x</td><td>y</td></tr></tbody></table></div>"
            "<div>z</div>")
        self.assertEqual(
            self.body("<table><tr><td>only</td></tr><tr><td>column</td></tr></table>"),
            "<div>only</div><div>column</div>")

    def test_structure_whitelist(self):
        self.assertEqual(
            self.body("<h1>T</h1><h3>sub</h3><p>p <em>e</em> <code>c</code></p>"
                      "<ul><li>one<li>two</ul><pre>  a\n  b</pre><font>f</font>"),
            "<h1>T</h1><div><b>sub</b></div><div>p <i>e</i> <tt>c</tt></div>"
            "<ul><li>one</li><li>two</li></ul><pre>  a\n  b</pre>f")

    def test_stray_end_tag_does_not_break_out_of_a_cell(self):
        self.assertEqual(
            self.body("<div><table><tr><td>a</div>b</td><td>c</td></tr></table></div>"),
            "<div><table><tbody><tr><td>ab</td><td>c</td></tr></tbody></table></div>")

    def test_forwarded_message_attachment_is_inlined(self):
        inner = EmailMessage()
        inner["Subject"] = "原始邮件"
        inner["From"] = "x@example.com"
        inner.set_content("正文")
        m = EmailMessage()
        m["Subject"] = "看看这个"
        m.set_content("见附件")
        m.add_attachment(inner)
        p, _ = parse(m)
        self.assertIn("<div><b>原始邮件</b></div><div>From: x@example.com</div>", p.body)
        self.assertIn("<div>正文</div>", p.body)
        self.assertEqual(p.files, [])

    def test_too_large_attachment_skipped(self):
        m = EmailMessage()
        m["Subject"] = "s"
        m.set_content("x")
        m.add_attachment(b"0" * 2000, maintype="application", subtype="zip",
                         filename="big.zip")
        p, _ = parse(m, max_bytes=1000)
        self.assertEqual(p.files, [])
        self.assertEqual(p.skipped, [("big.zip", 2000)])
        self.assertIn("big.zip: 0.0 MB, too large, not imported", p.body)


class Helpers(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(mailin.safe_filename("../../etc/passwd", "", 1), "passwd.bin")
        self.assertEqual(mailin.safe_filename("C:\\x\\a:b.JPG", "", 1), "ab.JPG")
        self.assertEqual(mailin.safe_filename("", "image/jpeg", 3), "attachment-3.jpg")
        self.assertEqual(mailin.safe_filename(".hidden", "image/png", 1), "hidden.png")
        long = mailin.safe_filename("长" * 200 + ".pdf", "", 1)
        self.assertTrue(long.endswith(".pdf"))
        self.assertLessEqual(len(long.encode()), 255)

    def test_rule_script_mirror_path(self):
        # where Mail actually opens a `run script` set through scripting
        out = mailin.MAIL_SCRIPTS / mailin.RULE_SCRIPT
        mirror = mailin._mirror_path(out)
        self.assertEqual(str(mirror), str(mailin.MAIL_SCRIPTS) + str(out))
        self.assertIn(mailin.MAIL_SCRIPTS, mirror.parents)

    def test_rule_script_compiles_and_touches(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            trig = Path(d) / 'we"ird $dir' / "mail.trigger"
            src = Path(d) / "s.applescript"
            src.write_text(mailin.rule_script_source(trig), encoding="utf-8")
            out = Path(d) / "s.scpt"
            r = subprocess.run(["osacompile", "-o", str(out), str(src)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            # run its handler the way Mail does
            r = subprocess.run(["osascript", "-e", f'set s to load script (POSIX file "{out}")',
                                "-e", 'using terms from application "Mail"',
                                "-e", "tell s to perform mail action with messages {} for rule missing value",
                                "-e", "end using terms from"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(trig.exists())

    def test_message_key(self):
        self.assertEqual(mailin.message_key({"messageId": "<a@b>"}), "a@b")
        k = mailin.message_key({"messageId": "", "subject": "s", "sender": "x",
                                "received": 12.5})
        self.assertTrue(k.startswith("noid:12:"))

    def test_sender_allowed(self):
        self.assertTrue(mailin.sender_allowed("A <a@x.com>", []))
        self.assertTrue(mailin.sender_allowed("A <A@X.com>", ["a@x.com"]))
        self.assertTrue(mailin.sender_allowed("b@x.com", ["@x.com"]))
        self.assertFalse(mailin.sender_allowed("b@evil.com", ["@x.com", "a@x.com"]))

    def test_scan_window(self):
        day = 86400
        self.assertEqual(mailin.scan_window(1, None, 10 * day), 1)
        self.assertEqual(mailin.scan_window(1, 10 * day - 300, 10 * day), 1)
        # asleep for three days: reach back to the last scan, plus an hour
        self.assertAlmostEqual(mailin.scan_window(1, 7 * day, 10 * day), 3 + 1 / 24)
        self.assertEqual(mailin.scan_window(1, 0, 100 * day), mailin.CATCHUP_MAX_DAYS)
        self.assertEqual(mailin.scan_window(0, 7 * day, 10 * day), 0)

    def test_settings(self):
        s = mailin.settings({"mail_import": {"address": [" A@b.c ", ""]}})
        self.assertEqual(s["addresses"], ["a@b.c"])
        self.assertEqual(s["after_import"], "move")
        self.assertEqual(mailin.settings({})["addresses"], [])


class Trigger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "c" / "mail.trigger"
        self.w = mailin.TriggerWait(self.path)
        self.assertIsNotNone(self.w.kq)

    def tearDown(self):
        self.w.close()
        self.tmp.cleanup()

    def touch_later(self, delay):
        import os
        import threading
        threading.Timer(delay, lambda: os.utime(self.path)).start()

    def test_timeout(self):
        import time
        t = time.time()
        self.assertFalse(self.w.wait(0.2))
        self.assertGreaterEqual(time.time() - t, 0.15)

    def test_touch_wakes_early(self):
        import time
        self.touch_later(0.1)
        t = time.time()
        self.assertTrue(self.w.wait(5))
        self.assertLess(time.time() - t, 2)
        self.assertFalse(self.w.wait(0))  # consumed

    def test_touch_while_busy_is_not_lost(self):
        import os
        os.utime(self.path)  # nobody waiting (a sync cycle is running)
        self.assertTrue(self.w.wait(0))

    def test_purged_file_rearms(self):
        import subprocess
        self.path.unlink()
        self.assertTrue(self.w.wait(1))
        self.assertTrue(self.path.exists())
        subprocess.run(["touch", str(self.path)], check=True)  # what the rule does
        self.assertTrue(self.w.wait(1))


class FakeMail:
    """Mail + Notes as the bridge sees them."""

    def __init__(self, messages):
        self.messages = messages  # id -> (hit dict, EmailMessage)
        self.notes, self.marks, self.fail_create = [], [], 0
        self.scans, self.running = [], True

    def bridge(self, cmd, **a):
        if cmd == "scan":
            self.scans.append(a["lookbackDays"])
            if not self.running:
                return {"running": False}
            return {"running": True, "hits": [h for h, _ in self.messages.values()]}
        if cmd == "source":
            Path(a["path"]).write_bytes(self.messages[a["id"]][1].as_bytes())
            return {"ok": True}
        if cmd == "mark":
            self.marks.append((a["id"], a["action"]))
            if a["action"] == "move":
                del self.messages[a["id"]]
            return {"ok": True}
        raise AssertionError(cmd)

    def create(self, account, folder, body_path, files):
        if self.fail_create:
            self.fail_create -= 1
            raise mailin.MailError("Notes said no")
        for f in files:
            self.assertExists(f)
        self.notes.append((folder, Path(body_path).read_text(encoding="utf-8")))
        return f"x-coredata://N/ICNote/p{len(self.notes)}"

    @staticmethod
    def assertExists(f):
        assert Path(f).exists(), f


class RunOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"state": self.tmp.name,
                    "mail_import": {"enabled": True, "address": "in@example.com"}}
        m = EmailMessage()
        m["Subject"] = "hello"
        m["From"] = "me@example.com"
        m["Message-ID"] = "<m1@example.com>"
        m.set_content("<p>body<img src='cid:i'></p>", subtype="html")
        m.add_related(PNG, "image", "png", cid="<i>")
        hit = {"id": 7, "messageId": "m1@example.com", "subject": "hello",
               "sender": "me@example.com", "received": 1}
        self.mail = FakeMail({7: (hit, m)})
        self.log = []
        mailin._paused_until[0] = 0.0
        mailin._said.clear()
        patches = [mock.patch.object(mailin, "mail_bridge", self.mail.bridge),
                   mock.patch.object(mailin, "create_note", self.mail.create),
                   mock.patch.object(mailin, "broken_images", lambda nid: 0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, **kw):
        return mailin.run_once(self.cfg, log=self.log.append, **kw)

    def test_import_once_then_moved(self):
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(len(self.mail.notes), 1)
        folder, html = self.mail.notes[0]
        self.assertEqual(folder, "")  # Notes default folder
        self.assertTrue(html.startswith("<div><h1>hello</h1></div><div>body<img src=\"file://"))
        self.assertEqual(self.mail.marks, [(7, "move")])
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.mail.notes), 1)
        db = DB(Path(self.tmp.name) / "state.db")
        row = db.mail_get("m1@example.com")
        self.assertEqual((row["status"], row["note_id"]), ("imported", "x-coredata://N/ICNote/p1"))
        db.close()
        # temp files are gone once the note exists
        self.assertEqual(list(Path(tempfile.gettempdir()).glob("notesync-mail-*/files/*.png")), [])

    def test_imported_but_still_in_inbox_is_not_reimported(self):
        self.cfg["mail_import"]["after_import"] = "flag"
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.mail.notes), 1)
        self.assertEqual(self.mail.marks, [(7, "flag")])

    def test_failures_retry_then_give_up(self):
        self.mail.fail_create = 99
        for _ in range(mailin.MAX_ATTEMPTS + 2):
            self.run_once()
        db = DB(Path(self.tmp.name) / "state.db")
        self.assertEqual(db.mail_get("m1@example.com")["attempts"], mailin.MAX_ATTEMPTS)
        db.close()
        self.assertEqual(self.mail.marks, [])  # left in the inbox, unmarked
        self.mail.fail_create = 0
        self.assertEqual(self.run_once(), 0)

    def test_disabled_dry_run_and_allowlist(self):
        self.cfg["mail_import"]["enabled"] = False
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.run_once(dry_run=True, force=True), 0)
        self.assertEqual(self.mail.notes, [])
        self.assertTrue(any("would import: hello (1 file(s))" in x for x in self.log))
        self.cfg["mail_import"].update(enabled=True, allowed_senders=["@other.org"])
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.mail.notes, [])
        self.cfg["mail_import"]["allowed_senders"] = ["me@example.com"]
        self.assertEqual(self.run_once(), 1)  # not remembered as ignored

    def test_overrides_for_one_run(self):
        self.assertEqual(self.run_once(overrides={"notes_folder": "Probe",
                                                  "after_import": "none"}), 1)
        self.assertEqual(self.mail.notes[0][0], "Probe")
        self.assertEqual(self.mail.marks, [])

    def last_scan(self):
        db = DB(Path(self.tmp.name) / "state.db")
        try:
            return db.meta_get(mailin.LAST_SCAN)
        finally:
            db.close()

    def test_window_marker(self):
        self.mail.running = False
        self.run_once()
        self.assertIsNone(self.last_scan())  # Mail down: the gap keeps growing
        self.mail.running = True
        self.run_once(dry_run=True)
        self.run_once(overrides={"after_import": "none"})
        self.assertIsNone(self.last_scan())  # one-off runs never move it
        self.run_once()
        self.assertIsNotNone(self.last_scan())
        db = DB(Path(self.tmp.name) / "state.db")
        db.meta_set(mailin.LAST_SCAN, float(self.last_scan()) - 5 * 86400)
        db.close()
        self.run_once()
        self.assertEqual(self.mail.scans[:4], [1, 1, 1, 1])
        self.assertGreater(self.mail.scans[4], 5)  # caught up after downtime

    def test_timeout_pauses_mail_step(self):
        calls = []

        def hangs(cmd, **a):
            calls.append(cmd)
            raise mailin.MailError("osascript timed out (a permission prompt may be waiting)")
        with mock.patch.object(mailin, "mail_bridge", hangs):
            self.assertEqual(self.run_once(), 0)
            self.assertEqual(self.run_once(), 0)  # paused: Mail not asked again
        self.assertEqual(calls, ["scan"])
        self.assertTrue(any("permission prompt" in x for x in self.log))
        self.assertEqual(self.run_once(force=True), 1)  # the CLI ignores the pause

    def test_not_authorized_is_reported_not_raised(self):
        def denied(cmd, **a):
            raise mailin.NotAuthorized("-1743")
        with mock.patch.object(mailin, "mail_bridge", denied):
            self.assertEqual(self.run_once(), 0)
        self.assertTrue(any("Automation" in x for x in self.log))


if __name__ == "__main__":
    unittest.main()
