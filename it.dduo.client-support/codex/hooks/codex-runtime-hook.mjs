#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { constants, accessSync, lstatSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

const event = process.argv[2];
const eventNames = { "session-start": "SessionStart", prompt: "UserPromptSubmit" };
const fallback = eventNames[event] ? {
  hookSpecificOutput: {
    hookEventName: eventNames[event],
    additionalContext: "dDuo memory is temporarily unavailable, so this turn may not be recorded. Recommend checking the connection and guiding setup repair now; the user may explicitly choose to continue temporarily without memory. Do not claim this turn was saved.",
  },
} : { continue: true };

function dispatcher() {
  const pointer = join(homedir(), ".config", "dduo-solo-founder", "hook-runtime-bin");
  try {
    const info = lstatSync(pointer);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 4096) return null;
    if (process.platform !== "win32" && (info.mode & 0o077)) return null;
    const bin = readFileSync(pointer, "utf8").trim();
    if (!bin || !isAbsolute(bin) || /[\r\n\0]/.test(bin)) return null;
    const name = `dduo-solo-founder-hook-dispatch${process.platform === "win32" ? ".exe" : ""}`;
    const executable = join(bin, name);
    if (!lstatSync(executable).isFile()) return null;
    accessSync(executable, process.platform === "win32" ? constants.F_OK : constants.X_OK);
    return executable;
  } catch {
    return null;
  }
}

let output = fallback;
if (["session-start", "prompt", "stop"].includes(event)) {
  const command = dispatcher();
  if (command) {
    const child = spawnSync(command, [event], {
      // Pass the client's stdin directly, without interpreting or logging it.
      stdio: ["inherit", "pipe", "pipe"], encoding: "utf8",
      // Lifecycle JSON is UTF-8, including on Windows pipes with an ANSI locale.
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
      timeout: event === "session-start" ? 195_000 : event === "prompt" ? 55_000 : 15_000,
      maxBuffer: 4 * 1024 * 1024, windowsHide: true,
    });
    try {
      const candidate = JSON.parse(child.stdout || "");
      const silentSuccess = candidate?.continue === true && Object.keys(candidate).length === 1;
      const validContext = silentSuccess || !eventNames[event] || (
        candidate?.hookSpecificOutput?.hookEventName === eventNames[event]
        && typeof candidate.hookSpecificOutput.additionalContext === "string"
        && candidate.hookSpecificOutput.additionalContext.length > 0
      );
      if (child.status === 0 && candidate && typeof candidate === "object"
        && !Array.isArray(candidate) && !("additional_context" in candidate) && validContext) {
        output = candidate;
      }
    } catch { /* A missing or interrupted runtime must still emit valid JSON. */ }
  }
}
process.stdout.write(`${JSON.stringify(output)}\n`);
