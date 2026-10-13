// Cameo Bridge: tab facts and tab actions over native messaging (ARCHITECTURE.md §4.6).
// No content scripts and no host permissions: web pages cannot reach this code.
const HOST = 'dev.cameo.bridge';
let port = null;

function send(message) { try { port?.postMessage(message); } catch { /* reconnect handles it */ } }

async function snapshot() {
  const windows = await chrome.windows.getAll({ populate: true, windowTypes: ['normal'] });
  const focused = await chrome.windows.getLastFocused({ windowTypes: ['normal'] }).catch(() => null);
  return {
    focusedWindowId: focused?.id ?? null,
    windows: windows.map(w => ({
      id: w.id, focused: w.focused, state: w.state, left: w.left, top: w.top, width: w.width, height: w.height,
      tabs: w.tabs.map(t => ({ id: t.id, index: t.index, active: t.active, title: (t.title || '').slice(0, 200) })),
    })),
  };
}

const OPS = {
  snapshot: () => snapshot(),
  create: async ({ windowId }) => ({ tabId: (await chrome.tabs.create({ windowId, active: true })).id }),
  activate: async ({ tabId }) => { await chrome.tabs.update(tabId, { active: true }); return { tabId }; },
  remove: async ({ tabId }) => { await chrome.tabs.remove(tabId); return { tabId }; },
  // Grab and drop between devices: the page in the front window (sender), and opening one (receiver).
  page: async () => {
    const w = await chrome.windows.getLastFocused({ populate: true, windowTypes: ['normal'] }).catch(() => null);
    const tab = w?.tabs?.find(t => t.active);
    return tab ? { tabId: tab.id, url: tab.url || '', title: (tab.title || '').slice(0, 300) } : null;
  },
  open: async ({ url }) => {
    if (!/^https?:\/\//i.test(url || '')) throw new Error('only http and https pages open from another device');
    const w = await chrome.windows.getLastFocused({ windowTypes: ['normal'] }).catch(() => null);
    if (!w) {
      const created = await chrome.windows.create({ url, focused: true });
      return { tabId: created.tabs?.[0]?.id ?? null, windowId: created.id };
    }
    const tab = await chrome.tabs.create({ windowId: w.id, url, active: true });
    await chrome.windows.update(w.id, { focused: true });
    return { tabId: tab.id, windowId: w.id };
  },
};

async function onRequest(message) {
  const op = OPS[message?.op];
  if (!op || typeof message.id !== 'string') return send({ id: message?.id ?? null, ok: false, error: 'unknown request' });
  try { send({ id: message.id, ok: true, result: await op(message.args || {}) }); }
  catch (error) { send({ id: message.id, ok: false, error: String(error?.message || error) }); }
}

// Events are the evidence the core waits for.
chrome.tabs.onCreated.addListener(tab => send({ event: 'tab.created', tabId: tab.id, windowId: tab.windowId, index: tab.index }));
chrome.tabs.onActivated.addListener(info => chrome.tabs.get(info.tabId).then(tab => send({ event: 'tab.activated', tabId: tab.id, windowId: tab.windowId, index: tab.index })).catch(() => {}));
chrome.tabs.onRemoved.addListener((tabId, info) => send({ event: 'tab.removed', tabId, windowId: info.windowId }));

function connect() {
  if (port) return;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener(onRequest);
  port.onDisconnect.addListener(() => { port = null; setTimeout(connect, 3000); });
  snapshot().then(state => send({ event: 'hello', ...state }));
}
// Stay connected permanently: on browser start, on install/reload, and every minute an alarm wakes
// the service worker (timers die with a sleeping worker; alarms do not). The open native port keeps it awake.
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.alarms.create('cameo-keepalive', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'cameo-keepalive') connect(); });
connect();
