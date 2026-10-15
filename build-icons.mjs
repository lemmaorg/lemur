/**
 * build-icons.mjs — fetch every icon SVG for every set from the Iconify API
 * and bundle them into static/icons-data.js for fully-offline use.
 *
 * Run:  node build-icons.mjs
 *
 * Output: window.LEMUR_ICONS = {
 *   sets: [ { id, name, group } … ],   // ordered, for the Settings dropdown
 *   defaultSet: 'pixel',
 *   icons: { '<setId>': { '<iconKey>': '<svg…>' } }   // resolved SVG per set
 * }
 *
 * Any icon a set can't represent falls back to the Minimal (Lucide) glyph,
 * so every toolbar stays complete. Missing-from-CDN names also fall back and
 * are reported at the end so they can be corrected.
 */
import { writeFileSync } from 'node:fs';

const API = 'https://api.iconify.design';

// ── The app's switchable icons (logical keys) ───────────────────────────────
// Each set maps these keys to its own icon name. `null` = no good glyph in
// that set → use the Lucide fallback.
const KEYS = [
  'library','open','pageview','immersive','draw','find','export','popout',
  'sidebar','settings','fill','strip','stripstyle','illuspanel','illushold',
  'bookmark','autoswitch','flip','dock','collapse',
  // Deck reading-control icons.
  'speed','size',
  // Transport icons (play toggles to pause).
  'play','pause','skipback','skipfwd',
  // Utility / dynamic-state icons (toggle states, loading spinner).
  'loading','pages','textlines','sidebarhide','sidebarshow',
  // Settings nav icons.
  'navtheme','navbackground','navtypography','navintegrations','player','comfort',
  'collage','scissors',
  // Action icons (version bar, etc.).
  'save','add','rename','delete',
  // Gallery card actions.
  'collection','reset',
];

// Lucide names — the universal fallback set. Every key MUST have one.
const LUCIDE = {
  library:'library', open:'folder-open', pageview:'book-open', immersive:'maximize',
  draw:'pen-line', find:'search', export:'share', popout:'external-link',
  sidebar:'panel-left', settings:'settings', fill:'paint-bucket',
  strip:'rectangle-horizontal', stripstyle:'sliders-horizontal', illuspanel:'image',
  illushold:'timer', bookmark:'bookmark', autoswitch:'refresh-cw',
  flip:'arrow-left-right', dock:'panel-right-close', collapse:'chevron-left',
  speed:'gauge', size:'a-large-small',
  play:'play', pause:'pause', skipback:'skip-back', skipfwd:'skip-forward',
  // Utility / dynamic-state icons (kept Lucide-neutral across all sets).
  loading:'loader-circle', pages:'book-open-text', textlines:'align-left',
  sidebarhide:'panel-left-close', sidebarshow:'panel-left-open',
  // Settings nav icons.
  navtheme:'palette', navbackground:'image', navtypography:'type',
  navintegrations:'plug', player:'headphones', comfort:'moon', collage:'images', scissors:'scissors',
  // Action icons.
  save:'save', add:'plus', rename:'pencil', delete:'trash-2',
  // Gallery card actions.
  collection:'folder-plus', reset:'rotate-ccw',
};

// Helper: prefix every value with its Iconify collection.
const pre = (collection, map) => Object.fromEntries(
  Object.entries(map).map(([k, v]) => [k, v == null ? null : `${collection}:${v}`]));

