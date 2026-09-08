"""Authenticated project-scoped HTTP transport shared by hooks and MCP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from dduo_solo_founder import __version__
from dduo_solo_founder.client_binding import ProjectBinding
from dduo_solo_founder.client_protocol import CLIENT_PROTOCOL_VERSION


SAFE_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,159}$")


@dataclass(frozen=True)
class CompatibilityDirective:
    status: str
    target_release: str | None = None


class ClientUpgradeRequired(httpx.HTTPStatusError):
    def __init__(self, response: httpx.Response, directive: CompatibilityDirective):
        super().__init__(
            "dDuo client update required; install the latest official release, reload the "
            "active client as instructed, and open a new chat or session",
            request=response.request,
            response=response,
        )
        self.directive = directive


def compatibility_directive(response: Any) -> CompatibilityDirective | None:
    headers = getattr(response, "headers", {}) or {}
    status = str(headers.get("X-DDUO-Client-Status") or "").strip().lower()
    target = str(headers.get("X-DDUO-Target-Release") or "").strip() or None
    if not status:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        update = payload.get("client_update") if isinstance(payload, dict) else None
        if isinstance(update, dict):
            status = str(update.get("status") or "").strip().lower()
            target = str(update.get("target_release") or "").strip() or None
    if status not in {"compatible", "grace", "blocked"}:
        status = "blocked" if getattr(response, "status_code", 0) == 426 else ""
    if target is not None and not SAFE_RELEASE_ID.fullmatch(target):
        target = None
    return CompatibilityDirective(status, target) if status else None


class ProjectHttpClient:
    """A narrow HTTP client that never serializes or logs its bearer credential."""

    def __init__(
        self,
        binding: ProjectBinding,
        *,
        component: str,
        session_id: str | None = None,
        request_function: Callable[..., Any] | None = None,
    ):
        self.binding = binding
        self.component = component
        self.session_id = session_id
        self.request_function = request_function or httpx.request

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "X-DDUO-Client-Version": __version__,
            "X-DDUO-Client-Protocol": CLIENT_PROTOCOL_VERSION,
            "X-DDUO-Client-Component": self.component,
            "X-DDUO-Binding-ID": self.binding.binding_id,
        }
        if self.session_id:
            headers["X-DDUO-Session-ID"] = self.session_id
        if self.binding.remote:
            if not self.binding.bearer_token:
                raise RuntimeError("remote binding credential is unavailable")
            headers["Authorization"] = f"Bearer {self.binding.bearer_token}"
        if extra:
            headers.update(extra)
        return headers

    def request(self, method: str, path: str, **kwargs):
        supplied = kwargs.pop("headers", None)
        response = self.request_function(
            method,
            f"{self.binding.api_url.rstrip('/')}/{path.lstrip('/')}",
            headers=self.headers(supplied),
            **kwargs,
        )
        directive = compatibility_directive(response)
        if getattr(response, "status_code", 0) == 426 or (
            directive is not None and directive.status == "blocked"
        ):
            raise ClientUpgradeRequired(
                response, directive or CompatibilityDirective("blocked", None)
            )
        return response

    def json(self, method: str, path: str, **kwargs) -> dict:
        response = self.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
