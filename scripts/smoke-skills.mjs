import { discoverSkills, readSkill } from '../skills.mjs';

const entry = new URL('../index.js', import.meta.url).href;
const discovered = discoverSkills(entry);
console.log('discovered:', discovered.map(skill => skill.name).join(', '));
for (const item of discovered) {
  const skill = readSkill(item);
  console.log(`  ${skill.name}: desc ${skill.description.length} chars, body ${skill.content.length} chars`);
}
