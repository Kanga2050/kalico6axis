#!/usr/bin/env bash
# Installs the parts of this repo on a Klipper Pi that already runs a
# KIAUH-style setup (~/klipper, ~/moonraker, ~/mainsail).
# Each part is optional:  ./install.sh kalico moonraker mainsail
# Existing folders are moved to <name>.orig-<date> first, never deleted.
set -euo pipefail

STACK="${STACK:-$HOME/printer-stack}"
REPO="${REPO:-https://github.com/Kanga2050/kalico6axis.git}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PARTS=("$@")
[ ${#PARTS[@]} -eq 0 ] && PARTS=(kalico mainsail)

if [ ! -d "$STACK/.git" ]; then
  git clone --depth 1 "$REPO" "$STACK"
else
  git -C "$STACK" pull --ff-only
fi

swap() { # swap <target> <source>
  local target="$1" src="$2"
  if [ -L "$target" ]; then rm "$target"
  elif [ -e "$target" ]; then mv "$target" "$target.orig-$STAMP"; echo "kept old $target as $target.orig-$STAMP"; fi
  ln -s "$src" "$target"
  echo "$target -> $src"
}

for part in "${PARTS[@]}"; do
  case "$part" in
    kalico)
      swap "$HOME/klipper" "$STACK/kalico"
      "$HOME/klippy-env/bin/pip" install -q -r "$STACK/kalico/scripts/klippy-requirements.txt"
      sudo systemctl restart klipper ;;
    moonraker)
      swap "$HOME/moonraker" "$STACK/moonraker"
      "$HOME/moonraker-env/bin/pip" install -q -r "$STACK/moonraker/scripts/moonraker-requirements.txt"
      sudo systemctl restart moonraker ;;
    mainsail)
      swap "$HOME/mainsail" "$STACK/web/mainsail" ;;
    *) echo "unknown part $part"; exit 1 ;;
  esac
done
echo "Done."
