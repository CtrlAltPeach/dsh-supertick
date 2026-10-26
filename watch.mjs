import { setTimeout as delay } from 'node:timers/promises';
import { snapshot } from './monitor.mjs';

const KIND = 'supertick';
function observation(reason, state) {
  return JSON.stringify({ reason, run: state.run, revision: state.revision, phase: state.phase, counts: state.counts, details: 'Use supertick_monitor action=status for the full snapshot' }) + '\n';
}
function bounded(value, fallback, min, max, field) {
  const result = value ?? fallback;
  if (!Number.isInteger(result) || result < min || result > max) throw new Error(`${field} must be an integer ${min}..${max}`);
  return result;
}

export function createMonitorTool(ctx, read = snapshot) {
  const active = new Set();
  ctx.effect(() => async () => {
    const running = [...active];
    for (const job of running) job.controller.abort('plugin disposed');
    await Promise.all(running.map(job => job.done));
  });
  return {
    name: 'supertick_monitor',
    description: 'Read a supertick run progress ledger, or start ONE bounded, session-owned background watch for RUN.json/report changes. Read-only: does not launch agents, change state, validate acceptance gates or prove native agent status. Watch completion uses native job notifications; collect via job_output, cancel via job_kill. No model polling.',
    parameters: {
      run: { type: 'string', required: true, description: 'Run directory, absolute or relative to this session cwd (not RUN.json itself).' },
      action: { type: 'string', enum: ['status', 'watch'], description: 'status (default): snapshot; watch: managed background wait.' },
      since_revision: { type: 'string', description: 'Optional revision from previous status. Watch returns immediately if already changed, avoiding missed changes between calls.' },
      interval_seconds: { type: 'integer', description: 'Watch scan interval, 2..60 seconds; default 5.' },
      timeout_seconds: { type: 'integer', description: 'Bounded watch duration, 5..3600 seconds; default 900. A timeout is not a failed test.' },
    },
    output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }] },
    async execute(args, exec) {
      if (!exec.agent) throw new Error('supertick_monitor requires an agent Session');
      if (typeof args.run !== 'string' || !args.run.trim() || args.run.length > 4096) throw new Error('run must be a nonempty directory path');
      const action = args.action ?? 'status';
      if (!['status', 'watch'].includes(action)) throw new Error('action must be status or watch');
      const interval = bounded(args.interval_seconds, 5, 2, 60, 'interval_seconds');
      const timeout = bounded(args.timeout_seconds, 900, 5, 3600, 'timeout_seconds');
      if (args.since_revision !== undefined && !/^[a-f0-9]{64}$/.test(args.since_revision)) throw new Error('since_revision must be SHA-256');
      exec.signal.throwIfAborted();
      const cwd = await ctx.workingDirectory.ensure(exec.agent, exec.signal);
      const root = await ctx.fs.resolve(args.run, { cwd, signal: exec.signal });
      const run = ctx.fs.processPath(root);
      const initial = await read(ctx.fs, run, exec.signal);
      exec.signal.throwIfAborted();
      if (action === 'status') return initial;
      if (args.since_revision !== undefined && args.since_revision !== initial.revision) return { reason: 'changed', snapshot: initial };
      const label = `Watch ${run}`;
      const existing = ctx.jobs.list(exec.agent.id).find(job => job.kind === KIND && job.label === label && ['running', 'stopping'].includes(job.status));
      if (existing) return { job_id: existing.id, already_running: true, revision: initial.revision };
      const controller = new AbortController();
      const record = { controller, done: undefined };
      const jobId = ctx.jobs.start({
        kind: KIND, label, owner: exec.agent.id,
        toolCall: { callId: exec.callId, rootCallId: exec.rootCallId },
        outputLimitBytes: 16000,
        run(handle) {
          record.done = (async () => {
            const started = Date.now();
            const deadline = started + timeout * 1000;
            handle.updateProgress(`Watching ${initial.name}; timeout ${timeout}s`);
            let current = initial;
            try {
              while (Date.now() < deadline) {
                await delay(Math.min(interval * 1000, deadline - Date.now()), undefined, { signal: controller.signal });
                current = await read(ctx.fs, run, controller.signal);
                if (current.revision !== initial.revision) {
                  handle.append(observation('changed', current));
                  return { status: 'completed', detail: 'supertick ledger or report changed' };
                }
              }
              handle.append(observation('timeout', current));
              return { status: 'completed', detail: 'watch timeout; no test verdict implied' };
            } catch (error) {
              if (controller.signal.aborted) return { status: 'killed', detail: String(controller.signal.reason ?? 'cancelled') };
              handle.append(JSON.stringify({ reason: 'error', message: String(error.message ?? error).slice(0, 2000) }) + '\n');
              return { status: 'failed', detail: 'supertick observation failed' };
            } finally { active.delete(record); }
          })();
          return { done: record.done, cancel: reason => controller.abort(reason ?? 'cancelled') };
        },
      });
      active.add(record);
      return { job_id: jobId, revision: initial.revision, timeout_seconds: timeout, collect: 'job_output', cancel: 'job_kill' };
    },
  };
}
