#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const files = execFileSync(
  'git',
  ['-C', root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
  { encoding: 'utf8' },
)
  .split('\0')
  .filter(
    (relativePath) =>
      relativePath &&
      relativePath !== 'checksums.sha256' &&
      existsSync(join(root, relativePath)),
  )
  .sort();
const lines = files.map((relativePath) => {
  const digest = createHash('sha256').update(readFileSync(join(root, relativePath))).digest('hex');
  return `${digest}  ${relativePath}`;
});
writeFileSync(join(root, 'checksums.sha256'), `${lines.join('\n')}\n`);
console.log(`Wrote ${files.length} checksums.`);
