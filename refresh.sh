#!/bin/bash
# Refresh dashboard data and publish to GitHub Pages.
# Used by the 8 AM / 8 PM launchd job. Safe to run manually too.
set -u
cd "$(dirname "$0")" || exit 1

/usr/bin/python3 fetch_data.py

# Push phone notifications for new Duke recruiting / major NBA news.
/usr/bin/python3 notify.py

# Publish only if there are changes and a git remote is configured.
if git rev-parse --git-dir >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
  git add index.html dashboard.json
  if ! git diff --cached --quiet; then
    git commit -m "Auto update $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1
    git push origin main >/dev/null 2>&1 && echo "pushed update" || echo "push failed"
  else
    echo "no changes to publish"
  fi
fi
