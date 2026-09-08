import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, mkdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { commandInvocation } from "../bin/native-command.mjs";
import {
  clientCommandInvocation, readClientRecord, recordedClientCommand, resolveClientCommand,
} from "../bin/client-installation.mjs";

test("native commands preserve literal arguments", () => {
  const args = ["with spaces", "percent%PATH%", "&echo injected", "Unicode è", 'a"b'];
  const call = commandInvocation(process.execPath, ["-e", "console.log(JSON.stringify(process.argv.slice(1)))", ...args]);
  const output = spawnSync(call.command, call.args, { ...call.options, encoding: "utf8" });
  assert.equal(output.status, 0, output.stderr);
  assert.deepEqual(JSON.parse(output.stdout), args);
});

test("malformed client records fail closed instead of behaving as absent", () => {
  const root = mkdtempSync(join(tmpdir(), "dduo-client-record-"));
  try {
    const record = join(root, "client-executables.json");
    writeFileSync(record, "[]\n");
    assert.throws(() => readClientRecord(record), /Invalid dDuo client record/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("dangling client record symlinks fail closed", {
  skip: process.platform === "win32" && "requires unprivileged POSIX symlinks",
}, () => {
  const root = mkdtempSync(join(tmpdir(), "dduo-client-record-link-"));
  try {
    const record = join(root, "client-executables.json");
    symlinkSync(join(root, "missing.json"), record);
    assert.throws(() => readClientRecord(record), /Invalid dDuo client record/);
    assert.throws(() => readClientRecord(record, { repairFamily: "codex" }), /Invalid dDuo client record/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("client record validation rejects malformed structure and repairs only the selected entry", () => {
  const root = mkdtempSync(join(tmpdir(), "dduo-client-structure-"));
  try {
    const record = join(root, "client-executables.json");
    for (const value of [{}, { version: 1 }, { clients: {} }, { version: 2, clients: {} }, { version: 1, clients: [] },
      { version: 1, clients: { codex: {} } }, { version: 1, clients: { codex: { launcher_path: 3 } } }]) {
      writeFileSync(record, JSON.stringify(value));
      assert.throws(() => readClientRecord(record), /Invalid dDuo client record/);
      if (value.version !== 1 || !value.clients || Array.isArray(value.clients)) {
        assert.throws(() => readClientRecord(record, { repairFamily: "codex" }), /Invalid dDuo client record/);
      }
    }
    const claude = { launcher_path: process.execPath };
    writeFileSync(record, JSON.stringify({ version: 1, clients: { codex: [], claude } }));
    assert.deepEqual(readClientRecord(record, { repairFamily: "codex" }), { version: 1, clients: { claude } });
    assert.throws(() => readClientRecord(record, { repairFamily: "claude" }), /Invalid dDuo client record/);
    writeFileSync(record, JSON.stringify({ version: 1, clients: { claude: { config_dir: "relative" } } }));
    assert.throws(() => readClientRecord(record, { kind: "scopes" }), /Invalid dDuo client record/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

for (const family of ["codex", "claude"]) {
  test(`${family} npm batch launchers run a verified entrypoint with recorded Node`, () => {
    const root = mkdtempSync(join(tmpdir(), "dduo-npm-shim-"));
    try {
      const packageName = family === "codex" ? "@openai/codex" : "@anthropic-ai/claude-code";
      const packageRoot = join(root, "node_modules", packageName);
      mkdirSync(packageRoot, { recursive: true });
      const entrypoint = join(packageRoot, "client.js");
      writeFileSync(entrypoint, "console.log(JSON.stringify(process.argv.slice(2)));\n");
      writeFileSync(join(packageRoot, "package.json"), JSON.stringify({ name: packageName, bin: { [family]: "client.js" } }));
      const launcher = join(root, `${family}.cmd`);
      writeFileSync(launcher, "@echo off\r\nTHIS BATCH FILE MUST NEVER EXECUTE\r\n");
      chmodSync(launcher, 0o755);
      const record = join(root, "record.json");
      writeFileSync(record, JSON.stringify({ version: 1, clients: {
        [family]: { launcher_path: launcher, node_executable: process.execPath },
      } }));
      const descriptor = recordedClientCommand(record, family);
      assert.equal(descriptor.launcher, launcher);
      assert.equal(descriptor.entrypoint, realpathSync(entrypoint));
      const args = ["a & b", "%PATH%", "!", "è", 'a"b'];
      const invocation = clientCommandInvocation(descriptor, args);
      const result = spawnSync(invocation.command, invocation.args, { ...invocation.options, encoding: "utf8" });
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(JSON.parse(result.stdout), args);
      const nativePayload = join(packageRoot, "vendor", "codex.exe");
      mkdirSync(join(packageRoot, "vendor"));
      writeFileSync(nativePayload, "MZ native payload fixture");
      chmodSync(nativePayload, 0o755);
      assert.deepEqual(resolveClientCommand(family, nativePayload), {
        launcher: nativePayload, nodeExecutable: null, entrypoint: null,
      });
      writeFileSync(record, JSON.stringify({ version: 1, clients: { [family]: { launcher_path: launcher } } }));
      assert.throws(() => recordedClientCommand(record, family), /recorded Node executable/);
      writeFileSync(join(packageRoot, "package.json"), JSON.stringify({ name: "unrelated-package", bin: "client.js" }));
      assert.throws(() => resolveClientCommand(family, launcher), /no verified official npm entry point/);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}

test("POSIX npm symlinks retain their stable launcher and use the official package entrypoint", {
  skip: process.platform === "win32" && "POSIX symlink installation contract",
}, () => {
  const root = mkdtempSync(join(tmpdir(), "dduo-npm-link-"));
  try {
    const packageRoot = join(root, "lib", "node_modules", "@openai", "codex");
    mkdirSync(packageRoot, { recursive: true });
    const script = join(packageRoot, "codex.js");
    writeFileSync(script, "#!/usr/bin/env node\nconsole.log('exact');\n");
    chmodSync(script, 0o755);
    writeFileSync(join(packageRoot, "package.json"), JSON.stringify({ name: "@openai/codex", bin: { codex: "codex.js" } }));
    const launcher = join(root, "codex");
    symlinkSync(script, launcher);
    const descriptor = resolveClientCommand("codex", launcher);
    assert.equal(descriptor.launcher, launcher);
    assert.equal(descriptor.entrypoint, realpathSync(script));
    assert.equal(descriptor.nodeExecutable, process.execPath);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("Windows batch adapters preserve arguments through cmd.exe", {
  skip: process.platform !== "win32" && "native Windows command processor contract",
}, () => {
  const root = mkdtempSync(join(tmpdir(), "dduo-cmd-"));
  try {
    const directory = join(root, "spaces & accents è");
    mkdirSync(directory);
    const script = join(directory, "arguments.mjs");
    writeFileSync(script, "console.log(JSON.stringify(process.argv.slice(2)));\n");
    const launcher = join(directory, "client.cmd");
    writeFileSync(launcher, `@echo off\r\nchcp 65001 >nul\r\n"${process.execPath}" "${script}" %*\r\n`);
    const args = ["plain", "with spaces", "ampersand&value", "percent%TEMP%", "bang!value", "paren(value)", "Unicode è"];
    const call = commandInvocation(launcher, args);
    const output = spawnSync(call.command, call.args, { ...call.options, encoding: "utf8" });
    assert.equal(output.status, 0, output.stderr);
    assert.deepEqual(JSON.parse(output.stdout), args);
    assert.throws(() => commandInvocation(launcher, ["bad\ncommand"]), /line breaks/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
