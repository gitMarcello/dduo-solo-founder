// Supported release lines with the CVE-2026-111497 fix. Do not infer safety
// from process.versions.brotli: Node backported the fix without changing it.
const minimumVersions = new Map([[22, [23, 4]], [24, [22, 1]], [26, [12, 1]]]);
export const NODE_REQUIREMENT = "Node.js 22.23.4+, 24.22.1+ or 26.12.1+ (within those release lines)";

export function assertSupportedNodeVersion(version = process.versions.node) {
  const match = typeof version === "string" && /^v?(\d+)\.(\d+)\.(\d+)$/.exec(version);
  const [major, minor, patch] = match ? match.slice(1).map(Number) : [];
  const minimum = minimumVersions.get(major);
  if (!minimum || minor < minimum[0] || (minor === minimum[0] && patch < minimum[1])) {
    throw new Error(`${NODE_REQUIREMENT} is required for CVE-2026-111497. Update Node.js and rerun the installer with that executable.`);
  }
}

export function assertInstallerNodeVersion(version, options) {
  // Keep removal and explicit recovery available, including on an old host.
  // Match the installer's action precedence; dry-run and verify still check.
  const recovery = !options.dryRun && (options.uninstall || (!options.verify && options.rollbackSnapshot));
  if (!recovery) assertSupportedNodeVersion(version);
}

export function pinNodeCommands(value, {
  executable = process.execPath, version = process.versions.node, platform = process.platform,
} = {}) {
  assertSupportedNodeVersion(version);
  const quoted = platform === "win32" ? `"${executable}"` : `'${executable.replace(/'/g, `'\\''`)}'`;
  const pin = (entry) => {
    if (Array.isArray(entry)) return entry.map(pin);
    if (!entry || typeof entry !== "object") return entry;
    return Object.fromEntries(Object.entries(entry).map(([key, item]) => {
      if (key === "command" && item === "node") return [key, executable];
      if (key === "command" && typeof item === "string" && item.startsWith("node ")) {
        return [key, `${quoted}${item.slice(4)}`];
      }
      return [key, pin(item)];
    }));
  };
  return pin(value);
}
