import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { assertInstallerNodeVersion, assertSupportedNodeVersion, pinNodeCommands } from "../bin/node-runtime.mjs";

test("accepts patched stable releases in every supported line", () => {
  for (const version of ["22.23.4", "v24.22.1", "26.12.1", "22.23.5", "24.23.0", "26.13.0"]) {
    assert.doesNotThrow(() => assertSupportedNodeVersion(version), version);
  }
});

test("rejects unpatched, unsupported, prerelease and malformed versions", () => {
  for (const version of ["18.20.8", "20.20.2", "22.23.3", "22.22.99", "24.2.0", "24.22.0", "26.12.0",
    "23.99.0", "25.99.0", "27.0.0", "24.22.1-rc.1", "22.23.4-nightly", "24.22.1junk", "", null, 24]) {
    assert.throws(() => assertSupportedNodeVersion(version), /CVE-2026-111497/, String(version));
  }
});

test("only removal and explicit restore can use an old runtime", () => {
  for (const options of [{ uninstall: true }, { rollbackSnapshot: "/snapshot" }]) {
    assert.doesNotThrow(() => assertInstallerNodeVersion("24.2.0", options));
  }
  for (const options of [{}, { verify: true }, { dryRun: true }, { uninstall: true, dryRun: true },
    { rollbackSnapshot: "/snapshot", verify: true }]) {
    assert.throws(() => assertInstallerNodeVersion("24.2.0", options), /CVE-2026-111497/);
  }
});

for (const platform of ["darwin", "linux", "win32"]) {
  test(`pins hooks and MCP to the validated runtime on ${platform}`, () => {
    const executable = platform === "win32" ? "C:\\Program Files\\nodejs\\node.exe" : "/tools/Node è 'safe'/node";
    const manifest = { mcp: { command: "node", args: ["launcher.mjs"] }, hooks: [{ command: 'node "${PLUGIN_ROOT}/hook.mjs" prompt' }],
      other: { command: "python", note: "node is a dependency" } };
    const result = pinNodeCommands(manifest, { executable, version: "24.22.1", platform });
    assert.equal(result.mcp.command, executable);
    assert.equal(result.hooks[0].command, platform === "win32"
      ? '"C:\\Program Files\\nodejs\\node.exe" "${PLUGIN_ROOT}/hook.mjs" prompt'
      : "'/tools/Node è '\\''safe'\\''/node' \"${PLUGIN_ROOT}/hook.mjs\" prompt");
    assert.deepEqual(result.other, manifest.other);
    assert.equal(manifest.mcp.command, "node");
    assert.throws(() => pinNodeCommands(manifest, { executable, version: "24.2.0", platform }), /CVE-2026-111497/);
  });
}

test("an unpatched installer fails before creating any installation state", () => {
  const home = mkdtempSync(join(tmpdir(), "dduo-node-preflight-"));
  try {
    const installer = fileURLToPath(new URL("../bin/install.mjs", import.meta.url));
    const override = "data:text/javascript," + encodeURIComponent("Object.defineProperty(process.versions, 'node', {value:'24.2.0'})");
    for (const args of [["--yes", "--only", "core"], ["--dry-run", "--yes"], ["--verify", "--only", "core"]]) {
      const result = spawnSync(process.execPath, ["--import", override, installer, ...args], {
        encoding: "utf8", env: { ...process.env, HOME: home, USERPROFILE: home, CODEX_HOME: join(home, ".codex"), CLAUDE_CONFIG_DIR: join(home, ".claude") },
      });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /CVE-2026-111497/);
      assert.equal(existsSync(join(home, ".config")), false);
      assert.equal(existsSync(join(home, ".local")), false);
    }
  } finally { rmSync(home, { recursive: true, force: true }); }
});
