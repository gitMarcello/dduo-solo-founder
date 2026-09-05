from __future__ import annotations

import runpy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = runpy.run_path(str(ROOT / "scripts/validate_release.py"))
ReleaseValidationError = VALIDATOR["ReleaseValidationError"]
validate_skill = VALIDATOR["validate_skill"]


def test_release_validator_delegates_to_agent_skills_reference(tmp_path: Path):
    skill = tmp_path / "invalid-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: another-name\n"
        "description: Invalid fixture.\n"
        "invented-field: true\n"
        "---\n\n"
        "Instructions.\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseValidationError, match="Agent Skills reference validator"):
        validate_skill(skill)


def test_release_workflow_binds_publication_to_verified_commit_and_installs_tarball():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    smoke = workflow.split("- name: Smoke-test packaged installer", 1)[1].split(
        "- name: Upload verified source package", 1
    )[0]

    assert "release_commit: ${{ steps.package.outputs.release_commit }}" in workflow
    assert "RELEASE_COMMIT: ${{ needs.verify_and_package.outputs.release_commit }}" in workflow
    assert '"${object_sha}" != "${RELEASE_COMMIT}"' in workflow
    assert "moved after verification; refusing to publish" in workflow
    assert "--dry-run" not in smoke
    assert "--project-root \"${project_root}\" --yes" in smoke
    assert '--verify --only codex --project-root "${project_root}"' in smoke.replace(
        "\\\n            ", ""
    )
    assert '"app-server"' in smoke
    assert '"enabled":true' in smoke
    assert "DDUO_RELEASE_SMOKE_PROJECT_ROOT" in smoke
    assert '"v*-beta.*"' in workflow


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_release_validator_rejects_nonportable_native_hooks(monkeypatch, client):
    validate = VALIDATOR["validate_agent_plugin"]
    original = validate.__globals__["read_json"]
    target = VALIDATOR["CODEX_HOOK_MANIFEST" if client == "codex" else "CLAUDE_ADAPTER_MANIFEST"]

    def read_json(relative):
        document = original(relative)
        if relative == target:
            document["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "/bin/sh old-hook.sh"
        return document

    monkeypatch.setitem(validate.__globals__, "read_json", read_json)
    with pytest.raises(ReleaseValidationError, match="portable Node runner"):
        validate()
