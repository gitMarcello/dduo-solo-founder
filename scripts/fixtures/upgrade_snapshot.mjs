// Linux-only, disposable Docker smoke. No project registry or installer import.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync, existsSync, ftruncateSync, mkdirSync, readFileSync,
  rmSync, rmdirSync, statSync, writeFileSync, writeSync,
} from "node:fs";
import { join } from "node:path";
import { writeVolumeSnapshot } from "/test/upgrade-snapshot.mjs";

const [prefix, source, output, postgresImage] = process.argv.slice(2);
assert.match(prefix, /^dduo-snapshot-smoke-[0-9a-f]{12}$/);
assert.equal(source, `${prefix}-source`);
assert.ok(output.endsWith(`/${prefix}-output/_data`));
assert.equal(process.getuid(), 1000);
assert.equal(statSync(output).uid, 1000);
let counter = 0;
const calls = [];

// Only routing changes: pin the existing image and name/label every temporary
// container. The production helper still uses real inherited OS descriptors,
// Docker, gzip/tar and real Linux filesystem ownership.
function run(command, args, options = {}) {
  assert.equal(command, "docker");
  assert.equal(args[0], "run");
  const mapped = args.map((arg) => arg === "postgres:16-alpine" ? postgresImage : arg);
  mapped.splice(1, 0, "--name", `${prefix}-helper-${++counter}`,
    "--label", `dduo.snapshot-smoke=${prefix}`);
  calls.push(mapped);
  return execFileSync(command, mapped, { timeout: 60_000, stdio: "pipe", ...options });
}

// Verbatim baseline: a76907f:bin/install.mjs, snapshotVolume(). The report
// includes its source hash, so later commits do not change this reproduction.
function snapshotVolume(volume, output, archiveName) {
  run("docker", [
    "run",
    "--rm",
    "-v",
    `${volume}:/source:ro`,
    "-v",
    `${output}:/snapshot`,
    "postgres:16-alpine",
    "sh",
    "-c",
    `tar -C /source -czf /snapshot/${archiveName} . && tar -tzf /snapshot/${archiveName} >/dev/null`,
  ]);
  const archive = join(output, archiveName);
  if (!existsSync(archive) || readFileSync(archive).length === 0) {
    throw new Error(`Upgrade snapshot for ${volume} could not be verified.`);
  }
  chmodSync(archive, 0o600);
  return {
    archive: archiveName,
    sha256: createHash("sha256").update(readFileSync(archive)).digest("hex"),
  };
}

const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const archiveStat = (name) => {
  const value = statSync(join(output, name));
  return { uid: value.uid, mode: (value.mode & 0o777).toString(8), size: value.size };
};
const readSource = () => JSON.parse(run("docker", [
  "run", "--rm", "--network", "none", "-v", `${source}:/source:ro`,
  postgresImage, "sh", "-c",
  "printf '{\"uid\":%s,\"mode\":\"%s\",\"version\":\"%s\",\"binary_sha256\":\"%s\"}' "
    + '"$(stat -c %u /source)" "$(stat -c %a /source)" "$(cat /source/PG_VERSION)" '
    + '"$(sha256sum /source/private/binary | cut -d\" \" -f1)"',
], { encoding: "utf8" }));

const before = readSource();
assert.equal(before.uid, 999);
assert.equal(before.mode, "700");
assert.throws(() => run("docker", [
  "run", "--rm", "--network", "none", "--user", "1000:1000",
  "-v", `${source}:/source:ro`, postgresImage, "cat", "/source/PG_VERSION",
]), /Command failed/);
assert.throws(() => run("docker", [
  "run", "--rm", "--network", "none", "-v", `${source}:/source:ro`,
  postgresImage, "touch", "/source/forbidden-write",
]), /Command failed/);

let oldError;
try { snapshotVolume(source, output, "old.tar.gz"); } catch (error) { oldError = error; }
assert.equal(oldError?.code, "EPERM");
const oldArchive = archiveStat("old.tar.gz");
assert.equal(oldArchive.uid, 0);