// ── Set definitions ─────────────────────────────────────────────────────────
// group is just for visual grouping in the dropdown.
const SETS = [
  {
    id:'pixel', name:'Pixel', group:'Fun',
    names: pre('pixelarticons', {
      library:'book', open:'folder', pageview:'book-open', immersive:'scale',
      draw:'edit', find:'search', export:'external-link', popout:'external-link',
      sidebar:'layout-sidebar-left', settings:'sliders', fill:'paint-bucket',
      strip:'card-text', stripstyle:'sliders-2', illuspanel:'image', illushold:'clock',
      bookmark:'bookmark', autoswitch:'reload', flip:'repeat',
      dock:'layout-sidebar-right', collapse:'chevron-left',
      speed:'speed-fast', size:'text-add',
      play:'play', pause:'pause', skipback:'prev', skipfwd:'next',
      navtheme:'drop', navbackground:'image', navtypography:'text-align-left',
      navintegrations:'power', player:'headphone', comfort:'moon',
      sidebarhide:'layout-sidebar-left', sidebarshow:'layout-sidebar-left',
      save:'save', add:'plus', rename:'edit', delete:'trash',
    }),
  },
  {
    id:'cute', name:'Cute Duotone', group:'Fun',
    names: pre('solar', {
      library:'book-2-bold-duotone', open:'folder-open-bold-duotone',
      pageview:'notebook-bold-duotone', immersive:'maximize-bold-duotone',
      draw:'pen-bold-duotone', find:'magnifer-bold-duotone',
      export:'square-share-line-bold-duotone', popout:'maximize-square-3-bold-duotone',
      sidebar:'sidebar-minimalistic-bold-duotone', settings:'settings-bold-duotone',
      fill:'palette-bold-duotone', strip:'notes-bold-duotone',
      stripstyle:'tuning-bold-duotone', illuspanel:'gallery-bold-duotone',
      illushold:'clock-circle-bold-duotone', bookmark:'bookmark-bold-duotone',
      autoswitch:'refresh-bold-duotone', flip:'transfer-horizontal-bold-duotone',
      dock:'sidebar-minimalistic-bold-duotone', collapse:'alt-arrow-left-bold-duotone',
      speed:'bolt-bold-duotone', size:'text-field-bold-duotone',
      play:'play-bold', pause:'pause-bold', skipback:'skip-previous-bold', skipfwd:'skip-next-bold',
      navtheme:'palette-bold-duotone', navbackground:'gallery-bold-duotone',
      navtypography:'text-bold-duotone', navintegrations:'plug-circle-bold-duotone',
      player:'headphones-round-bold-duotone', comfort:'moon-bold-duotone',
      sidebarhide:'sidebar-minimalistic-bold-duotone', sidebarshow:'sidebar-minimalistic-bold-duotone',
      save:'diskette-bold-duotone', add:'add-circle-bold-duotone', rename:'pen-bold-duotone', delete:'trash-bin-trash-bold-duotone',
    }),
  },
  {
    id:'bubbly', name:'Bubbly', group:'Fun',
    names: pre('mingcute', {
      library:'book-2-fill', open:'folder-open-fill', pageview:'book-fill',
      immersive:'fullscreen-fill', draw:'pen-fill', find:'search-fill',
      export:'share-2-fill', popout:'external-link-fill', sidebar:'layout-left-fill',
      settings:'settings-3-fill', fill:'paint-fill', strip:'rows-2-fill',
      stripstyle:'settings-5-fill', illuspanel:'pic-fill', illushold:'time-fill',
      bookmark:'bookmark-fill', autoswitch:'refresh-2-fill',
      flip:'transfer-horizontal-fill', dock:'layout-right-fill',
      collapse:'left-fill',
      speed:'dashboard-3-fill', size:'text-2-fill',
      play:'play-fill', pause:'pause-fill', skipback:'skip-previous-fill', skipfwd:'skip-forward-fill',
      navtheme:'palette-fill', navbackground:'pic-fill', navtypography:'text-fill',
      navintegrations:'plugin-2-fill', player:'headphone-fill', comfort:'moon-fill',
      sidebarhide:'layout-left-fill', sidebarshow:'layout-left-fill',
      save:'save-fill', add:'add-fill', rename:'edit-fill', delete:'delete-2-fill',
    }),
  },
  {
    id:'emoji-flat', name:'Emoji (Flat)', group:'Fun',
    names: pre('fluent-emoji-flat', {
      library:'books', open:'open-file-folder', pageview:'open-book',
      immersive:'up-right-arrow', draw:'pencil', find:'magnifying-glass-tilted-left',
      export:'outbox-tray', popout:'right-arrow', sidebar:null, settings:'gear',
      fill:'artist-palette', strip:null, stripstyle:'level-slider',
      illuspanel:'framed-picture', illushold:'hourglass-done', bookmark:'bookmark',
      autoswitch:'counterclockwise-arrows-button', flip:null, dock:null, collapse:null,
      speed:'high-voltage', size:'input-latin-letters',
      play:'play-button', pause:'pause-button', skipback:'fast-reverse-button', skipfwd:'fast-forward-button',
      navtheme:'artist-palette', navbackground:'framed-picture',
      navtypography:'input-latin-letters', navintegrations:'electric-plug',
      player:'headphone', comfort:'crescent-moon',
      sidebarhide:'bookmark-tabs', sidebarshow:'bookmark-tabs',
      save:'floppy-disk', add:'plus', rename:'pencil', delete:'wastebasket',
    }),
  },
  {
    id:'emoji-google', name:'Emoji (Google)', group:'Fun',
    names: pre('noto', {
      library:'books', open:'open-file-folder', pageview:'open-book',
      immersive:'up-right-arrow', draw:'pencil', find:'magnifying-glass-tilted-left',
      export:'outbox-tray', popout:'right-arrow', sidebar:null, settings:'gear',
      fill:'artist-palette', strip:null, stripstyle:'level-slider',
      illuspanel:'framed-picture', illushold:'hourglass-done', bookmark:'bookmark',
      autoswitch:'counterclockwise-arrows-button', flip:null, dock:null, collapse:null,
      speed:'high-voltage', size:'input-latin-letters',
      play:'play-button', pause:'pause-button', skipback:'fast-reverse-button', skipfwd:'fast-forward-button',
      navtheme:'artist-palette', navbackground:'framed-picture',
      navtypography:'input-latin-letters', navintegrations:'electric-plug',
      player:'headphone', comfort:'crescent-moon',
      sidebarhide:'bookmark-tabs', sidebarshow:'bookmark-tabs',
      save:'floppy-disk', add:'plus', rename:'pencil', delete:'wastebasket',
    }),
  },
  {
    id:'scifi', name:'Sci-Fi / Fantasy', group:'Fun',
    names: pre('game-icons', {
      library:'book-cover', open:'open-folder', pageview:'open-book', immersive:'expand',
      draw:'feather', find:'magnifying-glass', export:'share', popout:'exit-door',
      sidebar:null, settings:'gears', fill:'paint-bucket', strip:'horizontal-flip',
      stripstyle:'horizontal-flip', illuspanel:'mountains', illushold:'sands-of-time',
      bookmark:'bookmarklet', autoswitch:'cycle', flip:'arrows-shield', dock:null,
      collapse:'previous-button',
      speed:'speedometer', size:'resize',
      play:'play-button', pause:'pause-button', skipback:'previous-button', skipfwd:'next-button',
      navtheme:'palette', navbackground:'mountains', navtypography:'spell-book',
      navintegrations:'gears', player:'headphones', comfort:'moon',
      sidebarhide:'scroll-unfurled', sidebarshow:'scroll-unfurled',
      save:'save-arrow', add:'health-normal', rename:'feather', delete:'trash-can',
    }),
  },
  // ── Professional ──
  { id:'minimal', name:'Minimal', group:'Professional', names: pre('lucide', LUCIDE) },
  {
    id:'technical', name:'Technical', group:'Professional',
    names: pre('tabler', {
      library:'books', open:'folder-open', pageview:'book', immersive:'maximize',
      draw:'pencil', find:'search', export:'share', popout:'external-link',
      sidebar:'layout-sidebar', settings:'settings', fill:'paint',
      strip:'border-horizontal', stripstyle:'adjustments-horizontal', illuspanel:'photo',
      illushold:'clock', bookmark:'bookmark', autoswitch:'refresh',
      flip:'arrows-left-right', dock:'layout-sidebar-right', collapse:'chevron-left',
      speed:'gauge', size:'text-size',
      play:'player-play-filled', pause:'player-pause-filled', skipback:'player-skip-back-filled', skipfwd:'player-skip-forward-filled',
      navtheme:'palette', navbackground:'photo', navtypography:'typography',
      navintegrations:'plug', player:'headphones', comfort:'moon', collage:'layout-collage',
      sidebarhide:'layout-sidebar', sidebarshow:'layout-sidebar',
      save:'device-floppy', add:'plus', rename:'pencil', delete:'trash',
    }),
  },
  {
    id:'solid', name:'Solid', group:'Professional',
    names: pre('ph', {
      library:'books-fill', open:'folder-open-fill', pageview:'book-open-fill',
      immersive:'arrows-out-fill', draw:'pencil-simple-fill', find:'magnifying-glass-fill',
      export:'export-fill', popout:'arrow-square-out-fill', sidebar:'sidebar-simple-fill',
      settings:'gear-six-fill', fill:'paint-bucket-fill', strip:'rows-fill',
      stripstyle:'sliders-horizontal-fill', illuspanel:'image-fill', illushold:'timer-fill',
      bookmark:'bookmark-simple-fill', autoswitch:'arrows-clockwise-fill',
      flip:'arrows-left-right-fill', dock:'sidebar-fill', collapse:'caret-left-fill',
      speed:'gauge-fill', size:'text-aa-fill',
      play:'play-fill', pause:'pause-fill', skipback:'skip-back-fill', skipfwd:'skip-forward-fill',
      navtheme:'palette-fill', navbackground:'image-fill', navtypography:'text-aa-fill',
      navintegrations:'plug-fill', player:'headphones-fill', comfort:'moon-fill',
      sidebarhide:'sidebar-simple-fill', sidebarshow:'sidebar-simple-fill',
      save:'floppy-disk-fill', add:'plus-fill', rename:'pencil-fill', delete:'trash-fill',
    }),
  },
];

