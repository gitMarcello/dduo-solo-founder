import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { commandInvocation } from "../bin/native-command.mjs";

test("native commands preserve literal arguments", () => {
  const args = ["with spaces", "percent%PATH%", "&echo injected", "Unicode è", 'a"b'];
  const call = commandInvocation(process.execPath, ["-e", "console.log(JSON.stringify(process.argv.slice(1)))", ...args]);
  const output = spawnSync(call.command, call.args, { ...call.options, encoding: "utf8" });
  assert.equal(output.status, 0, output.stderr);
  assert.deepEqual(JSON.parse(output.stdout), args);
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
