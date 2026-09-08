import { statSync } from "node:fs";
import { delimiter, dirname, join, normalize } from "node:path";

// cmd.exe parses batch launchers before the executable parses its arguments.
// Quote both layers explicitly; never enable shell:true with raw arguments.
// Escaping follows the documented cmd/CRT rules used by node-cross-spawn:
// https://github.com/moxystudio/node-cross-spawn/blob/master/lib/util/escape.js
const metacharacters = /([()\][%!^"`<>&|;, *?])/g;
const escapeMeta = (value) => value.replace(metacharacters, "^$1");

export function commandInvocation(command, args) {
  if (process.platform !== "win32") return { command, args, options: {} };
  if (/[\r\n\0]/.test(command)) throw new Error("Batch command paths cannot contain line breaks or NUL.");
  const directories = dirname(command) === "."
    ? [".", ...(process.env.PATH || "").split(delimiter)] : [""];
  const extensions = ["", ...(process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";")];
  // Resolve in JS to preserve Unicode paths: where.exe stdout depends on the
  // current Windows console code page even when Node requests UTF-8 decoding.
  const found = directories.flatMap((directory) => extensions.map((extension) => (
    join(directory.replace(/^"|"$/g, ""), command + extension)
  ))).find((candidate) => {
    try { return statSync(candidate).isFile(); } catch { return false; }
  });
  if (!found || !/\.(?:cmd|bat)$/i.test(found)) {
    return { command: found || command, args, options: {} };
  }
  const doubleEscape = /node_modules[\\/]\.bin[\\/][^\\/]+\.cmd$/i.test(found);
  const quoted = args.map((argument) => {
    const value = String(argument);
    if (/[\r\n\0]/.test(value)) throw new Error("Batch command arguments cannot contain line breaks or NUL.");
    let escaped = value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/, "$1$1");
    escaped = escapeMeta(`"${escaped}"`);
    return doubleEscape ? escapeMeta(escaped) : escaped;
  });
  return {
    command: process.env.ComSpec || process.env.COMSPEC || "cmd.exe",
    args: ["/d", "/s", "/c", `"${[escapeMeta(normalize(found)), ...quoted].join(" ")}"`],
    options: { windowsVerbatimArguments: true, windowsHide: true },
  };
}
