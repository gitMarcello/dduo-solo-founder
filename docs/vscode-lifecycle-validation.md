[**EN · English**](vscode-lifecycle-validation.md) · [IT · Italiano](vscode-lifecycle-validation.it.md)

# VS Code lifecycle acceptance record

This record decides whether dDuo may claim that an **official** Codex or Claude
Code VS Code integration works on one operating system. It is deliberately not
a unit-test substitute and it must never be completed from CLI stdout alone.

## Scope and safety

Create a disposable repository, a new project id and a dedicated local or VPS
memory. Do not activate dDuo in its own repository. Do not use a real project,
existing project memory, account export, token, path, conversation or customer
data. Delete the disposable memory only after the record is complete.

The result is specific to this tuple:

```text
client family + client version + VS Code version + extension version + OS + memory mode
```

WSL, Remote SSH and Dev Containers are outside this Beta record. A normal local
checkout bound to a dDuo VPS is a remote-memory test, not a remote-IDE test.

## Test procedure

1. Record the six values above and the selected dDuo release.
2. Install with `--only <codex|claude> --surface vscode` using the exact client
   configuration. Complete only a native approval UI that is actually offered.
3. Reload the VS Code window and open a **new graphical conversation**. Do not
   open a CLI session in the integrated terminal as evidence.
4. Capture sanitized protocol envelopes for MCP initialization and the three
   lifecycle events. Preserve only field names, booleans and opaque identifiers;
   replace every value that could identify a person, computer, account or project.
5. Insert one artificial, unique phrase into test memory. In the next graphical
   chat ask a question that can be answered only if that phrase was delivered as
   dDuo context. The phrase must not appear in the prompt.
6. Complete two short turns. Verify separately that `SessionStart`,
   `UserPromptSubmit` and `Stop` are observed for their native session and that
   each turn is persisted exactly once. Verify one MCP operation and one sleep
   consolidation on the memory host.
7. Start a second graphical chat and retrieve the artificial memory.

`context_emitted_at` proves only that dDuo emitted an envelope. The unique
phrase check is the required proof that the graphical client accepted useful
context. An HTTP 200 is not persistence proof: the exact turn must report
`committed: true`.

## Sanitized evidence template

Save this next to the acceptance result, not in a user project or a public
issue. Replace identifier values with stable placeholders; never copy text from
the chat.

```json
{
  "record_version": 1,
  "result": "pass | fail | blocked",
  "client": "codex | claude",
  "surface": "vscode",
  "os": "macos | windows",
  "memory_mode": "local | remote",
  "versions": {
    "dduo": "<release>",
    "vscode": "<version>",
    "extension": "<version>",
    "client": "<version>"
  },
  "native_approval": "approved | not_offered | unavailable",
  "mcp_initialize": {"observed": false, "client_identity": "<redacted>"},
  "hooks": {
    "session_start": {"observed": false, "session": "<opaque>"},
    "user_prompt_submit": {"observed": false, "turn": "<opaque>"},
    "stop": {"observed": false, "turn": "<opaque>", "response_available": false}
  },
  "context_phrase_was_used": false,
  "two_turns_persisted_once": false,
  "mcp_operation": false,
  "sleep_on_memory_host": false,
  "second_chat_retrieval": false,
  "notes": "no personal, project or chat content"
}
```

## Pass, fail and blocked

Pass only when every boolean test above is true and the native approval path is
known. A client that cannot load MCP, cannot execute a required hook, has no
stable session identity, or does not provide a usable final response **fails**
this surface. Do not add a hidden polling or transcript-scraping fallback.

If a vendor offers no native approval path or the required UI is unavailable,
mark the result **blocked**, preserve the sanitized evidence and do not claim
support for that surface. A different client or the CLI may remain supported.
