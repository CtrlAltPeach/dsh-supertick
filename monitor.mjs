import { createHash } from 'node:crypto';
import path from 'node:path';

const LIMITS = {
  runJson: 512 * 1024,
  report: 128 * 1024,
  total: 2 * 1024 * 1024,
  tasks: 200,
  agents: 400,
  jobs: 400,
};

const TASK_STATUSES = new Set(['TODO', 'RUNNING', 'REVIEW', 'ACCEPTING', 'DONE', 'BLOCKED']);

function fail(msg) { throw new Error(`monitor: ${msg}`); }

function finiteInt(v) {
  return typeof v === 'number' && Number.isFinite(v) && Number.isInteger(v) ? v : undefined;
}

function boundedStr(v, max) {
  if (typeof v !== 'string') return undefined;
  return v.length > max ? v.slice(0, max) : v;
}

// Validate a report path; reject .., absolute, backslashes.
function safeRel(rel) {
  if (typeof rel !== 'string' || rel.length === 0 || rel.length > 512) fail('bad report path');
  if (rel.includes('\\')) fail('backslash in report path');
  if (path.posix.isAbsolute(rel) || path.win32.isAbsolute(rel)) fail('absolute report path');
  for (const s of rel.split('/')) {
    if (s === '' || s === '.' || s === '..') fail(`unsafe segment in report path: ${rel}`);
  }
  return rel;
}

function windowSafeId(id) {
  if (typeof id !== 'string' || id.length === 0 || id.length > 128) return false;
  if (/[<>:"/\\|?*\x00-\x1f]/.test(id)) return false;
  if (id.endsWith('.') || id.endsWith(' ')) return false;
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$/i.test(id)) return false;
  return true;
}

// Real backend signature: readBytes(target, signal, maxBytes)
async function readBounded(fs, target, signal, maxBytes, label) {
  const st = await fs.stat(target, signal);
  if (!st) {
    const e = new Error(`monitor: ${label}: not found`);
    e.code = 'FS_NOT_FOUND';
    throw e;
  }
  if (st.type !== 'file') fail(`${label}: not a file`);
  if (st.size > maxBytes) fail(`${label}: size ${st.size} exceeds limit ${maxBytes}`);
  const bytes = await fs.readBytes(target, signal, maxBytes + 1);
  if (bytes.length > maxBytes) fail(`${label}: read exceeds limit`);
  return bytes;
}

// Missing = stat/resolve returned undefined, or a genuine FS_NOT_FOUND error.
// Everything else (FS_PERMISSION_DENIED, parse errors, containment) propagates.
function isNotFound(e) {
  return e && typeof e === 'object' && e.code === 'FS_NOT_FOUND';
}

// Returns target or undefined when the known report is simply absent.
// Permission errors and containment violations propagate (never masked).
async function resolveKnownReport(fs, runTarget, runCwd, rel, signal) {
  safeRel(rel);
  const reportTarget = await fs.resolve(rel, { cwd: runCwd, signal });
  if (!reportTarget) return undefined;
  if (!fs.contains(runTarget, reportTarget)) fail(`${rel}: escapes run directory`);
  return reportTarget;
}

function extractReport(bytes, rel) {
  const info = { path: rel, present: true, kind: undefined, verdict: undefined };
  try {
    const parsed = JSON.parse(new TextDecoder('utf-8', { fatal: false }).decode(bytes));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      info.kind = boundedStr(parsed.kind, 64);
      info.verdict = boundedStr(parsed.verdict, 64);
    }
  } catch {
    info.kind = 'unparseable';
  }
  return info;
}

