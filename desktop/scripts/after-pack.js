// macOS: an app that is not signed by a paid Apple developer account still needs every program in it signed (Apple
// Silicon runs nothing unsigned). Sign them ad hoc: the bundled Python, its libraries and the chart server first, then
// the app. People open it once through System Settings > Privacy & Security > Open Anyway (README).
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function machO(file) {
  try {
    const fd = fs.openSync(file, 'r');
    const b = Buffer.alloc(4);
    fs.readSync(fd, b, 0, 4, 0);
    fs.closeSync(fd);
    const m = b.readUInt32BE(0);
    return [0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe].includes(m);
  } catch {
    return false;
  }
}

function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isSymbolicLink()) continue;
    if (e.isDirectory()) walk(p, out);
    else if (machO(p)) out.push(p);
  }
  return out;
}

exports.default = async function (ctx) {
  if (ctx.electronPlatformName !== 'darwin') return;
  const appPath = path.join(ctx.appOutDir, `${ctx.packager.appInfo.productFilename}.app`);
  const res = path.join(appPath, 'Contents', 'Resources');
  const bins = walk(res, []);
  for (const f of bins) execFileSync('codesign', ['--force', '--sign', '-', f]);
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath]);
  console.log(`  ad hoc signed ${bins.length} programs in Resources and the app`);
};
