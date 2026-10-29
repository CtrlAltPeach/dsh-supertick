import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { discoverSkills, readSkill } from '../skills.mjs';

const root = new URL('../', import.meta.url);
const entry = new URL('index.js', root).href;

test('every packaged skill directory is discovered for runtime registration', () => {
  const onDisk = readdirSync(fileURLToPath(new URL('skills/', root)), { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => entry.name)
    .sort();
  const discovered = discoverSkills(entry).map(skill => skill.name);
  assert.deepEqual(discovered, onDisk);
  // The regression this guards: a shipped skill that apply() never registered,
  // so it appeared neither in the skill catalog nor to the model.
  for (const name of ['supertick', 'supertick-conventions', 'supertick-interview', 'supertick-runs']) {
    assert.ok(discovered.includes(name), `undiscovered skill: ${name}`);
  }
});

test('every discovered skill parses into a registrable payload', () => {
  const skills = discoverSkills(entry);
  assert.ok(skills.length >= 4);
  for (const discovered of skills) {
    const skill = readSkill(discovered);
    assert.equal(skill.name, discovered.name);
    assert.equal(typeof skill.description, 'string');
    assert.ok(skill.description.length > 0);
    assert.ok(skill.content.length > 50);
    assert.equal(skill.resourceBase.kind, 'directory');
    assert.ok(skill.path.endsWith('SKILL.md'));
  }
});

test('the plugin enumerates skills instead of hardcoding names', () => {
  const source = readFileSync(new URL('index.js', root), 'utf8');
  assert.ok(source.includes('discoverSkills(import.meta.url)'), 'apply() must discover skills');
  // Any literal skill-name list would drift from the packaged directories again.
  assert.doesNotMatch(source, /for \(const skillName of \[/, 'no hardcoded skill list');
});
