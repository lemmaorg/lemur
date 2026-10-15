<p align="center">
  <img src="static/lemurtext.png" alt="Lemur" width="280">
</p>

<p align="center">
  A modular study platform for speed reading, annotation and knowledge mapping.<br>
  A desktop app for Windows and macOS. Free and open source. Made by <b>Lemma</b>.
</p>

---

## What it does

- **Speed reading** — RSVP word-by-word reading with adjustable speed, chunk
  size and optimal-recognition-point highlighting; shown in a focal strip, a
  floating bubble or a side panel, with focus, blur and blackout modes.
- **Page view** — read the real PDF/EPUB pages (scrolling or page-turn) while
  the reading position follows along, including correct column-by-column order
  on two-column papers.
- **Annotate** — notes, bookmarks, highlights and freehand drawing on pages,
  with multiple named annotation versions per book.
- **Study** — quizzes generated from the keywords you tag (fill-in, 4-choice,
  matching, and "click the region" questions on illustrations), plus a
  full-screen immersive quiz mode.
- **Map ideas** — a mind map per book, and a cross-book collage notebook you can
  clip text and figures into.
- **Make it yours** — colour themes, custom wallpapers, nine icon sets, and the
  interface in 11 languages (including right-to-left Arabic and Urdu).
- **Share and keep safe** — export a book with its annotations as a `.srbook`
  file, and back up or restore your whole library as a `.lemurlib` file.

Lemur has no account, no cloud and no telemetry — your books and notes never
leave your computer. Its only network traffic is checking this repository's
Releases for updates (plus Discord Rich Presence, off by default, which when
turned on shows the title and progress of the book you're reading in your
Discord status).

## Download

Get the latest installer from the [Releases](../../releases) page.

- **Windows:** run `Lemur-Setup-<version>.exe`. The build isn't code-signed yet,
  so if SmartScreen warns, choose **More info → Run anyway**.
- **macOS (Apple Silicon):** open `Lemur-<version>-arm64.dmg` and drag Lemur to
  Applications. The build isn't notarized yet, so the first time, **right-click
  the app → Open → Open**.

Lemur checks for new versions and updates itself (Windows today; macOS once
builds are signed).

## Your data

Your library, notes and settings live in a folder named **Lemur**:

| OS | Location |
|---|---|
| Windows | `%APPDATA%\Lemur` |
| macOS | `~/Library/Application Support/Lemur` |
| Linux | `~/.local/share/Lemur` |

Settings → Data & Backup can open this folder and make or restore backups.
Upgrading from a release that stored data in a `Lemma` folder copies it into
the new folder automatically and leaves the original untouched.

## Build from source

```bash
pip install -r requirements.txt   # Python 3.12 recommended
npm install
npm start
npm test                          # run the test suite
```

Packaging, releases and the reasoning behind the build setup are in
[BUILD.md](BUILD.md).

### Project layout

| Path | What it is |
|---|---|
| `app.py` | Flask backend: PDF/EPUB processing (PyMuPDF), reading order, the local API |
| `store.py` | Persistence: library, annotations, settings, backups, data-folder migration |
| `templates/index.html` | The whole frontend (UI, reader, quizzes, mind map, collage) |
| `electron/` | Desktop shell: window management, auto-update, storage migration |
| `locale/` | UI translations (one JSON file per language) |
| `tests/` | Automated tests (Python `unittest` and Node `node:test`) |

## Contributing

Bug reports and pull requests are welcome. Start with
[CONTRIBUTING.md](CONTRIBUTING.md); to report a security issue privately, see
[SECURITY.md](SECURITY.md).

## License

Lemur is free software, licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE). You may use, study,
share and modify it; if you distribute a modified version, you must make its
source available under the same license.

Lemur builds on [PyMuPDF](https://github.com/pymupdf/PyMuPDF), Flask, Electron
and nine open-source icon sets — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

© Lemma
