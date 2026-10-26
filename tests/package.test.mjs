import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL(name, root), 'utf8');

test('package declares a native DSH bundle, closed public exports and required sources', () => {
  const pkg = JSON.parse(read('package.json'));
  assert.equal(pkg.name, 'dsh-supertick');
  assert.equal(pkg.dsh.manifestVersion, 1);
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml');
  assert.equal(pkg.type, 'module');
  for (const name of ['index.js', 'watch.mjs', 'monitor.mjs']) {
    assert.ok(pkg.files.includes(name));
    assert.ok(existsSync(new URL(name, root)), `missing source ${name}`);
  }
  for (const name of ['tests', 'SUPERTICK', 'CHECKPOINT.md', 'scripts', 'skills']) assert.ok(!pkg.files.includes(name));
  for (const name of ['skills/*/SKILL.md', 'skills/supertick/references/*.MD', 'skills/supertick/references/ALLOWED-MODELS.json', 'skills/supertick/scripts/supervisor.py']) assert.ok(pkg.files.includes(name));
  assert.ok(read('cordis.patch.yml').includes('name: dsh-supertick'));
});

test('every packaged skill has exact metadata parsed by plugin and local resources', () => {
  for (const name of ['supertick', 'supertick-conventions', 'supertick-interview']) {
    const text = read(`skills/${name}/SKILL.md`);
    const frontmatter = /^---\r?\n([\s\S]+?)\r?\n---\r?\n([\s\S]*)$/.exec(text);
    assert.ok(frontmatter, name);
    assert.ok(frontmatter[1].split(/\r?\n/).includes(`name: ${name}`));
    const description = /^description: (".*")\r?$/m.exec(frontmatter[1]);
    assert.equal(typeof JSON.parse(description[1]), 'string');
    assert.ok(frontmatter[2].trim().length > 50);
  }
  assert.ok(existsSync(new URL('skills/supertick/scripts/supervisor.py', root)));
  for (const name of ['ROLES.MD', 'STATE.MD', 'ALLOWED-MODELS.json']) assert.ok(existsSync(new URL(`skills/supertick/references/${name}`, root)));
});
