from dduo_solo_founder.bridge_auth import (
    bridge_token_authorized,
    project_bridge_token,
)


def test_project_bridge_tokens_are_stable_and_strictly_project_scoped():
    master = "host-master-token"
    first = project_bridge_token(master, "project-a")
    assert first == project_bridge_token(master, "project-a")
    assert first != project_bridge_token(master, "project-b")
    assert bridge_token_authorized(master, master, project_id="project-b")
    assert bridge_token_authorized(master, first, project_id="project-a")
    assert not bridge_token_authorized(master, first, project_id="project-b")
    assert not bridge_token_authorized(master, first)
    assert not project_bridge_token("", "project-a")
    assert not project_bridge_token(master, "")
    assert not project_bridge_token(master, "x" * 161)
