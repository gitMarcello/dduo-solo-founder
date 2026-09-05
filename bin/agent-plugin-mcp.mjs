#!/usr/bin/env node

import { constants as fsConstants } from "node:fs";
import { access, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const runtimeExecutable =
  process.platform === "win32"
    ? "dduo-solo-founder-mcp-dispatch.exe"
    : "dduo-solo-founder-mcp-dispatch";

async function readableRuntimePointer() {
  const pointer = path.join(
    os.homedir(),
    ".config",
    "dduo-solo-founder",
    "hook-runtime-bin",
  );
  try {
    return (await readFile(pointer, "utf8")).trim();
  } catch {
    return "";
  }
}

async function isExecutable(candidate) {
  try {
    await access(
      candidate,
      process.platform === "win32" ? fsConstants.F_OK : fsConstants.X_OK,
    );
    return true;
  } catch {
    return false;
  }
}

async function findRuntime() {
  const bins = [];
  if (process.env.DDUO_SOLO_FOUNDER_RUNTIME_BIN) {
    bins.push(process.env.DDUO_SOLO_FOUNDER_RUNTIME_BIN);
  }
  const pointerBin = await readableRuntimePointer();
  if (pointerBin) {
    bins.push(pointerBin);
  }
  for (const entry of (process.env.PATH || "").split(path.delimiter)) {
    if (entry) {
      bins.push(entry);
    }
  }
  bins.push(path.join(os.homedir(), ".local", "bin"));

  for (const bin of [...new Set(bins)]) {
    const candidate = path.join(bin, runtimeExecutable);
    if (await isExecutable(candidate)) {
      return candidate;
    }
  }
  return null;
}

const runtime = await findRuntime();
if (!runtime) {
  process.stderr.write(
    "dDuo Solo Founder runtime is not installed. Open dDuo Setup from Codex or Claude Code to finish installation.\n",
  );
  process.exitCode = 78;
} else {
  const child = spawn(runtime, process.argv.slice(2), {
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      DDUO_SOLO_FOUNDER_PLUGIN_ENTRYPOINT: "agent-plugins-1.0",
    },
    stdio: "inherit",
  });

  const signalHandlers = new Map();
  for (const signal of ["SIGINT", "SIGTERM"]) {
    const handler = () => child.kill(signal);
    signalHandlers.set(signal, handler);
    process.on(signal, handler);
  }
  const removeSignalHandlers = () => {
    for (const [signal, handler] of signalHandlers) {
      process.off(signal, handler);
    }
  };
  child.on("error", (error) => {
    removeSignalHandlers();
    process.stderr.write(`dDuo Solo Founder runtime could not start: ${error.message}\n`);
    process.exitCode = 78;
  });
  child.on("exit", (code, signal) => {
    removeSignalHandlers();
    if (signal) {
      process.exit(128 + (os.constants.signals[signal] || 1));
      return;
    }
    process.exitCode = code ?? 1;
  });
}
