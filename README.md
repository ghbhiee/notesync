# NoteSync

Three-way, bidirectional sync between **plain-markdown files (iCloud Drive) ⇄ Apple Notes ⇄ Evernote**. A Mac acts as the hub; git provides history and three-way merges; a Swift menu bar app supervises the engine and owns the macOS permissions.

Edit a note in any of the three places — the other two follow within minutes. Files and git always hold standard markdown; notes apps get real rich formatting rendered from it.

## Why

Markdown is everywhere, and yet as a *product*, the markdown note-taking experience is genuinely poor — arguably unusable for normal people. Even Obsidian, the best of the bunch, clearly isn't a designed product; it is still miles away from ordinary users. New tools appear constantly, but they are mostly one-person projects with no business model and no sustained investment in sight. Notion, popular in certain circles, remains a niche, novelty way of working.

Evernote is one of the few products that has stayed genuinely focused on note-taking. It has plenty to complain about, but a note is far more than a saved file — I have tried to switch away to many other note products and failed every time, so Evernote stays. At the same time it is clearly disconnected from today's agent ecosystem — and frankly it doesn't need to be connected.

My reality is a mix of three worlds:

- **Evernote** — what I reach for heavily in certain scenarios.
- **Apple Notes** — daily use; I own many Apple devices, and sharing notes with family works beautifully.
- **Markdown files** — unavoidable: technical documents and AI agents all speak markdown. Even without Obsidian, the OS file manager handles md files just fine, and OneDrive / Google Drive / iCloud keep them synced across all my machines, servers included.

The pain lives in the intersection. Something written in one system is invisible from another. A document an AI agent produced on my Mac is unreachable from my phone. So the idea: connect them all with the most basic markdown syntax, syncing notes across channels — **solving exactly that intersection and nothing more**. Everything else stays where it belongs, undisturbed.

## What it does

- One whitelisted container per endpoint (an Apple Notes folder, an Evernote notebook, one folder in iCloud Drive) — nothing outside it is ever touched
- Files/git always store standard markdown; writes to the notes apps are rendered into each app's native rich format, and edits there are flattened back to markdown
- Conflicts resolved by diff3 three-way merge (non-overlapping edits merge automatically; same-line conflicts: newer side wins, loser's full text archived). Modification beats deletion
- Deletions propagate to all ends — each into its own trash/Recently Deleted, files recoverable from git history
- Notes containing images/attachments are excluded automatically (frozen) — content is never destroyed
- Optional one-way mirror into a Google Drive folder
- Menu bar app: status, manual sync, a note browser with per-version git history and markdown preview, reverse-chronological logs, settings

## How it works

The canonical store is a folder of markdown files. The engine polls all three ends, flattens everything to markdown, and compares content hashes at the markdown layer — endpoint byte-level noise (Apple's `<br>` jitter, entity rewriting, Evernote's re-serialization) cannot cause churn. The last agreed state of every note lives in a git commit; diff3 merges use it as the common ancestor.

The core invariant of the format layer: **`flatten(render(md)) == md`** for every element a profile declares. Anything an endpoint cannot round-trip losslessly stays literal text — ugly is acceptable, silent data loss is not.

| markdown | Evernote | Apple Notes |
|---|---|---|
| `#` / `##` headings | h1 / h2 | rendered (stored as bold + 24/18px) |
| `###` | h3 | rendered (encoded as 18px + italic — `<h3>` degrades to bare `<b>`, and Apple strips any font size below 18px) |
| `**bold**` / `*italic*` | rendered | rendered |
| `- ` lists | ul | ul |
| pipe tables (canonical form) | rendered | rendered as native table objects |
| ordered lists, links, checklists | links render; rest literal | literal (Apple drops `href` and list types) |
| images / attachments | note frozen | note frozen |

Hard-won endpoint facts are documented in [DEVLOG.zh.md](DEVLOG.zh.md) (Chinese): Evernote's search index lies in both directions (deletion needs primary-key confirmation), Apple's note `name` is a truncated first line, notes in Recently Deleted are still readable by id, and more.

## Install

```bash
git clone https://github.com/ghbhiee/notesync ~/notesync
cd ~/notesync && python3 -m notesync init      # writes ~/Documents/NoteSync/config.json
python3 -m unittest discover -s tests          # optional
cd macapp && ./build.sh                        # optionally set CODESIGN_ID / BUNDLE_ID
cp -R NoteSyncBar.app /Applications/ && open /Applications/NoteSyncBar.app
```

Requirements: macOS 13+, Xcode command line tools, Node (Evernote is reached through the official MCP server via `npx mcp-remote`; complete the Evernote OAuth once in any MCP client — credentials are shared in `~/.mcp-auth`). On first launch, allow the app access to Documents and iCloud Drive. See `config.example.json` (`engine_dir` should point at your clone); the menu bar app has a settings panel too.

The engine is plain-stdlib Python; the app is a single-file AppKit program. CLI: `python3 -m notesync init|sync|status|watch|rerender`.

## Documents

- [DESIGN.md](DESIGN.md) — full architecture & design decisions (Chinese)
- [DEVLOG.zh.md](DEVLOG.zh.md) — development log, calibration experiments, battle-tested endpoint quirks (Chinese)

## License

MIT
