"""Small, content-free notices shared by lifecycle and MCP adapters."""
from __future__ import annotations

import hashlib
import json


CONFIGURATION_CHOICE_INSTRUCTION = (
    "Explain the specific limitation briefly in the user's language. Recommend restoring memory FIRST "
    "and offer to help with the concrete next action now. Explicitly offer continuing temporarily "
    "with the stated limitation as the alternative; never ask only whether to continue without memory. "
    "Wait for the user's choice before project work or opening configuration. An explicit request to "
    "configure or repair already authorizes that assistance, not native login or permission consent. "
    "Respect an unchanged issue already acknowledged in THIS chat; do not ask again or infer consent. "
)


def sleep_connection_notice(status: dict, *, remote: bool = False) -> dict | None:
    state = status.get("state")
    if state in {"updated", "updating"}:
        return None
    provider = status.get("provider")
    provider = provider if provider in {"codex", "claude"} else None
    kind = status.get("error_kind")
    auth = state == "connection_required" or kind == "auth_required"
    limited = state == "limited" or kind == "rate_limited"
    waiting = state == "waiting"
    if auth:
        reason = "sleep_auth_required"
        message = (
            f"Memory consolidation is paused: the project's {(provider or 'sleep').title()} "
            "authentication needs reconnecting. Saved turns and existing memories are retained. "
            "This does not mean that conversation capture or retrieval is disconnected."
        )
        action = (
            "Offer to help request reconnection from the infrastructure manager for the sleep account "
            "on the memory server; do not contact anyone without consent. "
            "Do not change this collaborator's local account or start local Docker."
            if remote else
            "Offer to open this project's configuration now to reconnect the sleep account. "
            "After the user chooses repair, call open_setup and guide them to select Reconnect. "
            "Complete the official sign-in for the project's sleep account, then recheck. "
            "Do not copy credentials from another account or alter other projects."
        )
    elif limited:
        reason = "sleep_rate_limited"
        message = "Memory consolidation is temporarily usage-limited; saved turns remain queued."
        action = "Explain the temporary limit and its reported retry time, if available."
    elif waiting:
        reason = "sleep_waiting"
        message = "Memory consolidation is waiting after an execution failure; saved turns remain queued."
        action = "Explain the failure and retry state; do not claim that consolidation has recovered."
    else:
        reason = "memory_status_unavailable"
        message = "dDuo cannot verify the project's memory service. Do not claim it is connected."
        action = (
            "Offer to help the infrastructure manager check the remote memory service; do not start local Docker."
            if remote else "Offer to open this project's configuration with open_setup and check its memory service."
        )
    requires_choice = auth or bool(status.get("requires_action")) or not (limited or waiting)
    signature = [reason, provider, str(status.get("issue_id") or ""), remote]
    notice = {
        "scope": "project_memory",
        "ready": False,
        "reason": reason,
        "provider": provider,
        "requires_choice": requires_choice,
        "warning_id": hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:32],
        "message": message,
        "next_action": action,
        "response_instruction": (
            "Surface this issue briefly in the current conversation, in the user's language; "
            "the dashboard is not the notification channel. "
            + (
                CONFIGURATION_CHOICE_INSTRUCTION + "After explicit consent to continue, call "
                "check_memory_connection and use its current warning_id as memory_warning_ack "
                "in THIS chat only. "
                if requires_choice else "Continue work after explaining the temporary limitation. "
            )
            + "Do not repeat an unchanged issue already acknowledged in this chat. "
            "An authentication failure will NOT retry automatically before reconnection. "
            "A saved login or a scheduled retry is not proof of recovery; verify the next actual result."
        ),
    }
    return notice


def combine_connection_checks(native: dict, health: dict, *, binding_id: str, remote: bool) -> dict:
    issue = sleep_connection_notice(health, remote=remote)
    if issue is None:
        return {**native, "memory_state": health["state"]}
    result = dict(issue)
    result["capture_ready"] = native.get("ready")
    if native.get("requires_choice"):
        result["requires_choice"] = True
        result["message"] = native["message"] + " " + issue["message"]
        result["next_action"] = native["next_action"] + " " + issue["next_action"]
        result["response_instruction"] = native["response_instruction"] + " " + issue["response_instruction"]
    signature = [binding_id, native.get("warning_id"), issue["warning_id"]]
    result["warning_id"] = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:32]
    return result
