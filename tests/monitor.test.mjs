import test from 'node:test';
import assert from 'node:assert/strict';
import { snapshot, LIMITS } from '../monitor.mjs';

// Minimal fake fs matching real dsh-fs-local API:
// resolve(path,{cwd,signal})->target; stat(target,signal)->{type,size,version}|undefined;
// readBytes(target, signal, maxBytes)->Uint8Array; contains(parent,child)->bool (sync);
// processPath(target)->absolute path.
function makeFake(files, opts = {}) {
  const enc = new TextEncoder();
  const entries = new Map();
  for (const [p, content] of Object.entries(files)) {
    entries.set('/run/' + p, typeof content === 'string' ? enc.encode(content) : content);
  }
  let version = 1;
  const seen = { statSignals: [], readSignals: [], readCaps: [] };
  const fs = {
    async resolve(p, { cwd, signal } = {}) {
      signal?.throwIfAborted();
      if (opts.resolveDeny && opts.resolveDeny(p)) throw new Error(`monitor: permission denied: ${p}`);
      const base = cwd === undefined ? '' : cwd;
      if (base === '') return p; // absolute runPath resolves to itself
      return base + '/' + p;
    },
    async stat(target, signal) {
      seen.statSignals.push(signal);
      signal?.throwIfAborted();
      if (opts.statError) throw opts.statError;
      const bytes = entries.get(target);
      if (!bytes) return undefined;
      return { type: 'file', size: bytes.length, version: String(version) };
    },
    async readBytes(target, signal, maxBytes) {
      seen.readSignals.push(signal);
      seen.readCaps.push(maxBytes);
      signal?.throwIfAborted();
      if (opts.readDeny && opts.readDeny(target)) throw new Error('monitor: permission denied');
      const bytes = entries.get(target);
      if (!bytes) return undefined;
      return bytes.slice(0, maxBytes ?? bytes.length);
    },
    contains(parent, child) {
      if (opts.containsDeny && opts.containsDeny(parent, child)) return false;
      return child.startsWith(parent + '/') || child === parent;
    },
    processPath(target) { return target; },
  };
  return { fs, seen, bump: () => { version++; } };
}

const RUN = JSON.stringify({
  schema: 1,
  name: 'demo',
  phase: 'coding',
  tasks: [
    { id: 'task-a', attempt: 1, status: 'DONE', title: 'A' },
    { id: 'task-b', attempt: 2, status: 'RUNNING', title: 'B' },
  ],
  agents: [{ id: 'agent-1', role: 'coder', status: 'working' }],
  jobs: [{ id: 'job-1', status: 'done' }],
});

const baseFiles = { 'RUN.json': RUN };

test('valid run: statuses, counts, honest flags', async () => {
  const { fs } = makeFake(baseFiles);
  const snap = await snapshot(fs, '/run');
  assert.equal(snap.name, 'demo');
  assert.equal(snap.phase, 'coding');
  assert.deepEqual(snap.counts, { TODO: 0, RUNNING: 1, REVIEW: 0, ACCEPTING: 0, DONE: 1, BLOCKED: 0 });
  assert.equal(snap.tasks.length, 2);
  assert.equal(snap.tasks[1].attempt, 2);
  assert.equal(snap.ledger_only, true);
  assert.equal(snap.native_status_checked, false);
  assert.equal(snap.gate_validation, 'not_run');
  // 2 tasks * 2 + verification = 5 known report paths, all missing
  assert.equal(snap.reports.length, 5);
  assert.ok(snap.reports.every(r => r.missing));
});

test('reports extracted with kind/verdict; revision changes on report-only edit', async () => {
  const files = {
    ...baseFiles,
    'reports/task-a/attempt-1/review.json': JSON.stringify({ kind: 'review', verdict: 'accept', extra: 'dropped' }),
  };
  const a = makeFake(files);
  const s1 = await snapshot(a.fs, '/run');
  const rep = s1.reports.find(r => r.path === 'reports/task-a/attempt-1/review.json');
  assert.equal(rep.present, true);
  assert.equal(rep.kind, 'review');
  assert.equal(rep.verdict, 'accept');
  assert.equal(rep.extra, undefined);

  const files2 = { ...files, 'reports/task-a/attempt-1/review.json': JSON.stringify({ kind: 'review', verdict: 'reject' }) };
  const b = makeFake(files2);
  const s2 = await snapshot(b.fs, '/run');
  assert.notEqual(s1.revision, s2.revision);

  // missing -> present also changes revision
  const c = makeFake(baseFiles);
  const s3 = await snapshot(c.fs, '/run');
  assert.notEqual(s3.revision, s1.revision);
});

