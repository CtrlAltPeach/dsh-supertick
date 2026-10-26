import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { createMonitorTool } from './watch.mjs';

export const name = 'supertick';
export const inject = ['tools', 'skills', 'fs', 'workingDirectory', 'jobs'];

export function apply(ctx) {
  for (const skillName of ['supertick', 'supertick-interview', 'supertick-conventions']) {
    const base = new URL(`./skills/${skillName}/`, import.meta.url);
    // Only immutable package resources use host fs; all run data uses ctx.fs.
    const text = readFileSync(new URL('SKILL.md', base), 'utf8');
    const match = /^---\r?\n([\s\S]+?)\r?\n---\r?\n([\s\S]*)$/.exec(text);
    if (!match) throw new Error(`Invalid packaged skill: ${skillName}`);
    const description = /^description: (".*")\r?$/m.exec(match[1]);
    if (!description || !match[1].split(/\r?\n/).includes(`name: ${skillName}`)) throw new Error(`Invalid packaged metadata: ${skillName}`);
    ctx.skills.register({
      name: skillName,
      description: JSON.parse(description[1]),
      content: match[2].trim(),
      provider: 'runtime',
      source: 'dsh-supertick',
      resourceBase: { kind: 'directory', path: fileURLToPath(base) },
      path: fileURLToPath(new URL('SKILL.md', base)),
    });
  }
  ctx.tools.register(defineTool(createMonitorTool(ctx)));
}
