"""Canonical, shell-inert invitations for one remote project checkout."""

from __future__ import annotations

import base64
import json
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from . import __version__


INVITE_PAYLOAD_VERSION = 1
INVITE_PAYLOAD = re.compile(r"^[A-Za-z0-9_-]{1,8192}$")
RELEASE_VERSION = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
REMOTE_REPOSITORY_URL = "https://github.com/gitMarcello/dduo-solo-founder"
REMOTE_RELEASE_TAG = f"v{__version__}"
InvitationLanguage = Literal["en", "it"]


class InvitationPayloadError(ValueError):
    """A manager-generated invitation descriptor is invalid."""


def _canonical_https_url(value: str, *, field: str) -> str:
    """Normalize one public HTTPS endpoint without accepting embedded credentials."""
    supplied = str(value).strip()
    if any(ord(character) < 32 for character in supplied) or any(
        character in supplied for character in ('"', "\\")
    ):
        raise InvitationPayloadError(f"{field} contains unsafe characters")
    try:
        parsed = urlsplit(supplied)
        port = parsed.port
    except ValueError as exc:
        raise InvitationPayloadError(f"{field} is not a valid HTTPS URL") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise InvitationPayloadError(f"{field} must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise InvitationPayloadError(f"{field} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise InvitationPayloadError(f"{field} must not contain a query or fragment")
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    path = (parsed.path or "").rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def invitation_urls(api_url: str, dashboard_url: str) -> tuple[str, str]:
    """Return a canonical API/dashboard pair for one public project gateway."""
    canonical_api = _canonical_https_url(api_url, field="api_url")
    canonical_dashboard = _canonical_https_url(dashboard_url, field="dashboard_url")
    api = urlsplit(canonical_api)
    dashboard = urlsplit(canonical_dashboard)
    if (api.scheme, api.netloc) != (dashboard.scheme, dashboard.netloc):
        raise InvitationPayloadError("invitation endpoints must use the same HTTPS origin")
    expected_api_path = f"{dashboard.path.rstrip('/')}/api" or "/api"
    if api.path != expected_api_path:
        raise InvitationPayloadError("api_url must be the dashboard URL followed by /api")
    return canonical_api, canonical_dashboard


def encode_invite_payload(
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str,
    invitation_code: str,
) -> str:
    """Return one opaque, versioned base64url descriptor with no shell metacharacters."""
    canonical_api, canonical_dashboard = invitation_urls(api_url, dashboard_url)
    document = {
        "version": INVITE_PAYLOAD_VERSION,
        "project_id": str(project_id),
        "name": str(name),
        "api_url": canonical_api,
        "dashboard_url": canonical_dashboard,
        "invitation_code": str(invitation_code),
    }
    if any(not document[key].strip() for key in document if key != "version"):
        raise InvitationPayloadError("invitation fields must not be empty")
    if any(len(document[key]) > 2_000 for key in document if key != "version"):
        raise InvitationPayloadError("invitation payload is too large")
    encoded = base64.urlsafe_b64encode(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).decode("ascii")
    descriptor = encoded.rstrip("=")
    if not INVITE_PAYLOAD.fullmatch(descriptor):
        raise InvitationPayloadError("invitation payload is too large")
    return descriptor


def decode_invite_payload(value: str) -> dict[str, str]:
    """Strictly decode a canonical invitation without performing shell parsing."""
    supplied = str(value).strip()
    if not INVITE_PAYLOAD.fullmatch(supplied):
        raise InvitationPayloadError("invitation payload is malformed")
    try:
        padding = "=" * (-len(supplied) % 4)
        raw = base64.b64decode(supplied + padding, altchars=b"-_", validate=True)
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvitationPayloadError("invitation payload is malformed") from exc
    fields = (
        "project_id",
        "name",
        "api_url",
        "dashboard_url",
        "invitation_code",
    )
    if not isinstance(document, dict) or set(document) != {"version", *fields}:
        raise InvitationPayloadError("invitation payload is malformed")
    if document.get("version") != INVITE_PAYLOAD_VERSION or any(
        not isinstance(document.get(key), str) or not document[key].strip() for key in fields
    ):
        raise InvitationPayloadError("invitation payload is malformed")
    if any(len(document[key]) > 2_000 for key in fields):
        raise InvitationPayloadError("invitation payload is too large")
    try:
        canonical_api, canonical_dashboard = invitation_urls(
            document["api_url"], document["dashboard_url"]
        )
    except InvitationPayloadError as exc:
        raise InvitationPayloadError("invitation payload is malformed") from exc
    result = {key: document[key] for key in fields}
    result["api_url"] = canonical_api
    result["dashboard_url"] = canonical_dashboard
    return result


def invitation_setup_prompt(
    invite_payload: str,
    *,
    language: InvitationLanguage = "it",
    release_version: str = __version__,
) -> str:
    """Build the one canonical, self-contained prompt shared by API and CLI."""
    if not INVITE_PAYLOAD.fullmatch(str(invite_payload)):
        raise InvitationPayloadError("invitation payload is malformed")
    if language not in {"en", "it"}:
        raise InvitationPayloadError("invitation language is unsupported")
    if not RELEASE_VERSION.fullmatch(str(release_version)):
        raise InvitationPayloadError("invitation release version is malformed")
    release_tag = f"v{release_version}"
    join_command = (
        "dduo-solo-founder remote-join "
        f"--invite-payload {invite_payload} --project-root ."
    )
    if language == "en":
        return f"""You received access to the shared memory identified by this dDuo invitation.
Work only inside the project's authorized Git checkout. If you cannot access it, stop and ask
the user to obtain repository access from the Infrastructure manager.

Install or update dDuo Solo Founder from the official repository using exactly the immutable
release {release_tag}:
{REMOTE_REPOSITORY_URL}
Use a temporary checkout outside the project; never nest the dDuo repository inside the
project checkout.

From the project root, have the assistant perform this single remote binding. Do not ask the
user to use the terminal:
{join_command}

Then have the assistant open the authenticated dashboard:
dduo-solo-founder dashboard --tab tasks --project-root .

The opaque descriptor contains an expiring one-time invitation. Never reveal its decoded
contents, ask for SSH or VPS credentials, open local Setup, or start a replacement local Docker
stack. If memory is unavailable, recommend restoring the remote connection and offer to check
it. Offer continuing temporarily without current memory as an explicit alternative, using the
last verified project manual when available. Wait for the user's choice unless already given
in this chat; do not repeat an unchanged warning after they choose to continue. If help from
the Infrastructure manager is needed, explain the next step without contacting them unless
the user authorizes it.

After an installation, update or first activation, open a new chat or session from the same project root.
After installing or updating Codex, fully quit and reopen Codex first: a new chat alone does not
reload hooks, skills or MCP. With Claude, a new session is sufficient."""
    return f"""Hai ricevuto accesso alla memoria condivisa indicata da questo invito dDuo.
Lavora esclusivamente dentro il repository Git autorizzato del progetto. Se non riesci ad
accedervi, fermati e chiedi all'utente di ottenere l'accesso dal Gestore dell'infrastruttura.

Installa o aggiorna dDuo Solo Founder dal repository ufficiale usando esattamente
la release immutabile {release_tag}:
{REMOTE_REPOSITORY_URL}
Usa un checkout temporaneo esterno al progetto; non annidare mai il repository dDuo dentro il
checkout del progetto.

Dalla root del progetto, fai eseguire all'assistente questo unico binding remoto, senza
chiedere all'utente di usare il terminale:
{join_command}

Subito dopo fai aprire all'assistente la dashboard autenticata:
dduo-solo-founder dashboard --tab tasks --project-root .

Il descriptor opaco contiene un invito monouso e scade. Non mostrarne il contenuto decodificato,
non chiedere credenziali SSH o VPS, non aprire Setup locale e non avviare un Docker locale
sostitutivo. Se la memoria non è raggiungibile, consiglia di ripristinare la connessione remota
e proponi di verificarla. Offri come alternativa esplicita di proseguire temporaneamente senza
memoria aggiornata, usando l'ultima copia verificata del Manuale operativo se disponibile.
Attendi la scelta dell'utente, salvo che l'abbia già espressa in questa chat; se sceglie di
proseguire, non ripetere lo stesso avviso. Se serve il Gestore dell'infrastruttura, spiega il
prossimo passo senza contattarlo, a meno che l'utente lo autorizzi.

Dopo installazione, aggiornamento o prima attivazione, chiedi all'utente di aprire una
nuova chat o sessione nella stessa root. Se hai installato o aggiornato Codex, chiedi prima di
chiudere e riaprire completamente Codex: una nuova chat da sola non ricarica hook, skill e MCP.
Con Claude è sufficiente una nuova sessione."""


def create_invitation_bundle(
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str,
    invitation_code: str,
    language: InvitationLanguage = "it",
    release_version: str = __version__,
) -> tuple[str, str]:
    """Create the descriptor and its canonical onboarding prompt together."""
    payload = encode_invite_payload(
        project_id=project_id,
        name=name,
        api_url=api_url,
        dashboard_url=dashboard_url,
        invitation_code=invitation_code,
    )
    return payload, invitation_setup_prompt(
        payload,
        language=language,
        release_version=release_version,
    )
