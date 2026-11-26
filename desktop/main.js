// Chart Desk desktop app: one window over the app's web build, served by the bundled chart server (opentrader-server,
// 127.0.0.1:8799), with prices from the bundled data server (chartd, 127.0.0.1:8798). Both are started here and
// stopped on quit; a server already answering on its port (chartd as a Linux user service, or a copy left from a
// crash) is used as it is. Closing the window keeps the app in the tray so alerts keep firing; Quit stops it all.
// The window talks to this computer only: requests to anything but localhost are refused.
const { app, BrowserWindow, Menu, Notification, Tray, dialog, nativeImage, session, shell } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const PRODUCT = 'Chart Desk';
const REPO = 'ElRroRo/chartdesk';
const CHARTD_PORT = 8798;
const SERVER_PORT = 8799;
const WIN = process.platform === 'win32';
const MAC = process.platform === 'darwin';

app.setName(PRODUCT);
if (process.platform === 'linux') {
  app.setDesktopName?.('chartdesk.desktop'); // KDE/GNOME group the window under the menu entry and its icon
  if (!process.env.OZONE_DEFAULT) app.commandLine.appendSwitch('ozone-platform-hint', 'auto'); // Wayland when there
}
if (process.env.GPU_NODE) app.commandLine.appendSwitch('render-node-override', process.env.GPU_NODE);
if (process.env.PROFILE) app.setPath('userData', process.env.PROFILE);

// ── where things are: packaged (resources/) or a checkout (dev) ──
const RES = app.isPackaged ? process.resourcesPath : null;
const REPO_DIR = path.join(__dirname, '..');
const paths = {
  web: process.env.CHARTDESK_WEB || (RES ? path.join(RES, 'web') : path.join(REPO_DIR, 'dist')),
  server: process.env.CHARTDESK_SERVER || (RES
    ? path.join(RES, 'bin', WIN ? 'opentrader-server.exe' : 'opentrader-server')
    : path.join(REPO_DIR, 'src-tauri', 'target', 'release', WIN ? 'opentrader-server.exe' : 'opentrader-server')),
  python: RES ? path.join(RES, 'python', WIN ? 'python.exe' : path.join('bin', 'python3')) : null,
  icon: RES ? path.join(RES, 'icon.png') : path.join(__dirname, 'build', 'icon.png'),
};
const data = (...p) => path.join(app.getPath('userData'), ...p);

// ── child processes ──
const children = [];
let quitting = false;

function get(port, p, ms = 1500) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: p, timeout: ms }, (r) => {
      r.resume();
      resolve(r.statusCode);
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => resolve(0));
  });
}

function logTail(file, n = 15) {
  try {
    return fs.readFileSync(file, 'utf8').trim().split(/\r?\n/).slice(-n).join('\n');
  } catch {
    return '';
  }
}

function start(name, cmd, args, env) {
  fs.mkdirSync(data('logs'), { recursive: true });
  const log = data('logs', `${name}.log`);
  const out = fs.openSync(log, 'w');
  const child = spawn(cmd, args, { env: { ...process.env, ...env }, stdio: ['ignore', out, out], windowsHide: true });
  child.on('exit', (code) => {
    if (!quitting) {
      dialog.showErrorBox(`${PRODUCT}: ${name} stopped`, `${name} exited (code ${code}).\n\n${logTail(log)}\n\nLog: ${log}`);
    }
  });
  children.push(child);
  return log;
}

async function ensure(name, port, healthPath, launch) {
  if ((await get(port, healthPath)) === 200) return { own: false };
  const log = launch();
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if ((await get(port, healthPath)) === 200) return { own: true };
  }
  throw new Error(`${name} did not start on port ${port}.\n\n${logTail(log)}\n\nLog: ${log}`);
}

function startChartd() {
  return ensure('chartd', CHARTD_PORT, '/health', () => {
    const env = { CHARTD_DATA: process.env.CHARTD_DATA || data('chartd'), CHARTD_NOTIFY: 'off', PYTHONNOUSERSITE: '1' };
    if (paths.python) return start('chartd', paths.python, ['-m', 'chartd.server', '--port', String(CHARTD_PORT)], env);
    const dev = path.join(REPO_DIR, 'chartd', '.venv', WIN ? 'Scripts' : 'bin', WIN ? 'chartd.exe' : 'chartd');
    return start('chartd', process.env.CHARTD_CMD || dev, ['--port', String(CHARTD_PORT)], env);
  });
}

