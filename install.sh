#!/usr/bin/env bash
#
# UAEG - Universal Agent Execution Gateway installer (PRD FR-001).
#
# Usage:
#   git clone https://github.com/smahud/uaeg.git
#   cd uaeg
#   sudo ./install.sh --domain gw.example.com           # Let's Encrypt TLS
#   sudo ./install.sh --cert fullchain.pem --key priv.pem  # operator certs
#   sudo ./install.sh --self-signed                     # LAN/experimental
#
set -euo pipefail

# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------
MCP_PORT=3902
MCP_PORT_GIVEN=0
DASHBOARD_PORT=3903
DASHBOARD_PORT_GIVEN=0
DASHBOARD_BIND="loopback"     # loopback | lan | public | <ip>
DASHBOARD_BIND_GIVEN=0
DASHBOARD_PUBLIC=0
DOMAIN=""
PUBLIC_IP=""
PUBLIC_HOST=""               # resolved later; "" = keep existing on upgrades
PUBLIC_HOST_GIVEN=0
CERT_FILE=""
KEY_FILE=""
TLS_MODE="self-signed"
TLS_MODE_GIVEN=0
DATA_DIR="/var/lib/uaeg"
CONFIG_DIR="/etc/uaeg"
INSTALL_DIR="/opt/uaeg"
SERVICE_NAME="uaeg"
NO_SERVICE=0
ASSUME_YES=0
OPEN_FIREWALL=0
UPGRADING=0
SERVICE_WAS_ACTIVE=0
APP_BACKUP=""
CONFIG_BACKUP=""
# Release this installer belongs to (used for standalone bootstrap, FR-002).
# Override the source version at runtime with: --version X.Y.Z or UAEG_VERSION=X.Y.Z
UAEG_RELEASE_VERSION="0.3.2"
FETCH_VERSION=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
UAEG installer

Options:
  --port N               MCP (public HTTPS) port                    [3902]
  --dashboard-port N     dashboard port                             [3903]
  --dashboard-bind X     loopback | lan | public | <ip>            [loopback]
                           'public' binds 0.0.0.0 with HTTPS + password login
                           (requires 'sudo uaeg pass' to have been run first)
  --domain DOMAIN        public DNS name; provisions Let's Encrypt via certbot
  --public-ip IP         public IP address (for display / self-signed SAN)
  --cert FILE --key FILE operator-provided TLS certificate (manual mode)
  --self-signed          generate a self-signed certificate (default)
  --data-dir PATH        persistent data directory                  [/var/lib/uaeg]
  --config-dir PATH      configuration directory                    [/etc/uaeg]
  --install-dir PATH     application install directory              [/opt/uaeg]
  --no-service           install without registering systemd (containers/tests)
  --open-firewall        open the MCP (and public dashboard, if any) port in ufw
  --version X.Y.Z        standalone mode: install this release tag (default: $UAEG_RELEASE_VERSION)
  --yes                  assume yes for all prompts (unattended)
  -h, --help             show this help
USAGE
}

# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)             MCP_PORT="$2"; MCP_PORT_GIVEN=1; shift 2 ;;
    --dashboard-port)   DASHBOARD_PORT="$2"; DASHBOARD_PORT_GIVEN=1; shift 2 ;;
    --dashboard-bind)   DASHBOARD_BIND="$2"; DASHBOARD_BIND_GIVEN=1; shift 2 ;;
    --domain)           DOMAIN="$2"; TLS_MODE="certbot"; TLS_MODE_GIVEN=1; PUBLIC_HOST_GIVEN=1; shift 2 ;;
    --public-ip)        PUBLIC_IP="$2"; PUBLIC_HOST_GIVEN=1; shift 2 ;;
    --cert)             CERT_FILE="$2"; TLS_MODE="manual"; TLS_MODE_GIVEN=1; shift 2 ;;
    --key)              KEY_FILE="$2"; TLS_MODE="manual"; TLS_MODE_GIVEN=1; shift 2 ;;
    --self-signed)      TLS_MODE="self-signed"; TLS_MODE_GIVEN=1; shift ;;
    --data-dir)         DATA_DIR="$2"; shift 2 ;;
    --config-dir)       CONFIG_DIR="$2"; shift 2 ;;
    --install-dir)      INSTALL_DIR="$2"; shift 2 ;;
    --no-service)       NO_SERVICE=1; shift ;;
    --open-firewall)    OPEN_FIREWALL=1; shift ;;
    --yes|-y)           ASSUME_YES=1; shift ;;
    --version)          FETCH_VERSION="$2"; shift 2 ;;
    -h|--help)          usage; exit 0 ;;
    *)                  die "unknown option: $1 (see --help)" ;;
  esac
