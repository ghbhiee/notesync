// JXA bridge for Mail.app (mail-to-note import, mailin.py).
// osascript -l JavaScript mail_bridge.js <cmd> '<json args>'; prints JSON.
// Pure Apple Events against what Mail has already downloaded: no IMAP, no
// credentials, no focus stealing, works while the screen is locked.
//
// Scope rule: a message not addressed to the import address is only ever
// matched on its recipient lists (bulk-fetched) or by a filter Mail
// evaluates itself; nothing else about it reaches this script's output.
ObjC.import("Foundation");

function run(argv) {
  return JSON.stringify(main(argv[0], JSON.parse(argv[1] || "{}")));
}

function mailbox(Mail, account, name) {
  const mbs = Mail.accounts.byName(account).mailboxes.whose({ name: name })();
  if (mbs.length !== 1) {
    throw new Error("mailbox \"" + name + "\" of account \"" + account +
                    "\" is not unique (" + mbs.length + " found)");
  }
  return mbs[0];
}

function escRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Bcc'd copies carry the address only in delivery headers; the header
// filter can also hit a message that merely mentions it (e.g. Reply-To)
function deliveredTo(headers, addr) {
  const a = escRe(addr);
  return new RegExp("^(original-recipient|delivered-to|x-original-to|envelope-to|" +
                    "x-envelope-to)[ \\t]*:[^\\n]*" + a, "im").test(headers) ||
         new RegExp("\\bfor\\s+<?" + a + ">?", "i").test(headers);
}

function main(cmd, a) {
  const Mail = Application("Mail");
  if (cmd === "scan") {
    // never launch Mail: it is the fetcher, so if it is not running there
    // is nothing new to see anyway
    if (!Mail.running()) return { running: false };
    const mb = mailbox(Mail, a.account, a.mailbox);
    const cutoff = a.lookbackDays > 0
      ? new Date(Date.now() - a.lookbackDays * 86400000) : null;
    const recent = cutoff ? { dateReceived: { _greaterThan: cutoff } } : null;
    const msgs = recent ? mb.messages.whose(recent) : mb.messages;
    const want = a.addresses.map(function (x) { return x.toLowerCase(); });
    const hit = {};
    // one Apple Event per property for the whole window
    const ids = msgs.id();
    if (ids.length) {
      const tos = msgs.toRecipients.address();
      const ccs = msgs.ccRecipients.address();
      ids.forEach(function (id, i) {
        const rc = (tos[i] || []).concat(ccs[i] || []);
        if (rc.some(function (x) { return want.indexOf(String(x).toLowerCase()) >= 0; })) {
          hit[id] = true;
        }
      });
    }
    if (a.bcc) {
      want.forEach(function (addr) {
        const q = { allHeaders: { _contains: addr } };
        const ms = mb.messages.whose(recent ? { _and: [recent, q] } : q);
        ms.id().forEach(function (id) {
          if (hit[id]) return;
          if (deliveredTo(mb.messages.byId(id).allHeaders(), addr)) hit[id] = true;
        });
      });
    }
    const hits = Object.keys(hit).map(function (id) {
      const m = mb.messages.byId(Number(id));
      return { id: Number(id), messageId: m.messageId() || "", subject: m.subject() || "",
               sender: m.sender() || "", received: m.dateReceived().getTime() / 1000 };
    });
    hits.sort(function (x, y) { return x.received - y.received; });
    return { running: true, hits: hits };
  }
  if (cmd === "source") {
    // raw MIME goes to a file: attachments make it large, stdout is no place.
    // Mail decodes the raw bytes one byte per character (Latin-1), so an
    // 8bit UTF-8 body arrives as mojibake; encoding back to Latin-1 restores
    // the exact bytes. UTF-8 only if that is not lossless.
    const src = $(mailbox(Mail, a.account, a.mailbox).messages.byId(a.id).source());
    let data = src.dataUsingEncodingAllowLossyConversion($.NSISOLatin1StringEncoding, false);
    const latin1 = !data.isNil();
    if (!latin1) data = src.dataUsingEncoding($.NSUTF8StringEncoding);
    if (!data.writeToFileAtomically(a.path, true)) throw new Error("cannot write " + a.path);
    return { ok: true, length: src.length, latin1: latin1 };
  }
  if (cmd === "mark") {
    const m = mailbox(Mail, a.account, a.mailbox).messages.byId(a.id);
    if (a.action === "none") return { ok: true };
    m.readStatus = true;
    if (a.action === "flag") m.flaggedStatus = true;
    if (a.action === "move") {
      const acc = Mail.accounts.byName(a.account);
      let dest = acc.mailboxes.whose({ name: a.done })();
      if (!dest.length) {
        Mail.make({ new: "mailbox", withProperties: { name: a.done }, at: acc });
        dest = acc.mailboxes.whose({ name: a.done })();
      }
      if (dest.length !== 1) throw new Error("mailbox \"" + a.done + "\": " + dest.length + " found");
      Mail.move(m, { to: dest[0] });
    }
    return { ok: true };
  }
  throw new Error("unknown cmd: " + cmd);
}
