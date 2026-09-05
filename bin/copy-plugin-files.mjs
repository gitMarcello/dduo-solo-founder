import { chmodSync, copyFileSync, lstatSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

// Release content contains regular files and directories, never links/devices.
// Avoid both native cpSync fast paths (directory copy AND file overwrite): Node
// 22 mishandles Unicode Windows paths there (nodejs/node#61878).
export function copyPluginFiles(source, destination, { recursive = false } = {}) {
  const distance = relative(resolve(source), resolve(destination));
  if (!distance || (!isAbsolute(distance) && distance !== ".." && !distance.startsWith(`..${sep}`))) {
    throw new Error("Plugin copy destination must be outside its source.");
  }
  copyEntry(source, destination, recursive);
}

function copyEntry(source, destination, recursive) {
  const metadata = lstatSync(source);
  const existing = lstatSync(destination, { throwIfNoEntry: false });
  if (metadata.isDirectory()) {
    if (!recursive || (existing && !existing.isDirectory())) {
      throw new Error("Plugin directory copy requires a directory destination and recursive mode.");
    }
    mkdirSync(destination, { recursive: true });
    for (const entry of readdirSync(source)) {
      copyEntry(join(source, entry), join(destination, entry), true);
    }
  } else if (metadata.isFile()) {
    if (existing && !existing.isFile()) {
      throw new Error("Plugin file copy requires a regular file destination.");
    }
    mkdirSync(dirname(destination), { recursive: true });
    copyFileSync(source, destination);
  } else {
    throw new Error("Plugin copies accept only regular files and directories.");
  }
  chmodSync(destination, metadata.mode);
}
