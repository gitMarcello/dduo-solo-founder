/**
 * Tiny bootstrap counterpart of backend/client_installation.py.
 *
 * It deliberately resolves only paths and scope.  Native registration remains
 * in install.mjs, which owns its transaction and rollback.
 */
import { accessSync, closeSync, constants, lstatSync, openSync, readFileSync, readSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, extname, isAbsolute, join, relative, resolve } from "node:path";

export const CLIENT_FAMILIES = new Set(["codex", "claude"]);
export const CLIENT_SURFACES = new Set(["cli", "vscode", "desktop", "unknown"]);

function absolutePath(value, label, { executable = false } = {}) {
  if (typeof value !== "string" || !value || /[\r\n\0]/.test(value) || !isAbsolute(value)) {
    throw new Error(`${label} must be one absolute path without line breaks.`);
  }
  const path = resolve(value);
  if (executable) {
    try {
      if (!statSync(path).isFile()) throw new Error("not a file");
      accessSync(path, process.platform === "win32" ? constants.F_OK : constants.X_OK);
    }
    catch { throw new Error(`${label} is not an executable file.`); }
  }
  return path;
}

export function defaultClientConfigDir(family, environment = process.env) {
  if (!CLIENT_FAMILIES.has(family)) throw new Error("client must be codex or claude.");
  const key = family === "codex" ? "CODEX_HOME" : "CLAUDE_CONFIG_DIR";
  const fallback = join(homedir(), family === "codex" ? ".codex" : ".claude");
  return environment[key] ? absolutePath(environment[key], key) : fallback;
}

export function readClientRecord(path, { kind = "executables", repairFamily = null } = {}) {
  let value;
  try {
    let metadata;
    try { metadata = lstatSync(path); }
    catch (error) { if (error.code === "ENOENT") return {}; throw error; }
    if (metadata.isSymbolicLink() || !metadata.isFile()) throw new Error("unsafe record");
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error(`Invalid dDuo client record: ${path}`);
  }
  const object = (entry) => entry && typeof entry === "object" && !Array.isArray(entry);
  if (!object(value) || value.version !== 1 || !object(value.clients)) {
    throw new Error(`Invalid dDuo client record: ${path}`);
  }
  const clients = { ...(value.clients || {}) };
  for (const [family, entry] of Object.entries(clients)) {
    try {
      if (!CLIENT_FAMILIES.has(family) || !object(entry)) throw new Error("invalid client");
      if (kind === "scopes") absolutePath(entry.config_dir, "saved client config directory");
      else {
        absolutePath(entry.launcher_path, "saved client launcher");
        if (entry.node_executable !== undefined) absolutePath(entry.node_executable, "saved Node executable");
      }
    } catch {
      if (family === repairFamily) delete clients[family];
      else throw new Error(`Invalid dDuo client record: ${path}`);
    }
  }
  return { ...value, clients };
}

