// Discord Rich Presence for Lemur.
//
// Connects to the user's locally-running Discord desktop client over its IPC
// socket and reports what they're reading ("Reading <Title> · 34%"). Entirely
// local + per-user: nothing shows unless that person has Discord running on
// the same machine AND has enabled the toggle in Settings.
//
// Fails soft — if Discord isn't installed/running the app continues normally;
// presence is a nice-to-have. All steps are logged via the injected logger so
// problems are diagnosable in electron.log instead of vanishing.

const { Client } = require('@xhayper/discord-rpc');

// ── Discord Application ID ──────────────────────────────────────────────────
// From https://discord.com/developers/applications. Art assets ('lemur_logo')
// are uploaded under that app's Rich Presence → Art Assets.
const APP_ID = process.env.DISCORD_APP_ID || '1510870053611114556';

let client = null;
let ready = false;
let enabled = false;
let connecting = false;
let pending = null;          // last activity requested before we were ready
const startTimestamp = Date.now();

// Logger is injected by main.js so messages land in electron.log.
let log = () => {};
function setLogger(fn) { if (typeof fn === 'function') log = fn; }

function isConfigured() {
  return APP_ID && APP_ID !== 'YOUR_DISCORD_APP_ID_HERE';
}

// Connect lazily — only when presence is first enabled.
async function connect() {
  if (client || connecting) return;
  if (!isConfigured()) { log('[Discord] not configured — no APP_ID'); return; }
  connecting = true;
  log('[Discord] connecting (app ' + APP_ID + ')…');
  try {
    client = new Client({ clientId: APP_ID });

    client.on('ready', () => {
      ready = true;
      log('[Discord] ready — connected as ' +
          (client.user?.username || client.user?.id || 'unknown'));
      if (pending) { const p = pending; pending = null; setActivity(p); }
      else setIdle();
    });
    client.on('disconnected', () => { ready = false; log('[Discord] disconnected'); });

    await client.login();
  } catch (err) {
    // Most common: Discord desktop app not running, so the IPC socket is absent.
    log('[Discord] connect failed: ' + (err && err.message ? err.message : err));
    try { await client?.destroy?.(); } catch (_) {}
    client = null;
    ready = false;
  } finally {
    connecting = false;
  }
}

async function disconnect() {
  ready = false;
  if (client) {
    log('[Discord] clearing activity & disconnecting');
    try { await client.user?.clearActivity?.(); } catch (_) {}
    try { await client.destroy(); } catch (_) {}
    client = null;
  }
}

function _setActivityRaw(payload, label) {
  if (!client || !client.user) { log('[Discord] ' + label + ' skipped — no user yet'); return; }
  client.user.setActivity(payload)
    .then(() => log('[Discord] activity set: ' + label))
    .catch(err => log('[Discord] setActivity error: ' +
                      (err && err.message ? err.message : err)));
}

// Default "in the app, not reading anything" presence.
function setIdle() {
  if (!enabled || !ready) return;
  _setActivityRaw({
    details: 'In the library',
    largeImageKey: 'lemur_logo',
    largeImageText: 'Lemur',
    startTimestamp,
    instance: false,
  }, 'idle');
}

// activity: { title, percent } from the renderer.
function setActivity(activity) {
  if (!enabled) return;
  if (!ready || !client) { pending = activity; connect(); return; }
  if (!activity || !activity.title) { setIdle(); return; }

  const pct = Math.max(0, Math.min(100, Math.round(activity.percent || 0)));
  const title = String(activity.title).slice(0, 96);

  _setActivityRaw({
    details: `Reading ${title}`,
    state: `${pct}% complete`,
    largeImageKey: 'lemur_logo',
    largeImageText: 'Lemur',
    startTimestamp,
    instance: false,
  }, `Reading ${title} · ${pct}%`);
}

// Toggle from Settings. on=true connects + starts showing; on=false clears.
function setEnabled(on) {
  enabled = !!on;
  log('[Discord] setEnabled(' + enabled + ')');
  if (enabled) connect();
  else disconnect();
}

module.exports = { setEnabled, setActivity, isConfigured, setLogger };
