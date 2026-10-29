import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Skill directories are discovered, never listed. A packaged skill that is not
// enumerated cannot silently stay unregistered at runtime.
export function discoverSkills(packageUrl) {
  const root = new URL('skills/', packageUrl);
  return readdirSync(fileURLToPath(root), { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => ({ name: entry.name, base: new URL(`${entry.name}/`, root) }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

// Parse one packaged skill exactly as it is registered. Lives here (not in
// index.js) so tests exercise the real path without the host peer dependency.
export function readSkill({ name, base }) {
  const path = new URL('SKILL.md', base);
  const text = readFileSync(path, 'utf8');
  const match = /^---\r?\n([\s\S]+?)\r?\n---\r?\n([\s\S]*)$/.exec(text);
  if (!match) throw new Error(`Invalid packaged skill: ${name}`);
  const description = /^description: (".*")\r?$/m.exec(match[1]);
  if (!description || !match[1].split(/\r?\n/).includes(`name: ${name}`)) throw new Error(`Invalid packaged metadata: ${name}`);
  return {
    name,
    description: JSON.parse(description[1]),
    content: match[2].trim(),
    resourceBase: { kind: 'directory', path: fileURLToPath(base) },
    path: fileURLToPath(path),
  };
}
