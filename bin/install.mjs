#!/usr/bin/env node
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  accessSync,
  constants,
  chmodSync,
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { delimiter, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { parseChecksumManifest } from "./checksum-manifest.mjs";
import { commandInvocation } from "./native-command.mjs";
import { copyPluginFiles as cpSync } from "./copy-plugin-files.mjs";
import { writeVolumeSnapshot } from "./upgrade-snapshot.mjs";
import {
  clientCommandInvocation,
  readClientRecord,
  recordedClientCommand,
  resolveClientCommand,
} from "./client-installation.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const home = homedir();
const dataDir = join(home, ".local", "share", "dduo-solo-founder");
const runtime = join(dataDir, "runtime");
const configDir = join(home, ".config", "dduo-solo-founder");
const runtimePointer = join(configDir, "runtime-path");
const hookRuntimePointer = join(configDir, "hook-runtime-bin");
const claudeStatuslineState = join(configDir, "client-telemetry", "claude-statusline.json");
const legacyClaudeStatuslineState = join(configDir, "usage-guard", "claude-statusline.json");
const retiredUsageGuardState = join(configDir, "usage-guard", "usage-guard.json");
let claudeUserSettings = join(clientConfigDirectoryFromEnvironment("CLAUDE_CONFIG_DIR", join(home, ".claude")), "settings.json");
const projectRegistry = join(configDir, "projects.json");
const upgradeSnapshots = join(configDir, "upgrade-snapshots");
const installerLock = join(configDir, "installer.lock");
const legacyEnvironment = join(configDir, "env");
const retiredLegacyEnvironment = join(configDir, "env.alpha-retired");
const migratingLegacyEnvironment = join(configDir, "env.alpha-migrating");
const secretMigrationMarker = join(configDir, "legacy-secrets-migrated-v1.json");
const codexPlugin = join(home, "plugins", "dduo-solo-founder");
const claudePlugin = join(dataDir, "claude-plugin");
const clientSupportRoot = join(runtime, "it.dduo.client-support");
const codexAdapterSource = join(clientSupportRoot, "codex");
const claudeAdapterSource = join(clientSupportRoot, "claude-code");
const legacyCodexPlugin = join(home, "plugins", "opendduo");
let codexConfig = join(clientConfigDirectoryFromEnvironment("CODEX_HOME", join(home, ".codex")), "config.toml");
let legacyCodexCache = join(dirname(codexConfig), "plugins", "cache", "personal", "opendduo");
const codexMarketplace = join(home, ".agents", "plugins", "marketplace.json");
const checksumManifest = join(root, "checksums.sha256");
const backupRegistry = join(configDir, "backups.json");
const clientExecutableRecord = join(configDir, "client-executables.json");
const clientScopeRecord = join(configDir, "client-scopes.json");
const minimumCodexVersion = [0, 150, 0];
const expectedCodexHookEvents = new Set([
  "sessionStart",
  "userPromptSubmit",
  "stop",
]);
const defaultCodexDesktopExecutable = "/Applications/ChatGPT.app/Contents/Resources/codex";
const codexDesktopExecutableEnv = "DDUO_SOLO_FOUNDER_CODEX_DESKTOP";
const runtimeExecutables = [
  "dduo-solo-founder",
  "dduo-solo-founder-mcp",
  "dduo-solo-founder-bridge",
  "dduo-solo-founder-agent",
  "dduo-solo-founder-claude-statusline",
  "dduo-solo-founder-claude-statusline-restore",
  "dduo-solo-founder-hook-session-start",
  "dduo-solo-founder-hook-prompt",
  "dduo-solo-founder-hook-stop",
  "dduo-solo-founder-hook-dispatch",
  "dduo-solo-founder-mcp-dispatch",
];
const retiredRuntimeExecutables = ["dduo-solo-founder-hook-post-tool"];
const retiredUpdaterExecutables = ["dduo-solo-founder-client-update"];
const retiredUpdaterState = [
  join(configDir, "update-queue"),
  join(configDir, "update-trust.json"),
  join(configDir, "session-pins"),
  join(configDir, "client-update-state.json"),
  join(dataDir, "client-releases"),
  join(dataDir, "client-current.json"),
  join(dataDir, "client-previous.json"),
];
const runtimeShimMarker = "# dduo-runtime-shim-v1";
const projectSecretKeys = new Set([
  "OPENAI_API_KEY",
  "DDUO_DATABASE_PASSWORD",
  "DDUO_AUTH_SIGNING_SECRET",
  "DDUO_INFRASTRUCTURE_TOKEN",
  "DDUO_SESSION_SECRET",
  "DDUO_NODE_AUTHORITY_SECRET",
]);

const log = (message) => process.stdout.write(`${message}\n`);
const warn = (message) => process.stderr.write(`WARN  ${message}\n`);

process.on("uncaughtException", (error) => {
  const message = String(error?.message || error).split("\n", 1)[0];
  process.stderr.write(`ERROR ${message}\n`);
  process.exitCode = 1;
});

const options = parseArgs(process.argv.slice(2));
const clientDescriptors = new Map();
const clientCandidateDescriptors = new Map();
applyClientScope(options);
const step = (message) => {
  if (options.verbose) log(message);
};

function clientConfigDirectoryFromEnvironment(variable, fallback) {
  const value = process.env[variable];
  if (!value) return fallback;
  if (!isAbsolute(value) || /[\r\n\0]/.test(value)) {
    throw new Error(`${variable} must be one absolute path without line breaks.`);
  }
  const directory = resolve(value);
  if (existsSync(directory)) {
    if (!statSync(directory).isDirectory()) throw new Error(`${variable} must identify a directory.`);
    try { accessSync(directory, constants.R_OK | constants.W_OK); }
    catch { throw new Error(`${variable} is not accessible for this user.`); }
  }
  return directory;
}

function parseArgs(args) {
  const parsed = {
    only: new Set(),
    dryRun: false,
    force: false,
    rollbackSnapshot: "",
    uninstall: false,
    verify: false,
    verbose: false,
    yes: false,
    noSetup: false,
    projectRoot: process.cwd(),
    surface: "unknown",
    clientConfigDir: "",
    clientExecutable: "",
    clientProbeExecutable: "",
  };
  const optionValue = (flag, index) => {
    const value = args[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${flag} requires a value.`);
    return value;
  };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--only") parsed.only.add(optionValue(arg, index++));
    else if (arg === "--dry-run") parsed.dryRun = true;
    else if (arg === "--force") parsed.force = true;
    else if (arg === "--restore-upgrade-snapshot") parsed.rollbackSnapshot = resolve(optionValue(arg, index++));
    else if (arg === "--uninstall") parsed.uninstall = true;
    else if (arg === "--verify") parsed.verify = true;
    else if (arg === "--verbose") parsed.verbose = true;
    else if (arg === "--yes" || arg === "-y" || arg === "--non-interactive") parsed.yes = true;
    else if (arg === "--no-setup") parsed.noSetup = true;
    else if (arg === "--headless") { parsed.only.add("core"); parsed.noSetup = true; }
    else if (arg === "--project-root") parsed.projectRoot = resolve(optionValue(arg, index++));
    else if (arg === "--surface") parsed.surface = optionValue(arg, index++);
    else if (arg === "--client-config-dir") parsed.clientConfigDir = optionValue(arg, index++);
    else if (arg === "--client-executable") parsed.clientExecutable = optionValue(arg, index++);
    else if (arg === "--client-probe-executable") parsed.clientProbeExecutable = optionValue(arg, index++);
    else if (arg === "--help" || arg === "-h") {
      log("Usage: node bin/install.mjs [--only core|codex|claude] [--surface cli|vscode|desktop|unknown] [--client-config-dir PATH] [--client-executable PATH] [--client-probe-executable PATH] [--headless] [--no-setup] [--project-root PATH] [--dry-run] [--force] [--verify] [--uninstall] [--restore-upgrade-snapshot PATH] [--yes] [--verbose]");
      log("--only core / --headless: install the runtime without client adapters or browser Setup. --no-setup: install selected adapters without opening Setup.");
      process.exit(0);
    } else throw new Error(`Unknown argument: ${arg}`);
  }
  for (const target of parsed.only) if (!["core", "codex", "claude"].includes(target)) throw new Error(`Unknown agent: ${target}`);
  if (parsed.only.has("core")) {
    if (parsed.only.size > 1) throw new Error("--only core / --headless cannot be combined with a client adapter.");
    parsed.noSetup = true;
  }
  if (!["cli", "vscode", "desktop", "unknown"].includes(parsed.surface)) {
    throw new Error("--surface must be cli, vscode, desktop, or unknown.");
  }
  const scoped = [parsed.clientConfigDir, parsed.clientExecutable, parsed.clientProbeExecutable]
    .filter(Boolean);
  if (scoped.length || parsed.surface !== "unknown") {
    if (parsed.only.size !== 1 || parsed.only.has("core")) {
      throw new Error("Client surface and path options require exactly one --only codex or --only claude.");
    }
  }
  if (parsed.surface === "desktop" && parsed.only.has("claude")) {
    throw new Error("--surface desktop is supported only for Codex.");
  }
  for (const [flag, value] of [
    ["--client-config-dir", parsed.clientConfigDir],
    ["--client-executable", parsed.clientExecutable],
    ["--client-probe-executable", parsed.clientProbeExecutable],
  ]) {
    if (!value) continue;
    if (!isAbsolute(value) || /[\r\n\0]/.test(value)) {
      throw new Error(`${flag} must be one absolute path without line breaks.`);
    }
  }
  return parsed;
}

function applyClientScope(parsed) {
  if (parsed.only.has("core")) return;
  const saved = readClientRecord(clientScopeRecord, { kind: "scopes", repairFamily: recordRepairFamily() });
  for (const family of ["codex", "claude"]) {
    if (parsed.only.size && !parsed.only.has(family)) continue;
    const explicit = parsed.only.has(family) && parsed.clientConfigDir;
    const environmentKey = family === "codex" ? "CODEX_HOME" : "CLAUDE_CONFIG_DIR";
    const recorded = saved.clients?.[family]?.config_dir;
    const chosen = explicit || process.env[environmentKey] || recorded;
    if (!chosen) continue;
    const directory = resolve(chosen);
    if (recorded && directory !== recorded) {
      throw new Error(`dDuo already manages another ${clientLabel(family)} profile on this host; remove it explicitly before selecting a second profile.`);
    }
    if (existsSync(directory)) {
      if (!statSync(directory).isDirectory()) {
        throw new Error("--client-config-dir must identify a directory.");
      }
      try {
        accessSync(directory, constants.R_OK | constants.W_OK);
      } catch {
        throw new Error("--client-config-dir is not accessible for this user.");
      }
    }
    if (family === "codex") {
      process.env.CODEX_HOME = directory;
      codexConfig = join(directory, "config.toml");
      legacyCodexCache = join(directory, "plugins", "cache", "personal", "opendduo");
    } else {
      process.env.CLAUDE_CONFIG_DIR = directory;
      claudeUserSettings = join(directory, "settings.json");
    }
  }
}

function has(command) {
  if (command === "codex" || command === "claude") return Boolean(selectedClientDescriptor(command) || findInPath(command));
  return Boolean(findInPath(command));
}

function findInPath(command) {
  const checker = process.platform === "win32" ? "where" : "sh";
  const args = process.platform === "win32" ? [command] : ["-c", `command -v ${command}`];
  const result = spawnSync(checker, args, { encoding: "utf8" });
  const candidate = String(result.stdout || "").trim().split(/\r?\n/, 1)[0];
  return candidate && executableFile(candidate) ? candidate : "";
}

function executableFile(path) {
  if (!path || !existsSync(path)) return false;
  const metadata = statSync(path);
  if (!metadata.isFile()) return false;
  try {
    accessSync(path, process.platform === "win32" ? constants.F_OK : constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function statuslineRestoreCommand() {
  const name = "dduo-solo-founder-claude-statusline-restore";
  if (has(name)) return name;
  if (!existsSync(hookRuntimePointer)) return "";
  const metadata = lstatSync(hookRuntimePointer);
  if (
    metadata.isSymbolicLink()
    || !metadata.isFile()
    || (process.platform !== "win32" && (metadata.mode & 0o077) !== 0)
  ) {
    throw new Error(`Unsafe dDuo runtime pointer: ${hookRuntimePointer}`);
  }
  const runtimeBin = readFileSync(hookRuntimePointer, "utf8").trim();
  if (!runtimeBin || runtimeBin.length > 4096 || !isAbsolute(runtimeBin)) return "";
  const candidate = join(runtimeBin, runtimeExecutableFilename(name));
  return executableFile(candidate) ? candidate : "";
}

function readJsonObject(path) {
  if (!existsSync(path)) return null;
  const metadata = lstatSync(path);
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    throw new Error(`Unsafe Claude configuration file: ${path}`);
  }
  let value;
  try {
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error(`Invalid Claude configuration file: ${path}`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Invalid Claude configuration file: ${path}`);
  }
  return value;
}

function statuslineCommandIsDduo(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  let command = typeof value.command === "string" ? value.command : "";
  const encoded = command.match(/FromBase64String\('([^']+)'\)/)?.[1];
  if (encoded) {
    try {
      command = Buffer.from(encoded, "base64").toString("utf8");
    } catch {
      return false;
    }
  }
  return command.includes("dduo-solo-founder-claude-statusline")
    && !command.includes("dduo-solo-founder-claude-statusline-restore");
}

function settingsContainDduoStatusline(path) {
  const settings = readJsonObject(path);
  return Boolean(settings && statuslineCommandIsDduo(settings.statusLine));
}

function registeredProjectRoots() {
  const registry = readJsonObject(projectRegistry);
  if (!registry || !registry.projects || typeof registry.projects !== "object") return [];
  return Object.values(registry.projects)
    .map((project) => project && typeof project === "object" ? project.root_path : "")
    .filter((path) => typeof path === "string" && path.length > 0);
}

function claudeStatuslineRestoreRequired() {
  if (
    existsSync(claudeStatuslineState)
    || existsSync(legacyClaudeStatuslineState)
    || settingsContainDduoStatusline(claudeUserSettings)
  ) {
    return true;
  }
  return registeredProjectRoots().some((projectRoot) => (
    settingsContainDduoStatusline(join(projectRoot, ".claude", "settings.local.json"))
    || settingsContainDduoStatusline(join(projectRoot, ".claude", "settings.json"))
  ));
}

function restoreClaudeStatuslineBeforeUninstall() {
  const required = claudeStatuslineRestoreRequired();
  const command = statuslineRestoreCommand();
  if (!command) {
    if (required) {
      throw new Error(
        "Claude usage telemetry is installed, but its restore command is unavailable; repair the dDuo runtime before uninstalling.",
      );
    }
    return;
  }
  run(command, []);
}

function run(command, args, settings = {}) {
  const displayCommand = command === "claude" || command === "codex"
    ? selectedClientCommand(command) : command;
  step(`${displayCommand} ${args.join(" ")}`);
  if (options.dryRun) return "";
  try {
    const stdio = settings.capture || !options.verbose ? "pipe" : "inherit";
    const invocation = installerCommand(command, args);
    const result = execFileSync(invocation.command, invocation.args, {
      encoding: "utf8", stdio, ...settings, ...invocation.options,
    });
    step(`Command completed: ${displayCommand}`);
    return result;
  } catch (error) {
    if (settings.optional) return "";
    if (!options.verbose) {
      const detail = String(error.stderr || error.stdout || "").trim();
      if (detail) process.stderr.write(`${detail}\n`);
    }
    throw error;
  }
}

function installerCommand(command, args) {
  // Runtime entrypoints have native .exe files in the committed venv. Calling
  // those directly avoids going through the user's interactive .cmd launcher.
  if (process.platform === "win32" && runtimeExecutables.includes(command)) {
    const candidate = join(hookRuntimeBin(), runtimeExecutableFilename(command));
    if (executableFile(candidate)) return { command: candidate, args, options: {} };
  }
  if (command === "codex" || command === "claude") {
    return selectedClientInvocation(command, args);
  }
  if (clientCandidateDescriptors.has(command)) {
    return clientDescriptorInvocation(clientCandidateDescriptors.get(command), args);
  }
  for (const family of ["codex", "claude"]) {
    if (!selected(family)) continue;
    const descriptor = selectedClientDescriptor(family);
    if (descriptor?.launcher === command) {
      return clientDescriptorInvocation(descriptor, args);
    }
  }
  return commandInvocation(command, args);
}

function clientDescriptorInvocation(descriptor, args) {
  return clientCommandInvocation(descriptor, args);
}

function selectedClientInvocation(family, args) {
  const descriptor = selectedClientDescriptor(family);
  if (descriptor) return clientDescriptorInvocation(descriptor, args);
  return commandInvocation(family, args);
}

function lstatIfExists(path) {
  try {
    return lstatSync(path);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

function fileSnapshot(path) {
  const metadata = lstatIfExists(path);
  if (!metadata) return { type: "missing", contents: null, mode: 0o600 };
  if (metadata.isSymbolicLink()) {
    return { type: "symlink", target: readlinkSync(path), contents: null, mode: 0o777 };
  }
  if (!metadata.isFile()) {
    throw new Error(`Unsafe installer state file: ${path}`);
  }
  return { type: "file", contents: readFileSync(path), mode: metadata.mode & 0o777 };
}

function restoreFile(path, snapshot) {
  rmSync(path, { force: true });
  if (snapshot.type === "missing") return;
  mkdirSync(dirname(path), { recursive: true });
  if (snapshot.type === "symlink") {
    symlinkSync(snapshot.target, path, process.platform === "win32" ? "file" : undefined);
    return;
  }
  const temporary = privateTemporaryPath(path, "restore");
  try {
    writeExclusiveFile(temporary, snapshot.contents, snapshot.mode);
    chmodSync(temporary, snapshot.mode);
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function stableLauncherFilename(name) {
  return process.platform === "win32" ? `${name}.cmd` : name;
}

function runtimeExecutableFilename(name) {
  return process.platform === "win32" ? `${name}.exe` : name;
}

function shellSingleQuote(value) {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function runtimeShim(name, path, fallback) {
  if (process.platform === "win32") {
    const pointer = hookRuntimePointer.replaceAll("%", "%%");
    const executable = runtimeExecutableFilename(name);
    return [
      "@echo off",
      "chcp 65001 >nul",
      "setlocal DisableDelayedExpansion",
      `set /p "DDUO_RUNTIME_BIN="<"${pointer}"`,
      "if not defined DDUO_RUNTIME_BIN exit /b 127",
      `if not exist "%DDUO_RUNTIME_BIN%\\${executable}" exit /b 127`,
      `"%DDUO_RUNTIME_BIN%\\${executable}" %*`,
      "exit /b %ERRORLEVEL%",
      "",
    ].join("\r\n");
  }
  return [
    "#!/bin/sh",
    runtimeShimMarker,
    "set -eu",
    `pointer=${shellSingleQuote(hookRuntimePointer)}`,
    `shim=${shellSingleQuote(resolve(path))}`,
    `legacy=${shellSingleQuote(resolve(fallback))}`,
    'runtime_bin=""',
    'if [ -r "$pointer" ]; then IFS= read -r runtime_bin < "$pointer"; fi',
    'case "$runtime_bin" in /*) ;; *) exit 127 ;; esac',
    'runtime_bin=$(CDPATH= cd "$runtime_bin" 2>/dev/null && pwd -P) || exit 127',
    `target="$runtime_bin/${name}"`,
    'if [ "$target" = "$shim" ]; then target="$legacy"; fi',
    '[ -x "$target" ] || exit 127',
    'exec "$target" "$@"',
    "",
  ].join("\n");
}

function snapshotsEqual(left, right) {
  if (left.type !== right.type || left.mode !== right.mode) return false;
  if (left.type === "missing") return true;
  if (left.type === "symlink") return left.target === right.target;
  return left.contents.equals(right.contents);
}

function isStableLauncher(path) {
  return existsSync(path)
    && !lstatSync(path).isSymbolicLink()
    && lstatSync(path).isFile()
    && readFileSync(path, "utf8").slice(0, 4096).includes(runtimeShimMarker);
}

function preserveLegacyLauncher(path, fallback) {
  if (process.platform === "win32" || !existsSync(path) || isStableLauncher(path)) return;
  const source = fileSnapshot(path);
  const existing = fileSnapshot(fallback);
  if (existing.type !== "missing") {
    if (snapshotsEqual(source, existing)) return;
    throw new Error(`Conflicting preserved runtime launcher: ${fallback}`);
  }
  const temporary = `${fallback}.${process.pid}.next`;
  try {
    if (source.type === "symlink") symlinkSync(source.target, temporary);
    else {
      writeFileSync(temporary, source.contents, { mode: source.mode });
      chmodSync(temporary, source.mode);
    }
    renameSync(temporary, fallback);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function installStableLauncher(path, name, fallback) {
  preserveLegacyLauncher(path, fallback);
  const contents = runtimeShim(name, path, fallback);
  if (
    existsSync(path)
    && !lstatSync(path).isSymbolicLink()
    && lstatSync(path).isFile()
    && readFileSync(path, "utf8") === contents
  ) return;
  const temporary = `${path}.${process.pid}.next`;
  try {
    writeFileSync(temporary, contents, { mode: 0o755 });
    chmodSync(temporary, 0o755);
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function copyRuntime() {
  step(`Install immutable runtime copy at ${runtime}`);
  if (options.dryRun) return;
  const entries = parseChecksumManifest(readFileSync(checksumManifest, "utf8"));
  const candidate = `${runtime}.${process.pid}.next`;
  const previous = `${runtime}.${process.pid}.rollback`;
  const hadRuntime = existsSync(runtime);
  const pointerBefore = fileSnapshot(runtimePointer);
  const hookPointerBefore = fileSnapshot(hookRuntimePointer);
  rmSync(candidate, { recursive: true, force: true });
  rmSync(previous, { recursive: true, force: true });
  try {
    mkdirSync(candidate, { recursive: true });
    for (const { expected, relative } of entries) {
      const source = join(root, relative);
      const contents = readFileSync(source);
      const actual = createHash("sha256").update(contents).digest("hex");
      if (actual !== expected) throw new Error(`Distribution changed while copying: ${relative}`);
      const destination = join(candidate, relative);
      mkdirSync(dirname(destination), { recursive: true });
      writeFileSync(destination, contents, { mode: statSync(source).mode & 0o777 });
    }
    writeFileSync(join(candidate, "checksums.sha256"), readFileSync(checksumManifest));
    if (hadRuntime) renameSync(runtime, previous);
    renameSync(candidate, runtime);
    privateDirectory(configDir);
    writeFileSync(runtimePointer, `${runtime}\n`, { mode: 0o600 });
    chmodSync(runtimePointer, 0o600);
  } catch (error) {
    rmSync(candidate, { recursive: true, force: true });
    if (existsSync(previous)) {
      rmSync(runtime, { recursive: true, force: true });
      renameSync(previous, runtime);
    } else if (!hadRuntime) {
      rmSync(runtime, { recursive: true, force: true });
    }
    throw error;
  }
  return {
    previous,
    rollback() {
      rmSync(runtime, { recursive: true, force: true });
      if (hadRuntime && existsSync(previous)) renameSync(previous, runtime);
      restoreFile(runtimePointer, pointerBefore);
      restoreFile(hookRuntimePointer, hookPointerBefore);
    },
    commit() {
      rmSync(previous, { recursive: true, force: true });
    },
  };
}

function verifySource() {
  if (!existsSync(checksumManifest)) throw new Error("checksums.sha256 is missing from this distribution.");
  const failures = [];
  const entries = parseChecksumManifest(readFileSync(checksumManifest, "utf8"));
  for (const { expected, relative } of entries) {
    const path = join(root, relative);
    if (!existsSync(path)) failures.push(`${relative} missing`);
    else if (lstatSync(path).isSymbolicLink() || !statSync(path).isFile()) {
      failures.push(`${relative} is not a regular file`);
    }
    else {
      const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
      if (actual !== expected) failures.push(`${relative} changed`);
    }
  }
  if (failures.length) throw new Error(`Distribution integrity check failed: ${failures.join(", ")}`);
  log("PASS  distribution checksums");
}

function ensureUv() {
  if (has("uv")) return;
  if (options.dryRun) return step("Install uv from the official Astral installer");
  if (process.platform === "win32") {
    run("powershell", ["-ExecutionPolicy", "ByPass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"]);
  } else {
    run("sh", ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]);
  }
  // Astral updates the persistent shell profile; this installer process must
  // also see its freshly installed uv on Windows and on POSIX hosts.
  process.env.PATH = [join(home, ".local", "bin"), join(home, ".cargo", "bin"), process.env.PATH || ""].join(delimiter);
  if (!has("uv")) throw new Error("uv installation completed but `uv` is not available on PATH.");
}

function installCore() {
  ensureUv();
  const environment = join(runtime, ".venv");
  const buildSource = `${runtime}.${process.pid}.build-source`;
  const runtimeBin = join(environment, process.platform === "win32" ? "Scripts" : "bin");
  const toolBin = run("uv", ["tool", "dir", "--bin"], { capture: true }).trim();
  if (!toolBin) throw new Error("dDuo runtime installed but its executable directory could not be resolved.");
  const launchers = runtimeExecutables.map((name) => {
    const source = join(runtimeBin, runtimeExecutableFilename(name));
    const target = join(toolBin, stableLauncherFilename(name));
    const legacy = process.platform === "win32"
      ? join(toolBin, runtimeExecutableFilename(name))
      : null;
    const fallback = process.platform === "win32"
      ? null
      : join(toolBin, `.dduo-legacy-${name}`);
    return {
      name,
      source,
      target,
      snapshot: fileSnapshot(target),
      legacy,
      legacySnapshot: legacy ? fileSnapshot(legacy) : null,
      fallback,
      fallbackSnapshot: fallback ? fileSnapshot(fallback) : null,
    };
  });
  try {
    step("Prepare isolated locked-build source");
    rmSync(buildSource, { recursive: true, force: true });
    cpSync(runtime, buildSource, { recursive: true });
    step("Locked-build source ready");
    const sync = [
      "sync",
      "--frozen",
      "--no-dev",
      "--no-editable",
      "--group",
      "runtime-build",
      "--project",
      buildSource,
      "--no-config",
    ];
    const syncEnvironment = { ...process.env, UV_PROJECT_ENVIRONMENT: environment };
    // PEP 517 build isolation would resolve build tools outside uv.lock. The
    // first phase materializes the locked runtime-build group; the second uses
    // only that environment to build the local project wheel.
    try {
      run("uv", [...sync, "--no-install-project"], { env: syncEnvironment });
      run("uv", [...sync, "--no-build-isolation"], { env: syncEnvironment });
    } finally {
      rmSync(buildSource, { recursive: true, force: true });
    }
    for (const { source } of launchers) {
      if (!existsSync(source) || !statSync(source).isFile()) {
        throw new Error(`Locked dDuo runtime is missing ${source}.`);
      }
    }
    for (const { name, target, fallback } of launchers) {
      mkdirSync(dirname(target), { recursive: true });
      installStableLauncher(target, name, fallback);
    }
    for (const { legacy } of launchers) {
      if (legacy) rmSync(legacy, { force: true });
    }
    writeHookRuntimePointer(runtimeBin);
    for (const { fallback } of launchers) {
      if (fallback) rmSync(fallback, { force: true });
    }
  } catch (error) {
    rmSync(buildSource, { recursive: true, force: true });
    for (const {
      target,
      snapshot,
      legacy,
      legacySnapshot,
      fallback,
      fallbackSnapshot,
    } of launchers) {
      restoreFile(target, snapshot);
      if (legacy) restoreFile(legacy, legacySnapshot);
      if (fallback) restoreFile(fallback, fallbackSnapshot);
    }
    throw error;
  }
  process.env.PATH = `${toolBin}${process.platform === "win32" ? ";" : ":"}${process.env.PATH}`;
  return {
    rollback() {
      for (const {
        target,
        snapshot,
        legacy,
        legacySnapshot,
        fallback,
        fallbackSnapshot,
      } of launchers) {
        restoreFile(target, snapshot);
        if (legacy) restoreFile(legacy, legacySnapshot);
        if (fallback) restoreFile(fallback, fallbackSnapshot);
      }
    },
    commit() {},
  };
}

const hookExecutables = [
  "dduo-solo-founder-hook-dispatch",
  "dduo-solo-founder-hook-session-start",
  "dduo-solo-founder-hook-prompt",
  "dduo-solo-founder-hook-stop",
];

function executable(path) {
  return executableFile(path);
}

function parseCodexVersion(output) {
  const match = String(output).match(/(^|\D)(\d+)\.(\d+)\.(\d+)(?!\d)/);
  return match ? match.slice(2, 5).map((value) => Number(value)) : null;
}

function compareVersions(left, right) {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return 0;
}

function codexCandidates() {
  const candidates = [];
  const selectedDescriptor = selectedClientDescriptor("codex");
  if (selectedDescriptor) {
    candidates.push({ command: selectedDescriptor.launcher, desktop: false });
    return candidates;
  }
  const pathCommand = findInPath("codex");
  if (pathCommand) candidates.push({ command: pathCommand, desktop: false });
  const configuredDesktop = process.env[codexDesktopExecutableEnv];
  const desktopCommand = configuredDesktop || defaultCodexDesktopExecutable;
  if (
    ["unknown", "desktop"].includes(options.surface)
    && (process.platform === "darwin" || Boolean(configuredDesktop))
    && executable(desktopCommand)
    && !candidates.some(({ command }) => command === desktopCommand)
  ) {
    candidates.push({ command: desktopCommand, desktop: true });
  }
  for (const candidate of candidates) {
    clientCandidateDescriptors.set(candidate.command, resolveClientCommand("codex", candidate.command));
  }
  return candidates;
}

function codexVersion(command) {
  const invocation = installerCommand(command, ["--version"]);
  const result = spawnSync(invocation.command, invocation.args, {
    encoding: "utf8",
    env: process.env,
    timeout: 3_000,
    ...invocation.options,
  });
  if (result.status !== 0) return null;
  return parseCodexVersion(`${result.stdout || ""}\n${result.stderr || ""}`);
}

let codexResolution;

function resolveCodexExecutable({ required = false } = {}) {
  if (codexResolution === undefined) {
    const discovered = codexCandidates().map((candidate) => ({
      ...candidate,
      version: codexVersion(candidate.command),
    }));
    const compatible = discovered.filter(
      ({ version }) => version && compareVersions(version, minimumCodexVersion) >= 0,
    );
    compatible.sort((left, right) => (
      compareVersions(right.version, left.version) || Number(right.desktop) - Number(left.desktop)
    ));
    codexResolution = { discovered, selected: compatible[0] || null };
    if (codexResolution.selected) {
      const selected = codexResolution.selected;
      clientDescriptors.set("codex", clientCandidateDescriptors.get(selected.command)
        || selectedClientDescriptor("codex"));
      step(`Use Codex ${selected.version.join(".")} at ${selected.command}`);
    }
  }
  if (codexResolution.selected) return codexResolution.selected.command;
  if (!required) return null;
  const minimum = minimumCodexVersion.join(".");
  if (!codexResolution.discovered.length) {
    throw new Error(`Codex ${minimum} or newer is required but was not found.`);
  }
  const found = codexResolution.discovered
    .map(({ command, version }) => `${version ? version.join(".") : "unknown"} at ${command}`)
    .join(", ");
  throw new Error(
    `Codex ${minimum} or newer is required for dDuo lifecycle hooks; found ${found}. Update Codex and retry installation.`,
  );
}

function selectedClientCommand(family) {
  const descriptor = selectedClientDescriptor(family);
  if (descriptor) return descriptor.launcher;
  return family;
}

function selectedClientDescriptor(family) {
  if (clientDescriptors.has(family)) return clientDescriptors.get(family);
  if (options.only.has(family) && options.clientExecutable) {
    // Explicit selection may replace a stale launcher, but cannot make an
    // unreadable global record or an unrelated malformed client disappear.
    readClientRecord(clientExecutableRecord, { repairFamily: recordRepairFamily() });
    const descriptor = resolveClientCommand(family, options.clientExecutable);
    clientDescriptors.set(family, descriptor);
    return descriptor;
  }
  try {
    let descriptor = recordedClientCommand(clientExecutableRecord, family, { repairFamily: recordRepairFamily() });
    if (!descriptor && family === "claude") {
      const launcher = findInPath(family);
      if (launcher) descriptor = resolveClientCommand(family, launcher);
    }
    if (descriptor) clientDescriptors.set(family, descriptor);
    return descriptor;
  } catch (error) {
    const message = String(error?.message || error);
    throw new Error(`${message} Do not select another ${clientLabel(family)} executable automatically.`);
  }
}

function hookRuntimeBin() {
  if (!existsSync(hookRuntimePointer)) return "";
  try {
    const value = readFileSync(hookRuntimePointer, "utf8").trim();
    return value && resolve(value);
  } catch {
    return "";
  }
}

function hookRuntimeReady() {
  const bin = hookRuntimeBin();
  return Boolean(bin) && hookExecutables.every((name) => executable(join(bin, runtimeExecutableFilename(name))));
}

function writeHookRuntimePointer(toolBin) {
  const bin = resolve(toolBin);
  const missing = hookExecutables.filter((name) => !executable(join(bin, runtimeExecutableFilename(name))));
  if (missing.length) {
    throw new Error(`dDuo runtime is missing lifecycle hook executables: ${missing.join(", ")}.`);
  }
  privateDirectory(configDir);
  const temporary = `${hookRuntimePointer}.${process.pid}.next`;
  try {
    writeFileSync(temporary, `${bin}\n`, { mode: 0o600 });
    chmodSync(temporary, 0o600);
    renameSync(temporary, hookRuntimePointer);
    chmodSync(hookRuntimePointer, 0o600);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function materializeClaudePlugin(candidate) {
  rmSync(candidate, { recursive: true, force: true });
  mkdirSync(candidate, { recursive: true });
  cpSync(
    join(claudeAdapterSource, ".claude-plugin"),
    join(candidate, ".claude-plugin"),
    { recursive: true },
  );
  cpSync(join(runtime, "skills"), join(candidate, "skills"), { recursive: true });
  mkdirSync(join(candidate, "bin"), { recursive: true });
  cpSync(
    join(runtime, "bin", "agent-plugin-mcp.mjs"),
    join(candidate, "bin", "agent-plugin-mcp.mjs"),
  );
  cpSync(
    join(codexAdapterSource, "hooks", "codex-runtime-hook.mjs"),
    join(candidate, "bin", "agent-plugin-hook.mjs"),
  );
  const manifest = join(candidate, ".claude-plugin", "plugin.json");
  writeFileSync(manifest, `${JSON.stringify(pinNodeCommands(readJsonObject(manifest)), null, 2)}\n`);
  cpSync(join(runtime, "LICENSE"), join(candidate, "LICENSE"));
}

function pinNodeCommands(value) {
  if (Array.isArray(value)) return value.map(pinNodeCommands);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => {
    if (key === "command" && entry === "node") return [key, process.execPath];
    if (key === "command" && typeof entry === "string" && entry.startsWith("node ")) {
      const executable = process.platform === "win32"
        ? `"${process.execPath}"` : shellSingleQuote(process.execPath);
      return [key, `${executable}${entry.slice(4)}`];
    }
    return [key, pinNodeCommands(entry)];
  }));
}

function installClaude() {
  if (!has("claude")) return false;
  const candidate = `${claudePlugin}.${process.pid}.next`;
  const previous = `${claudePlugin}.${process.pid}.rollback`;
  const hadPlugin = existsSync(claudePlugin);
  let oldMoved = false;
  let newInstalled = false;
  const restoreFiles = () => {
    rmSync(candidate, { recursive: true, force: true });
    if (newInstalled) {
      rmSync(claudePlugin, { recursive: true, force: true });
      newInstalled = false;
    }
    if (oldMoved && existsSync(previous)) {
      renameSync(previous, claudePlugin);
      oldMoved = false;
    }
  };
  rmSync(previous, { recursive: true, force: true });
  try {
    materializeClaudePlugin(candidate);
    run("claude", ["plugin", "uninstall", "dduo-solo-founder@dduo-solo-founder"], { optional: true });
    run("claude", ["plugin", "marketplace", "remove", "dduo-solo-founder"], { optional: true });
    if (hadPlugin) {
      renameSync(claudePlugin, previous);
      oldMoved = true;
    }
    renameSync(candidate, claudePlugin);
    newInstalled = true;
    run("claude", ["plugin", "marketplace", "add", claudePlugin]);
    run("claude", ["plugin", "install", "dduo-solo-founder@dduo-solo-founder"]);
  } catch (error) {
    run("claude", ["plugin", "uninstall", "dduo-solo-founder@dduo-solo-founder"], { optional: true });
    run("claude", ["plugin", "marketplace", "remove", "dduo-solo-founder"], { optional: true });
    restoreFiles();
    throw error;
  }
  return {
    installed: true,
    rollback() {
      run("claude", ["plugin", "uninstall", "dduo-solo-founder@dduo-solo-founder"], { optional: true });
      run("claude", ["plugin", "marketplace", "remove", "dduo-solo-founder"], { optional: true });
      restoreFiles();
    },
    commit() {
      rmSync(previous, { recursive: true, force: true });
      oldMoved = false;
    },
  };
}

function claudeMarketplaceInstalled() {
  if (!has("claude")) return false;
  const marketplaces = parseClientJson(
    "claude",
    ["plugin", "marketplace", "list", "--json"],
    "Claude marketplaces",
  );
  if (!Array.isArray(marketplaces)) {
    throw new Error("Could not verify Claude marketplaces: the marketplace list is unavailable.");
  }
  return marketplaces.some((marketplace) => marketplace?.name === "dduo-solo-founder");
}

function captureClaudeState() {
  if (!selected("claude") || !has("claude")) return null;
  return {
    plugin: claudePluginInstalled(),
    marketplace: claudeMarketplaceInstalled(),
  };
}

function restoreClaudeState(snapshot) {
  if (!snapshot) return;
  run("claude", ["plugin", "uninstall", "dduo-solo-founder@dduo-solo-founder"], { optional: true });
  run("claude", ["plugin", "marketplace", "remove", "dduo-solo-founder"], { optional: true });
  if (snapshot.marketplace || snapshot.plugin) {
    const source = existsSync(claudePlugin) ? claudePlugin : runtime;
    if (!existsSync(source)) throw new Error("Previous Claude plugin package is unavailable.");
    run("claude", ["plugin", "marketplace", "add", source]);
  }
  if (snapshot.plugin) {
    run("claude", ["plugin", "install", "dduo-solo-founder@dduo-solo-founder"]);
  }
  if (claudePluginInstalled() !== snapshot.plugin) {
    throw new Error("Claude plugin rollback was incomplete.");
  }
  if (claudeMarketplaceInstalled() !== snapshot.marketplace) {
    throw new Error("Claude marketplace rollback was incomplete.");
  }
}

function readMarketplace() {
  if (!existsSync(codexMarketplace)) return { name: "personal", interface: { displayName: "Personal" }, plugins: [] };
  const data = JSON.parse(readFileSync(codexMarketplace, "utf8"));
  data.name ||= "personal";
  data.interface ||= { displayName: "Personal" };
  data.plugins ||= [];
  return data;
}

function codexHooksEnabled(command = resolveCodexExecutable()) {
  if (!command) return false;
  const features = run(command, ["features", "list"], { capture: true, optional: true });
  return /^hooks\s+\S+(?:\s+\S+)*\s+true\s*$/m.test(features);
}

function enableCodexHooks(command) {
  run(command, ["features", "enable", "hooks"]);
  if (!codexHooksEnabled(command)) {
    throw new Error("Codex lifecycle hooks could not be enabled. Update Codex and retry installation.");
  }
}

function materializeCodexPlugin(candidate) {
  rmSync(candidate, { recursive: true, force: true });
  mkdirSync(candidate, { recursive: true });
  cpSync(
    join(codexAdapterSource, ".codex-plugin"),
    join(candidate, ".codex-plugin"),
    { recursive: true },
  );
  cpSync(join(runtime, "skills"), join(candidate, "skills"), { recursive: true });
  cpSync(join(codexAdapterSource, "hooks"), join(candidate, "hooks"), { recursive: true });
  const hooksPath = join(candidate, "hooks", "hooks.json");
  writeFileSync(hooksPath, `${JSON.stringify(pinNodeCommands(readJsonObject(hooksPath)), null, 2)}\n`);
  for (const file of ["LICENSE"]) {
    cpSync(join(runtime, file), join(candidate, file));
  }
  const dispatcher = join(
    hookRuntimeBin(),
    runtimeExecutableFilename("dduo-solo-founder-mcp-dispatch"),
  );
  if (!executable(dispatcher)) {
    throw new Error("The installed dDuo MCP dispatcher is unavailable.");
  }
  const manifestPath = join(candidate, ".codex-plugin", "plugin.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  manifest.mcpServers["dduo-solo-founder"].command = dispatcher;
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}

function regularTreeFiles(directory) {
  const files = [];
  const visit = (current, prefix = "") => {
    if (lstatSync(current).isSymbolicLink() || !statSync(current).isDirectory()) {
      throw new Error("not a regular directory");
    }
    for (const name of readdirSync(current)) {
      const path = join(current, name);
      const relative = prefix ? join(prefix, name) : name;
      const metadata = lstatSync(path);
      if (metadata.isSymbolicLink()) throw new Error("symbolic link found");
      if (metadata.isDirectory()) visit(path, relative);
      else if (metadata.isFile()) files.push(relative);
      else throw new Error("non-regular file found");
    }
  };
  try {
    visit(directory);
    return files.sort();
  } catch {
    return null;
  }
}

function identicalRegularFiles(source, installed) {
  try {
    const sourceMetadata = lstatSync(source);
    const installedMetadata = lstatSync(installed);
    if (sourceMetadata.isSymbolicLink() || installedMetadata.isSymbolicLink()) return false;
    if (!sourceMetadata.isFile() || !installedMetadata.isFile()) return false;
    if (process.platform !== "win32"
      && (sourceMetadata.mode & 0o111) !== (installedMetadata.mode & 0o111)) return false;
    return readFileSync(source).equals(readFileSync(installed));
  } catch {
    return false;
  }
}

function copiedTreeReady(source, installed, transformed = {}) {
  const sourceFiles = regularTreeFiles(source);
  const installedFiles = regularTreeFiles(installed);
  if (!sourceFiles || !installedFiles) return false;
  if (JSON.stringify(sourceFiles) !== JSON.stringify(installedFiles)) return false;
  return sourceFiles.every((file) => {
    if (!(file in transformed)) return identicalRegularFiles(join(source, file), join(installed, file));
    try {
      return JSON.stringify(readJsonObject(join(installed, file))) === JSON.stringify(transformed[file]);
    } catch { return false; }
  });
}

function codexPluginPackageReady() {
  let topLevel;
  try {
    topLevel = readdirSync(codexPlugin).sort();
  } catch {
    return false;
  }
  if (JSON.stringify(topLevel) !== JSON.stringify([
    ".codex-plugin",
    "LICENSE",
    "hooks",
    "skills",
  ])) return false;
  if (
    !copiedTreeReady(join(runtime, "skills"), join(codexPlugin, "skills"))
    || !copiedTreeReady(join(codexAdapterSource, "hooks"), join(codexPlugin, "hooks"), {
      "hooks.json": pinNodeCommands(readJsonObject(join(codexAdapterSource, "hooks", "hooks.json"))),
    })
  ) return false;
  if (!identicalRegularFiles(join(runtime, "LICENSE"), join(codexPlugin, "LICENSE"))) return false;
  const sourceManifestFiles = regularTreeFiles(join(codexAdapterSource, ".codex-plugin"));
  const installedManifestFiles = regularTreeFiles(join(codexPlugin, ".codex-plugin"));
  if (!sourceManifestFiles || !installedManifestFiles) return false;
  if (JSON.stringify(sourceManifestFiles) !== JSON.stringify(installedManifestFiles)) return false;
  let manifest;
  let expectedManifest;
  try {
    manifest = JSON.parse(readFileSync(join(codexPlugin, ".codex-plugin", "plugin.json"), "utf8"));
    expectedManifest = JSON.parse(
      readFileSync(join(codexAdapterSource, ".codex-plugin", "plugin.json"), "utf8"),
    );
  } catch {
    return false;
  }
  const dispatcher = join(
    hookRuntimeBin(),
    runtimeExecutableFilename("dduo-solo-founder-mcp-dispatch"),
  );
  expectedManifest.mcpServers["dduo-solo-founder"].command = dispatcher;
  return JSON.stringify(manifest) === JSON.stringify(expectedManifest)
    && executable(dispatcher)
    && !existsSync(join(codexPlugin, ".mcp.json"))
    && !existsSync(join(codexPlugin, "plugin.json"))
    && !existsSync(join(codexPlugin, "mcp.json"))
    && !existsSync(join(codexPlugin, ".claude-plugin"));
}

function claudePluginPackageReady() {
  let topLevel;
  try {
    topLevel = readdirSync(claudePlugin).sort();
  } catch {
    return false;
  }
  if (JSON.stringify(topLevel) !== JSON.stringify([
    ".claude-plugin",
    "LICENSE",
    "bin",
    "skills",
  ])) return false;
  if (
    !copiedTreeReady(
      join(claudeAdapterSource, ".claude-plugin"),
      join(claudePlugin, ".claude-plugin"),
      { "plugin.json": pinNodeCommands(readJsonObject(join(claudeAdapterSource, ".claude-plugin", "plugin.json"))) },
    )
    || !copiedTreeReady(join(runtime, "skills"), join(claudePlugin, "skills"))
  ) return false;
  if (!identicalRegularFiles(join(runtime, "LICENSE"), join(claudePlugin, "LICENSE"))) {
    return false;
  }
  const binFiles = regularTreeFiles(join(claudePlugin, "bin"));
  return JSON.stringify(binFiles) === JSON.stringify(["agent-plugin-hook.mjs", "agent-plugin-mcp.mjs"])
    && identicalRegularFiles(
      join(codexAdapterSource, "hooks", "codex-runtime-hook.mjs"),
      join(claudePlugin, "bin", "agent-plugin-hook.mjs"),
    )
    && identicalRegularFiles(
      join(runtime, "bin", "agent-plugin-mcp.mjs"),
      join(claudePlugin, "bin", "agent-plugin-mcp.mjs"),
    )
    && !existsSync(join(claudePlugin, "plugin.json"))
    && !existsSync(join(claudePlugin, "mcp.json"))
    && !existsSync(join(claudePlugin, ".codex-plugin"));
}

function codexHookReadiness() {
  const unavailable = { discovered: false, authorized: false, authorizationRequired: false };
  if (!has("dduo-solo-founder")) return unavailable;
  const descriptor = selectedClientDescriptor("codex");
  const readinessArgs = [
      "client-readiness",
      "--client",
      "codex",
      "--project-root",
      options.projectRoot,
      "--client-config-dir",
      selectedConfigDirectory("codex"),
    ];
  if (descriptor) readinessArgs.push("--client-executable", descriptor.launcher);
  if (descriptor?.nodeExecutable) readinessArgs.push("--client-node-executable", descriptor.nodeExecutable);
  if (recordRepairFamily() === "codex") readinessArgs.push("--repair-client-record");
  if (options.only.has("codex")) {
    readinessArgs.push("--surface", options.surface);
    if (options.clientProbeExecutable) readinessArgs.push("--client-probe-executable", options.clientProbeExecutable);
  }
  const invocation = installerCommand("dduo-solo-founder", readinessArgs);
  const result = spawnSync(
    invocation.command,
    invocation.args,
    {
      encoding: "utf8",
      env: process.env,
      timeout: 30_000,
      ...invocation.options,
    },
  );
  // client-readiness uses exit 8 for protected actions that still need the
  // user's consent. Other failures must never become a successful hook check.
  if (result.error || ![0, 8].includes(result.status)) return unavailable;
  let payload;
  try {
    payload = JSON.parse(String(result.stdout || ""));
  } catch {
    return unavailable;
  }
  const hooks = payload?.hooks;
  const events = new Set(Array.isArray(hooks?.events) ? hooks.events : []);
  const pendingReasons = new Set([
    "authorization_required",
    "reauthorization_required",
  ]);
  const authorized = hooks?.reason === "authorized" && hooks?.ready === true;
  const authorizationRequired = pendingReasons.has(hooks?.reason) && hooks?.ready === false;
  const discovered = (authorized || authorizationRequired)
    && hooks?.hook_count === expectedCodexHookEvents.size
    && events.size === expectedCodexHookEvents.size
    && [...expectedCodexHookEvents].every((event) => events.has(event));
  return {
    discovered,
    authorized: discovered && authorized,
    authorizationRequired: discovered && authorizationRequired,
    reason: hooks?.reason,
  };
}

function captureCodexState() {
  if (!selected("codex") || !codexCandidates().length) return null;
  const command = resolveCodexExecutable({ required: true });
  const hadPluginSource = existsSync(codexPlugin);
  const registered = codexPluginRegistered(command);
  if (registered && !hadPluginSource) {
    throw new Error("Codex has a dDuo registration without its managed package source; repair that installation before updating.");
  }
  // Validate every native/file snapshot before enabling hooks or replacing
  // any package. A broken list response must leave the previous state intact.
  const marketplace = existsSync(codexMarketplace) ? readFileSync(codexMarketplace) : null;
  readMarketplace();
  return { command, hadPluginSource, registered, marketplace, hooks: codexHooksEnabled(command) };
}

function installCodex(before) {
  if (!codexCandidates().length) return false;
  before ||= captureCodexState();
  const { command, hooks: hooksBefore } = before;
  step(`Install Codex plugin source at ${codexPlugin}`);
  if (!options.dryRun) {
    const candidate = `${codexPlugin}.${process.pid}.next`;
    const previous = `${codexPlugin}.${process.pid}.rollback`;
    const { hadPluginSource, registered: registrationBefore, marketplace: marketplaceBefore } = before;
    let oldMoved = false;
    let newInstalled = false;
    const restore = () => {
      rmSync(candidate, { recursive: true, force: true });
      if (newInstalled) {
        rmSync(codexPlugin, { recursive: true, force: true });
        newInstalled = false;
      }
      if (oldMoved && existsSync(previous)) {
        renameSync(previous, codexPlugin);
        oldMoved = false;
      }
      if (marketplaceBefore === null) rmSync(codexMarketplace, { force: true });
      else writeFileSync(codexMarketplace, marketplaceBefore);
      if (registrationBefore) {
        const restored = readMarketplace();
        run(command, ["plugin", "add", `dduo-solo-founder@${restored.name}`]);
      } else {
        run(command, ["plugin", "remove", "dduo-solo-founder"], { optional: true });
      }
      if (!hooksBefore) {
        run(command, ["features", "disable", "hooks"]);
        if (codexHooksEnabled(command)) {
          throw new Error("Codex hooks rollback was incomplete.");
        }
      }
    };
    rmSync(previous, { recursive: true, force: true });
    try {
      if (!hooksBefore) enableCodexHooks(command);
      materializeCodexPlugin(candidate);
      if (hadPluginSource) {
        renameSync(codexPlugin, previous);
        oldMoved = true;
      }
      renameSync(candidate, codexPlugin);
      newInstalled = true;
      const marketplace = readMarketplace();
      marketplace.plugins = marketplace.plugins.filter((plugin) => plugin.name !== "dduo-solo-founder");
      marketplace.plugins.push({
        name: "dduo-solo-founder",
        source: { source: "local", path: "./plugins/dduo-solo-founder" },
        policy: { installation: "AVAILABLE", authentication: "ON_INSTALL" },
        category: "Productivity",
      });
      mkdirSync(dirname(codexMarketplace), { recursive: true });
      writeFileSync(codexMarketplace, `${JSON.stringify(marketplace, null, 2)}\n`);
      run(command, ["plugin", "add", `dduo-solo-founder@${marketplace.name}`]);
    } catch (error) {
      try { restore(); }
      catch { throw new Error("Codex plugin installation failed and rollback was incomplete."); }
      throw error;
    }
    return {
      installed: true,
      rollback: restore,
      commit() {
        rmSync(previous, { recursive: true, force: true });
        oldMoved = false;
      },
    };
  }
  return { installed: true, rollback() {}, commit() {} };
}

function protectConfiguredProjects(trigger) {
  if (!existsSync(runtime) || !has("dduo-solo-founder")) return;
  step(`Create verified backups for configured projects before ${trigger}`);
  const before = lstatIfExists(backupRegistry);
  if (!before) return;
  if (
    before.isSymbolicLink()
    || !before.isFile()
    || !ownedByCurrentUser(before)
  ) {
    throw new Error("The local dDuo backup registry is unsafe; refusing to upgrade project data.");
  }
  let configured;
  try {
    const bytes = readFileSync(backupRegistry);
    const after = lstatSync(backupRegistry);
    if (!sameFileIdentity(before, after)) {
      throw new Error("backup registry changed while it was read");
    }
    configured = JSON.parse(bytes.toString("utf8"));
    if (
      configured?.version !== 1
      || !configured.projects
      || typeof configured.projects !== "object"
      || Array.isArray(configured.projects)
      || Object.entries(configured.projects).some(([projectId, value]) => (
        !projectId.trim()
        || projectId.includes("\0")
        || !value
        || typeof value !== "object"
        || Array.isArray(value)
      ))
    ) {
      throw new Error("unsupported backup registry structure");
    }
  } catch {
    throw new Error("The local dDuo backup registry is unreadable; refusing to upgrade project data.");
  }
  const configuredIds = new Set(Object.keys(configured.projects));
  for (const { projectId, rootPath } of registeredProjects()) {
    if (!configuredIds.has(projectId)) continue;
    run(
      "dduo-solo-founder",
      ["backup", "create", "--trigger", trigger, "--project-root", rootPath],
      { optional: options.force },
    );
  }
}

function privateDirectory(path) {
  if (existsSync(path)) {
    const metadata = lstatSync(path);
    if (
      metadata.isSymbolicLink()
      || !metadata.isDirectory()
      || !ownedByCurrentUser(metadata)
    ) {
      throw new Error(`Unsafe private dDuo directory: ${path}`);
    }
  } else {
    mkdirSync(path, { recursive: true, mode: 0o700 });
  }
  chmodSync(path, 0o700);
}

function ownedByCurrentUser(metadata) {
  return process.platform === "win32"
    || typeof process.getuid !== "function"
    || metadata.uid === process.getuid();
}

function privateTemporaryPath(path, purpose) {
  return `${path}.${purpose}.${process.pid}.${randomBytes(12).toString("hex")}`;
}

function writeExclusiveFile(path, contents, mode) {
  let descriptor;
  try {
    descriptor = openSync(path, "wx", mode);
    writeFileSync(descriptor, contents);
    closeSync(descriptor);
    descriptor = undefined;
    chmodSync(path, mode);
  } finally {
    if (descriptor !== undefined) {
      try { closeSync(descriptor); } catch {}
    }
  }
}

function processIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    // EPERM means the process exists but cannot be signalled. Treat it as an
    // active owner instead of stealing a potentially live installation lock.
    return error?.code === "EPERM";
  }
}

function sameFileIdentity(left, right) {
  return left.dev === right.dev
    && left.ino === right.ino
    && left.size === right.size
    && left.mtimeMs === right.mtimeMs;
}

function acquireInstallerLock() {
  privateDirectory(configDir);
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const nonce = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    let descriptor;
    try {
      descriptor = openSync(installerLock, "wx", 0o600);
      writeFileSync(
        descriptor,
        `${JSON.stringify({ version: 1, pid: process.pid, nonce, started_at: new Date().toISOString() })}\n`,
      );
      closeSync(descriptor);
      chmodSync(installerLock, 0o600);
      return () => {
        if (!existsSync(installerLock)) return;
        let owner;
        try { owner = JSON.parse(readFileSync(installerLock, "utf8")); }
        catch { return; }
        if (owner?.pid === process.pid && owner?.nonce === nonce) {
          rmSync(installerLock, { force: true });
        }
      };
    } catch (error) {
      if (descriptor !== undefined) {
        try { closeSync(descriptor); } catch {}
      }
      if (error?.code !== "EEXIST") throw error;
      let metadata;
      try {
        metadata = lstatSync(installerLock);
      } catch (readError) {
        if (readError?.code === "ENOENT") continue;
        throw readError;
      }
      if (metadata.isSymbolicLink() || !metadata.isFile()) {
        throw new Error(`Unsafe dDuo installer lock: ${installerLock}`);
      }
      let owner;
      let ownerValid = false;
      try {
        owner = JSON.parse(readFileSync(installerLock, "utf8"));
        ownerValid = owner?.version === 1
          && Number.isInteger(owner?.pid)
          && owner.pid > 0
          && typeof owner?.nonce === "string"
          && owner.nonce.length >= 8
          && typeof owner?.started_at === "string"
          && Number.isFinite(Date.parse(owner.started_at));
      } catch {}
      if (!ownerValid) {
        const ageMs = Date.now() - metadata.mtimeMs;
        if (ageMs < 5_000) {
          throw new Error("Another dDuo installation is acquiring the installer lock.");
        }
        owner = null;
      }
      if (processIsAlive(Number(owner?.pid))) {
        throw new Error(`Another dDuo installation is running with process ${owner.pid}.`);
      }
      let current;
      try {
        current = lstatSync(installerLock);
      } catch (readError) {
        if (readError?.code === "ENOENT") continue;
        throw readError;
      }
      if (!sameFileIdentity(metadata, current)) continue;
      rmSync(installerLock, { force: true });
    }
  }
  throw new Error("Could not acquire the dDuo installer lock.");
}

function registeredProjects() {
  try {
    const before = lstatIfExists(projectRegistry);
    if (!before) return [];
    if (
      before.isSymbolicLink()
      || !before.isFile()
      || !ownedByCurrentUser(before)
    ) {
      throw new Error("unsafe project registry");
    }
    const registryBytes = readFileSync(projectRegistry);
    const after = lstatSync(projectRegistry);
    if (!sameFileIdentity(before, after)) {
      throw new Error("project registry changed while it was read");
    }
    const registry = JSON.parse(registryBytes.toString("utf8"));
    if (
      registry?.version !== 1
      || !registry.projects
      || typeof registry.projects !== "object"
      || Array.isArray(registry.projects)
    ) {
      throw new Error("unsupported registry structure");
    }
    return Object.entries(registry.projects).map(([projectId, value]) => {
      if (
        !projectId.trim()
        || projectId.includes("\0")
        || !value
        || typeof value !== "object"
        || Array.isArray(value)
        || typeof value.root_path !== "string"
        || !value.root_path.trim()
        || !isAbsolute(value.root_path)
      ) {
        throw new Error("invalid project registry entry");
      }
      return { projectId, rootPath: resolve(value.root_path) };
    });
  } catch {
    throw new Error("The local dDuo project registry is unreadable; refusing to upgrade project data.");
  }
}

function parseProjectSecrets(content) {
  const values = {};
  for (const rawLine of String(content).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const separator = line.indexOf("=");
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (projectSecretKeys.has(key) && value) values[key] = value;
  }
  return values;
}

function serializeProjectSecrets(values) {
  return Object.keys(values)
    .filter((key) => projectSecretKeys.has(key) && String(values[key]).trim())
    .sort()
    .map((key) => `${key}=${String(values[key]).trim()}\n`)
    .join("");
}

function projectSecretDirectory(projectId) {
  const original = String(projectId).trim();
  if (!original) throw new Error("Project id is required for secret migration.");
  let normalized = original.toLowerCase().replace(/^urn:uuid:/, "");
  if (normalized.startsWith("{") && normalized.endsWith("}")) {
    normalized = normalized.slice(1, -1);
  }
  const compact = normalized.replaceAll("-", "");
  const canonicalUuid = /^[0-9a-f]{32}$/.test(compact)
    ? `${compact.slice(0, 8)}-${compact.slice(8, 12)}-${compact.slice(12, 16)}-${compact.slice(16, 20)}-${compact.slice(20)}`
    : null;
  const slug = canonicalUuid
    || createHash("sha256").update(original).digest("hex");
  return join(configDir, "project-secrets", slug);
}

function requireSafeSecretDestination(path) {
  const secretRoot = join(configDir, "project-secrets");
  for (const directory of [secretRoot, dirname(path)]) {
    const metadata = lstatIfExists(directory);
    if (!metadata) continue;
    if (
      metadata.isSymbolicLink()
      || !metadata.isDirectory()
      || !ownedByCurrentUser(metadata)
    ) {
      throw new Error(`Unsafe project secret directory: ${directory}`);
    }
  }
  const metadata = lstatIfExists(path);
  if (!metadata) return;
  if (
    metadata.isSymbolicLink()
    || !metadata.isFile()
    || !ownedByCurrentUser(metadata)
  ) {
    throw new Error(`Unsafe project secret file: ${path}`);
  }
  chmodSync(path, 0o600);
}

function writePrivateFile(path, content) {
  privateDirectory(dirname(path));
  const temporary = privateTemporaryPath(path, "next");
  try {
    writeExclusiveFile(temporary, content, 0o600);
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function requireOwnedRegularFile(path, label) {
  const metadata = lstatSync(path);
  if (
    metadata.isSymbolicLink()
    || !metadata.isFile()
    || !ownedByCurrentUser(metadata)
  ) {
    throw new Error(`Unsafe ${label}: ${path}`);
  }
  return metadata;
}

function migrateLegacyProjectSecrets() {
  if (lstatIfExists(migratingLegacyEnvironment)) {
    requireOwnedRegularFile(migratingLegacyEnvironment, "Alpha migration file");
    if (!existsSync(legacyEnvironment) && !existsSync(retiredLegacyEnvironment)) {
      renameSync(migratingLegacyEnvironment, legacyEnvironment);
      chmodSync(legacyEnvironment, 0o600);
    } else {
      throw new Error(
        "An interrupted Alpha secret migration conflicts with another environment file; preserve both files and resolve them before upgrading.",
      );
    }
  }
  if (!lstatIfExists(legacyEnvironment)) {
    return { rollback() {}, commit() {} };
  }
  const projects = registeredProjects();
  if (!projects.length) {
    step("Keep the unclaimed Alpha environment file until a project is configured");
    return { rollback() {}, commit() {} };
  }
  if (lstatIfExists(retiredLegacyEnvironment)) {
    throw new Error(
      "Both active and retired Alpha environment files exist; resolve them before upgrading.",
    );
  }
  requireOwnedRegularFile(legacyEnvironment, "Alpha environment file");
  const legacySnapshot = fileSnapshot(legacyEnvironment);
  if (legacySnapshot.type !== "file") {
    throw new Error(`Unsafe Alpha environment file: ${legacyEnvironment}`);
  }
  const retiredSnapshot = fileSnapshot(retiredLegacyEnvironment);
  const markerSnapshot = fileSnapshot(secretMigrationMarker);
  const targets = projects.map(({ projectId }) => {
    const destination = join(projectSecretDirectory(projectId), "dduo.env");
    requireSafeSecretDestination(destination);
    return { projectId, destination, snapshot: fileSnapshot(destination) };
  });
  if (new Set(targets.map(({ destination }) => destination)).size !== targets.length) {
    throw new Error("The project registry contains colliding secret identities.");
  }
  let legacyMetadata;
  let legacyBytes;
  let legacy;
  try {
    renameSync(legacyEnvironment, migratingLegacyEnvironment);
    chmodSync(migratingLegacyEnvironment, 0o600);
    legacyMetadata = lstatSync(migratingLegacyEnvironment);
    legacyBytes = readFileSync(migratingLegacyEnvironment);
    legacy = parseProjectSecrets(legacyBytes.toString("utf8"));
    for (const { destination } of targets) {
      const existing = existsSync(destination)
        ? parseProjectSecrets(readFileSync(destination, "utf8"))
        : {};
      writePrivateFile(destination, serializeProjectSecrets({ ...legacy, ...existing }));
    }
    writePrivateFile(
      secretMigrationMarker,
      `${JSON.stringify({
        version: 1,
        migrated_at: new Date().toISOString(),
        projects: targets.map(({ projectId }) => projectId).sort(),
        source_sha256: createHash("sha256").update(legacyBytes).digest("hex"),
      }, null, 2)}\n`,
    );
    if (existsSync(legacyEnvironment)) {
      throw new Error(
        "The Alpha environment was recreated during migration; both copies were preserved for a safe retry.",
      );
    }
    const currentMetadata = lstatSync(migratingLegacyEnvironment);
    const currentBytes = readFileSync(migratingLegacyEnvironment);
    if (
      !sameFileIdentity(legacyMetadata, currentMetadata)
      || !currentBytes.equals(legacyBytes)
    ) {
      throw new Error("The Alpha environment changed during migration; retry the installation.");
    }
    renameSync(migratingLegacyEnvironment, retiredLegacyEnvironment);
    chmodSync(retiredLegacyEnvironment, 0o600);
    if (existsSync(legacyEnvironment)) {
      throw new Error(
        "The Alpha environment was recreated while retirement completed; both copies were preserved.",
      );
    }
  } catch (error) {
    for (const { destination, snapshot } of targets) restoreFile(destination, snapshot);
    restoreFile(secretMigrationMarker, markerSnapshot);
    const frozen = existsSync(migratingLegacyEnvironment)
      ? migratingLegacyEnvironment
      : existsSync(retiredLegacyEnvironment) && retiredSnapshot.type === "missing"
        ? retiredLegacyEnvironment
        : null;
    const concurrentAlphaWriter = Boolean(frozen && existsSync(legacyEnvironment));
    if (frozen && !existsSync(legacyEnvironment)) {
      renameSync(frozen, legacyEnvironment);
      chmodSync(legacyEnvironment, 0o600);
    }
    if (!existsSync(legacyEnvironment)) restoreFile(legacyEnvironment, legacySnapshot);
    if (!concurrentAlphaWriter) restoreFile(retiredLegacyEnvironment, retiredSnapshot);
    if (concurrentAlphaWriter) {
      throw new AggregateError(
        [error],
        "Alpha rewrote its environment during migration; both copies were preserved for a safe retry.",
      );
    }
    throw error;
  }
  return {
    rollback() {
      for (const { destination, snapshot } of targets) restoreFile(destination, snapshot);
      restoreFile(secretMigrationMarker, markerSnapshot);
      const frozen = existsSync(migratingLegacyEnvironment)
        ? migratingLegacyEnvironment
        : existsSync(retiredLegacyEnvironment) && retiredSnapshot.type === "missing"
          ? retiredLegacyEnvironment
          : null;
      const concurrentAlphaWriter = Boolean(frozen && existsSync(legacyEnvironment));
      if (frozen && !existsSync(legacyEnvironment)) {
        renameSync(frozen, legacyEnvironment);
        chmodSync(legacyEnvironment, 0o600);
      }
      if (!existsSync(legacyEnvironment)) restoreFile(legacyEnvironment, legacySnapshot);
      if (!concurrentAlphaWriter) restoreFile(retiredLegacyEnvironment, retiredSnapshot);
      if (concurrentAlphaWriter) {
        throw new Error(
          "Alpha rewrote its environment during rollback; both copies were preserved and require an explicit retry.",
        );
      }
    },
    commit() {
      if (existsSync(legacyEnvironment)) {
        throw new Error(
          "The Alpha environment was recreated before migration commit; both copies were preserved.",
        );
      }
      if (!existsSync(retiredLegacyEnvironment)) {
        throw new Error("The retired Alpha environment disappeared before migration commit.");
      }
      const currentMetadata = lstatSync(retiredLegacyEnvironment);
      const currentBytes = readFileSync(retiredLegacyEnvironment);
      if (
        currentMetadata.isSymbolicLink()
        || !currentMetadata.isFile()
        || !sameFileIdentity(legacyMetadata, currentMetadata)
        || !currentBytes.equals(legacyBytes)
      ) {
        throw new Error("The retired Alpha environment changed before migration commit.");
      }
    },
  };
}

function volumeName(projectId, suffix) {
  const project = createHash("sha256").update(projectId).digest("hex").slice(0, 12);
  return `dduo-solo-founder-${project}_${suffix}`;
}

function dockerVolumeExists(name) {
  try {
    execFileSync("docker", ["volume", "inspect", name], { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

function snapshotVolume(volume, output, archiveName) {
  return writeVolumeSnapshot(volume, output, archiveName, run);
}

function stopRegisteredProject(rootPath) {
  // A failed stop can leave a database writing. --force must not turn that
  // live volume into a supposedly consistent recovery snapshot.
  run("dduo-solo-founder", ["stop", "--project-root", rootPath]);
}

function restartUpgradedProjects(projectRoots) {
  const failures = [];
  for (const rootPath of projectRoots) {
    step(`Rebuild and restart upgraded project ${rootPath}`);
    try {
      run("dduo-solo-founder", ["stop", "--project-root", rootPath]);
      run("dduo-solo-founder", ["start", "--project-root", rootPath, "--build"]);
    } catch (error) {
      failures.push(error);
    }
  }
  if (failures.length) {
    throw new AggregateError(failures, "One or more projects could not be restarted after the upgrade snapshot.");
  }
}

function createUpgradeSnapshots() {
  if (!existsSync(runtime) || options.uninstall || options.dryRun) return [];
  const projects = registeredProjects().filter(({ rootPath }) => existsSync(rootPath));
  if (!projects.length) return [];

  privateDirectory(upgradeSnapshots);
  const snapshotId = new Date().toISOString().replace(/[:.]/g, "-");
  const destination = join(upgradeSnapshots, snapshotId);
  const temporary = `${destination}.${process.pid}.tmp`;
  privateDirectory(temporary);
  const captured = [];
  const restartRoots = [];
  try {
    for (const { projectId, rootPath } of projects) {
      const config = join(rootPath, ".dduo-solo-founder", "project.toml");
      const volumes = ["postgres-data", "qdrant-data"]
        .map((suffix) => ({ suffix, name: volumeName(projectId, suffix) }))
        .filter(({ name }) => dockerVolumeExists(name));
      if (!volumes.length) continue;

      // Raw PostgreSQL files are only copied after the stack has shut down.
      // That keeps this fallback snapshot consistent even before an encrypted
      // project backup has ever been configured.
      restartRoots.push(rootPath);
      stopRegisteredProject(rootPath);
      const projectDestination = join(temporary, projectId);
      privateDirectory(projectDestination);
      if (existsSync(config)) {
        const copy = join(projectDestination, "project.toml");
        writeFileSync(copy, readFileSync(config), { mode: 0o600 });
      }
      const archives = volumes.map(({ suffix, name }) => ({
        volume: name,
        suffix,
        ...snapshotVolume(name, projectDestination, `${suffix}.tar.gz`),
      }));
      const manifest = {
        format: "dduo-solo-founder-upgrade-snapshot",
        version: 1,
        created_at: new Date().toISOString(),
        project_id: projectId,
        root_path: rootPath,
        archives,
      };
      writeFileSync(
        join(projectDestination, "manifest.json"),
        `${JSON.stringify(manifest, null, 2)}\n`,
        { mode: 0o600 },
      );
      captured.push(projectId);
    }
    if (!captured.length) {
      rmSync(temporary, { recursive: true, force: true });
      return [];
    }
    writeFileSync(
      join(temporary, "manifest.json"),
      `${JSON.stringify({
        format: "dduo-solo-founder-upgrade-snapshot-set",
        version: 1,
        created_at: new Date().toISOString(),
        projects: captured,
      }, null, 2)}\n`,
      { mode: 0o600 },
    );
    renameSync(temporary, destination);
    step(`Verified local upgrade snapshot: ${destination}`);
    return restartRoots;
  } catch (error) {
    const recoveryErrors = [error];
    let restartFailed = false;
    let cleanupFailed = false;
    try {
      // The caller receives restartRoots only after every snapshot succeeds.
      // Recover here when a later project/archive fails so already-stopped
      // memories are never silently left offline.
      restartUpgradedProjects(restartRoots);
    } catch (restartError) {
      restartFailed = true;
      recoveryErrors.push(restartError);
    }
    try {
      rmSync(temporary, { recursive: true, force: true });
    } catch (cleanupError) {
      cleanupFailed = true;
      recoveryErrors.push(cleanupError);
    }
    if (recoveryErrors.length > 1) {
      throw new AggregateError(recoveryErrors,
        "Upgrade snapshot failed. "
        + (restartFailed ? "One or more stopped projects could not be restarted. " : "Stopped projects were restarted. ")
        + (cleanupFailed ? `Incomplete private snapshot retained at ${temporary}; cleanup failed. ` : "Temporary snapshot removed. ")
        + `Cause: ${String(error?.message || error).split("\n", 1)[0]}`,
      );
    }
    throw error;
  }
}

function projectIdFromConfig(projectRoot) {
  const config = join(projectRoot, ".dduo-solo-founder", "project.toml");
  if (!existsSync(config)) throw new Error("Project configuration is unavailable; refusing to restore a snapshot.");
  const match = readFileSync(config, "utf8").match(/^\s*id\s*=\s*"([^"]+)"\s*$/m);
  if (!match) throw new Error("Project configuration has no valid id; refusing to restore a snapshot.");
  return match[1];
}

function verifiedUpgradeSnapshot(snapshotPath, projectRoot) {
  const setManifestPath = join(snapshotPath, "manifest.json");
  if (!existsSync(setManifestPath)) throw new Error("Upgrade snapshot manifest is missing.");
  let setManifest;
  try {
    setManifest = JSON.parse(readFileSync(setManifestPath, "utf8"));
  } catch {
    throw new Error("Upgrade snapshot manifest is unreadable.");
  }
  if (
    setManifest?.format !== "dduo-solo-founder-upgrade-snapshot-set"
    || setManifest?.version !== 1
    || !Array.isArray(setManifest.projects)
  ) {
    throw new Error("Upgrade snapshot format is not supported.");
  }
  const projectId = projectIdFromConfig(projectRoot);
  if (!setManifest.projects.includes(projectId)) {
    throw new Error("This upgrade snapshot does not belong to the selected project.");
  }
  const projectPath = join(snapshotPath, projectId);
  const manifestPath = join(projectPath, "manifest.json");
  if (!existsSync(manifestPath)) throw new Error("Project upgrade snapshot manifest is missing.");
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch {
    throw new Error("Project upgrade snapshot manifest is unreadable.");
  }
  if (
    manifest?.format !== "dduo-solo-founder-upgrade-snapshot"
    || manifest?.version !== 1
    || manifest?.project_id !== projectId
    || !Array.isArray(manifest.archives)
  ) {
    throw new Error("Project upgrade snapshot is invalid.");
  }
  const allowed = new Map([
    ["postgres-data", volumeName(projectId, "postgres-data")],
    ["qdrant-data", volumeName(projectId, "qdrant-data")],
  ]);
  const archives = manifest.archives.map((entry) => {
    if (
      !entry
      || typeof entry.archive !== "string"
      || entry.archive !== entry.archive.split(/[\\/]/).pop()
      || typeof entry.sha256 !== "string"
      || allowed.get(entry.suffix) !== entry.volume
    ) {
      throw new Error("Project upgrade snapshot contains an unsafe volume entry.");
    }
    const archivePath = join(projectPath, entry.archive);
    if (!existsSync(archivePath)) throw new Error(`Upgrade snapshot archive is missing: ${entry.archive}`);
    const actual = createHash("sha256").update(readFileSync(archivePath)).digest("hex");
    if (actual !== entry.sha256) throw new Error(`Upgrade snapshot checksum failed: ${entry.archive}`);
    return { ...entry, archivePath };
  });
  if (!archives.some(({ suffix }) => suffix === "postgres-data")) {
    throw new Error("Upgrade snapshot has no PostgreSQL archive.");
  }
  return { projectId, projectPath, archives };
}

function removeStoppedSnapshotContainers(projectId) {
  const project = `dduo-solo-founder-${createHash("sha256").update(projectId).digest("hex").slice(0, 12)}`;
  const listed = run("docker", [
    "container", "ls", "--all", "--quiet", "--no-trunc",
    "--filter", `label=com.docker.compose.project=${project}`,
  ], { capture: true }).trim();
  if (!listed) return;
  const ids = [...new Set(listed.split(/\s+/))];
  if (ids.some((id) => !/^[a-f0-9]{64}$/.test(id))) {
    throw new Error("Docker returned an invalid project container identity; refusing to restore volumes.");
  }
  const containers = JSON.parse(run("docker", ["container", "inspect", ...ids], { capture: true }));
  if (
    !Array.isArray(containers)
    || containers.length !== ids.length
    || new Set(containers.map((container) => container?.Id)).size !== ids.length
    || containers.some((container) => (
      !ids.includes(container?.Id)
      || container?.Config?.Labels?.["com.docker.compose.project"] !== project
      || container?.State?.Running !== false
      || container?.State?.Paused !== false
    ))
  ) {
    throw new Error("Project containers are not verified and stopped; refusing to restore volumes.");
  }
  // `compose stop` leaves volume references behind. Remove only these validated
  // stopped containers, without --force or --volumes; any error aborts restore.
  run("docker", ["container", "rm", ...ids]);
}

function restoreUpgradeSnapshot(snapshotPath) {
  if (!options.yes) {
    log("Rollback replaces local memory volumes. Re-run with --yes after explicit founder confirmation.");
    process.exitCode = 3;
    return false;
  }
  if (!has("docker") || !has("dduo-solo-founder")) {
    throw new Error("Docker Desktop and the dDuo CLI are required to restore an upgrade snapshot.");
  }
  const projectRoot = resolve(options.projectRoot);
  const snapshot = verifiedUpgradeSnapshot(snapshotPath, projectRoot);
  log("Restoring verified local upgrade snapshot...");
  run("dduo-solo-founder", ["stop", "--project-root", projectRoot]);
  removeStoppedSnapshotContainers(snapshot.projectId);
  for (const archive of snapshot.archives) {
    if (dockerVolumeExists(archive.volume)) run("docker", ["volume", "rm", archive.volume]);
    run("docker", ["volume", "create", archive.volume]);
    run("docker", [
      "run",
      "--rm",
      "-v",
      `${archive.volume}:/destination`,
      "-v",
      `${snapshot.projectPath}:/snapshot:ro`,
      "postgres:16-alpine",
      "sh",
      "-c",
      `tar -tzf /snapshot/${archive.archive} >/dev/null && tar -xzf /snapshot/${archive.archive} -C /destination`,
    ]);
  }
  log("Snapshot restored. Reinstall the intended dDuo release, then open a new project chat.");
  return true;
}

function stopCliBridge() {
  if (!has("dduo-solo-founder")) return;
  step("Stop the authenticated CLI bridge before replacing its runtime");
  run("dduo-solo-founder", ["bridge-stop"], { optional: true });
}

function projectRootAvailable() {
  try {
    return statSync(options.projectRoot).isDirectory();
  } catch {
    return false;
  }
}

function openSetupForProject() {
  if (options.noSetup) return false;
  if (!projectRootAvailable()) {
    log("SETUP_OPEN_DEFERRED");
    return false;
  }
  // This opens the native, local setup page. Secrets and protected consent
  // remain there, never in the agent conversation or installer output.
  try {
    run("dduo-solo-founder", ["setup", "--project-root", options.projectRoot]);
  } catch (error) {
    if (!projectRootAvailable()) {
      log("SETUP_OPEN_DEFERRED");
      return false;
    }
    throw error;
  }
  return true;
}

function requireProjectRoot() {
  if (!projectRootAvailable()) {
    throw new Error(`Project folder is unavailable: ${options.projectRoot}`);
  }
}

function captureLegacyPluginState() {
  const codexCommand = selected("codex") ? resolveCodexExecutable() : null;
  return {
    codexCommand,
    codexInstalled: Boolean(codexCommand) && legacyCodexPluginInstalled(codexCommand),
    claudeInstalled: selected("claude") && has("claude") && legacyClaudePluginInstalled(),
  };
}

function deactivateLegacyPluginNames(snapshot) {
  if (snapshot.codexInstalled) {
    run(snapshot.codexCommand, ["plugin", "remove", "opendduo@personal"]);
  }
  if (snapshot.claudeInstalled) {
    run("claude", ["plugin", "uninstall", "opendduo@opendduo"]);
  }
  verifyLegacyRegistrationsRemoved(snapshot.codexCommand);
}

function restoreLegacyPluginNames(snapshot) {
  if (!snapshot) return;
  if (snapshot.codexInstalled && !legacyCodexPluginInstalled(snapshot.codexCommand)) {
    run(snapshot.codexCommand, ["plugin", "add", "opendduo@personal"]);
  }
  if (snapshot.claudeInstalled && !legacyClaudePluginInstalled()) {
    run("claude", ["plugin", "install", "opendduo@opendduo"]);
  }
  if (
    (snapshot.codexInstalled && !legacyCodexPluginInstalled(snapshot.codexCommand))
    || (snapshot.claudeInstalled && !legacyClaudePluginInstalled())
  ) {
    throw new Error("Legacy OpenDduo plugin rollback was incomplete.");
  }
}

function removeLegacyPluginNames() {
  step("Remove legacy OpenDduo client registrations; preserve project configuration and Docker volumes");
  const codexCommand = selected("codex") ? resolveCodexExecutable() : null;
  let restoreCodexConfig = null;
  try {
    if (codexCommand) {
      run(codexCommand, ["plugin", "remove", "opendduo@personal"], { optional: true });
    }
    if (selected("claude") && has("claude")) {
      run("claude", ["plugin", "uninstall", "opendduo@opendduo"], { optional: true });
      run("claude", ["plugin", "marketplace", "remove", "opendduo"], { optional: true });
    }
    if (options.only.size === 0 && has("opendduo")) {
      run("uv", ["tool", "uninstall", "opendduo"], { optional: true });
    }
    if (!options.dryRun && selected("codex")) {
      rmSync(legacyCodexPlugin, { recursive: true, force: true });
      rmSync(legacyCodexCache, { recursive: true, force: true });
      if (codexCommand) restoreCodexConfig = removeLegacyCodexConfigSections();
      if (existsSync(codexMarketplace)) {
        const marketplace = readMarketplace();
        marketplace.plugins = marketplace.plugins.filter((plugin) => plugin.name !== "opendduo");
        writeFileSync(codexMarketplace, `${JSON.stringify(marketplace, null, 2)}\n`);
      }
    }
    verifyLegacyPluginNamesRemoved(codexCommand);
  } catch (error) {
    if (restoreCodexConfig) restoreCodexConfig();
    throw error;
  }
}

function legacyCodexConfigSection(header) {
  return /^\[plugins\."opendduo@personal"(?:\.[^\]]+)?\]\s*(?:#.*)?$/.test(header)
    || /^\[hooks\.state\."opendduo@personal:[^"]+"\]\s*(?:#.*)?$/.test(header);
}

function escapedAt(line, index) {
  let slashes = 0;
  for (let cursor = index - 1; cursor >= 0 && line[cursor] === "\\"; cursor -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function nextTomlMultilineState(line, initialState) {
  let multiline = initialState;
  let quoted = null;
  for (let index = 0; index < line.length; index += 1) {
    if (multiline === "basic") {
      if (line.startsWith('"""', index) && !escapedAt(line, index)) {
        multiline = null;
        index += 2;
      }
      continue;
    }
    if (multiline === "literal") {
      if (line.startsWith("'''", index)) {
        multiline = null;
        index += 2;
      }
      continue;
    }
    const character = line[index];
    if (quoted === "basic") {
      if (character === '"' && !escapedAt(line, index)) quoted = null;
      continue;
    }
    if (quoted === "literal") {
      if (character === "'") quoted = null;
      continue;
    }
    if (character === "#") break;
    if (line.startsWith('"""', index)) {
      multiline = "basic";
      index += 2;
    } else if (line.startsWith("'''", index)) {
      multiline = "literal";
      index += 2;
    } else if (character === '"') quoted = "basic";
    else if (character === "'") quoted = "literal";
  }
  return multiline;
}

function withoutLegacyCodexConfigSections(original) {
  let multiline = null;
  let removing = false;
  const retained = [];
  for (const line of original.split("\n")) {
    const startedInsideMultiline = Boolean(multiline);
    if (!startedInsideMultiline && /^\s*\[\[?.*\]\]?\s*(?:#.*)?$/.test(line)) {
      removing = legacyCodexConfigSection(line.trim());
    }
    if (!removing || (!startedInsideMultiline && /^\s*(?:#.*)?$/.test(line))) {
      retained.push(line);
    }
    multiline = nextTomlMultilineState(line, multiline);
  }
  return retained.join("\n");
}

function writeCodexConfig(target, content, mode) {
  const temporary = `${target}.dduo.tmp`;
  try {
    writeFileSync(temporary, content, { mode });
    chmodSync(temporary, mode);
    renameSync(temporary, target);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function removeLegacyCodexConfigSections() {
  if (!existsSync(codexConfig)) return null;
  const target = realpathSync(codexConfig);
  const original = readFileSync(target);
  const updated = Buffer.from(withoutLegacyCodexConfigSections(original.toString("utf8")));
  if (updated.equals(original)) return null;
  const mode = statSync(target).mode & 0o777;
  writeCodexConfig(target, updated, mode);
  return () => {
    writeCodexConfig(target, original, mode);
    if (!readFileSync(target).equals(original)) {
      throw new Error("Legacy OpenDduo cleanup could not restore the Codex configuration.");
    }
  };
}

function legacyCodexConfigSectionsRemain() {
  if (!existsSync(codexConfig)) return false;
  let multiline = null;
  for (const line of readFileSync(codexConfig, "utf8").split("\n")) {
    const startedInsideMultiline = Boolean(multiline);
    if (!startedInsideMultiline && legacyCodexConfigSection(line.trim())) return true;
    multiline = nextTomlMultilineState(line, multiline);
  }
  return false;
}

function parseClientJson(command, args, subject) {
  const output = run(command, args, { capture: true });
  try {
    return JSON.parse(output);
  } catch {
    throw new Error(`Could not verify ${subject}: ${command} did not return valid JSON.`);
  }
}

function legacyCodexPluginInstalled(codexCommand) {
  const result = parseClientJson(codexCommand, ["plugin", "list", "--json"], "Codex plugins");
  if (!Array.isArray(result?.installed)) {
    throw new Error("Could not verify Codex plugins: the installed plugin list is unavailable.");
  }
  return result.installed.some((plugin) => plugin?.pluginId === "opendduo@personal");
}

function codexPluginRegistered(codexCommand) {
  const result = parseClientJson(codexCommand, ["plugin", "list", "--json"], "Codex plugins");
  if (!Array.isArray(result?.installed)) {
    throw new Error("Could not verify Codex plugins: the installed plugin list is unavailable.");
  }
  return result.installed.some((plugin) => (
    plugin?.name === "dduo-solo-founder"
    && String(plugin?.pluginId || "").startsWith("dduo-solo-founder@")
    && plugin?.installed !== false
  ));
}

function legacyClaudePluginInstalled() {
  const plugins = parseClientJson("claude", ["plugin", "list", "--json"], "Claude plugins");
  if (!Array.isArray(plugins)) {
    throw new Error("Could not verify Claude plugins: the installed plugin list is unavailable.");
  }
  return plugins.some((plugin) => plugin?.id === "opendduo@opendduo");
}

function verifyLegacyRegistrationsRemoved(codexCommand) {
  if (codexCommand && legacyCodexPluginInstalled(codexCommand)) {
    throw new Error("Legacy OpenDduo Codex plugin is still installed.");
  }
  if (selected("claude") && has("claude") && legacyClaudePluginInstalled()) {
    throw new Error("Legacy OpenDduo Claude plugin is still installed.");
  }
}

function verifyLegacyPluginNamesRemoved(codexCommand) {
  verifyLegacyRegistrationsRemoved(codexCommand);
  if (selected("codex")) {
    if (existsSync(legacyCodexPlugin)) {
      throw new Error("Legacy OpenDduo Codex plugin files are still present.");
    }
    if (existsSync(legacyCodexCache)) {
      throw new Error("Legacy OpenDduo Codex plugin cache is still present.");
    }
    if (legacyCodexConfigSectionsRemain()) {
      throw new Error("Legacy OpenDduo Codex configuration is still present.");
    }
    if (
      existsSync(codexMarketplace)
      && readMarketplace().plugins.some((plugin) => plugin.name === "opendduo")
    ) {
      throw new Error("Legacy OpenDduo Codex marketplace entry is still present.");
    }
  }
  if (selected("claude") && has("claude")) {
    const marketplaces = parseClientJson(
      "claude",
      ["plugin", "marketplace", "list", "--json"],
      "Claude marketplaces",
    );
    if (!Array.isArray(marketplaces)) {
      throw new Error("Could not verify Claude marketplaces: the marketplace list is unavailable.");
    }
    if (marketplaces.some((marketplace) => marketplace?.name === "opendduo")) {
      throw new Error("Legacy OpenDduo Claude marketplace is still configured.");
    }
  }
  if (options.only.size === 0 && has("opendduo")) {
    throw new Error("Legacy OpenDduo CLI is still installed.");
  }
}

function claudePluginInstalled() {
  if (!has("claude")) return false;
  const plugins = parseClientJson("claude", ["plugin", "list", "--json"], "Claude plugins");
  if (!Array.isArray(plugins)) {
    throw new Error("Could not verify Claude plugins: the installed plugin list is unavailable.");
  }
  return plugins.some(
    (plugin) => plugin?.id === "dduo-solo-founder@dduo-solo-founder",
  );
}

function expectedClaudePluginVersion() {
  try {
    const manifest = JSON.parse(
      readFileSync(join(claudeAdapterSource, ".claude-plugin", "plugin.json"), "utf8"),
    );
    return typeof manifest?.version === "string" && manifest.version ? manifest.version : null;
  } catch {
    return null;
  }
}

function claudePluginHealthy() {
  if (!has("claude")) return false;
  const plugins = parseClientJson("claude", ["plugin", "list", "--json"], "Claude plugins");
  if (!Array.isArray(plugins)) {
    throw new Error("Could not verify Claude plugins: the installed plugin list is unavailable.");
  }
  const plugin = plugins.find(
    (candidate) => candidate?.id === "dduo-solo-founder@dduo-solo-founder",
  );
  const expectedVersion = expectedClaudePluginVersion();
  if (!plugin || plugin.enabled === false || !expectedVersion || plugin.version !== expectedVersion) {
    return false;
  }
  return plugin.errors === undefined || (Array.isArray(plugin.errors) && plugin.errors.length === 0);
}

function verificationTargets() {
  if (options.only.size) return [...options.only].filter((target) => target !== "core");
  const targets = [];
  if (claudePluginInstalled()) targets.push("claude");
  if (existsSync(codexPlugin)) targets.push("codex");
  return targets;
}

function clientLabel(target) {
  return target === "claude" ? "Claude" : "Codex";
}

function codexReloadInstruction() {
  if (options.only.has("codex") && options.surface === "vscode") {
    return "Reload the VS Code window, then open a new graphical Codex conversation.";
  }
  if (options.only.has("codex") && options.surface === "cli") {
    return "Start a new Codex CLI session.";
  }
  return "Fully quit and reopen Codex.";
}

function codexHookApprovalInstruction() {
  const verifyAgain = "Run --verify again for this project after approving.";
  if (options.surface === "vscode") {
    return `${codexReloadInstruction()} Native hook approval and graphical lifecycle acceptance are still required. `
      + "Use only the native approval UI offered by the Codex extension. If no approval UI is available, stop support validation; "
      + `a Desktop or terminal check does not establish graphical acceptance. ${verifyAgain}`;
  }
  if (options.surface === "cli") {
    return `${codexReloadInstruction()} Open /hooks if this Codex CLI offers it, then review and approve dDuo's hooks in that native UI. `
      + `If native hook review is unavailable, stop support validation. ${verifyAgain}`;
  }
  return `${codexReloadInstruction()} Then open Codex Settings > Hooks, select dDuo Solo Founder, `
    + "choose Review and Trust all, and run --verify again for this project after approving.";
}

function installationReloadInstruction({ codexInstalled, claudeInstalled, setupOpened }) {
  const setup = setupOpened ? "Setup opened." : "Setup was not opened automatically.";
  // Preserve the concise, proven Desktop/legacy instructions when no surface
  // was selected. A named surface below gets only directions applicable to it.
  if (options.surface === "unknown") {
    if (codexInstalled && claudeInstalled) {
      return setupOpened
        ? "Setup opened. Fully quit and reopen Codex; for Claude, open a new session in the same project folder."
        : "Setup was not opened automatically. Fully quit and reopen Codex; for Claude, open a new session and then open Setup in the same project folder.";
    }
    if (codexInstalled) {
      return setupOpened
        ? "Setup opened. Fully quit and reopen Codex, then open a new chat in the same project folder."
        : "Setup was not opened automatically. Fully quit and reopen Codex, then open Setup from a new chat in the same project folder.";
    }
    return setupOpened
      ? "Setup opened. Close this session and open a new session in the same project folder."
      : "Setup was not opened automatically. Open it from the next session in the same project folder.";
  }
  const instructions = [];
  if (codexInstalled) instructions.push(codexReloadInstruction());
  if (claudeInstalled) {
    instructions.push(
      options.only.has("claude") && options.surface === "vscode"
        ? "Reload the VS Code window and open a new graphical Claude Code conversation."
        : "Open a new Claude Code session in the same project folder.",
    );
  }
  if (!instructions.length) instructions.push("Open a new session in the same project folder.");
  return `${setup} ${instructions.join(" ")}`;
}

function verify(targets = verificationTargets(), { allowPendingCodexAuthorization = false } = {}) {
  if (options.surface === "vscode" && targets.length) {
    log("SCOPE  Installation checks only; graphical lifecycle acceptance remains unverified in VS Code.");
  }
  const checks = [
    ["runtime", existsSync(runtime)],
    ["runtime pointer", existsSync(runtimePointer)],
    ["hook runtime", hookRuntimeReady()],
    ["dDuo Solo Founder CLI", has("dduo-solo-founder")],
    ["MCP server", has("dduo-solo-founder-mcp-dispatch")],
    ["local dDuo agent", has("dduo-solo-founder-agent")],
  ];
  if (targets.includes("claude")) {
    checks.push(["Claude native plugin package", claudePluginPackageReady()]);
    checks.push(["Claude plugin", claudePluginHealthy()]);
  }
  let codexHooks = null;
  if (targets.includes("codex")) {
    codexHooks = codexHookReadiness();
    checks.push(["Codex native plugin package", codexPluginPackageReady()]);
    checks.push(["Codex lifecycle hooks feature", codexHooksEnabled()]);
    checks.push(["Codex lifecycle hook discovery (3/3)", codexHooks.discovered]);
    if (codexHooks.discovered && !allowPendingCodexAuthorization) {
      checks.push(["Codex lifecycle hook authorization", codexHooks.authorized]);
    }
    if (codexHooks.authorizationRequired) {
      const action = codexHooks.reason === "reauthorization_required" ? "reauthorization" : "authorization";
      warn(
        `Codex lifecycle hooks require ${action}; automatic memory is not ready. `
        + codexHookApprovalInstruction(),
      );
    }
  }
  const failures = checks.filter(([, ok]) => !ok);
  if (failures.length) {
    for (const [name] of failures) log(`FAIL  ${name}`);
    process.exitCode = 1;
    return false;
  }
  const clients = targets.length ? targets.map(clientLabel).join(" + ") : "core";
  if (codexHooks?.authorizationRequired) {
    log(`INSTALLED  components verified (${clients}); Codex lifecycle hook authorization is pending.`);
    return true;
  }
  log(`PASS  installation verified (${clients})`);
  return true;
}

function planUninstall() {
  const plan = [];
  const records = readClientRecord(clientExecutableRecord);
  for (const family of ["codex", "claude"]) {
    if (!selected(family)) continue;
    const command = family === "codex" ? resolveCodexExecutable() : selectedClientDescriptor(family)?.launcher;
    const source = family === "codex" ? codexPlugin : claudePlugin;
    if (!command) {
      if (records.clients?.[family] || existsSync(source)) {
        throw new Error(`The managed ${clientLabel(family)} executable is unavailable; repair it before uninstalling dDuo.`);
      }
      continue;
    }
    plan.push({
      family,
      command,
      registered: family === "codex" ? codexPluginRegistered(command) : claudePluginInstalled(),
      marketplace: family === "claude" && claudeMarketplaceInstalled(),
    });
  }
  return plan;
}

function uninstall(plan) {
  // Restore the exact Claude status line that dDuo wrapped before removing the
  // runtime that owns the adapter command. A missing adapter remains a no-op.
  if (selected("claude")) restoreClaudeStatuslineBeforeUninstall();
  for (const entry of plan) {
    if (entry.registered) {
      if (entry.family === "codex") {
        run(entry.command, ["plugin", "remove", "dduo-solo-founder"]);
        if (codexPluginRegistered(entry.command)) throw new Error("Codex plugin removal was not confirmed.");
      } else {
        run("claude", ["plugin", "uninstall", "dduo-solo-founder@dduo-solo-founder"]);
        if (claudePluginInstalled()) throw new Error("Claude plugin removal was not confirmed.");
      }
    }
    if (entry.marketplace) {
      run("claude", ["plugin", "marketplace", "remove", "dduo-solo-founder"]);
      if (claudeMarketplaceInstalled()) throw new Error("Claude marketplace removal was not confirmed.");
    }
  }
  const executables = readClientRecord(clientExecutableRecord);
  const scopes = readClientRecord(clientScopeRecord, { kind: "scopes" });
  const keepRuntime = ["codex", "claude"].some((family) => !selected(family)
    && (executables.clients?.[family] || scopes.clients?.[family]
      || existsSync(family === "codex" ? codexPlugin : claudePlugin)));
  let launcherBin = "";
  if (!keepRuntime && has("uv")) {
    launcherBin = run("uv", ["tool", "dir", "--bin"], { capture: true, optional: true }).trim();
    run("uv", ["tool", "uninstall", "dduo-solo-founder"], { optional: true });
  }
  step("Remove dDuo Solo Founder runtime and plugin files; preserve every project data volume");
  if (!options.dryRun) {
    if (!keepRuntime) {
      removeRetiredRuntimeLaunchers();
      removeRetiredUsageGuardState();
      rmSync(runtime, { recursive: true, force: true });
      rmSync(runtimePointer, { force: true });
      rmSync(hookRuntimePointer, { force: true });
    }
    if (selected("codex")) rmSync(codexPlugin, { recursive: true, force: true });
    if (selected("claude")) rmSync(claudePlugin, { recursive: true, force: true });
    if (launcherBin) {
      for (const name of runtimeExecutables) {
        rmSync(join(launcherBin, stableLauncherFilename(name)), { force: true });
        if (process.platform === "win32") {
          rmSync(join(launcherBin, runtimeExecutableFilename(name)), { force: true });
        }
      }
    }
    if (selected("codex") && existsSync(codexMarketplace)) {
      const marketplace = readMarketplace();
      marketplace.plugins = marketplace.plugins.filter((plugin) => plugin.name !== "dduo-solo-founder");
      writeFileSync(codexMarketplace, `${JSON.stringify(marketplace, null, 2)}\n`);
    }
    // Registration removal succeeded above; now retire dDuo's own descriptor
    // records.  Vendor CLIs and their credentials are deliberately retained.
    for (const [path, record] of [[clientExecutableRecord, executables], [clientScopeRecord, scopes]]) {
      if (!record.clients) continue;
      if (selected("codex")) delete record.clients.codex;
      if (selected("claude")) delete record.clients.claude;
      writePrivateJson(path, record);
    }
    if (!keepRuntime) removeRetiredUpdaterArtifacts();
  }
}

function removeRetiredUpdaterArtifacts() {
  step("Remove retired Alpha updater launchers and machine-local cache state");
  let launcherBin = "";
  if (has("uv")) {
    launcherBin = run("uv", ["tool", "dir", "--bin"], {
      capture: true,
      optional: true,
    }).trim();
  }
  if (launcherBin) {
    for (const name of retiredUpdaterExecutables) {
      rmSync(join(launcherBin, stableLauncherFilename(name)), { force: true });
      rmSync(join(launcherBin, `.dduo-legacy-${name}`), { force: true });
      if (process.platform === "win32") {
        rmSync(join(launcherBin, runtimeExecutableFilename(name)), { force: true });
      }
    }
  }
  for (const path of retiredUpdaterState) {
    rmSync(path, { recursive: true, force: true });
  }
}

function removeRetiredRuntimeLaunchers() {
  step("Remove retired dDuo runtime launchers");
  let launcherBin = "";
  if (has("uv")) {
    launcherBin = run("uv", ["tool", "dir", "--bin"], {
      capture: true,
      optional: true,
    }).trim();
  }
  if (!launcherBin) return;
  for (const name of retiredRuntimeExecutables) {
    const target = join(launcherBin, stableLauncherFilename(name));
    const metadata = lstatIfExists(target);
    if (!metadata) continue;
    const owned = process.platform === "win32"
      ? metadata.isFile()
        && !metadata.isSymbolicLink()
        && readFileSync(target, "utf8") === runtimeShim(name, target, null)
      : isStableLauncher(target);
    if (!owned) {
      throw new Error(`Refusing to remove an unowned retired runtime launcher: ${target}`);
    }
    rmSync(target, { force: true });
  }
}

function removeRetiredUsageGuardState() {
  step("Remove the retired host-local usage reserve policy");
  const directory = dirname(retiredUsageGuardState);
  const directoryMetadata = lstatIfExists(directory);
  if (!directoryMetadata) return;
  if (directoryMetadata.isSymbolicLink() || !directoryMetadata.isDirectory()) {
    throw new Error(`Unsafe retired usage reserve directory: ${directory}`);
  }
  const policyMetadata = lstatIfExists(retiredUsageGuardState);
  if (!policyMetadata) return;
  if (policyMetadata.isSymbolicLink() || !policyMetadata.isFile()) {
    throw new Error(`Unsafe retired usage reserve policy: ${retiredUsageGuardState}`);
  }
  rmSync(retiredUsageGuardState, { force: true });
}

function selected(target) {
  return options.only.size === 0 || options.only.has(target);
}

function recordRepairFamily() {
  // Replacing unverifiable metadata requires an explicit full selection. A
  // normal update never silently discards a saved launcher or scope.
  return options.force && options.only.size === 1 && options.clientExecutable && options.clientConfigDir
    ? [...options.only][0] : null;
}

function writePrivateJson(path, value) {
  privateDirectory(dirname(path));
  const candidate = `${path}.${process.pid}.next`;
  try {
    writeFileSync(candidate, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    chmodSync(candidate, 0o600);
    renameSync(candidate, path);
    chmodSync(path, 0o600);
  } finally {
    rmSync(candidate, { force: true });
  }
}

function clientRecordSnapshot() {
  return {
    executables: fileSnapshot(clientExecutableRecord),
    scopes: fileSnapshot(clientScopeRecord),
  };
}

function restoreClientRecord(snapshot) {
  restoreFile(clientExecutableRecord, snapshot.executables);
  restoreFile(clientScopeRecord, snapshot.scopes);
}

function persistClientRecord(target) {
  if (target === "core" || options.dryRun) return;
  const family = target;
  const descriptor = selectedClientDescriptor(family);
  if (!descriptor) throw new Error(`No verified ${clientLabel(family)} descriptor to save.`);
  const existingScopes = readClientRecord(clientScopeRecord, { kind: "scopes", repairFamily: recordRepairFamily() });
  existingScopes.version = 1;
  existingScopes.clients ||= {};
  const currentScope = existingScopes.clients[family]?.config_dir;
  const configDir = family === "codex"
    ? dirname(codexConfig)
    : dirname(claudeUserSettings);
  if (currentScope && currentScope !== configDir) {
    throw new Error(
      `dDuo already manages another ${clientLabel(family)} profile on this host; remove it explicitly before selecting a second profile.`,
    );
  }
  existingScopes.clients[family] = { config_dir: configDir };
  const executables = readClientRecord(clientExecutableRecord, { repairFamily: recordRepairFamily() });
  executables.version = 1;
  executables.clients ||= {};
  const entry = { launcher_path: descriptor.launcher };
  if (descriptor.nodeExecutable) entry.node_executable = descriptor.nodeExecutable;
  executables.clients[family] = entry;
  writePrivateJson(clientExecutableRecord, executables);
  writePrivateJson(clientScopeRecord, existingScopes);
}

function selectedConfigDirectory(family) {
  return family === "codex" ? dirname(codexConfig) : dirname(claudeUserSettings);
}

function requireAvailableClientProfile(family) {
  if (!selected(family)) return;
  const records = readClientRecord(clientScopeRecord, { kind: "scopes", repairFamily: recordRepairFamily() });
  const existing = records?.clients?.[family]?.config_dir;
  const selectedScope = selectedConfigDirectory(family);
  if (existing && existing !== selectedScope) {
    throw new Error(
      `dDuo already manages another ${clientLabel(family)} profile on this host; remove it explicitly before selecting a second profile.`,
    );
  }
}

function requireSelectedClients() {
  if (options.only.has("claude") && !has("claude")) {
    throw new Error("Claude Code CLI not found.");
  }
  const codexDiscovered = selected("codex") && codexCandidates().length > 0;
  if (options.only.has("codex") && !codexDiscovered) {
    throw new Error("Codex CLI not found.");
  }
  if (selected("codex") && codexDiscovered) {
    resolveCodexExecutable({ required: true });
  }
}

function runInstaller() {
if (Number(process.versions.node.split(".")[0]) < 18) throw new Error("Node.js 18 or newer is required.");
if (options.dryRun) {
  verifySource();
  const targets = options.only.size ? verificationTargets() : ["claude", "codex"].filter(has);
  log(targets.length
    ? `Preview: install the local runtime and ${targets.map(clientLabel).join(" + ")} plugin.`
    : "Preview: install the core runtime without client adapters.");
  log("Existing project data stays untouched. Docker and this project are not started.");
}
else if (options.uninstall) {
  const uninstallPlan = planUninstall();
  protectConfiguredProjects("uninstall");
  stopCliBridge();
  uninstall(uninstallPlan);
  log("dDuo Solo Founder uninstalled. Project data was preserved.");
}
else if (options.verify) {
  const targets = verificationTargets();
  verify(targets);
}
else if (options.rollbackSnapshot) {
  restoreUpgradeSnapshot(options.rollbackSnapshot);
}
else {
  log("Installing dDuo Solo Founder...");
  if (!options.yes && !options.dryRun) {
    log("Review README and SECURITY.md, then run again with --yes.");
    process.exitCode = 3;
    return;
  }
  verifySource();
  requireSelectedClients();
  requireAvailableClientProfile("codex");
  requireAvailableClientProfile("claude");
  requireProjectRoot();
  const clientRecordsBefore = clientRecordSnapshot();
  const codexBefore = captureCodexState();
  const claudeBefore = captureClaudeState();
  protectConfiguredProjects("update");
  let projectsToRestart = [];
  let runtimeTransaction = null;
  let coreTransaction = null;
  let secretTransaction = null;
  let claudeTransaction = null;
  let codexTransaction = null;
  let legacyBefore = null;
  let legacyDeactivationStarted = false;
  const installedTargets = [];
  stopCliBridge();
  try {
    projectsToRestart = createUpgradeSnapshots();
    legacyBefore = captureLegacyPluginState();
    legacyDeactivationStarted = true;
    deactivateLegacyPluginNames(legacyBefore);
    runtimeTransaction = copyRuntime();
    coreTransaction = installCore();
    // Keep the Alpha secret-migration window as short as possible: the new
    // runtime is ready before the legacy file is frozen, but projects have not
    // yet restarted and therefore cannot observe a half-migrated state.
    secretTransaction = migrateLegacyProjectSecrets();
    restartUpgradedProjects(projectsToRestart);
    if (selected("claude")) {
      claudeTransaction = installClaude();
      if (claudeTransaction?.installed) installedTargets.push("claude");
    }
    if (selected("codex")) {
      codexTransaction = installCodex(codexBefore);
      if (codexTransaction?.installed) installedTargets.push("codex");
    }
    // A first install cannot receive native hook consent until Codex reloads
    // the new adapter. Commit valid files while keeping that pending action
    // explicit; standalone --verify requires authorization as well.
    if (!verify(installedTargets, { allowPendingCodexAuthorization: true })) {
      throw new Error("Installed dDuo components did not pass verification.");
    }
    for (const target of installedTargets) persistClientRecord(target);
    // This is intentionally the last fallible migration check inside the
    // coordinated rollback boundary. A late Alpha writer therefore fails the
    // upgrade while both the old and frozen copies are still recoverable.
    secretTransaction?.commit();
  } catch (error) {
    const rollbackErrors = [];
    for (const rollback of [
      codexTransaction?.rollback,
      claudeTransaction?.rollback,
      coreTransaction?.rollback,
      runtimeTransaction?.rollback,
      () => restoreClaudeState(claudeBefore),
      () => restoreClientRecord(clientRecordsBefore),
      secretTransaction?.rollback,
      legacyDeactivationStarted ? () => restoreLegacyPluginNames(legacyBefore) : null,
      projectsToRestart.length ? () => restartUpgradedProjects(projectsToRestart) : null,
    ]) {
      if (!rollback) continue;
      try { rollback(); } catch (rollbackError) { rollbackErrors.push(rollbackError); }
    }
    if (rollbackErrors.length) {
      throw new Error("Installation failed and coordinated rollback was incomplete; repair the client installation before continuing.");
    }
    throw error;
  }
  // Finalizers and migrations are deliberately best-effort after the verified
  // commit boundary. Failure must remain visible without turning a usable,
  // non-rollbackable install into a false failure or blocking later cleanup.
  for (const [subject, cleanup] of [
    ["core installation finalization", coreTransaction?.commit],
    ["Claude installation finalization", claudeTransaction?.commit],
    ["Codex installation finalization", codexTransaction?.commit],
    ["runtime installation finalization", runtimeTransaction?.commit],
    ["retired runtime launcher cleanup", removeRetiredRuntimeLaunchers],
    ["retired usage reserve cleanup", removeRetiredUsageGuardState],
    ["retired Alpha updater cleanup", removeRetiredUpdaterArtifacts],
    ["legacy OpenDduo cleanup", removeLegacyPluginNames],
  ]) {
    if (!cleanup) continue;
    try {
      cleanup();
    } catch (error) {
      const detail = String(error?.message || error).split("\n", 1)[0];
      warn(
        `Installed components verified, but ${subject} is incomplete. `
        + `Re-run the installer to retry cleanup: ${detail}`,
      );
    }
  }
  const codexInstalled = installedTargets.includes("codex");
  const claudeInstalled = installedTargets.includes("claude");
  if (codexInstalled) log("CODEX_APP_RESTART_REQUIRED");
  if (claudeInstalled) log("SESSION_RELOAD_REQUIRED");
  const setupOpened = openSetupForProject();
  if (options.noSetup) {
    log("Runtime installed. Browser Setup was not requested. Run dduo-solo-founder --help for project configuration commands.");
    return;
  }
  log(installationReloadInstruction({ codexInstalled, claudeInstalled, setupOpened }));
}
}

const installerLockRequired = !options.dryRun && !options.verify;
const releaseInstallerLock = installerLockRequired ? acquireInstallerLock() : () => {};
try {
  runInstaller();
} finally {
  releaseInstallerLock();
}
