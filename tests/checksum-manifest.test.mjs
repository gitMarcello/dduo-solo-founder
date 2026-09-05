import assert from "node:assert/strict";
import test from "node:test";

import { parseChecksumManifest } from "../bin/checksum-manifest.mjs";

const digest = "a".repeat(64);

test("parses checksum manifests with LF and CRLF line endings", () => {
  const expected = [{ expected: digest, relative: ".claude-plugin/marketplace.json" }];
  assert.deepEqual(
    parseChecksumManifest(`${digest}  .claude-plugin/marketplace.json\n`),
    expected,
  );
  assert.deepEqual(
    parseChecksumManifest(`${digest}  .claude-plugin/marketplace.json\r\n`),
    expected,
  );
});

test("rejects malformed checksum entries", () => {
  assert.throws(() => parseChecksumManifest("not-a-checksum  file.txt\r\n"), {
    message: "Invalid checksum line: not-a-checksum  file.txt",
  });
});

test("rejects unsafe, duplicate, and non-portable paths", () => {
  assert.throws(() => parseChecksumManifest(`${digest}  ../escape\n`), {
    message: "Unsafe checksum path: ../escape",
  });
  assert.throws(() => parseChecksumManifest(`${digest}  C:\\escape\n`), {
    message: "Unsafe checksum path: C:\\escape",
  });
  assert.throws(
    () => parseChecksumManifest(`${digest}  Hooks/file\n${digest}  hooks/file\n`),
    { message: "Duplicate checksum path: hooks/file" },
  );
  assert.throws(() => parseChecksumManifest(`${digest}  docs/CON.txt\n`), {
    message: "Unsafe checksum path: docs/CON.txt",
  });
  assert.throws(() => parseChecksumManifest(`${digest}  docs/file.\n`), {
    message: "Unsafe checksum path: docs/file.",
  });
});
