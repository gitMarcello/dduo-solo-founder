from __future__ import annotations

import pytest

from dduo_solo_founder import project_activation as activation, project_config


def test_decline_is_private_per_folder_idempotent_and_explicitly_reversible(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    activation.decline_setup(first)
    activation.decline_setup(first)
    assert activation.setup_declined(first)
    assert not activation.setup_declined(second)
    assert not (first / project_config.CONFIG_PATH).exists()
    activation.allow_setup(first)
    activation.allow_setup(first)
    assert not activation.setup_declined(first)


def test_decline_cannot_disable_an_existing_project(tmp_path):
    project_config.new_project_config(tmp_path, "Existing")
    with pytest.raises(ValueError, match="already configured"):
        activation.decline_setup(tmp_path)
    assert not activation.setup_declined(tmp_path)


def test_marker_is_not_allowed_to_be_a_directory(tmp_path, monkeypatch):
    activation.ACTIVATION_DIR.mkdir(parents=True)
    activation._marker(tmp_path).mkdir()
    monkeypatch.setattr(activation.os, "open", lambda *_args: pytest.fail("unsafe marker opened"))
    with pytest.raises(RuntimeError, match="regular file"):
        activation.decline_setup(tmp_path)


@pytest.mark.parametrize("error_type", [FileExistsError, PermissionError])
def test_directory_created_during_marker_open_is_rejected(tmp_path, monkeypatch, error_type):
    marker = activation._marker(tmp_path)

    def raced_open(path, flags, mode):
        assert path == marker
        assert flags & activation.os.O_EXCL
        marker.mkdir()
        raise error_type("marker appeared")

    monkeypatch.setattr(activation.os, "open", raced_open)
    with pytest.raises(RuntimeError, match="regular file"):
        activation.decline_setup(tmp_path)
    assert marker.is_dir()


def test_regular_marker_created_during_open_is_idempotent(tmp_path, monkeypatch):
    marker = activation._marker(tmp_path)

    def raced_open(*_args):
        marker.write_text("")
        raise FileExistsError("marker appeared")

    monkeypatch.setattr(activation.os, "open", raced_open)
    activation.decline_setup(tmp_path)
    assert activation.setup_declined(tmp_path)


@pytest.mark.parametrize("marker_created", [False, True])
def test_decline_does_not_mask_permission_errors(tmp_path, monkeypatch, marker_created):
    error = PermissionError("access denied")

    def denied_open(*_args):
        if marker_created:
            activation._marker(tmp_path).write_text("")
        raise error

    monkeypatch.setattr(activation.os, "open", denied_open)
    with pytest.raises(PermissionError) as raised:
        activation.decline_setup(tmp_path)
    assert raised.value is error


def test_preference_directory_must_not_follow_a_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    try:
        activation.ACTIVATION_DIR.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this Windows account cannot create symlinks")
    for action in (activation.decline_setup, activation.allow_setup):
        with pytest.raises(RuntimeError, match="symlink"):
            action(tmp_path)
