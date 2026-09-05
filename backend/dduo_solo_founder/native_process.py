"""Launch installed client payloads without interpreting Windows batch scripts."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path


def _windows() -> bool:
    return sys.platform == "win32"


def _package_roots(shim: Path, package: str) -> list[Path]:
    """Only inspect the npm installation adjacent to the discovered launcher."""
    locations = [shim.parent / "node_modules" / package]
    if shim.parent.name == ".bin":
        locations.append(shim.parent.parent / package)
    roots = []
    for location in locations:
        try:
            manifest = json.loads((location / "package.json").read_text(encoding="utf-8"))
            if isinstance(manifest, dict) and manifest.get("name") == package:
                roots.append(location.resolve())
        except (OSError, ValueError):
            continue
    return roots


def native_codex_executable(executable: str) -> str | None:
    """Resolve official native or npm Codex, never a shell-interpreted command.

    Layout matches openai/codex rust-v0.150.0 codex-cli/bin/codex.js. Both
    optional platform packages and the bundled vendor fallback are supported.
    """
    if not _windows() or Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return executable
    shim = Path(executable)
    sibling = shim.with_suffix(".exe")
    if sibling.is_file():
        return str(sibling)
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        architecture, target = "x64", "x86_64-pc-windows-msvc"
    elif machine in {"arm64", "aarch64"}:
        architecture, target = "arm64", "aarch64-pc-windows-msvc"
    else:
        return None
    package = f"@openai/codex-win32-{architecture}"
    for root in _package_roots(shim, "@openai/codex"):
        vendors = [root / "vendor"]
        # Node resolves package dependencies from the package's own node_modules
        # and then each ancestor node_modules (including pnpm's resolved root).
        for parent in (root, *root.parents):
            platform_root = parent / "node_modules" / package
            try:
                metadata = json.loads((platform_root / "package.json").read_text(encoding="utf-8"))
                # Official platform dependencies are npm aliases, for example
                # @openai/codex-win32-x64 -> npm:@openai/codex@VERSION-win32-x64.
                # Their manifest keeps the original package name, not the alias.
                if isinstance(metadata, dict) and metadata.get("name") in {package, "@openai/codex"}:
                    vendors.insert(0, platform_root / "vendor")
                    break
            except (OSError, ValueError):
                continue
        for vendor in vendors:
            candidate = vendor / target / "bin" / "codex.exe"
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def client_command(executable: str, arguments: list[str], *, client: str) -> list[str]:
    """Return a native argv vector; stdin/schema/path arguments remain literal."""
    if not _windows() or Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return [executable, *arguments]
    native = native_codex_executable(executable) if client == "codex" else None
    if native:
        return [native, *arguments]
    shim = Path(executable)
    if client == "claude":
        sibling = shim.with_suffix(".exe")
        if sibling.is_file():
            return [str(sibling), *arguments]
        for root in _package_roots(shim, "@anthropic-ai/claude-code"):
            try:
                metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
                bins = metadata.get("bin", {})
                entry = bins.get("claude") if isinstance(bins, dict) else bins
                if not isinstance(entry, str):
                    continue
                script = (root / entry).resolve()
                script.relative_to(root)
                node = shutil.which("node")
                if script.is_file() and node and Path(node).suffix.lower() == ".exe":
                    return [node, str(script), *arguments]
            except (OSError, ValueError):
                continue
    raise FileNotFoundError(
        f"{client.title()} launcher has no supported native payload. "
        "Repair its official native or npm installation before retrying."
    )
