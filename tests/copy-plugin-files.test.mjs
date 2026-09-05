import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { copyPluginFiles } from "../bin/copy-plugin-files.mjs";

test("release copies preserve nested Unicode paths, bytes and overwrite semantics", () => {
  const root = mkdtempSync(join(tmpdir(), "dduo-copy-"));
  try {
    const source = join(root, "source è", "nested 日本語");
    const destination = join(root, "profile qualità & space", "plugin");
    mkdirSync(source, { recursive: true });
    const payload = Buffer.from("Memoria — qualità 🚀\n\u0000", "utf8");
    writeFileSync(join(source, "memory.md"), payload);
    copyPluginFiles(join(root, "source è"), destination, { recursive: true });
    assert.deepEqual(readFileSync(join(destination, "nested 日本語", "memory.md")), payload);
    writeFileSync(join(source, "memory.md"), "updated");
    copyPluginFiles(join(root, "source è"), destination, { recursive: true });
    assert.equal(readFileSync(join(destination, "nested 日本語", "memory.md"), "utf8"), "updated");
    copyPluginFiles(join(source, "memory.md"), join(destination, "single.md"));
    assert.equal(readFileSync(join(destination, "single.md"), "utf8"), "updated");
    assert.throws(() => copyPluginFiles(source, join(root, "no-recursive")), /recursive/);
    assert.throws(() => copyPluginFiles(source, source, { recursive: true }), /outside/);
    assert.throws(() => copyPluginFiles(source, join(source, "child"), { recursive: true }), /outside/);
    if (process.platform !== "win32") {
      chmodSync(join(source, "memory.md"), 0o755);
      copyPluginFiles(join(source, "memory.md"), join(destination, "executable"));
      assert.equal(statSync(join(destination, "executable")).mode & 0o777, 0o755);
    }
    const linked = join(root, "linked-directory");
    symlinkSync(source, linked, process.platform === "win32" ? "junction" : "dir");
    assert.throws(() => copyPluginFiles(linked, join(root, "link-copy"), { recursive: true }), /regular/);
    assert.throws(() => copyPluginFiles(source, linked, { recursive: true }), /directory destination/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
