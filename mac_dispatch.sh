#!/bin/bash
# Mac-side trigger backstop — fired by launchd (com.ncaaw.dispatch) because
# GitHub's scheduler drops the repo's crons. Uses the Mac's existing gh auth;
# precheck/dedupe on the receiving side makes repeat dispatches free no-ops.
# launchd runs missed calendar jobs when the Mac wakes from sleep.
GH=/Users/scottgutwetter/miniforge3/bin/gh
LOG=/tmp/ncaaw_dispatch.log
{
  echo "--- $(date) ---"
  $GH workflow run line-watch.yml --repo Scottguts/ncaaw-sharp --ref main 2>&1
  # daily card window: 11:30am-1:30pm ET
  H=$(date +%H)
  if [ "$H" -ge 11 ] && [ "$H" -le 13 ]; then
    $GH workflow run daily-bets.yml --repo Scottguts/ncaaw-sharp --ref main 2>&1
  fi
} >> "$LOG" 2>&1
