#!/bin/bash
# Hook script: runs pytest when an API-related file is edited.
# Reads JSON from stdin (PostToolUse hook payload), extracts the file path,
# and runs tests only if the file matches an API source pattern.

FILE=$(jq -r '.tool_input.file_path // .tool_response.filePath' 2>/dev/null)

if echo "$FILE" | grep -qE 'app/(routers/.*\.py|main\.py|schemas\.py|errors\.py)$'; then
  cd /Users/sage/Desktop/FastAPI
  OUTPUT=$(.venv/bin/pytest tests/ -q 2>&1)
  EXIT_CODE=$?

  # Show summary line to the user via systemMessage
  SUMMARY=$(echo "$OUTPUT" | tail -1)

  if [ $EXIT_CODE -eq 0 ]; then
    echo "{\"systemMessage\": \"pytest: ${SUMMARY}\"}"
  else
    FAILURES=$(echo "$OUTPUT" | grep "^FAILED" || true)
    echo "{\"systemMessage\": \"pytest FAILED: ${SUMMARY}\", \"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": \"Test failures:\\n${FAILURES}\"}}"
  fi
fi