done

# ----------------------------------------------------------------------
# Root + OS + prerequisites
# ----------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    say "re-running as root via sudo..."
    exec sudo "$0" "$@"
  fi
  die "this installer must run as root (try: sudo ./install.sh)"
fi

say "UAEG installer starting"
if [[ ! -f "$SCRIPT_DIR/pyproject.toml" ]]; then
  # Standalone bootstrap (FR-002): this script was downloaded on its own
  # (checksum-verified by the operator); fetch the matching release source.
  VERSION="${FETCH_VERSION:-${UAEG_VERSION:-$UAEG_RELEASE_VERSION}}"
  say "standalone installer: fetching UAEG v${VERSION} source from GitHub"
  command -v curl >/dev/null 2>&1 || die "curl is required for standalone installation"
  TMP_SRC="$(mktemp -d /tmp/uaeg-bootstrap.XXXXXX)"
  TARBALL_URL="https://github.com/smahud/uaeg/archive/refs/tags/v${VERSION#v}.tar.gz"
  curl -fsSL "$TARBALL_URL" | tar -C "$TMP_SRC" -xz
  SCRIPT_DIR="$(echo "$TMP_SRC"/uaeg-*)"
  [[ -f "$SCRIPT_DIR/pyproject.toml" ]] || die "downloaded release archive is missing pyproject.toml"
  say "source ready: $SCRIPT_DIR"
fi

# OS check: Debian 12/13 supported; others need explicit confirmation.
SUPPORTED_OS=1
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "1"[23] ]]; then
    SUPPORTED_OS=0
  fi
else
  SUPPORTED_OS=0
fi
if [[ $SUPPORTED_OS -eq 0 ]]; then
  warn "unsupported or unrecognized OS (expected Debian 12/13): ${PRETTY_NAME:-unknown}"
  if [[ $ASSUME_YES -eq 1 ]]; then
    warn "continuing anyway (--yes)"
  else
    read -r -p "Continue anyway? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || die "aborted"
  fi
fi

command -v python3 >/dev/null 2>&1 || die "python3 not found"
PY_VERSION_OK=$(python3 - <<'PY'
import sys
print(1 if sys.version_info >= (3, 11) else 0)
PY
)
[[ "$PY_VERSION_OK" == "1" ]] || die "python3 >= 3.11 required (found $(python3 --version))"

say "installing system dependencies (python3-venv, openssl, curl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv openssl curl >/dev/null