const fixedCallsStart = calls.length;
const result = writeVolumeSnapshot(source, output, "fixed.tar.gz", run);
const fixedArchive = archiveStat("fixed.tar.gz");
assert.equal(fixedArchive.uid, 1000);
assert.equal(fixedArchive.mode, "600");
assert.ok(fixedArchive.size > 1024 * 1024);
assert.equal(result.sha256, sha256(readFileSync(join(output, result.archive))));
const fixedCalls = calls.slice(fixedCallsStart);
assert.equal(fixedCalls.length, 2);
assert.ok(fixedCalls[0].includes(`${source}:/source:ro`));
assert.ok(fixedCalls.every((args) => !args.some((arg) => arg.includes(":/snapshot"))));

const extract = (path) => run("docker", [
  "run", "--rm", "-i", "--network", "none", postgresImage,
  "tar", "-xzOf", "-", path,
], { input: readFileSync(join(output, result.archive)), maxBuffer: 8 * 1024 * 1024 });
assert.equal(extract("./PG_VERSION").toString(), "16\n");
assert.equal(extract("./private/unicode.txt").toString(), "Caffè — memoria artificiale 🚀\n");
assert.equal(sha256(extract("./private/binary")), before.binary_sha256);

const previousCount = calls.length;
assert.throws(() => writeVolumeSnapshot(source, output, "fixed.tar.gz", run), { code: "EEXIST" });
assert.equal(calls.length, previousCount);
assert.equal(sha256(readFileSync(join(output, result.archive))), result.sha256);

const failures = [];
for (const fault of ["truncated", "corrupt", "generation_failure"]) {
  let verificationAttempted = false;
  const faultyRun = (command, args, options) => {
    if (args.includes("-tzf")) verificationAttempted = true;
    if (fault === "generation_failure" && args.includes("-czf")) {
      const commandStart = args.indexOf("postgres:16-alpine") + 1;
      const actualArgs = [
        ...args.slice(0, commandStart), "sh", "-c",
        "tar -C /source -czf - .; exit 17",
      ];
      return run(command, actualArgs, options);
    }
    const returned = run(command, args, options);
    if (args.includes("-czf")) {
      const fd = options.stdio[1];
      assert.equal(typeof fd, "number");
      if (fault === "truncated") ftruncateSync(fd, 64);
      if (fault === "corrupt") writeSync(fd, Buffer.from("not-gzip"), 0, 8, 0);
    }
    return returned;
  };
  assert.throws(() => writeVolumeSnapshot(source, output, `${fault}.tar.gz`, faultyRun));
  assert.equal(verificationAttempted, fault !== "generation_failure");
  assert.equal(archiveStat(`${fault}.tar.gz`).uid, 1000);
  assert.equal(archiveStat(`${fault}.tar.gz`).mode, "600");
  // This is exactly the installer's ability to clean its owned staging files,
  // not a simulated root chmod or an elevation to remove failed snapshots.
  rmSync(join(output, `${fault}.tar.gz`));
  assert.equal(existsSync(join(output, `${fault}.tar.gz`)), false);
  failures.push({ fault, rejected: true, cleanup_by_uid_1000: true });
}

const readOnlyOutput = join(output, "read-only");
mkdirSync(readOnlyOutput, { mode: 0o500 });
const callsBeforeDenied = calls.length;
assert.throws(() => writeVolumeSnapshot(source, readOnlyOutput, "denied.tar.gz", run), { code: "EACCES" });
assert.equal(calls.length, callsBeforeDenied);
chmodSync(readOnlyOutput, 0o700);
rmdirSync(readOnlyOutput);
assert.deepEqual(readSource(), before);

const report = {
  installer_uid: process.getuid(),
  kernel: execFileSync("uname", ["-s"], { encoding: "utf8" }).trim(),
  node: process.version,
  docker_cli: execFileSync("docker", ["--version"], { encoding: "utf8" }).trim(),
  baseline: {
    revision: "a76907f",
    function_sha256: sha256(snapshotVolume.toString()),
    error: oldError.code,
    archive: oldArchive,
  },
  fixed: { archive: fixedArchive, sha256: result.sha256, actual_module: true },
  source: { ...before, unprivileged_read_denied: true, read_only_verified: true, unchanged: true },
  unicode_and_large_binary_round_trip: true,
  overwrite_prevented_before_docker: true,
  output_permission_failure_before_docker: true,
  failures,
  scope: "Actual snapshot helper, not full installer, database recovery or VPS deployment",
};
writeFileSync(join(output, "report.json"), JSON.stringify(report), { mode: 0o600 });
process.stdout.write(`${JSON.stringify(report)}\n`);
