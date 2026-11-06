#!/usr/bin/env bash
#
# UAEG uninstaller. Removes the service, unit and application files.
# Keeps /etc/uaeg and /var/lib/uaeg (config, keys, job output, audit logs)
# unless --purge is given.
#
set -euo pipefail

SERVICE_NAME="uaeg"
INSTALL_DIR="/opt/uaeg"
CONFIG_DIR="/etc/uaeg"
DATA_DIR="/var/lib/uaeg"
PURGE=0

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge)          PURGE=1; shift ;;
    --install-dir)    INSTALL_DIR="$2"; shift 2 ;;
    --config-dir)     CONFIG_DIR="$2"; shift 2 ;;
    --data-dir)       DATA_DIR="$2"; shift 2 ;;
    -h|--help)
      cat <<'USAGE'
UAEG uninstaller

Options:
  --purge            also remove config and data (keys, logs, job output)
  --install-dir PATH [/opt/uaeg]
  --config-dir PATH  [/etc/uaeg]
  --data-dir PATH    [/var/lib/uaeg]
USAGE
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo "$0" "$@"
  fi
  echo "must run as root" >&2
  exit 1
fi

say "stopping and removing the UAEG service"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload || true

say "removing application files: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
rm -f /usr/local/bin/uaeg

# Kill any background jobs started by UAEG? They are detached by design;
# leave them running (they may be intentional long-lived processes).
warn "detached background jobs (if any) keep running; inspect $DATA_DIR/jobs"

if [[ $PURGE -eq 1 ]]; then
  say "purging configuration and data"
  rm -rf "$CONFIG_DIR" "$DATA_DIR"
else
  say "kept for your records (use --purge to remove):"
  echo "  $CONFIG_DIR"
  echo "  $DATA_DIR"
fi

say "UAEG uninstalled"