# ----------------------------------------------------------------------
# Application installation (with upgrade rollback)
# ----------------------------------------------------------------------
if [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/.uaeg-installed" ]]; then
  UPGRADING=1
  say "existing installation found at $INSTALL_DIR - upgrading in place"
  if [[ $NO_SERVICE -eq 0 ]] && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    SERVICE_WAS_ACTIVE=1
    systemctl stop "$SERVICE_NAME" || true
  fi
fi

CONFIG_FILE="$CONFIG_DIR/config.json"

# Rollback on failure: an interrupted upgrade must never leave the VPS with
# a stopped service, a half-replaced application or a reset configuration.
rollback() {
  local status=$?
  if [[ $status -ne 0 && $UPGRADING -eq 1 ]]; then
    warn "installation failed - rolling back to the previous working state"
    if [[ -n "$CONFIG_BACKUP" && -f "$CONFIG_BACKUP" && -f "$CONFIG_FILE" ]]; then
      if cp "$CONFIG_BACKUP" "$CONFIG_FILE"; then
        say "restored previous configuration from $CONFIG_BACKUP"
      else
        warn "could not restore the configuration backup - restore manually from $CONFIG_BACKUP"
      fi
    fi
    if [[ -n "$APP_BACKUP" && -d "$APP_BACKUP" ]]; then
      rm -rf "$INSTALL_DIR"
      if mv "$APP_BACKUP" "$INSTALL_DIR"; then
        say "restored previous application files"
      else
        warn "could not restore application backup - recover manually from $APP_BACKUP"
      fi
    fi
    if [[ $SERVICE_WAS_ACTIVE -eq 1 && $NO_SERVICE -eq 0 ]]; then
      systemctl restart "$SERVICE_NAME" \
        || warn "could not restart the previous service - start it manually: systemctl start $SERVICE_NAME"
    fi
  fi
  exit $status
}
trap rollback EXIT

if [[ $UPGRADING -eq 1 ]]; then
  APP_BACKUP="${INSTALL_DIR}.upgrade-bak-$(date +%Y%m%d-%H%M%S)"
  say "backing up the current application (rollback safety): $APP_BACKUP"
  cp -a "$INSTALL_DIR" "$APP_BACKUP"
fi

say "installing application to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
tar -C "$SCRIPT_DIR" -cf - \
  --exclude=.git --exclude=.venv --exclude='*.egg-info' \
  --exclude=__pycache__ --exclude=.pytest_cache --exclude=dist --exclude=build \
  . | tar -C "$INSTALL_DIR" -xf -
touch "$INSTALL_DIR/.uaeg-installed"
chmod -R go-w "$INSTALL_DIR"

VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  say "creating virtualenv"
  python3 -m venv "$VENV_DIR"
fi
say "installing python dependencies (pinned: mcp/starlette/uvicorn)"
"$VENV_DIR/bin/pip" install --upgrade -q pip setuptools wheel
"$VENV_DIR/bin/pip" install -q --no-build-isolation -e "$INSTALL_DIR"

ln -sf "$VENV_DIR/bin/uaeg" /usr/local/bin/uaeg

# ----------------------------------------------------------------------
# Directories + dashboard bind address
# ----------------------------------------------------------------------
mkdir -p "$CONFIG_DIR"
chmod 0750 "$CONFIG_DIR"
mkdir -p "$DATA_DIR"
chmod 0700 "$DATA_DIR"
mkdir -p "$DATA_DIR/jobs" "$DATA_DIR/sessions" "$DATA_DIR/tls"

resolve_dashboard_bind() {
  case "$DASHBOARD_BIND" in
    loopback|127.0.0.1|localhost)
      echo "127.0.0.1" ;;
    public|0.0.0.0|::)
      DASHBOARD_PUBLIC=1
      echo "0.0.0.0" ;;
    lan)
      # Prefer a proper private (RFC1918) address; "lan" must never
      # silently bind the dashboard to a public interface.
      local ip
      ip=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 \
           | while read -r a; do
               case "$a" in
                 10.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.168.*) echo "$a"; break ;;
               esac
             done)
      [[ -z "$ip" ]] && ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
      [[ -z "$ip" ]] && ip=$(hostname -I 2>/dev/null | awk '{print $1}')
      [[ -n "$ip" ]] || die "could not detect a LAN address; use --dashboard-bind <ip> or loopback"
      if ! "$VENV_DIR/bin/python" -c "import ipaddress,sys; sys.exit(0 if ipaddress.ip_address(sys.argv[1]).is_private else 1)" "$ip" >/dev/null 2>&1; then
        warn "no private (RFC1918) address was found on this host."
        warn "The only detected address is '$ip', which appears to be PUBLIC."
        warn "For a public dashboard use: --dashboard-bind public (requires 'sudo uaeg pass' + TLS)."
        die "use --dashboard-bind loopback, --dashboard-bind public, or --dashboard-bind <private-ip>"
      fi
      echo "$ip" ;;
    *)
      echo "$DASHBOARD_BIND" ;;
  esac
}
DASH_BIND_IP="$(resolve_dashboard_bind)"

# ----------------------------------------------------------------------
# Public dashboard requires a password BEFORE anything is reconfigured
# ----------------------------------------------------------------------
if [[ $DASHBOARD_PUBLIC -eq 1 ]]; then
  if ! UAEG_DATA_DIR="$DATA_DIR" "$VENV_DIR/bin/python" -c \
      "from uaeg import dashauth; import sys; sys.exit(0 if dashauth.password_is_configured() else 1)" >/dev/null 2>&1; then
    die "public dashboard requested (--dashboard-bind public) but no dashboard password is
configured. For security the installer refuses to expose an unauthenticated dashboard.

  Step 1: run  sudo uaeg pass   interactively on this server
  Step 2: re-run this installer with --dashboard-bind public

The password is stored as a salted scrypt hash and is separate from the MCP
agent keys (which are not affected)."
  fi
  say "public dashboard: password verified, enabling HTTPS + password login"
