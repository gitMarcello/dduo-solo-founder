"""Bounded, read-only native hook checks; user choices never live on the server.

MCP connections may serve several chats. A notice is acknowledged explicitly on
each tool call, using the current issue identifier, rather than muting a whole
connection, computer or project. No credentials or chat content enter this cache.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from dduo_solo_founder.client_readiness import codex_hook_status


@dataclass
class _Check:
    signature: tuple
    value: dict
    expires_at: float


class MemoryConnectionChecks:
    def __init__(self, *, ttl_seconds: float = 30, max_entries: int = 64):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._cache: OrderedDict[tuple, _Check] = OrderedDict()
        # Only calls for the same binding wait for its native probe. The shared
        # condition protects brief cache updates, never native process work.
        self._condition = threading.Condition()
        self._inflight: set[tuple] = set()

    def check(
        self, client: str, root: Path, binding_id: str, *, refresh: bool = False
    ) -> dict:
        if client != "codex":
            return {
                "scope": "local_hook_authorization",
                "client": client,
                "ready": None,
                "reason": "native_verification_unavailable",
                "requires_choice": False,
                "response_instruction": (
                    "Native hook authorization cannot be verified for this client. "
                    "Do not claim capture is verified; keep the existing client workflow."
                ),
            }
        key = (client, str(root.resolve()), binding_id)
        with self._condition:
            while key in self._inflight:
                self._condition.wait()
            previous = self._cache.get(key)
            if previous and not refresh and time.monotonic() < previous.expires_at:
                self._cache.move_to_end(key)
                return dict(previous.value)
            self._inflight.add(key)
        try:
            status = codex_hook_status(root)
            signature = (
                status.ready, status.reason, status.hook_count,
                tuple(sorted(status.trust_statuses)), tuple(sorted(status.events)),
            )
            value = {
                "scope": "local_hook_authorization",
                "client": client,
                "ready": status.ready,
                "reason": status.reason,
                "requires_choice": not status.ready,
            }
            if status.ready:
                value["response_instruction"] = (
                    "Native hooks are authorized. This is not proof of turn capture or sleep. "
                    "Discard any previous memory_warning_ack. After a plugin update restart "
                    "Codex and open a new chat; otherwise verify the next completed turn."
                )
            else:
                # A fresh occurrence invalidates an old choice even for an
                # observed modified -> authorized -> modified transition.
                value["warning_id"] = (
                    previous.value["warning_id"]
                    if previous and previous.signature == signature
                    else uuid.uuid4().hex
                )
                consent = status.reason in {
                    "authorization_required", "reauthorization_required"
                }
                value["message"] = (
                    "dDuo needs your authorization. Automatic conversation capture is inactive; "
                    "saved information and Work tools remain available."
                    if consent
                    else "dDuo cannot verify automatic conversation capture. "
                    "Do not assume new turns are being recorded."
                )
                value["next_action"] = (
                    "Open Codex Settings > Hooks > dDuo Solo Founder, review and trust the hooks."
                    if consent
                    else "Inspect Codex Settings > Hooks. Repair or enable dDuo if needed; "
                    "a failed check is not proof that permission is missing."
                )
                value["response_instruction"] = (
                    "Before project work, explain this notice briefly in the user's language and "
                    "ask: fix it now or continue without automatic memory? If fixing, wait for "
                    "'done' then call check_memory_connection. Only after explicit consent to "
                    "continue, pass this warning_id as memory_warning_ack on subsequent dDuo "
                    "calls in THIS chat. Do not repeat an accepted unchanged warning. Never "
                    "reuse another chat's choice or claim missing turns will replay automatically."
                )
            with self._condition:
                self._cache[key] = _Check(
                    signature, value, time.monotonic() + self.ttl_seconds
                )
                self._cache.move_to_end(key)
                while len(self._cache) > self.max_entries:
                    self._cache.popitem(last=False)
            return dict(value)
        finally:
            with self._condition:
                self._inflight.remove(key)
                self._condition.notify_all()
