#!/bin/bash
# Scheduled Torvik snapshot refresh — fired by launchd (com.ncaaw.torvik).
# barttorvik.com blocks GitHub's servers, so fresh ratings can only come from
# a residential IP: this Mac. Pulls, fetches live ratings, pushes the shared
# snapshot CI grades from, then nudges a line sweep so grading picks it up.
# launchd runs missed calendar jobs when the Mac wakes from sleep.
set -e
cd /Users/scottgutwetter/.ncaaw-sharp
export PATH="/Users/scottgutwetter/miniforge3/bin:/usr/bin:/bin"
LOG=/tmp/ncaaw_torvik.log
{
  echo "--- $(date) ---"
  git pull --rebase --autostash origin main
  python3 - <<'EOF'
from datetime import datetime, timezone
from ncaaw_data_scraper import fetch_torvik, torvik_season_for, _save_json, DATA_ROOT, _ET
season = torvik_season_for(datetime.now(_ET).date())
teams = fetch_torvik(season)
_save_json(DATA_ROOT / "latest_teams.json", {
    "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "season": season, "teams": teams})
print(f"refreshed {len(teams)} teams (season {season})")
EOF
  git add ncaaw_data/latest_teams.json
  if git diff --staged --quiet; then
    echo "no rating changes"
  else
    git commit -m "torvik snapshot refresh (scheduled)"
    for i in 1 2 3; do
      git pull --rebase --autostash origin main && git push && break || sleep $((i*5))
    done
    # fresh ratings -> regrade the board now instead of waiting for the hour
    gh workflow run line-watch.yml --repo Scottguts/ncaaw-sharp --ref main || true
  fi
} >> "$LOG" 2>&1
