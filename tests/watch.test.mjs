import test from 'node:test';
import assert from 'node:assert/strict';
import { createMonitorTool } from '../watch.mjs';

const R1 = 'a'.repeat(64), R2 = 'b'.repeat(64);
function fixture(read = async () => ({ name: 'demo', revision: R1 })) {
  const records = [], disposers = [], roots = [];
  const ctx = {
    effect(fn) { disposers.push(fn()); },
    workingDirectory: { async ensure(agent) { assert.equal(agent.id, 'session-1'); return '/work'; } },
    fs: { async resolve(path, options) { roots.push({path, options}); return { displayPath: '/work/run', targetKey: '/work/run' }; }, processPath(target) { return target.targetKey; } },
    jobs: {
      list(owner) { return records.filter(record => record.owner === owner); },
      start(spec) {
        const record = { id: `supertick-${records.length}`, owner: spec.owner, kind: spec.kind, label: spec.label, status: 'running', output: '' };
        records.push(record);
        record.hooks = spec.run({ append(text) { record.output += text; }, updateProgress(line) { record.progress = line; } });
        record.done = record.hooks.done.then(result => { record.status = result.status; return result; });
        return record.id;
      },
    },
  };
  const controller = new AbortController();
  const exec = { agent: { id: 'session-1' }, signal: controller.signal, callId: 'call-1', rootCallId: 'call-1' };
  const tool = createMonitorTool(ctx, read);
  return { ctx, tool, exec, controller, records, roots, async dispose() { for (const fn of disposers) await fn(); } };
}

test('status resolves against calling session cwd and creates no job', async () => {
  const f = fixture();
  assert.equal((await f.tool.execute({run:'run'}, f.exec)).revision, R1);
  assert.equal(f.roots[0].options.cwd, '/work');
  assert.equal(f.records.length, 0);
  await f.dispose();
});

test('watch is session owned, deduplicated, cancellable and survives tool-call signal', async () => {
  const f = fixture();
  const first = await f.tool.execute({run:'run',action:'watch'}, f.exec);
  const second = await f.tool.execute({run:'run',action:'watch'}, f.exec);
  assert.equal(first.job_id, second.job_id);
  assert.equal(second.already_running, true);
  assert.equal(f.records.length, 1);
  assert.equal(f.records[0].owner, 'session-1');
  f.controller.abort('tool call ended');
  assert.equal(f.records[0].status, 'running');
  f.records[0].hooks.cancel('requested by user');
  const outcome = await f.records[0].done;
  assert.equal(outcome.status, 'killed');
  assert.equal(outcome.detail, 'requested by user');
  await f.dispose();
});

test('concurrent watch calls share a synchronous session-owned registration', async () => {
  const f = fixture();
  const results = await Promise.all(Array.from({length:8}, () => f.tool.execute({run:'run',action:'watch'}, f.exec)));
  assert.equal(new Set(results.map(item => item.job_id)).size, 1);
  assert.equal(f.records.length, 1);
  await f.dispose();
});

test('different owners never deduplicate into another session job', async () => {
  const f = fixture();
  f.ctx.workingDirectory.ensure = async () => '/work';
  const first = await f.tool.execute({run:'run',action:'watch'}, f.exec);
  const second = await f.tool.execute({run:'run',action:'watch'}, {...f.exec,agent:{id:'session-2'}});
  assert.notEqual(first.job_id, second.job_id);
  assert.equal(f.records.length, 2);
  await f.dispose();
});

test('since_revision avoids missing changes between status and watch', async () => {
  const f = fixture(async () => ({ name: 'demo', revision: R2 }));
  const result = await f.tool.execute({run:'run', action:'watch', since_revision:R1}, f.exec);
  assert.equal(result.reason, 'changed');
  assert.equal(result.snapshot.revision, R2);
  assert.equal(f.records.length, 0);
  await f.dispose();
});

test('watch wakes on revision change without the model polling', async () => {
  let calls = 0;
  const f = fixture(async () => ({name:'demo', revision: ++calls === 1 ? R1 : R2}));
  await f.tool.execute({run:'run',action:'watch',interval_seconds:2,timeout_seconds:5}, f.exec);
  const outcome = await f.records[0].done;
  assert.equal(outcome.status, 'completed');
  assert.equal(JSON.parse(f.records[0].output).reason, 'changed');
  assert.equal(calls, 2);
  await f.dispose();
});

test('timeout is successful bounded observation, not an acceptance verdict', async () => {
  const f = fixture();
  await f.tool.execute({run:'run',action:'watch',interval_seconds:2,timeout_seconds:5}, f.exec);
  const outcome = await f.records[0].done;
  assert.equal(outcome.status, 'completed');
  assert.equal(JSON.parse(f.records[0].output).reason, 'timeout');
  assert.match(outcome.detail, /no test verdict/);
  await f.dispose();
});

test('observation errors fail job, plugin teardown cancels and joins jobs', async () => {
  let calls = 0;
  const f = fixture(async () => { if (++calls > 1) throw new Error('FS_PERMISSION_DENIED'); return {name:'demo',revision:R1}; });
  await f.tool.execute({run:'run',action:'watch',interval_seconds:2,timeout_seconds:5}, f.exec);
  assert.equal((await f.records[0].done).status, 'failed');
  assert.equal(JSON.parse(f.records[0].output).reason, 'error');
  const g = fixture();
  await g.tool.execute({run:'run',action:'watch'}, g.exec);
  await g.dispose();
  assert.equal(g.records[0].status, 'killed');
  await f.dispose();
});

test('invalid arguments and cancelled foreground calls cannot start jobs', async () => {
  const f = fixture();
  for (const args of [ {}, {run:''}, {run:'run',action:'bad'}, {run:'run',interval_seconds:1}, {run:'run',timeout_seconds:3601}, {run:'run',since_revision:'invalid'} ]) {
    await assert.rejects(() => f.tool.execute(args, f.exec));
  }
  await assert.rejects(() => f.tool.execute({run:'run'}, {...f.exec,agent:undefined}));
  f.controller.abort();
  await assert.rejects(() => f.tool.execute({run:'run',action:'watch'}, f.exec));
  assert.equal(f.records.length, 0);
  await f.dispose();
});
