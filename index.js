import { readFileSync } from 'node:fs';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { createMonitorTool } from './watch.mjs';
import { discoverSkills, readSkill } from './skills.mjs';

export const name = 'supertick';
export const inject = ['tools', 'skills', 'fs', 'workingDirectory', 'jobs'];

export function apply(ctx) {
  for (const discovered of discoverSkills(import.meta.url)) {
    // Only immutable package resources use host fs; all run data uses ctx.fs.
    const { name: skillName, description, content, resourceBase, path } = readSkill(discovered);
    ctx.skills.register({
      name: skillName,
      description,
      content,
      provider: 'runtime',
      source: 'dsh-supertick',
      resourceBase,
      path,
    });
  }
  ctx.tools.register(defineTool(createMonitorTool(ctx)));
}
