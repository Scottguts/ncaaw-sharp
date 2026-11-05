#!/bin/bash
# Double-click to push a fresh Torvik ratings snapshot to GitHub.
# Why: barttorvik.com 403-blocks GitHub's runner IPs, so CI grades slates on
# the last committed snapshot (ncaaw_data/latest_teams.json). Running this
# from your Mac (residential IP) keeps that snapshot fresh. Ratings drift
# slowly — every day or two is plenty.
set -e
cd "$(dirname "$0")"
git pull --rebase --autostash origin main
python3 - <<'EOF'
from datetime import datetime, timezone
from ncaaw_data_scraper import fetch_torvik, torvik_season_for, _save_json, DATA_ROOT, _ET
season = torvik_season_for(datetime.now(_ET).date())
teams = fetch_torvik(season)
_save_json(DATA_ROOT / "latest_teams.json", {
    "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "season": season,
    "teams": teams,
})
print(f"refreshed {len(teams)} teams (season {season})")
EOF
git add ncaaw_data/latest_teams.json
if git diff --staged --quiet; then
  echo "No rating changes to push."
else
  git commit -m "refresh torvik snapshot"
  git push
  echo "Pushed fresh Torvik snapshot."
fi
read -n 1 -s -r -p "Done — press any key to close"
