#!/usr/bin/env bash
# Printer twin launcher for Linux/macOS:  ./twin.sh /path/to/config   |   ./twin.sh --stop
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
name=printer-twin
port="${PORT:-8080}"
if [ "${1:-}" = "--stop" ]; then docker rm -f "$name" >/dev/null; echo "Twin stopped."; exit 0; fi
cfg="${1:?usage: twin.sh /path/to/config}"
[ -f "$cfg/printer.cfg" ] || { echo "No printer.cfg in $cfg"; exit 1; }
work="${XDG_DATA_HOME:-$HOME/.local/share}/printer-twin/config"
rm -rf "$work"; mkdir -p "$work"; cp -r "$cfg"/. "$work"/
docker build -f "$repo/twin/Dockerfile" -t "$name" "$repo"
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" -p "$port:80" -p 7125:7125 \
  -v "$work:/data/printer_data/config" -v printer-twin-gcodes:/data/printer_data/gcodes \
  -v printer-twin-cache:/data/cache "$name" >/dev/null
echo "Twin starting at http://localhost:$port (logs: docker logs -f $name)"
