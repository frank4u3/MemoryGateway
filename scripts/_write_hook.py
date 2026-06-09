import os

content = r'''#!/usr/bin/env bash
# post-commit hook: trigger memory pack regeneration via the gateway API
# Install: cp scripts/post-commit .git/hooks/post-commit && chmod +x .git/hooks/post-commit

set -euo pipefail

GATEWAY_URL="${MEMORY_GATEWAY_URL:-http://localhost:8765}"
AUTH_TOKEN="${ME...n
# Determine what changed in this commit
CHANGED_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null || echo "")

TRIGGER_TYPE="git_commit"

# Detect the type of change
if echo "$CHANGED_FILES" | grep -qiE '(pyproject\.toml|requirements.*\.txt|package\.json|Cargo\.toml|go\.mod|\.cfg|\.ini|setup\.py)'; then
    TRIGGER_TYPE="dep_change"
elif echo "$CHANGED_FILES" | grep -qiE '(architecture|design|adr|spec)'; then
    TRIGGER_TYPE="arch_change"
elif echo "$CHANGED_FILES" | grep -qiE '(\.md$|\.rst$|\.txt$|docs/)'; then
    TRIGGER_TYPE="doc_change"
fi

echo "[post-commit] Triggering memory pack generation (type: $TRIGGER_TYPE)"

# Build auth header
AUTH_HEADER=()
if [ -n "$AUTH_TOKEN" ]; then
    AUTH_HEADER=(-H "Authorization: Bearer $AUTH_TOKEN")
fi

RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
    -X POST "${GATEWAY_URL}/v1/memory/pack/generate" \
    -H "Content-Type: application/json" \
    -H "X-Agent-ID: git-hook" \
    "${AUTH_HEADER[@]}" \
    -d '{"trigger_type": "'"$TRIGGER_TYPE"'"}' 2>/dev/null || echo "HTTP_CODE:000")

HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE:" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | grep -v "HTTP_CODE:")

if [ "$HTTP_CODE" = "200" ]; then
    VERSION_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version_id','unknown'))" 2>/dev/null || echo "unknown")
    echo "[post-commit] Memory pack regenerated: ${VERSION_ID}"
else
    echo "[post-commit] Warning: Memory pack generation failed (HTTP ${HTTP_CODE})"
    echo "[post-commit] Response: ${BODY}"
fi
'''

target = r'H:\memory-gateway\scripts\post-commit'
with open(target, 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)
os.chmod(target, 0o755)
print(f"Written {len(content)} bytes to {target}")
