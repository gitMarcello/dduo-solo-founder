#!/usr/bin/env bash
set -euo pipefail

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY for the live embeddings test}"
command -v docker >/dev/null
command -v curl >/dev/null
command -v jq >/dev/null
command -v codex >/dev/null
command -v claude >/dev/null
command -v dduo-solo-founder-agent >/dev/null

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-dduo-solo-founder-live-e2e}"
export DDUO_SOLO_FOUNDER_API_PORT="${DDUO_SOLO_FOUNDER_API_PORT:-18765}"
export DDUO_SOLO_FOUNDER_WEB_PORT="${DDUO_SOLO_FOUNDER_WEB_PORT:-20765}"
bridge_port="${DDUO_LIVE_E2E_BRIDGE_PORT:-18787}"
export DDUO_CLI_BRIDGE_TOKEN="dduo-live-e2e-${RANDOM}-${RANDOM}-$$"
export DDUO_CLI_BRIDGE_URL="http://host.docker.internal:${bridge_port}"
base="http://127.0.0.1:${DDUO_SOLO_FOUNDER_API_PORT}"
project="22222222-2222-4222-8222-222222222222"
bridge_log="$(mktemp -t dduo-live-e2e-bridge.XXXXXX)"
bridge_pid=""

cleanup() {
  docker compose down -v >/dev/null 2>&1 || true
  if [ -n "$bridge_pid" ]; then kill "$bridge_pid" >/dev/null 2>&1 || true; fi
  rm -f "$bridge_log"
}
trap cleanup EXIT

DDUO_CLI_BRIDGE_TOKEN="$DDUO_CLI_BRIDGE_TOKEN" \
  dduo-solo-founder-agent --port "$bridge_port" >"$bridge_log" 2>&1 &
bridge_pid=$!
for _ in {1..50}; do
  if curl --fail --silent \
    -H "Authorization: Bearer $DDUO_CLI_BRIDGE_TOKEN" \
    "http://127.0.0.1:${bridge_port}/health" >/dev/null; then break; fi
  sleep 0.2
done
curl --fail --silent \
  -H "Authorization: Bearer $DDUO_CLI_BRIDGE_TOKEN" \
  "http://127.0.0.1:${bridge_port}/health" | jq -e \
  '.providers.codex == true and .providers.claude == true' >/dev/null

docker compose up -d --build
for _ in {1..90}; do
  if curl --fail --silent "$base/health" >/dev/null; then break; fi
  sleep 2
done
curl --fail --silent "$base/health" >/dev/null

curl --fail --silent -X POST "$base/projects" \
  -H "Content-Type: application/json" \
  -d '{"id":"22222222-2222-4222-8222-222222222222","name":"dDuo Solo Founder E2E","root_path":"/tmp/dduo-solo-founder-e2e","cause":"Verify durable shared project memory","principles":["Production rules must survive client changes"],"objectives":["Complete a real Codex and Claude round trip"]}' >/dev/null

wait_for_sleep() {
  local session_id="$1"
  local provider="$2"
  local scheduled job_id status jobs
  scheduled=$(curl --fail --silent -X POST "$base/projects/$project/sleep" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg session "$session_id" '{session_id:$session,trigger:"manual"}')")
  test "$(printf '%s' "$scheduled" | jq -r .scheduled)" -gt 0
  job_id=$(printf '%s' "$scheduled" | jq -r '.items[0].id')
  for _ in {1..150}; do
    jobs=$(curl --fail --silent "$base/projects/$project/sleep-jobs?limit=20")
    status=$(printf '%s' "$jobs" | jq -r --arg id "$job_id" '.items[] | select(.id == $id) | .status')
    if [ "$status" = "completed" ]; then
      test "$(printf '%s' "$jobs" | jq -r --arg id "$job_id" '.items[] | select(.id == $id) | .provider')" = "$provider"
      return
    fi
    if [ "$status" = "waiting" ] || [ "$status" = "failed" ]; then
      printf 'Sleep job %s stopped as %s. Bridge output:\n' "$job_id" "$status" >&2
      cat "$bridge_log" >&2
      return 1
    fi
    sleep 2
  done
  printf 'Timed out waiting for %s sleep job %s.\n' "$provider" "$job_id" >&2
  return 1
}

wait_for_memory() {
  local query="$1"
  local result
  for _ in {1..60}; do
    result=$(curl --fail --silent --get "$base/projects/$project/memories/search" \
      --data-urlencode "q=$query" --data-urlencode "limit=4")
    if [ "$(printf '%s' "$result" | jq '.items | length')" -gt 0 ]; then return; fi
    sleep 1
  done
  printf 'Semantic retrieval did not return a memory for: %s\n' "$query" >&2
  return 1
}

codex_session=$(curl --fail --silent -X POST "$base/projects/$project/sessions" \
  -H "Content-Type: application/json" \
  -d '{"client":"codex","external_id":"e2e-codex-source"}' | jq -r .id)
codex_turn=$(curl --fail --silent -X POST "$base/projects/$project/turns/begin" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg session "$codex_session" '{session_id:$session,external_id:"e2e-codex-turn",user_prompt:"The production migration invariant is ORBIT-731: every migration requires a tested rollback before release."}')" | jq -r .turn.id)
curl --fail --silent -X POST "$base/turns/$codex_turn/commit" \
  -H "Content-Type: application/json" \
  -d '{"assistant_response":"ORBIT-731 is confirmed as the production migration rule.","receipt":"e2e-codex-commit"}' | jq -e '.committed == true' >/dev/null
wait_for_sleep "$codex_session" codex
wait_for_memory "Which production migration rule is ORBIT-731?"

claude_session=$(curl --fail --silent -X POST "$base/projects/$project/sessions" \
  -H "Content-Type: application/json" \
  -d '{"client":"claude","external_id":"e2e-claude-source"}' | jq -r .id)
claude_begin=$(curl --fail --silent -X POST "$base/projects/$project/turns/begin" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg session "$claude_session" '{session_id:$session,external_id:"e2e-claude-turn",user_prompt:"Recall ORBIT-731. Also establish LANTERN-842: support exports must redact private contact details."}')")
test "$(printf '%s' "$claude_begin" | jq '.memories | length')" -gt 0
claude_turn=$(printf '%s' "$claude_begin" | jq -r .turn.id)
curl --fail --silent -X POST "$base/turns/$claude_turn/commit" \
  -H "Content-Type: application/json" \
  -d '{"assistant_response":"ORBIT-731 was recalled. LANTERN-842 is confirmed as the support-export rule.","receipt":"e2e-claude-commit"}' | jq -e '.committed == true' >/dev/null
wait_for_sleep "$claude_session" claude
wait_for_memory "What does LANTERN-842 require for support exports?"

codex_return=$(curl --fail --silent -X POST "$base/projects/$project/sessions" \
  -H "Content-Type: application/json" \
  -d '{"client":"codex","external_id":"e2e-codex-return"}' | jq -r .id)
final_turn=$(curl --fail --silent -X POST "$base/projects/$project/turns/begin" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg session "$codex_return" '{session_id:$session,external_id:"e2e-final-turn",user_prompt:"What does LANTERN-842 require for support exports?"}')")
test "$(printf '%s' "$final_turn" | jq '.memories | length')" -gt 0
test "$(docker compose exec -T postgres psql -U dduo_solo_founder -d dduo_solo_founder -Atc \
  "select count(*) from sleep_jobs where status = 'completed' and provider in ('codex', 'claude')")" -ge 2
printf 'Live E2E passed: Codex sleep -> Claude recall/sleep -> Codex recall.\n'