function startServer() {
  return ensure('chart server', SERVER_PORT, '/', () =>
    start('server', paths.server, [], {
      OPENTRADER_BIND: `127.0.0.1:${SERVER_PORT}`,
      OPENTRADER_STATIC_DIR: paths.web,
      OPENTRADER_CACHE_DIR: data('cache'),
      OPENTRADER_GATEWAY_TOKEN: 'local',
      OPENTRADER_SCREENER_FEED: 'off',
      OPENTRADER_RATE_PER_MIN: '0',
      OPENTRADER_ALLOWED_ORIGINS: `http://127.0.0.1:${SERVER_PORT},http://localhost:${SERVER_PORT}`,
    }),
  );
}

function stopChildren() {
  quitting = true;
  for (const c of children) {
    try {
      c.kill();
    } catch {
      /* already gone */
    }
  }
}

// ── window ──
let win = null;
let tray = null;
let keepInTray = true;
const BOUNDS = () => data('window-bounds.json');

const SPLASH = `data:text/html;charset=utf-8,${encodeURIComponent(`<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#131722;color:#b2b5be;font:15px 'DejaVu Sans','Segoe UI','Helvetica Neue',sans-serif">Starting ${PRODUCT}…</body></html>`)}`;

function createWindow(visible = true) {
  let saved = {};
  try {
    saved = JSON.parse(fs.readFileSync(BOUNDS(), 'utf8'));
  } catch {
    /* first run */
  }
  win = new BrowserWindow({
    width: saved.width || 1600,
    height: saved.height || 950,
    x: saved.x,
    y: saved.y,
    title: PRODUCT,
    icon: fs.existsSync(paths.icon) ? paths.icon : undefined,
    backgroundColor: '#131722',
    autoHideMenuBar: true,
    show: visible,
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  if (saved.maximized) win.maximize();
  const keep = () => {
    try {
      const b = win.isMaximized() ? saved : win.getNormalBounds();
      fs.writeFileSync(BOUNDS(), JSON.stringify({ ...b, maximized: win.isMaximized() }));
    } catch {
      /* read-only profile */
    }
  };
  win.on('close', (e) => {
    keep();
    if (keepInTray && !quitting) {
      // Hidden, not closed: the page keeps running, so its alerts (price, indicator, drawing) keep firing.
      e.preventDefault();
      win.hide();
      trayHint();
    }
  });
  win.on('closed', () => (win = null));
  // Links out of the app (a filing, a website) open in the default browser, never inside the window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) && !/^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.loadURL(SPLASH);
}

function show() {
  if (!win) createWindow();
  win.show();
  win.focus();
}

function trayHint() {
  const flag = data('tray-hint-shown');
  if (fs.existsSync(flag)) return;
  fs.writeFileSync(flag, '1');
  new Notification({
    title: `${PRODUCT} is still running`,
    body: `Alerts keep firing while it is in the ${MAC ? 'menu bar' : 'tray'}. Quit it from there.`,
  }).show();
}

// ── about: the credits the licences ask for (OpenTrader, MIT; TradingView Lightweight Charts, Apache-2.0 + NOTICE) ──
const CREDITS = [
  'Built on OpenTrader (MIT licence), github.com/deepentropy/opentrader.',
  'Charts: TradingView Lightweight Charts™, Copyright © 2025 TradingView, Inc. (Apache-2.0), https://www.tradingview.com/.',
  'Prices: Yahoo Finance, Binance, FRED, Is Yatirim and the other public sources named on each chart.',
  'Chart Desk is not affiliated with TradingView, and nothing in it is investment advice.',
].join('\n\n');

async function about() {
  const r = await dialog.showMessageBox({
    title: `About ${PRODUCT}`,
    message: `${PRODUCT} ${app.getVersion()}`,
    detail: CREDITS,
    buttons: ['OK', 'tradingview.com', 'Source code'],
    defaultId: 0,
  });
  if (r.response === 1) shell.openExternal('https://www.tradingview.com/');
  if (r.response === 2) shell.openExternal(`https://github.com/${REPO}`);
}

// ── tray ──
let update = null; // { version, url } when a newer release is out

function trayMenu() {
  const login = app.getLoginItemSettings();
  return Menu.buildFromTemplate([
    { label: `Open ${PRODUCT}`, click: show },
    ...(update ? [{ label: `Download ${update.version}…`, click: () => shell.openExternal(update.url) }] : []),
    { type: 'separator' },
    {
      label: 'Start at login (in the tray)',
      type: 'checkbox',
      checked: login.openAtLogin,
      click: (m) => app.setLoginItemSettings({ openAtLogin: m.checked, openAsHidden: true, args: ['--hidden'] }),
    },
    { label: 'Python indicators folder', click: () => openFolder(pyFolder()) },
    { label: 'Logs folder', click: () => openFolder(data('logs')) },
    { label: 'Check for updates', click: () => checkUpdate(true) },
    { label: `About ${PRODUCT}`, click: about },
    { type: 'separator' },
    { label: `Quit ${PRODUCT}`, click: () => app.quit() },
  ]);
}

function pyFolder() {
  return process.env.CHARTD_INDICATORS || path.join(app.getPath('home'), 'chartdesk', 'indicators');
}

function openFolder(p) {
  fs.mkdirSync(p, { recursive: true });
  shell.openPath(p);
}

function makeTray() {
  let img = nativeImage.createFromPath(paths.icon);
  if (!img.isEmpty()) img = img.resize({ width: MAC ? 18 : 22, height: MAC ? 18 : 22 });
  tray = new Tray(img);
  tray.setToolTip(PRODUCT);
  tray.setContextMenu(trayMenu());
  tray.on('click', show);
}

// ── updates: a note when GitHub has a newer release (nothing is downloaded by itself) ──
function newer(a, b) {
  const p = (v) => v.replace(/^v/, '').split(/[.-]/).map((x) => parseInt(x, 10) || 0);
  const [x, y] = [p(a), p(b)];
  for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  return false;
}

async function checkUpdate(manual = false) {
  try {
    const r = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { Accept: 'application/vnd.github+json' } });
    if (!r.ok) throw new Error(`GitHub answered ${r.status}`);
    const j = await r.json();
    if (j.tag_name && newer(j.tag_name, app.getVersion())) {
      update = { version: j.tag_name, url: j.html_url };
      tray?.setContextMenu(trayMenu());
      const n = new Notification({ title: `${PRODUCT} ${j.tag_name} is out`, body: 'Click to open the download page.' });
      n.on('click', () => shell.openExternal(j.html_url));
      n.show();
    } else if (manual) {
      dialog.showMessageBox({ message: `${PRODUCT} ${app.getVersion()} is the latest version.` });
    }
  } catch (e) {
    if (manual) dialog.showMessageBox({ type: 'warning', message: `Could not check for updates: ${e.message}` });
  }
}

