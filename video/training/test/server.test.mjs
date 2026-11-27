import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { startServer, committedConfig } from '../lib/server.mjs';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const md5 = (file) => crypto.createHash('md5').update(fs.readFileSync(file)).digest('hex');

function connects(port) {
  return new Promise((resolve) => {
    const s = net.connect(port, '127.0.0.1');
    s.once('connect', () => { s.destroy(); resolve(true); });
    s.once('error', () => resolve(false));
  });
}

test('the temporary server serves the dashboard API', async () => {
  const server = await startServer({ repoRoot: REPO });
  try {
    const cores = await (await fetch(server.url + '/cores')).json();
    assert.ok(JSON.stringify(cores).includes('pru0'), JSON.stringify(cores));
  } finally { await server.stop(); }
});

test('a clock change on the temporary server leaves the repo memory.cfg alone', async () => {
  const before = md5(path.join(REPO, 'memory.cfg'));
  const server = await startServer({ repoRoot: REPO });
  try {
    const reply = await fetch(server.url + '/config/clock_speed', {
      method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ mhz: 200 }) });
    assert.deepEqual(await reply.json(), { ok: true });
    const copy = fs.readFileSync(path.join(server.root, 'memory.cfg'), 'utf8');
    assert.match(copy, /pru_clock_mhz = 200/);          // the copy was rewritten ...
  } finally { await server.stop(); }
  assert.equal(md5(path.join(REPO, 'memory.cfg')), before);   // ... the repo file was not
});

test('stop() removes the copy and frees the port', async () => {
  const server = await startServer({ repoRoot: REPO });
  assert.ok(fs.existsSync(server.root));
  assert.equal(await connects(server.port), true);
  await server.stop();
  assert.equal(fs.existsSync(server.root), false);
  assert.equal(await connects(server.port), false);
});

test('the copy gets the committed memory.cfg, not a locally edited one', () => {
  // The dashboard's clock selector rewrites memory.cfg, so a working copy often differs from HEAD.
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'cfg-test-'));
  try {
    const git = (...args) => execFileSync('git', ['-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', ...args]);
    git('init', '-q');
    fs.writeFileSync(path.join(repo, 'memory.cfg'), '[device]\npru_clock_mhz = 250\n');
    git('add', 'memory.cfg'); git('commit', '-q', '-m', 'cfg');
    fs.writeFileSync(path.join(repo, 'memory.cfg'), '[device]\npru_clock_mhz = 200\n');
    assert.match(committedConfig(repo).toString(), /pru_clock_mhz = 250/);
  } finally { fs.rmSync(repo, { recursive: true, force: true }); }
});

test('outside a git repository there is no committed memory.cfg', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cfg-test-'));
  try { assert.equal(committedConfig(dir), null); } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
