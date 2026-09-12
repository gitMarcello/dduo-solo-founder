import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  closeSync, constants, fchmodSync, fstatSync, lstatSync, openSync, readSync,
} from "node:fs";
import { join } from "node:path";

// Docker may need root to read PostgreSQL's UID-owned 0700 directories.
// Only archive bytes cross stdout: Docker never creates files on the host.
export function writeVolumeSnapshot(volume, output, archiveName, run = execFileSync) {
  if (typeof volume !== "string" || typeof archiveName !== "string"
      || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(volume)
      || !/^[A-Za-z0-9][A-Za-z0-9_.-]*\.tar\.gz$/.test(archiveName)) {
    throw new Error("Invalid upgrade snapshot volume or archive name.");
  }
  const archive = join(output, archiveName);
  const outputFd = openSync(archive, "wx", 0o600);
  let written;
  try {
    fchmodSync(outputFd, 0o600);
    run("docker", [
      "run", "--rm", "--network", "none",
      "-v", `${volume}:/source:ro`,
      "postgres:16-alpine", "tar", "-C", "/source", "-czf", "-", ".",
    ], { stdio: ["ignore", outputFd, "pipe"] });
    written = fstatSync(outputFd);
    if (!written.isFile() || written.size === 0) {
      throw new Error(`Upgrade snapshot for ${volume} is empty.`);
    }
  } finally {
    closeSync(outputFd);
  }

  // Open a fresh read-only descriptor at offset zero for Docker's stdin.
  // Host tar is not required (including on native Windows).
  const inputFd = openSync(archive, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = fstatSync(inputFd);
    if (!opened.isFile() || opened.dev !== written.dev || opened.ino !== written.ino
        || opened.size !== written.size || opened.mtimeMs !== written.mtimeMs) {
      throw new Error(`Upgrade snapshot for ${volume} changed before verification.`);
    }
    run("docker", [
      "run", "--rm", "-i", "--network", "none",
      "postgres:16-alpine", "tar", "-tzf", "-",
    ], { stdio: [inputFd, "ignore", "pipe"] });

    // Docker advanced the shared descriptor offset. Explicit positions keep
    // hashing independent, with bounded RAM even for multi-gigabyte volumes.
    const digest = createHash("sha256");
    const buffer = Buffer.alloc(1024 * 1024);
    let position = 0;
    let count;
    while ((count = readSync(inputFd, buffer, 0, buffer.length, position)) > 0) {
      digest.update(buffer.subarray(0, count));
      position += count;
    }
    const verified = fstatSync(inputFd);
    if (position !== written.size || verified.size !== written.size
        || verified.mtimeMs !== written.mtimeMs) {
      throw new Error(`Upgrade snapshot for ${volume} changed during verification.`);
    }
    const finalPath = lstatSync(archive);
    if (!finalPath.isFile() || finalPath.dev !== verified.dev || finalPath.ino !== verified.ino
        || finalPath.size !== verified.size || finalPath.mtimeMs !== verified.mtimeMs) {
      throw new Error(`Upgrade snapshot for ${volume} was replaced during verification.`);
    }
    return { archive: archiveName, sha256: digest.digest("hex") };
  } finally {
    closeSync(inputFd);
  }
}
