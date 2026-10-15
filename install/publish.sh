#!/usr/bin/env bash
# Publishes what printers install from this repo, in the shapes KIAUH and
# Moonraker's update manager expect:
#   branch kalico     kalico/ at the root, on top of Kalico's rotation-support history
#   branch moonraker  moonraker/ at the root, on top of upstream Moonraker's history
#   release vX        web/mainsail zipped as mainsail.zip (needs GITHUB_TOKEN)
# Run from anywhere in the repo after committing:  install/publish.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

MOONRAKER_UPSTREAM=https://github.com/Arksine/moonraker.git
MOONRAKER_BASE=9e676eba6b02661a4dfa3ec6e7ac3f3504498e6d

git fetch -q origin
git cat-file -e "$MOONRAKER_BASE^{commit}" 2>/dev/null || git fetch -q "$MOONRAKER_UPSTREAM" "$MOONRAKER_BASE"

publish() { # publish <branch> <folder> <base commit>
  local branch=$1 dir=$2 base=$3 tree parent commit
  tree=$(git rev-parse "HEAD:$dir")
  parent=$(git rev-parse -q --verify "origin/$branch" || git rev-parse "$base")
  if [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
    echo "$branch: up to date"
    return
  fi
  commit=$(git commit-tree "$tree" -p "$parent" -m "$dir from $(git rev-parse --short HEAD)")
  git push -q origin "$commit:refs/heads/$branch"
  echo "$branch: $commit"
}

publish kalico kalico origin/rotation-support
publish moonraker moonraker "$MOONRAKER_BASE"

# Mainsail release for Moonraker's "web" updater
version=$(cat web/mainsail/.version)
if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "mainsail: set GITHUB_TOKEN to publish release $version"
  exit 0
fi
api=https://api.github.com/repos/Kanga2050/kalico6axis
auth=(-H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json")
if curl -sf "${auth[@]}" "$api/releases/tags/$version" >/dev/null; then
  echo "mainsail: release $version exists"
  exit 0
fi
py=$(command -v python3 || command -v python)
rm -rf .publish
zip=.publish/mainsail
"$py" -c 'import shutil,sys; shutil.make_archive(sys.argv[1], "zip", sys.argv[2])' "$zip" web/mainsail
zip="$zip.zip"
upload=$(curl -sf "${auth[@]}" -X POST "$api/releases" \
  -d "{\"tag_name\":\"$version\",\"target_commitish\":\"$(git rev-parse HEAD)\",\"name\":\"Mainsail $version\",\"body\":\"Mainsail with the 3D printer model and rotary controls.\"}" \
  | "$py" -c 'import json,sys; print(json.load(sys.stdin)["upload_url"].split("{")[0])')
curl -sf "${auth[@]}" -H "Content-Type: application/zip" --data-binary "@$zip" "$upload?name=mainsail.zip" >/dev/null
rm -rf .publish
echo "mainsail: released $version"
