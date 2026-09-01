// JXA bridge for Apple Notes (DESIGN.md #9, M3).
// osascript -l JavaScript apple_bridge.js <cmd> <args...>; prints JSON.
// Pure Apple Events: no focus stealing, works while screen is locked.
function run(argv) {
  const cmd = argv[0];
  const app = Application("Notes");
  return JSON.stringify(main(app, cmd, argv.slice(1)));
}

function getFolder(app, accName, folderName) {
  return app.accounts.byName(accName).folders.byName(folderName);
}

function main(app, cmd, a) {
  if (cmd === "list") {
    // one Apple Event per property for the whole folder: fast bulk arrays
    const f = getFolder(app, a[0], a[1]);
    const ids = f.notes.id();
    const names = f.notes.name();
    const mods = f.notes.modificationDate();
    const created = f.notes.creationDate();
    return ids.map(function (id, i) {
      return { id: id, name: names[i], mod: mods[i].getTime() / 1000,
               created: created[i].getTime() / 1000 };
    });
  }
  if (cmd === "read") {
    const ids = JSON.parse(a[0]);
    return ids.map(function (id) {
      try {
        const n = app.notes.byId(id);
        return { id: id, name: n.name(), body: n.body(),
                 mod: n.modificationDate().getTime() / 1000,
                 locked: n.passwordProtected() };
      } catch (e) {
        return { id: id, gone: true };
      }
    });
  }
  if (cmd === "create") {
    const f = getFolder(app, a[0], a[1]);
    const n = app.Note({ body: a[2] });
    f.notes.push(n);
    return { id: n.id() };
  }
  if (cmd === "update") {
    const n = app.notes.byId(a[0]);
    n.body = a[1];
    return { id: a[0], ok: true };
  }
  if (cmd === "delete") {
    try {
      app.delete(app.notes.byId(a[0]));
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }
  throw new Error("unknown cmd: " + cmd);
}