fi

# ----------------------------------------------------------------------
# Public host
# ----------------------------------------------------------------------
if [[ $UPGRADING -eq 1 && $PUBLIC_HOST_GIVEN -eq 0 && $TLS_MODE_GIVEN -eq 0 ]]; then
  say "upgrade: keeping the existing public_host and TLS configuration"
else
  PUBLIC_HOST="${DOMAIN:-$PUBLIC_IP}"
  if [[ -z "$PUBLIC_HOST" ]]; then
    # best-effort: primary interface address
    PUBLIC_HOST=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
    [[ -n "$PUBLIC_HOST" ]] || PUBLIC_HOST="localhost"
    warn "no --domain/--public-ip given; using detected address '$PUBLIC_HOST' (self-signed TLS only)"
    TLS_MODE="self-signed"
  fi
fi

# ----------------------------------------------------------------------
# TLS provisioning
# ----------------------------------------------------------------------
RUN_TLS_PROVISION=0
if [[ $UPGRADING -eq 0 || $TLS_MODE_GIVEN -eq 1 ]]; then
  RUN_TLS_PROVISION=1
fi

if [[ $RUN_TLS_PROVISION -eq 1 ]]; then
if [[ "$TLS_MODE" == "certbot" ]]; then
  say "provisioning Let's Encrypt certificate for $DOMAIN (requires open port 80)"
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE '(:|\])80$'; then
    die "port 80 is already in use; free it (or stop the listener) and re-run, or use --cert/--key"
  fi
  apt-get install -y -qq certbot >/dev/null
  if certbot certonly --standalone --non-interactive --agree-tos \
       --register-unsafely-without-email -d "$DOMAIN"; then
    say "certificate issued: /etc/letsencrypt/live/$DOMAIN/"
    # restart UAEG on renewal
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/uaeg.sh <<EOF
#!/bin/sh
# Reload UAEG with the renewed certificate (written by install.sh)
systemctl restart uaeg || true
EOF
    chmod +x /etc/letsencrypt/renewal-hooks/deploy/uaeg.sh
  else
    die "certbot failed to issue a certificate for $DOMAIN. Check DNS A record and port 80 reachability, then re-run. (Alternative: --cert/--key with your own certificate, or --self-signed for LAN-only testing.)"
  fi
elif [[ "$TLS_MODE" == "manual" ]]; then
  [[ -n "$CERT_FILE" && -n "$KEY_FILE" ]] || die "manual TLS requires both --cert and --key"
  [[ -f "$CERT_FILE" ]] || die "certificate file not found: $CERT_FILE"
  [[ -f "$KEY_FILE" ]] || die "key file not found: $KEY_FILE"
  openssl x509 -in "$CERT_FILE" -noout >/dev/null 2>&1 || die "cannot parse certificate: $CERT_FILE"
  openssl pkey -in "$KEY_FILE" -noout >/dev/null 2>&1 || die "cannot parse key: $KEY_FILE"
  say "using operator-provided certificate: $CERT_FILE"
else
  # self-signed (generated now, reused later by the server)
  CERT_OUT="$DATA_DIR/tls/self-signed.crt"
  KEY_OUT="$DATA_DIR/tls/self-signed.key"
  if [[ ! -f "$CERT_OUT" || ! -f "$KEY_OUT" ]]; then
    say "generating self-signed certificate for $PUBLIC_HOST (NOT trusted by hosted clients)"
    openssl req -x509 -newkey rsa:2048 -sha256 -days 397 -nodes \
      -subj "/CN=$PUBLIC_HOST" \
      -addext "subjectAltName=DNS:$PUBLIC_HOST,IP:$PUBLIC_HOST" \
      -keyout "$KEY_OUT" -out "$CERT_OUT" >/dev/null 2>&1 \
      || openssl req -x509 -newkey rsa:2048 -sha256 -days 397 -nodes \
           -subj "/CN=$PUBLIC_HOST" -keyout "$KEY_OUT" -out "$CERT_OUT" >/dev/null
    chmod 0600 "$KEY_OUT"
  fi
fi
fi  # end RUN_TLS_PROVISION

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
# Fresh install: write a new config from the installer flags.
# Upgrade: PRESERVE the existing configuration (FR-003) and apply ONLY the
# values explicitly passed on this invocation's flags; everything the
# operator changed with `uaeg config set` or the dashboard survives.
# ----------------------------------------------------------------------
if [[ -f "$CONFIG_FILE" ]]; then
  CONFIG_BACKUP="$CONFIG_DIR/config.json.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG_FILE" "$CONFIG_BACKUP"
  say "existing configuration found - backing up to $CONFIG_BACKUP"
