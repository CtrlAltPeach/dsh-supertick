// Development-only read-only inspection of the installed Electron ASAR.
import fs from 'node:fs';
const archive = process.env.DSH_ASAR;
if (!archive) throw new Error('Set DSH_ASAR to the app.asar of your DeepSeek Harness installation');
const fd = fs.openSync(archive, 'r');
try {
  const head = Buffer.alloc(16); fs.readSync(fd, head, 0, 16, 0);
  const header = Buffer.alloc(head.readUInt32LE(12)); fs.readSync(fd, header, 0, header.length, 16);
  const base = 8 + head.readUInt32LE(4);
  const entries = new Map();
  function walk(node, prefix = '') {
    for (const [name, entry] of Object.entries(node.files ?? {})) {
      const key = prefix + name;
      if (entry.files) walk(entry, key + '/'); else entries.set(key, entry);
    }
  }
  walk(JSON.parse(header.toString('utf8')));
  const mode = process.argv[2];
  if (mode === 'list') {
    const re = new RegExp(process.argv[3] ?? 'dsh/package.json');
    const found = [...entries.keys()].filter(key => re.test(key));
    console.log(found.slice(0, 150).join('\n'));
    console.log(`Matches: ${found.length}, shown: ${Math.min(150, found.length)}`);
  } else if (mode === 'cat' || mode === 'search') {
    const search = mode === 'search' ? new RegExp(process.argv[3]) : null;
    for (const key of process.argv.slice(search ? 4 : 3)) {
      const entry = entries.get(key);
      if (!entry || entry.link || entry.unpacked) throw new Error('Not a packed regular file: ' + key);
      if (entry.size > (search ? 8000000 : 500000)) throw new Error('Narrow inspection: file too large');
      const body = Buffer.alloc(entry.size); fs.readSync(fd, body, 0, entry.size, base + Number(entry.offset));
      const text = body.toString('utf8');
      console.log(`=== ${key} ===`);
      if (!search) console.log(text);
      else {
        const lines = text.split('\n'); const selected = new Set();
        lines.forEach((line, i) => { if (search.test(line)) for (let j = Math.max(0, i - 4); j <= Math.min(lines.length - 1, i + 10); j++) selected.add(j); });
        console.log([...selected].sort((a,b) => a-b).map(i => `${i+1}: ${lines[i]}`).join('\n'));
      }
    }
  } else throw new Error('Usage: inspect-host.mjs list REGEX | cat PATH... | search REGEX PATH...');
} finally { fs.closeSync(fd); }
