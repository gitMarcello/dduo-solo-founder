import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import {
  fstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, unlinkSync,
  writeFileSync, writeSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { writeVolumeSnapshot } from "../bin/upgrade-snapshot.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "dduo-upgrade-snapshot-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, "source qualità 日本語");
  const output = join(root, "private output & space");
  mkdirSync(source);
  mkdirSync(output, { mode: 0o700 });
  writeFileSync(join(source, "artificial-data.bin"), randomBytes(3 * 1024 * 1024));
  return { source, output, archive: join(output, "postgres-data.tar.gz") };
}

function actualTarRunner(source, calls) {
  return (command, args, options) => {
    assert.equal(command, "docker");
    assert.ok(args.includes("--rm"));
    assert.ok(args.includes("--network"));
    assert.equal(args[args.indexOf("--network") + 1], "none");
    assert.ok(!args.some((arg) => arg.includes(":/snapshot")));
    calls.push({ command, args, options });
    if (args.includes("-czf")) {
      assert.equal(args[args.indexOf("-v") + 1], "artificial-volume:/source:ro");
      assert.equal(options.stdio[0], "ignore");
      assert.equal(typeof options.stdio[1], "number");
      if (process.platform !== "win32") {
        assert.equal(fstatSync(options.stdio[1]).mode & 0o777, 0o600);
        assert.equal(fstatSync(options.stdio[1]).uid, process.getuid());
      }
      return execFileSync("tar", ["-C", source, "-czf", "-", "."], options);
    }
    assert.ok(args.includes("-i"));
    assert.ok(args.includes("-tzf"));
    assert.equal(typeof options.stdio[0], "number");
    assert.equal(options.stdio[1], "ignore");
    return execFileSync("tar", ["-tzf", "-"], options);
  };
}

test("snapshot streams a real archive through host-owned private descriptors and hashes all bytes", (t) => {
  const { source, output, archive } = fixture(t);
  const calls = [];
  const receipt = writeVolumeSnapshot("artificial-volume", output, "postgres-data.tar.gz",
    actualTarRunner(source, calls));
  assert.equal(calls.length, 2);
  const bytes = readFileSync(archive);
  assert.ok(bytes.length > 2 * 1024 * 1024);
  assert.deepEqual(receipt, {
    archive: "postgres-data.tar.gz",
    sha256: createHash("sha256").update(bytes).digest("hex"),
  });
  assert.match(execFileSync("tar", ["-tzf", archive], { encoding: "utf8" }), /artificial-data.bin/);
  for (const descriptor of [calls[0].options.stdio[1], calls[1].options.stdio[0]]) {
    assert.throws(() => fstatSync(descriptor), { code: "EBADF" });
  }
  if (process.platform !== "win32") {
    assert.equal(statSync(archive).mode & 0o777, 0o600);
    assert.equal(statSync(archive).uid, process.getuid());
  }
});

test("snapshot refuses existing files and invalid volume/archive identities before Docker", (t) => {
  const { output, archive } = fixture(t);
  const unexpected = () => assert.fail("Docker must not run");
  writeFileSync(archive, "already owned by the user");
  assert.throws(() => writeVolumeSnapshot("artificial-volume", output, "postgres-data.tar.gz", unexpected),
    { code: "EEXIST" });
  assert.equal(readFileSync(archive, "utf8"), "already owned by the user");
  for (const [volume, name] of [
    ["../other-volume", "new.tar.gz"],
    ["artificial-volume:/other", "new.tar.gz"],
    ["artificial-volume", "../outside.tar.gz"],
    ["artificial-volume", "archive\n.tar.gz"],
  ]) {
    assert.throws(() => writeVolumeSnapshot(volume, output, name, unexpected), /Invalid/);
  }
});

test("creation failure and an empty archive never return a successful receipt and close the output", (t) => {
  const { output } = fixture(t);
  for (const mode of ["error", "empty"]) {
    let outputFd;
    let calls = 0;
    const failure = new Error("Docker tar failed");
    assert.throws(() => writeVolumeSnapshot("artificial-volume", output, mode + ".tar.gz",
      (_command, _args, options) => {
        calls += 1;
        outputFd = options.stdio[1];
        if (mode === "error") {
          writeSync(outputFd, "partial archive");
          throw failure;
        }
      }), mode === "error" ? failure : /empty/);
    assert.equal(calls, 1);
    assert.throws(() => fstatSync(outputFd), { code: "EBADF" });
  }
});

test("truncated gzip fails the actual verifier without returning a receipt", (t) => {
  const { source, output } = fixture(t);
  const calls = [];
  const realTar = actualTarRunner(source, calls);
  let inputFd;
  assert.throws(() => writeVolumeSnapshot("artificial-volume", output, "truncated.tar.gz",
    (command, args, options) => {
      if (args.includes("-czf")) {
        writeSync(options.stdio[1], Buffer.from([0x1f, 0x8b, 0x08, 0x00]));
        return;
      }
      inputFd = options.stdio[0];
      return realTar(command, args, options);
    }));
  assert.throws(() => fstatSync(inputFd), { code: "EBADF" });
});

for (const mutation of ["replace-before", "replace-during", "delete-during", "append-during"]) {
  test("snapshot detects concurrent archive " + mutation + " and refuses a receipt", (t) => {
    const { source, output, archive } = fixture(t);
    const realTar = actualTarRunner(source, []);
    assert.throws(() => writeVolumeSnapshot("artificial-volume", output, "postgres-data.tar.gz",
      (command, args, options) => {
        realTar(command, args, options);
        const before = args.includes("-czf");
        if (mutation === "replace-before" && before
            || mutation === "replace-during" && !before) {
          const payload = readFileSync(archive);
          unlinkSync(archive);
          writeFileSync(archive, payload, { mode: 0o600 });
        }
        if (mutation === "delete-during" && !before) unlinkSync(archive);
        if (mutation === "append-during" && !before) writeFileSync(archive, "changed", { flag: "a" });
      }), /changed|replaced|ENOENT/);
  });
}