fi
CONFIG_EXISTS=0
[[ -f "$CONFIG_FILE" ]] && CONFIG_EXISTS=1

say "writing configuration: $CONFIG_FILE"
UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" "$VENV_DIR/bin/python" - "$CONFIG_FILE" \
  "$CONFIG_EXISTS" "$PUBLIC_HOST" "$MCP_PORT" "$MCP_PORT_GIVEN" \
  "$DASHBOARD_PORT" "$DASHBOARD_PORT_GIVEN" "$DASH_BIND_IP" "$DASHBOARD_BIND_GIVEN" \
  "$TLS_MODE" "$TLS_MODE_GIVEN" "$CERT_FILE" "$KEY_FILE" "$PUBLIC_HOST_GIVEN" \
  "$DASHBOARD_PUBLIC" <<'PY' \
  || die "configuration could not be written - see the error above"
import sys

from uaeg.config import Config, ConfigError

(
    config_file, config_exists, public_host,
    mcp_port, mcp_port_given,
    dash_port, dash_port_given,
    dash_bind, dash_bind_given,
    tls_mode, tls_mode_given, cert_file, key_file,
    public_host_given, dash_public,
) = sys.argv[1:16]

if config_exists == "1":
    try:
        cfg = Config.load(config_file)  # operator's values survive upgrades
    except ConfigError as exc:
        print(f"CONFIG_ERROR: existing configuration is invalid: {exc}", file=sys.stderr)
        sys.exit(9)
else:
    cfg = Config()

if mcp_port_given == "1":
    cfg.mcp.port = int(mcp_port)
if dash_port_given == "1":
    cfg.dashboard.port = int(dash_port)
if dash_bind_given == "1":
    cfg.dashboard.bind = dash_bind
if dash_public == "1":
    # Explicit public dashboard opt-in: password was verified by the
    # installer before reaching here; TLS is mandatory for public binds.
    cfg.dashboard.allow_public = True
    cfg.dashboard.tls = True
if tls_mode_given == "1":
    cfg.tls.mode = tls_mode
    cfg.tls.cert = cert_file or None
    cfg.tls.key = key_file or None
if public_host_given == "1" and public_host:
    cfg.public_host = public_host
elif config_exists == "0" and public_host:
    # fresh install: the detected/explicit host is the initial value
    cfg.public_host = public_host

try:
    warnings = cfg.validate()
except ConfigError as exc:
    print(f"CONFIG_ERROR: {exc}", file=sys.stderr)
    sys.exit(9)
cfg.save(config_file)
for w in warnings:
    print(f"config warning: {w}", file=sys.stderr)
print(f"config written ({'preserved existing settings' if config_exists == '1' else 'fresh install'})")
PY

# ----------------------------------------------------------------------
# systemd service
# ----------------------------------------------------------------------
if [[ $NO_SERVICE -eq 1 ]]; then
  warn "--no-service: skipping systemd registration (start manually with: uaeg serve)"
else
  say "installing systemd service"
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=UAEG - Universal Agent Execution Gateway
Documentation=https://github.com/smahud/uaeg
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# UAEG intentionally runs as root: authenticated agents receive full native
# OS execution by design (PRD FR-010, FR-013). Do not harden-sandbox this
# unit - that would break the product's core capability.
User=root
ExecStart=$VENV_DIR/bin/uaeg serve
Environment=UAEG_CONFIG=$CONFIG_FILE
Environment=UAEG_DATA_DIR=$DATA_DIR
Restart=on-failure
RestartSec=2
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null
  systemctl restart "$SERVICE_NAME"
fi

# ----------------------------------------------------------------------
# Health verification
# ----------------------------------------------------------------------
# Use the EFFECTIVE values from the (possibly preserved) configuration -
# not the installer defaults - so upgrades of customized installs verify
# the real listener.
EFFECTIVE_PORTS="$($VENV_DIR/bin/python - "$CONFIG_FILE" <<'PY'
import json, sys

