#!/bin/bash
# Wait for the exact source commit's full Mac and Windows CI, before creating a release tag.
set -euo pipefail
release_sha="${1:?pass the source commit SHA}"
run=""
for attempt in $(seq 1 12); do
  run="$(gh run list --workflow test.yml --event push --branch main --commit "$release_sha" --limit 1 --json databaseId --jq '.[0].databaseId // empty')"
  [ -n "$run" ] && break
  sleep 10
done
[ -n "$run" ] || { echo "::error::No Test run found for the main commit $release_sha"; exit 1; }
gh run watch "$run" --exit-status --interval 10
