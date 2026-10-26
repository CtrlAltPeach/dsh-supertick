// Development-only exact snapshot of this task's two NEW files (not the whole tree).
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { gzipSync, gunzipSync } from 'node:zlib';
import assert from 'node:assert/strict';
const root = new URL('../', import.meta.url);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const owned = ['monitor.mjs', 'tests/monitor.test.mjs'];
if (process.argv[2] === 'create') {
  console.log(JSON.stringify({ task:'monitor-core', attempt:1, encoding:'gzip+base64', files:owned.map(path => {
    const bytes = readFileSync(new URL(path, root));
    return { path, before:null, after_sha256:hash(bytes), after_content:gzipSync(bytes).toString('base64') };
  }) }, null, 2));
} else if (process.argv[2] === 'check') {
  const bytes = readFileSync(process.argv[3]);
  const change = JSON.parse(bytes);
  assert.equal(change.task, 'monitor-core');
  assert.equal(change.attempt, 1);
  assert.equal(change.encoding, 'gzip+base64');
  assert.deepEqual(change.files.map(file => file.path), owned);
  for (const file of change.files) {
    const exact = gunzipSync(Buffer.from(file.after_content, 'base64'));
    assert.equal(hash(exact), file.after_sha256);
    assert.deepEqual(readFileSync(new URL(file.path, root)), exact, `stale snapshot: ${file.path}`);
  }
  console.log(JSON.stringify({status:'PASS', change_id:hash(bytes), files:owned.length, exact_current_bytes:true}));
} else throw new Error('Usage: change-snapshot.mjs create | check CHANGE.json');
