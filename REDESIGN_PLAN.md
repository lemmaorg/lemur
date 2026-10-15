# Lemur — Player / View redesign

> **STATUS: ✅ COMPLETE & VERIFIED** (end-to-end live sweep passed, 0 console
> errors; all settings restored). Kept as the record of what shipped before the
> next build (immersive quiz mode).

Goal (met): evolve from "speed reader with a strip" into a reading app where the
reading display is one of three forms, the controls feel like a single "player,"
and you can strip the screen down to just the book. **Strip is off by default.**

## The three reading-display modes (mutually exclusive) — DONE
A single setting `sr_reader_display` ∈ { `panel` (default), `strip`, `bubble` },
chosen from a segmented control (▭ ▤ ◯) in the deck. One word renderer
(`#focal-words`) is re-homed into whichever shell is active.
- **panel** (default) — the reading **bubble docked to the deck's top edge**,
  draggable horizontally along it (persisted `sr_panel_dock_x`). Leaves the page
  / wallpaper untouched and keeps the deck thin. *(Evolved from the original
  "static panel / spotlight screen" idea per review.)*
- **strip** — today's full-width focal strip with ALL prior behaviour (drag
  height, dock-on-scroll + Jump, keep-word-visible fade/scroll, custom colours).
- **bubble** — a free, draggable, always-on-top in-app bubble; position
  persisted (`sr_bubble_pos`); not dismissed by outside clicks.

Strip-only logic (`positionFocalStrip`, `updateStripReveal`) is guarded to no-op
in the other modes.

## Player / deck unification — DONE
- `#controls` is a 3-zone grid: **reading params left · transport centered ·
  appearance/output right**, with a full-width progress scrubber on top.
- **Pop-out controls**: Speed, Size, Color, and Illus-hold are compact
  icon+value buttons that open a vertical-slider/picker popover (mutually
  exclusive, close on outside click), so the bar stays thin. Readout/label
  widths are fixed so sliders don't jitter as digits change.
- **Icons follow the active icon set** everywhere — Speed/Size/Hold + transport
  (play/pause/skip) + the immersive and pop-out-player transports — via new
  `build-icons.mjs` keys (`speed`, `size`, `play`, `pause`, `skipback`,
  `skipfwd`) mapped across all 9 sets; they re-skin together.
- Words/Mode selects de-chromed with a custom chevron.
- **Illus. Hold** merged into a single slider (0 = off), deck + immersive shared.
- Illustration-panel toggle moved off the deck into the **View menu**.

## Immersive — DONE
- **Arises from the player**: entering animates the field **growing upward**
  (`immGrowUp`, respects reduced-motion).
- Bar slimmed to match the deck: transport icons, **no inline sliders**
  (Speed/Hold/Text-size/Anim-speed/Color are pop-out buttons with popovers above
  the bar), Context/Highlight joined into a segmented group, tidy separators,
  selects with chevrons.

## View menu + Book-only — DONE
- Topbar **▦ View** dropdown: checkboxes for Top bar / Player & controls /
  Sidebar / Illustration panel / Reading stats, plus **Book only** and **Show
  everything**. (Menu is a sibling of the topbar so it survives topbar hiding.)
- `ViewManager` toggles body classes; state persisted in `sr_view_state`.
- **Book-only** hides all chrome; restore via `Ctrl/Cmd+Shift+B`, `Esc`, or
  nudging the mouse to the top edge (peek zone) to reveal the topbar + View menu.

## Persistence keys (in SETTINGS_KEYS)
`sr_reader_display`, `sr_bubble_pos`, `sr_panel_dock_x`, `sr_view_state`
(+ existing strip keys: `sr_focal_fraction`, `sr_strip_reveal`, `sr_strip_bg`,
`sr_strip_border`, `sr_hl_color`).

## i18n — DONE
New keys translated across all 11 locales (`ui.disp.*`, `ui.view.illus`,
`ui.bubble.drag`); everything else reuses existing translated keys. Parity = 383
keys/locale. Verified live in Spanish; fixed the Hold "Off" label to seed the
translated string on load; fixed a stale `ui.typo.size` → `settings.typo.size`.

## Build order — all shipped
1. ✅ ViewManager + View menu + Book-only + hotkey/hover-reveal.
2. ✅ Reading-display modes + Panel/Strip/Bubble selector (strip off by default).
3. ✅ Floating in-app bubble (drag, persist, always-on-top, no-dismiss) — folded
   in with #2; docked-panel bubble added later per review.
4. ✅ Player visuals (3-zone deck, transport cluster, pop-out controls, icon-set
   glyphs, thin bar) + immersive-from-player grow-upward; immersive bar slimmed.
5. ✅ i18n all new labels; persistence; full end-to-end verification sweep.

## Verification (final sweep)
6 batches, live: display modes • deck pop-outs/transport/playback • View
menu/book-only/peek • immersive (grow-up, icons, merged hold, grouped toggles,
clean exit) • icon-set re-skinning + pop-out player • strip + view-mode
regression. All passed; no console errors. Real settings restored on disk.

## Deferred / next
- **Immersive quiz mode** ← next build.
- Optional: give the pop-out-player window's own play button the icon-set
  treatment if desired (left as-is for now); group deck zones with dividers.
