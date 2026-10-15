# Lemur translations — guide for translators

Each `<code>.json` file is one language (e.g. `fr.json` = French). `en.json` is
the **source of truth** — it holds the original English for every piece of UI
text. The other files were **machine-drafted and need human revision.**

## How it works
- Every entry is `"key": "text"`. **Translate only the right-hand side** (the
  text). **Never change the keys** on the left.
- If a language is missing a key, Lemur automatically falls back to the English
  text — so the app never shows a blank.
- A language only appears in the in-app picker if its `<code>.json` file exists
  here. Drop in a new file and it shows up automatically — no code changes.

## Rules when editing
1. **Keep placeholders exactly as-is.** `{name}` and `{error}` get replaced with
   real values at runtime. Keep them, move them where the sentence needs them,
   but don't rename or remove them.
   - `"data.restore.failed": "Restore failed: {error}"`
2. **Keep `\n` (line breaks) and `\"` (quotes inside text).** These are needed
   for the dialog layout and valid JSON.
3. **Don't translate brand / technical terms:** `Lemur`, `Discord`, `.srbook`,
   `USB`, `PC`, `Ctrl`.
4. **`lang.name`** is the language's own native name shown in the picker
   (e.g. `Français`). Leave it in that language.
5. Keep it valid JSON — every line ends in a comma except the last in the file.
   When in doubt, paste the file into any "JSON validator" online.

## Current languages
`en` English (source) · `es` Spanish · `fr` French · `pt` Portuguese ·
`ru` Russian · `zh` Chinese · `hi` Hindi · `bn` Bengali · `ar` Arabic (RTL) ·
`ur` Urdu (RTL)

> Arabic and Urdu display right-to-left automatically — translate the text
> normally, the app handles the direction.

## To add a NEW language
Copy `en.json`, rename it to the language's ISO code (e.g. `de.json` for
German), set `lang.name` to the native name, translate the values, and save it
in this folder.