const DEFAULT_SET = 'pixel';

// ── Fetch with a tiny cache so the Lucide fallbacks aren't re-fetched ───────
const cache = new Map();
async function fetchSVG(fullName) {
  if (cache.has(fullName)) return cache.get(fullName);
  const [coll, ...rest] = fullName.split(':');
  const name = rest.join(':');
  const url = `${API}/${coll}/${name}.svg`;
  let svg = null;
  try {
    const res = await fetch(url);
    if (res.ok) {
      const txt = await res.text();
      if (txt.trim().startsWith('<svg')) svg = txt.trim();
    }
  } catch (_) {}
  cache.set(fullName, svg);
  return svg;
}

// ── Build ───────────────────────────────────────────────────────────────────
const missing = [];
const out = { defaultSet: DEFAULT_SET, sets: [], icons: {} };

// Pre-fetch all Lucide fallbacks first (every key guaranteed present).
const fallback = {};
for (const key of KEYS) {
  const svg = await fetchSVG(`lucide:${LUCIDE[key]}`);
  if (!svg) { missing.push(`lucide:${LUCIDE[key]} (FALLBACK — must fix!)`); }
  fallback[key] = svg || '<svg viewBox="0 0 24 24"></svg>';
}

for (const set of SETS) {
  out.sets.push({ id: set.id, name: set.name, group: set.group });
  out.icons[set.id] = {};
  for (const key of KEYS) {
    const named = set.names[key];
    let svg = null;
    if (named) {
      svg = await fetchSVG(named);
      if (!svg) missing.push(`${set.id}/${key} → ${named}`);
    }
    out.icons[set.id][key] = svg || fallback[key];   // graceful fallback
  }
  process.stdout.write(`✓ ${set.name} (${set.id})\n`);
}

const banner = '/* AUTO-GENERATED by build-icons.mjs — do not edit by hand. */\n';
writeFileSync('static/icons-data.js',
  banner + 'window.LEMUR_ICONS = ' + JSON.stringify(out) + ';\n');

const sizeKB = Math.round(Buffer.byteLength(JSON.stringify(out)) / 1024);
console.log(`\nWrote static/icons-data.js (${sizeKB} KB, ${SETS.length} sets, ${KEYS.length} icons each)`);
if (missing.length) {
  console.log(`\n⚠ ${missing.length} icon(s) fell back to Lucide (name needs fixing):`);
  for (const m of missing) console.log('   ' + m);
} else {
  console.log('\nAll icon names resolved cleanly. ✨');
}
