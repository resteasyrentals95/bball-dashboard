#!/bin/bash
# Refresh dashboard data and publish to GitHub Pages.
# Runs every 15 min via launchd. Safe to run manually too.
set -u
cd "$(dirname "$0")" || exit 1

# Never let git try to prompt for credentials from a background job (it would hang/fail).
export GIT_TERMINAL_PROMPT=0

/usr/bin/python3 fetch_data.py

# Push phone notifications for new Duke recruiting / major NBA news.
/usr/bin/python3 notify.py

# Publish only if there are changes and a git remote is configured.
if git rev-parse --git-dir >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
  git add index.html dashboard.json
  if ! git diff --cached --quiet; then
    git commit -m "Auto update $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1
    # Retry the push a few times so a transient blip can't strand commits.
    pushed=""
    for attempt in 1 2 3; do
      if git push origin main >/dev/null 2>&1; then pushed="yes"; echo "pushed update"; break; fi
      echo "push attempt $attempt failed; retrying"
      sleep 10
    done
    [ -z "$pushed" ] && echo "push FAILED after retries (commits queued; will push next run)"
  else
    echo "no changes to publish"
  fi
fi