function npmEntrypoint(launcher, family) {
  const packageName = family === "codex" ? "@openai/codex" : "@anthropic-ai/claude-code";
  const locations = [join(dirname(launcher), "node_modules", packageName)];
  if (dirname(launcher).endsWith(`${process.platform === "win32" ? "\\" : "/"}.bin`)) {
    locations.push(join(dirname(dirname(launcher)), packageName));
  }
  let parent = dirname(realpathSync(launcher));
  while (dirname(parent) !== parent) {
    locations.push(parent);
    parent = dirname(parent);
  }
  for (const root of locations) {
    try {
      const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
      if (manifest?.name !== packageName) continue;
      const bin = typeof manifest.bin === "string" ? manifest.bin : manifest.bin?.[family];
      if (typeof bin !== "string" || !bin) continue;
      const packageRoot = realpathSync(root);
      const entrypoint = realpathSync(resolve(packageRoot, bin));
      const within = relative(packageRoot, entrypoint);
      if (within === ".." || within.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`)
          || isAbsolute(within) || !statSync(entrypoint).isFile()) continue;
      return entrypoint;
    } catch { /* Another supported npm layout may match. */ }
  }
  return null;
}

export function resolveClientCommand(family, launcher, { nodeExecutable = null, requireRecordedNode = false } = {}) {
  if (!CLIENT_FAMILIES.has(family)) throw new Error("client must be codex or claude.");
  launcher = absolutePath(launcher, "client executable", { executable: true });
  const entrypoint = npmEntrypoint(launcher, family);
  const extension = extname(launcher).toLowerCase();
  const batch = [".cmd", ".bat"].includes(extension);
  let header = "";
  let handle;
  try {
    handle = openSync(launcher, "r");
    const bytes = Buffer.alloc(256);
    header = bytes.subarray(0, readSync(handle, bytes, 0, bytes.length, 0)).toString("utf8");
  }
  catch { /* Native files need no Node dependency. */ }
  finally { if (handle !== undefined) closeSync(handle); }
  const needsNode = batch || [".js", ".mjs", ".cjs"].includes(extension)
    || /^#![^\r\n]*\bnode(?:\s|$)/.test(header)
    || Boolean(entrypoint && /^#![^\r\n]*\b(?:sh|bash|zsh|dash|ksh)(?:\s|$)/.test(header));
  if (!needsNode) return { launcher, nodeExecutable: null, entrypoint: null };
  if (!entrypoint) {
    throw new Error(`${family} launcher has no verified official npm entry point; repair its installation.`);
  }
  if (!nodeExecutable && requireRecordedNode) {
    throw new Error(`Saved dDuo ${family} launcher requires its recorded Node executable; run setup repair.`);
  }
  const node = absolutePath(nodeExecutable || process.execPath, "Node executable", { executable: true });
  if (/\.(?:cmd|bat|js|mjs|cjs)$/i.test(node)) throw new Error("Node executable must be a native executable.");
  return { launcher, nodeExecutable: node, entrypoint };
}

export function clientCommandInvocation(descriptor, args) {
  return descriptor.nodeExecutable
    ? { command: descriptor.nodeExecutable, args: [descriptor.entrypoint, ...args], options: {} }
    : { command: descriptor.launcher, args, options: {} };
}

/**
 * Read one already-managed launcher without ever falling back to PATH.
 * A record is deliberately fail-closed: picking a different binary can also
 * mean picking a different account/configuration than the one dDuo verified.
 */
export function recordedClientCommand(recordPath, family, options = {}) {
  if (!CLIENT_FAMILIES.has(family)) throw new Error("client must be codex or claude.");
  const clients = readClientRecord(recordPath, options).clients;
  const entry = clients && typeof clients === "object" && !Array.isArray(clients)
    ? clients[family] : null;
  if (!entry) return null;
  if (typeof entry !== "object" || Array.isArray(entry)) {
    throw new Error(`Saved dDuo ${family} launcher is invalid; run setup repair.`);
  }
  let launcher;
  try {
    launcher = absolutePath(entry.launcher_path, `saved dDuo ${family} launcher`, { executable: true });
  } catch (error) {
    throw new Error(`Saved dDuo ${family} launcher is invalid; run setup repair.`, { cause: error });
  }
  let nodeExecutable = null;
  if (entry.node_executable !== undefined) {
    try {
      nodeExecutable = absolutePath(entry.node_executable, "saved Node executable", { executable: true });
    } catch (error) {
      throw new Error("Saved Node executable is invalid; run setup repair.", { cause: error });
    }
  }
  return resolveClientCommand(family, launcher, { nodeExecutable, requireRecordedNode: true });
}

export function validateClientSelection({ family, surface = "unknown", configDir, executable, probeExecutable } = {}) {
  if (!CLIENT_FAMILIES.has(family)) throw new Error("client must be codex or claude.");
  if (!CLIENT_SURFACES.has(surface)) throw new Error("surface must be cli, vscode, desktop, or unknown.");
  if (family === "claude" && surface === "desktop") throw new Error("desktop is supported only for Codex.");
  return {
    family,
    surface,
    configDir: configDir ? absolutePath(configDir, "client config directory") : null,
    executable: executable ? absolutePath(executable, "client executable", { executable: true }) : null,
    probeExecutable: probeExecutable
      ? absolutePath(probeExecutable, "client probe executable", { executable: true })
      : null,
  };
}
