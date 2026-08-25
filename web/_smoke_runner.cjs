const esbuild = require('E:/work/code/agent-dev/mio-taskhub/web/node_modules/esbuild');
esbuild.build({
  entryPoints: ['E:/work/code/agent-dev/mio-taskhub/web/_smoke_entry.mjs'],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  outfile: 'E:/work/code/agent-dev/mio-taskhub/scripts/_smoke.bundle.cjs',
  logLevel: 'info',
}).then(() => {
  const { spawnSync } = require('child_process');
  const r = spawnSync(process.execPath, ['E:/work/code/agent-dev/mio-taskhub/scripts/_smoke.bundle.cjs'], { stdio: 'inherit' });
  process.exit(r.status ?? 1);
}).catch(e => { console.error(e); process.exit(1); });
