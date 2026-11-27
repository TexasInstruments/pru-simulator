// Runs the dashboard server from a temporary copy of the tree.
//
// ui/server.py rewrites memory.cfg (a clock change saves it) and looks the file up
// beside itself, not in the working directory. Recording must never touch the repo's
// own memory.cfg, so the server always runs from a copy that is deleted afterwards.
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

const SKIP = new Set(['.git', '.venv', 'tests', 'docs', 'references', 'video', 'node_modules',
  '__pycache__', '.pytest_cache', '.pru_runtime', '.superpowers', '.claude', '.worktrees']);

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => { const { port } = srv.address(); srv.close(() => resolve(port)); });
  });
}

/**
 * memory.cfg as committed, or null outside a git repository. The dashboard's clock selector
 * rewrites the file in place, so a working copy often holds another core clock than HEAD;
 * the tours start from the committed one.
 */
export function committedConfig(repoRoot) {
  try {
    return execFileSync('git', ['-C', repoRoot, 'show', 'HEAD:./memory.cfg'], { stdio: ['ignore', 'pipe', 'ignore'] });
  } catch { return null; }
}

const live = new Set();   // servers to stop if the process ends first
let hooked = false;
function hookExit() {
  if (hooked) return;
  hooked = true;
  const killAll = () => { for (const s of live) s.killNow(); };
  process.on('exit', killAll);
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { killAll(); process.exit(130); });
}

/**
 * @param {{repoRoot: string, python?: string}} opts
 * @returns {Promise<{url: string, port: number, root: string, stop(): Promise<void>}>}
 */
export async function startServer({ repoRoot, python = process.env.PRU_PYTHON || 'python3' }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pru-training-'));
  // Skip by name below the root only: the repository folder itself may be called anything.
  fs.cpSync(repoRoot, root, { recursive: true, filter: (src) => src === repoRoot || !SKIP.has(path.basename(src)) });
  const config = committedConfig(repoRoot);
  if (config) fs.writeFileSync(path.join(root, 'memory.cfg'), config);
  const port = await freePort();
  const env = { ...process.env };
  delete env.PYTHONPATH;                        // a ROS profile puts foreign packages there
  const child = spawn(python, ['ui/server.py', '--port', String(port)], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '';
  child.stdout.on('data', (d) => { log += d; });
  child.stderr.on('data', (d) => { log += d; });
  let exited = false;
  const gone = new Promise((resolve) => child.once('exit', () => { exited = true; resolve(); }));
  child.once('error', (e) => { log += String(e); exited = true; });

  const handle = {
    url: `http://127.0.0.1:${port}`, port, root,
    killNow() {
      if (!exited) { try { child.kill('SIGKILL'); } catch { /* already gone */ } }
      fs.rmSync(root, { recursive: true, force: true });
    },
    async stop() {
      live.delete(handle);
      if (!exited) { child.kill('SIGTERM'); await Promise.race([gone, new Promise((r) => setTimeout(r, 3000))]); }
      if (!exited) { child.kill('SIGKILL'); await gone; }
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
  live.add(handle);
  hookExit();

  const deadline = Date.now() + 30000;
  for (;;) {
    if (exited) { handle.killNow(); live.delete(handle); throw new Error(`dashboard server exited at start (${python}):\n${log}`); }
    try { if ((await fetch(handle.url + '/cores')).ok) return handle; } catch { /* not up yet */ }
    if (Date.now() > deadline) { await handle.stop(); throw new Error(`dashboard server did not answer within 30 s:\n${log}`); }
    await new Promise((r) => setTimeout(r, 150));
  }
}
