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


def test_authority_smoke_checks_the_current_migration_head():
    import runpy
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    expected = ScriptDirectory.from_config(config).get_current_head()
    smoke = runpy.run_path(str(ROOT / "scripts/e2e_authority_transfer.py"))
    assert smoke["EXPECTED_SCHEMA_REVISION"] == expected


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


def test_release_notes_are_curated_and_come_from_the_verified_archive():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    notes = (ROOT / f".github/release-notes/{version}.md").read_text(encoding="utf-8")
    publish = workflow.split("- name: Verify and publish immutable assets", 1)[1]

    assert 'test -s ".github/release-notes/${version}.md"' in workflow
    assert "--generate-notes" not in workflow
    assert '--notes-file "dist/RELEASE_NOTES.md"' in publish
    assert 'tar -xOf "dist/dduo-solo-founder-${RELEASE_VERSION}.tar.gz"' in publish
    assert (
        '"dduo-solo-founder-${RELEASE_VERSION}/.github/release-notes/${RELEASE_VERSION}.md"'
        in publish
    )
    assert publish.index("sha256sum --check SHA256SUMS") < publish.index("tar -xOf")
    assert publish.index('test -s "dist/RELEASE_NOTES.md"') < publish.index("gh release create")
    assert "## Italiano" in notes and "## English" in notes
    assert "ispirata alla memoria umana" in notes and "inspired by human memory" in notes
    assert f"/tree/v{version}" in notes


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