// ── start ──
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', show);
  app.on('activate', show); // macOS: the Dock icon
  app.on('before-quit', () => {
    quitting = true;
    stopChildren();
  });
  app.on('window-all-closed', () => {
    if (!keepInTray) app.quit();
  });

  app.whenReady().then(async () => {
    if (!MAC) Menu.setApplicationMenu(null);
    else {
      // macOS keeps its menu bar (copy, paste, quit shortcuts); About shows the credits.
      const m = Menu.buildFromTemplate([
        { label: PRODUCT, submenu: [{ label: `About ${PRODUCT}`, click: about }, { label: 'Check for updates', click: () => checkUpdate(true) }, { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { type: 'separator' }, { role: 'quit' }] },
        { role: 'editMenu' },
        { role: 'viewMenu' },
        { role: 'windowMenu' },
      ]);
      Menu.setApplicationMenu(m);
    }
    session.defaultSession.webRequest.onBeforeRequest((d, cb) => {
      const u = new URL(d.url);
      const ok = ['127.0.0.1', 'localhost', ''].includes(u.hostname) || !/^(https?|wss?):$/.test(u.protocol);
      cb({ cancel: !ok });
    });
    const hidden = process.argv.includes('--hidden') || app.getLoginItemSettings().wasOpenedAsHidden;
    createWindow(!hidden);
    let chartd;
    try {
      chartd = await startChartd();
      await startServer();
    } catch (e) {
      dialog.showErrorBox(`${PRODUCT} could not start`, String(e.message || e));
      stopChildren();
      app.exit(1);
      return;
    }
    // A chartd this app did not start (a Linux user service) keeps alerting after the window closes: closing quits.
    keepInTray = chartd.own || process.env.CHARTDESK_TRAY === '1';
    if (keepInTray) makeTray();
    if (!win) createWindow(!hidden);
    win.loadURL(process.env.APP_URL || `http://127.0.0.1:${SERVER_PORT}/`);
    if (app.isPackaged) {
      setTimeout(() => checkUpdate(false), 15_000);
      setInterval(() => checkUpdate(false), 24 * 3600_000);
    }
  });
}