test('invalid schema / types / statuses rejected', async () => {
  for (const [mut, re] of [
    [o => ({ ...o, schema: 2 }), /unsupported schema/],
    [o => ({ ...o, name: 5 }), /invalid name/],
    [o => ({ ...o, phase: null }), /invalid phase/],
    [o => ({ ...o, tasks: 'nope' }), /tasks must be an array/],
    [o => ({ ...o, tasks: [{ id: 'x', attempt: 1, status: 'WEIRD' }] }), /invalid task status/],
    [o => ({ ...o, tasks: [{ id: 'x', attempt: 0, status: 'TODO' }] }), /attempt must be positive int/],
    [o => ({ ...o, tasks: [{ id: 'x', attempt: 1.5, status: 'TODO' }] }), /attempt must be positive int/],
    [o => ({ ...o, tasks: [{ id: 'bad<id', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: 'aux', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: 'X', attempt: 1, status: 'TODO' }, { id: 'x', attempt: 1, status: 'TODO' }] }), /duplicate id/],
    [o => ({ ...o, tasks: [{ id: '.', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: '..', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: 'CON.txt', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: 'NUL.json', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: 'x\\y', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, tasks: [{ id: 'x/y', attempt: 1, status: 'TODO' }] }), /unsafe id/],
    [o => ({ ...o, agents: 3 }), /agents must be an array/],
    [o => ({ ...o, jobs: [{}] }), /missing id/],
  ]) {
    const { fs } = makeFake({ 'RUN.json': JSON.stringify(mut(JSON.parse(RUN))) });
    await assert.rejects(snapshot(fs, '/run'), re);
  }
});

test('oversize: stat-level and read-level enforcement', async () => {
  const big = new Uint8Array(LIMITS.runJson + 1);
  const { fs } = makeFake({ 'RUN.json': big });
  await assert.rejects(snapshot(fs, '/run'), /exceeds limit/);

  // lie in stat: size small, actual bytes big -> readBytes cap catches it.
  // Real oversize payload: 1 byte over the RUN.json cap.
  const bigPayload = new Uint8Array(LIMITS.runJson + 1);
  bigPayload.set(new TextEncoder().encode(JSON.stringify({ schema: 1, name: 'x', phase: 'p', tasks: [], agents: [], jobs: [] })));
  const fake2 = makeFake({ 'RUN.json': bigPayload });
  const realStat = fake2.fs.stat;
  fake2.fs.stat = async (t, s) => ({ ...(await realStat(t, s)), size: 10 });
  // readBytes honors maxBytes in the base fake, so bypass the cap to simulate a lying backend
  fake2.fs.readBytes = async (t, s, m) => bigPayload.slice(0, Math.max(m ?? 0, bigPayload.length));
  await assert.rejects(snapshot(fake2.fs, '/run'), /read exceeds limit/);

  // report too big
  const bigReport = 'y'.repeat(LIMITS.report + 1);
  const { fs: fs3 } = makeFake({ ...baseFiles, 'reports/verification.json': new TextEncoder().encode(bigReport) });
  await assert.rejects(snapshot(fs3, '/run'), /verification\.json.*exceeds limit/);
});

test('containment denial: escaping report target rejected', async () => {
  const fake = makeFake({ ...baseFiles, 'reports/verification.json': '{}' });
  fake.fs.contains = () => false;
  await assert.rejects(snapshot(fake.fs, '/run'), /escapes run directory/);
});

test('permission denial propagates (not treated as missing)', async () => {
  const files = { ...baseFiles, 'reports/verification.json': '{}' };
  const fake2 = makeFake(files, { readDeny: t => t.includes('verification') });
  await assert.rejects(snapshot(fake2.fs, '/run'), /permission denied/);
  // resolve-level denial too
  const fake3 = makeFake(files, { resolveDeny: p => p.includes('verification') });
  await assert.rejects(snapshot(fake3.fs, '/run'), /permission denied/);
});

// Real backend throws FsError with a `code` property.
function fsError(code, msg) { const e = new Error(msg); e.code = code; return e; }

test('FS_NOT_FOUND code treated as missing; FS_PERMISSION_DENIED with "not found" text propagates', async () => {
  // genuine FS_NOT_FOUND from readBytes -> report missing, not a crash
  const files = { ...baseFiles, 'reports/verification.json': '{}' };
  const fake = makeFake(files);
  fake.fs.readBytes = async (t, s, m) => {
    if (String(t).includes('verification')) throw fsError('FS_NOT_FOUND', 'no such file');
    const bytes = files['RUN.json'];
    return new TextEncoder().encode(bytes).slice(0, m ?? 1e9);
  };
  const snap = await snapshot(fake.fs, '/run');
  assert.equal(snap.reports.find(r => r.path === 'reports/verification.json').missing, true);

  // permission denial disguised with "not found" text must NOT be masked as missing
  const fake2 = makeFake(files);
  fake2.fs.readBytes = async (t, s, m) => { throw fsError('FS_PERMISSION_DENIED', 'not found: reports/verification.json'); };
  await assert.rejects(snapshot(fake2.fs, '/run'), e => e.code === 'FS_PERMISSION_DENIED');

  // RUN.json stage: FS_PERMISSION_DENIED propagates even with misleading text
  const fake3 = makeFake(baseFiles);
  fake3.fs.readBytes = async (t, s, m) => { throw fsError('FS_PERMISSION_DENIED', 'not found: RUN.json'); };
  await assert.rejects(snapshot(fake3.fs, '/run'), e => e.code === 'FS_PERMISSION_DENIED');
});

test('real signal object is passed to stat and readBytes; abort mid-run honored', async () => {
  const { fs, seen } = makeFake(baseFiles);
  const ac = new AbortController();
  await snapshot(fs, '/run', ac.signal);
  assert.ok(seen.statSignals.length >= 1);
  assert.ok(seen.readSignals.length >= 1);
  assert.ok(seen.statSignals.every(s => s === ac.signal));
  assert.ok(seen.readSignals.every(s => s === ac.signal));
  // abort after RUN.json read (before report loop) still triggers
  const ac2 = new AbortController();
  const f2 = makeFake({ ...baseFiles, 'reports/verification.json': '{}' });
  const origRead = f2.fs.readBytes;
  let calls = 0;
  f2.fs.readBytes = async (...a) => { if (++calls === 2) ac2.abort(); return origRead(...a); };
  await assert.rejects(snapshot(f2.fs, '/run', ac2.signal), /abort/i);
});

test('RUN.json target must be inside canonical run target (containment)', async () => {
  const fake = makeFake(baseFiles);
  fake.fs.contains = (parent, child) => child.includes('RUN.json') ? false : true;
  await assert.rejects(snapshot(fake.fs, '/run'), /RUN.json escapes run directory/);
});

test('aggregate total limit enforced, including at final boundary', async () => {
  const files = {
    ...baseFiles,
    'reports/verification.json': 'z'.repeat(200),
    'reports/task-a/attempt-1/review.json': 'z'.repeat(200),
    'reports/task-a/attempt-1/acceptance.json': 'z'.repeat(200),
  };
  // shrink LIMITS temporarily to cross the total boundary on the LAST report
  const origTotal = LIMITS.total;
  LIMITS.total = 400; // RUN.json + first 200-byte report ok, second crosses
  try {
    const { fs } = makeFake(files);
    await assert.rejects(snapshot(fs, '/run'), /total size .* exceeds limit/);
  } finally {
    LIMITS.total = origTotal;
  }
  // with real limits the same run passes and revision is stable
  const { fs } = makeFake(files);
  const snap = await snapshot(fs, '/run');
  assert.equal(snap.reports.filter(r => r.present).length, 3);
});

test('task IDs: positive cases accepted, real control chars rejected', async () => {
  const good = ['task-1', 'X123', 'fix-x', 'Task_09', 'CONSOLE'];
  const base = JSON.parse(RUN);
  base.tasks = good.map(id => ({ id, attempt: 1, status: 'TODO' }));
  const ok = makeFake({ 'RUN.json': JSON.stringify(base) });
  const snap = await snapshot(ok.fs, '/run');
  assert.deepEqual(snap.tasks.map(t => t.id), good);

  // actual control characters (not escape text) are rejected
  for (const bad of ['a\u0000b', 'a\u001fb', 'a\tb']) {
    const m = JSON.parse(RUN);
    m.tasks = [{ id: bad, attempt: 1, status: 'TODO' }];
    const f = makeFake({ 'RUN.json': JSON.stringify(m) });
    await assert.rejects(snapshot(f.fs, '/run'), /unsafe id/);
  }
  // reserved names lowercase with extension rejected too
  for (const bad of ['con.txt', 'nul.json', 'com1.log']) {
    const m = JSON.parse(RUN);
    m.tasks = [{ id: bad, attempt: 1, status: 'TODO' }];
    const f = makeFake({ 'RUN.json': JSON.stringify(m) });
    await assert.rejects(snapshot(f.fs, '/run'), /unsafe id/);
  }
});

test('revision hash is length-framed: shifted concatenation yields different revision', async () => {
  const M = 'reports/task-a/attempt-1/acceptance.json\0';
  const filesA = {
    ...baseFiles,
    'reports/task-a/attempt-1/review.json': JSON.stringify({ verdict: 'foo' }),
    'reports/task-a/attempt-1/acceptance.json': JSON.stringify({ verdict: 'bar' + M + 'baz' }),
  };
  const filesB = {
    ...baseFiles,
    'reports/task-a/attempt-1/review.json': JSON.stringify({ verdict: 'foo' + M + 'bar' }),
    'reports/task-a/attempt-1/acceptance.json': JSON.stringify({ verdict: 'baz' }),
  };
  const a = await snapshot(makeFake(filesA).fs, '/run');
  const b = await snapshot(makeFake(filesB).fs, '/run');
  assert.notEqual(a.revision, b.revision, 'raw concatenation collision: framing broken');
});

const ABORT = /abort/i;

test('abort: throwIfAborted honored before IO', async () => {
  const { fs } = makeFake(baseFiles);
  const ac = new AbortController();
  ac.abort();
  await assert.rejects(snapshot(fs, '/run', ac.signal), ABORT);
});

test('oversize report at stat level', async () => {
  const bigReport = 'y'.repeat(LIMITS.report + 1);
  const { fs } = makeFake({ ...baseFiles, 'reports/verification.json': new TextEncoder().encode(bigReport) });
  await assert.rejects(snapshot(fs, '/run'), /verification\.json.*exceeds limit/);
});