cfg = json.load(open(sys.argv[1]))
print(cfg["mcp"]["port"])
print(cfg["dashboard"]["port"])
print(cfg.get("public_host", ""))
PY
)" || die "could not read the effective configuration"
EFFECTIVE_MCP_PORT="$(echo "$EFFECTIVE_PORTS" | sed -n 1p)"
EFFECTIVE_DASH_PORT="$(echo "$EFFECTIVE_PORTS" | sed -n 2p)"
EFFECTIVE_HOST="$(echo "$EFFECTIVE_PORTS" | sed -n 3p)"

if [[ $NO_SERVICE -eq 0 ]]; then
  say "verifying service health"
  HEALTH_OK=0
  for _ in $(seq 1 30); do
    if curl -sk --max-time 2 "https://127.0.0.1:${EFFECTIVE_MCP_PORT}/healthz" | grep -q '"ok"'; then
      HEALTH_OK=1
      break
    fi
    sleep 1
  done
  if [[ $HEALTH_OK -eq 0 ]]; then
    warn "service did not become healthy in 30s"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    die "installation incomplete - rolling back; inspect the logs above, fix the cause and re-run"
  fi
  say "health check passed"
fi

# Upgrade completed successfully: drop the rollback backup.
if [[ -n "$APP_BACKUP" && -d "$APP_BACKUP" ]]; then
  say "upgrade verified - removing rollback backup $APP_BACKUP"
  rm -rf "$APP_BACKUP"
  APP_BACKUP=""
fi

# ----------------------------------------------------------------------
# Firewall hints
# ----------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  if [[ $OPEN_FIREWALL -eq 1 ]]; then
    say "opening MCP port $EFFECTIVE_MCP_PORT/tcp in ufw"
    ufw allow "$EFFECTIVE_MCP_PORT/tcp"
    if [[ "$DASH_BIND_IP" == "0.0.0.0" ]]; then
      say "opening public dashboard port $EFFECTIVE_DASH_PORT/tcp in ufw"
      ufw allow "$EFFECTIVE_DASH_PORT/tcp"
    fi
  else
    warn "ufw is active. Allow the MCP port yourself, e.g.:"
    warn "    ufw allow ${EFFECTIVE_MCP_PORT}/tcp"
    if [[ "$DASH_BIND_IP" == "0.0.0.0" ]]; then
      warn "The dashboard is public - also allow its port:"
      warn "    ufw allow ${EFFECTIVE_DASH_PORT}/tcp"
    else
      warn "(the dashboard is LAN/loopback-private - do NOT open ${EFFECTIVE_DASH_PORT})"
    fi
  fi
fi

# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
echo
printf '\033[1mUAEG installed successfully.\033[0m\n'
echo
if [[ "$DASH_BIND_IP" == "0.0.0.0" ]]; then
  echo "  Dashboard    : https://${EFFECTIVE_HOST}:${EFFECTIVE_DASH_PORT} (public - password login + HTTPS)"
else
  echo "  Dashboard    : http://${DASH_BIND_IP}:${EFFECTIVE_DASH_PORT} (LAN/loopback only)"
fi
echo "  MCP endpoint : https://${EFFECTIVE_HOST}:${EFFECTIVE_MCP_PORT}/mcp"
echo "  Config       : $CONFIG_FILE"
echo "  Data         : $DATA_DIR"
echo "  TLS mode     : $TLS_MODE"
[[ $NO_SERVICE -eq 0 ]] && echo "  Service      : systemctl status ${SERVICE_NAME}"
echo
echo "Next steps:"
if [[ "$DASH_BIND_IP" != "0.0.0.0" ]]; then
  echo "  0. (optional) enable dashboard login + remote access:"
  echo "       sudo uaeg pass"
  echo "       sudo ./install.sh --dashboard-bind public  # re-run with your TLS flags"
fi
echo "  1. Create an agent key:"
echo "       uaeg generate --name \"Claude\""
echo "  2. Configure your hosted agent's MCP connector:"
echo "       URL:                  https://${EFFECTIVE_HOST}:${EFFECTIVE_MCP_PORT}/mcp"
echo "       Authentication:       Bearer token (the generated uaeg_... secret)"
echo "  3. Verify health:"
echo "       uaeg doctor"
echo
[[ "$TLS_MODE" == "self-signed" ]] && warn "self-signed TLS: most hosted platforms will reject this certificate. Use --domain (Let's Encrypt) for real agent connections."
echo "Docs: https://github.com/smahud/uaeg/tree/main/docs"