export async function snapshot(fs, runPath, signal) {
  signal?.throwIfAborted();

  const runTarget = await fs.resolve(runPath, { cwd: undefined, signal });
  if (!runTarget) fail('run path not found');
  const runCwd = fs.processPath(runTarget);
  const runJsonTarget = await fs.resolve('RUN.json', { cwd: runCwd, signal });
  if (!runJsonTarget) fail('RUN.json not found');
  if (!fs.contains(runTarget, runJsonTarget)) fail('RUN.json escapes run directory');
  signal?.throwIfAborted();
  const runBytes = await readBounded(fs, runJsonTarget, signal, LIMITS.runJson, 'RUN.json');
  signal?.throwIfAborted();

  let run;
  try {
    run = JSON.parse(new TextDecoder('utf-8').decode(runBytes));
  } catch {
    fail('RUN.json: invalid JSON');
  }
  if (!run || typeof run !== 'object' || Array.isArray(run)) fail('RUN.json: not an object');
  if (run.schema !== 1) fail('unsupported schema');
  if (typeof run.name !== 'string' || run.name.length > 256) fail('RUN.json: invalid name');
  if (typeof run.phase !== 'string' || run.phase.length > 64) fail('RUN.json: invalid phase');

  const counts = { TODO: 0, RUNNING: 0, REVIEW: 0, ACCEPTING: 0, DONE: 0, BLOCKED: 0 };
  const tasks = [];
  if (!Array.isArray(run.tasks)) fail('RUN.json: tasks must be an array');
  if (run.tasks.length > LIMITS.tasks) fail(`RUN.json: too many tasks (${run.tasks.length})`);
  const seenIds = new Set();
  for (const t of run.tasks) {
    if (!t || typeof t !== 'object' || Array.isArray(t)) fail('task: not an object');
    if (!windowSafeId(t.id)) fail(`task: unsafe id (input bounded out)`);
    const key = t.id.toLowerCase();
    if (seenIds.has(key)) fail(`task: duplicate id (case-insensitive) ${t.id}`);
    seenIds.add(key);
    const attempt = finiteInt(t.attempt);
    if (attempt === undefined || attempt < 1) fail(`task ${t.id}: attempt must be positive int`);
    if (!TASK_STATUSES.has(t.status)) fail(`task ${t.id}: invalid task status`);
    counts[t.status] += 1;
    tasks.push({
      id: t.id,
      attempt,
      status: t.status,
      title: boundedStr(t.title, 200),
      coder_id: boundedStr(t.coder_id, 128) ?? null,
      reviewer_id: boundedStr(t.reviewer_id, 128) ?? null,
      checker_id: boundedStr(t.checker_id, 128) ?? null,
    });
  }

  const agents = [];
  if (!Array.isArray(run.agents)) fail('RUN.json: agents must be an array');
  if (run.agents.length > LIMITS.agents) fail(`RUN.json: too many agents (${run.agents.length})`);
  for (const a of run.agents) {
    if (!a || typeof a !== 'object' || Array.isArray(a)) fail('agent: not an object');
    agents.push({
      id: boundedStr(a.id, 128) ?? fail('agent: missing id'),
      task: boundedStr(a.task, 128) ?? null,
      role: boundedStr(a.role, 64),
      provider: boundedStr(a.provider, 64),
      model: boundedStr(a.model, 128),
      status: boundedStr(a.status, 32),
      last_event_at: boundedStr(a.last_event_at, 64),
    });
  }

  const jobs = [];
  if (!Array.isArray(run.jobs)) fail('RUN.json: jobs must be an array');
  if (run.jobs.length > LIMITS.jobs) fail(`RUN.json: too many jobs (${run.jobs.length})`);
  for (const j of run.jobs) {
    if (!j || typeof j !== 'object' || Array.isArray(j)) fail('job: not an object');
    jobs.push({
      id: boundedStr(j.id, 128) ?? fail('job: missing id'),
      purpose: boundedStr(j.purpose, 200),
      status: boundedStr(j.status, 32),
    });
  }

  // Known report paths
  const rels = ['reports/verification.json'];
  for (const t of tasks) {
    rels.push(`reports/${t.id}/attempt-${t.attempt}/review.json`);
    rels.push(`reports/${t.id}/attempt-${t.attempt}/acceptance.json`);
  }

  signal?.throwIfAborted();
  const reports = [];
  let totalBytes = runBytes.length;
  const revision = createHash('sha256');
  revision.update(`RUN.json\0${runBytes.length}\0`).update(runBytes);

  for (const rel of rels) {
    if (totalBytes > LIMITS.total) fail(`total size ${totalBytes} exceeds limit ${LIMITS.total}`);
    let bytes;
    try {
      const reportTarget = await resolveKnownReport(fs, runTarget, runCwd, rel, signal);
      signal?.throwIfAborted();
      if (!reportTarget) bytes = undefined;
      else bytes = await readBounded(fs, reportTarget, signal, LIMITS.report, rel);
    } catch (e) {
      if (!isNotFound(e)) throw e;
      bytes = undefined;
    }
    if (!bytes) {
      reports.push({ path: rel, present: false, missing: true });
      revision.update(`${rel}\0missing\0`);
      continue;
    }
    totalBytes += bytes.length;
    reports.push(extractReport(bytes, rel));
    revision.update(`${rel}\0${bytes.length}\0`).update(bytes);
  }
  if (totalBytes > LIMITS.total) fail(`total size ${totalBytes} exceeds limit ${LIMITS.total}`);

  signal?.throwIfAborted();

  return {
    run: runCwd,
    name: boundedStr(run.name, 256),
    phase: boundedStr(run.phase, 64),
    counts,
    tasks,
    agents,
    jobs,
    reports,
    revision: revision.digest('hex'),
    ledger_only: true,
    native_status_checked: false,
    gate_validation: 'not_run',
  };
}

export default snapshot;
export { LIMITS };
