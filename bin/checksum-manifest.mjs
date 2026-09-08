/**
 * Parse a SHA-256 manifest written with POSIX or Windows line endings.
 *
 * @param {string} contents
 * @returns {Array<{ expected: string, relative: string }>}
 */
export function parseChecksumManifest(contents) {
  const entries = [];
  const exactPaths = new Set();
  const portablePaths = new Set();
  for (const line of contents.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const match = line.match(/^([a-f0-9]{64})  (.+)$/);
    if (!match) throw new Error(`Invalid checksum line: ${line}`);
    const [, expected, relative] = match;
    const parts = relative.split("/");
    if (
      relative.startsWith("/")
      || relative.includes("\\")
      || relative.includes(":")
      || /[\u0000-\u001f\u007f]/.test(relative)
      || parts.some((part) => (
        !part
        || part === "."
        || part === ".."
        || /[. ]$/.test(part)
        || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part)
      ))
    ) {
      throw new Error(`Unsafe checksum path: ${relative}`);
    }
    const portable = relative.normalize("NFC").toLocaleLowerCase("en-US");
    if (exactPaths.has(relative) || portablePaths.has(portable)) {
      throw new Error(`Duplicate checksum path: ${relative}`);
    }
    exactPaths.add(relative);
    portablePaths.add(portable);
    entries.push({ expected, relative });
  }
  return entries;
}
