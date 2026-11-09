#!/usr/bin/env bash
#
# UAEG - Universal Agent Execution Gateway unified installer (PRD FR-001/FR-002).
#
# Since v0.4.0 this ONE file is the public installation AND upgrade entrypoint
# (docs/UPGRADE_CONTRACT.md, contract v4 / docs/INSTALL.md). Download it ONCE
# and reuse the same local file forever:
#
#   sudo bash install.sh                  # fresh install if absent,
#                                         # SAFE UPGRADE to the latest release
#                                         # if already installed (signature-
#                                         # verified bootstrap -> updater)
#   sudo bash install.sh --check          # status + available update
#   sudo bash install.sh --dry-run        # preview, change nothing
#   sudo bash install.sh --rollback       # restore the latest upgrade backup
#   sudo bash install.sh --repair         # re-run the installer in place,
#                                         # persistent data is preserved
#   sudo bash install.sh --start|--stop|--restart|--status
#   sudo bash install.sh --version        # print this file's version
#   sudo bash install.sh --version X.Y.Z  # pin the target release (fresh
#                                         # install tag or upgrade target)
#
# First-install flags (--port, --domain, --cert/--key, --dashboard-bind, ...)
# are all preserved; on an existing installation passing any of them runs the
# IN-PLACE installer flow (reconfiguration, v0.3.x behavior) - the verified
# safe-upgrade path is selected by the BARE invocation (optionally --yes and
# --version X.Y.Z) and by the commands above.
#
# The safe-upgrade path reuses the permanent upgrade architecture (contract
# v3/v4, docs/BOOTSTRAP.md): resolve the target release -> download the signed
# manifest -> verify the Ed25519 signature against the trusted keyring
# embedded below -> download the versioned updater -> verify sha256+size
# against the signed manifest -> execute it (detect -> backup -> isolated
# staging -> install -> verify zero data loss -> auto-rollback). The
# bootstrap logic embedded here speaks the SAME binding interface (v1) as
# upgrade.sh, which remains available as a standalone asset for operators
# who downloaded it in the past.
#
# Usage (fresh install):
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
# Which TLS SOURCE the operator selected on the command line (exactly one
# per invocation - enforced after parsing, before any mutation):
TLS_SRC_DOMAIN=0        # --domain        (certbot)
TLS_SRC_MANUAL=0        # --cert / --key  (manual)
TLS_SRC_SELFSIGNED=0    # --self-signed
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
UAEG_RELEASE_VERSION="0.4.1.zaudit1"
FETCH_VERSION=""

# ----------------------------------------------------------------------
# Unified-installer state (contract v4)
# ----------------------------------------------------------------------
MODE_CMD=""                  # "" (install/upgrade) | start|stop|restart|status |
                             # check|dry-run|rollback|repair
PRINT_VERSION_ONLY=0
FORCE_INPLACE=0              # --repair (and the updater-invoked in-place flow)
BARE_INVOCATION=1            # 1 = only --yes/--version were given (safe-upgrade
                             # eligible on an existing installation)
CONFIG_FLAGS_GIVEN=0         # any first-install configuration flag was given
TARGETING_FLAGS_GIVEN=0      # any --install-dir/--data-dir/... flag was given

# Optional rollback backup ID (--rollback [ID])
ROLLBACK_ID=""

# Bootstrap identity of THIS file (docs/BOOTSTRAP.md). The embedded
# bootstrap speaks updater interface v1 - identical to upgrade.sh.
INSTALLER_INTERFACE="1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_FILE="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SELF_SHA_BEFORE="$(sha256sum "$SELF_FILE" 2>/dev/null | awk '{print $1}' || echo unknown)"

# Release channel (env overrides exist for mirrors and the contract test
# suite; signature verification applies UNCONDITIONALLY).
RELEASE_API_BASE="${UAEG_RELEASE_API_BASE:-https://api.github.com/repos/smahud/uaeg}"
RELEASE_DOWNLOAD_BASE="${UAEG_RELEASE_DOWNLOAD_BASE:-https://github.com/smahud/uaeg/releases/download}"
# "latest" asset alias (docs/BOOTSTRAP.md): .../releases/latest/download/<asset>.
# Derived from the download base so mirrors keep working; v0.4.1 fixes the
# v0.4.0 recovery hints that pointed at the non-existent
# .../releases/download/latest-mismatch/... path.
RELEASE_LATEST_BASE="${RELEASE_DOWNLOAD_BASE%/download}/latest/download"

# System paths (env-overridable so isolated tests can redirect them).
SYSTEM_UAEG_BIN="${UAEG_SYSTEM_UAEG_BIN:-/usr/local/bin/uaeg}"

# ----------------------------------------------------------------------
# Trusted release-signing public keys (Ed25519, PEM) - used by the embedded
# safe-upgrade bootstrap below. Key rotation policy: docs/BOOTSTRAP.md.
# Adding a key here is an exceptional, operator-visible bootstrap refresh -
# never silent.
# --- TRUSTED KEYRING BEGIN (tests substitute the key between these markers) ---
_TRUSTED_KEY_1="-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAGnV8BEK2kZMk8JER3qe+Azc2Qidn2+c1tn4LSsCB5r4=
-----END PUBLIC KEY-----"
_TRUSTED_KEY_2="-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAS6XMyzmiARYxhkDoBsMZdNE1SXNtyGCNJV6R+Uvznq4=
-----END PUBLIC KEY-----"
_TRUSTED_KEY_3="-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEABBIDBLITvWPB+YC0iDMYvVi3r/0gqdbaAGhy8CLuC/Q=
-----END PUBLIC KEY-----"
_TRUSTED_KEY_4="-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA2Ei9ZnJs97L3KzRQwjdASwGbp/ooWSr9retQyZj7qcU=
-----END PUBLIC KEY-----"
# ---- TRUSTED KEYRING END ----

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  { local _rc="${2:-1}"; printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit "$_rc"; }

# confirm_ask <prompt> — read the operator's y/N answer.
#
# The answer MUST come from the operator's KEYBOARD, never from the script's
# stdin. The installer can execute with stdin already consumed or detached
# (`curl ... | bash`, `bash install.sh < /dev/null`, closed stdin under
# sudo/CI); a plain `read` then returns EOF or script text while the typed
# "y" never arrives — previously misreported as a plain "aborted", or a
# silent `set -e` death with no message at all.
#   1. stdin is a terminal  -> read stdin (normal interactive run)
#   2. /dev/tty available   -> read the controlling terminal (the fix)
#   3. neither              -> read one line from stdin (piped automation)
# Returns: 0 = explicit yes (y/yes, any case); 1 = explicit no / empty line;
#          2 = NO answer at all — callers must report that truthfully.
confirm_ask() {
  local prompt="$1" reply="" rrc=0
  if [[ -t 0 ]]; then
    read -r -p "$prompt" reply || rrc=2
  elif (exec 9<>/dev/tty) 2>/dev/null; then
    # Controlling terminal exists (curl|bash, detached stdin, sudo): read
    # the operator's keyboard from /dev/tty. The open was probed in a
    # subshell above, so this read needs no stderr suppression and `read
    # -p` displays the prompt normally on the real stderr.
    read -r -p "$prompt" reply < /dev/tty || rrc=2
  else
    if read -r reply; then
      printf '%s%s\n' "$prompt" "$reply"
    else
      rrc=2
      printf '%s\n' "$prompt"
    fi
  fi
  [[ $rrc -ne 0 ]] && return 2
  reply="${reply//[[:space:]]/}"
  if [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]; then
    return 0
  fi
  return 1
}

ver_gt() {  # true when $1 > $2 (semver-ish); equal is NOT greater
  [[ "$1" != "$2" && "$(printf '%s\n' "$1" "$2" | sort -V | sed -n 2p)" == "$1" ]]
}

pyproject_version() {  # $1 = pyproject.toml path (prints version or nothing)
  sed -n 's/^version[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$1" 2>/dev/null | head -1
}

installed_app_version() {  # version deployed at $INSTALL_DIR ("" if none/unknown)
  [[ -f "$INSTALL_DIR/pyproject.toml" ]] || return 0
  pyproject_version "$INSTALL_DIR/pyproject.toml"
}

app_python() {  # sanitized application interpreter (v0.4.1 invariant):
  # the code that validates and writes the configuration MUST be the code
  # deployed at $INSTALL_DIR - never a stale PYTHONPATH entry, a stray
  # package in the working directory (-P keeps CWD out of sys.path) or an
  # inherited environment surprise. UAEG_* overrides stay caller-controlled.
  env -u PYTHONPATH "$VENV_DIR/bin/python" -P "$@"
}

usage() {
  cat <<'USAGE'
UAEG unified installer & upgrade entrypoint (docs/INSTALL.md, docs/UPGRADE_CONTRACT.md)

Download this file ONCE. It installs UAEG on a fresh host and performs every
future SAFE UPGRADE of an existing installation (verified signed release
manifest -> versioned updater -> backup -> zero-data-loss verification ->
automatic rollback).

Commands:
  (default)              no existing installation: fresh install.
                         existing installation + bare invocation: safe upgrade
                         to the latest stable release
  --check                read-only: installation status + available update
  --dry-run              preview: upgrade plan (or fresh-install plan), no
                         changes
  --rollback [ID]        restore the latest upgrade backup (works offline from
                         the cached updater after the first upgrade)
  --repair               re-run the installer in place over the existing
                         installation: redeploys the application, rebuilds the
                         venv, rewrites the systemd unit - persistent data
                         (keys, database, dashboard password, TLS material)
                         and the existing configuration are preserved.
                         v0.4.1 safety: the source tree this script runs
                         from must not be OLDER than the installed app
                         (refused, exit 5) and its schema must accept the
                         existing configuration (verified BEFORE the
                         service is stopped or any file is replaced)
  --start                start the UAEG systemd service
  --stop                 stop the UAEG systemd service
  --restart              restart the UAEG systemd service
  --status               show the UAEG service status
  --version              print this installer's version and exit
  --version X.Y.Z        pin the target release (fresh-install tag, or the
                         safe-upgrade target on an existing installation)
  -h, --help             show this help

First-install options (a fresh install accepts all of these; on an existing
installation they select the IN-PLACE reconfiguration flow - v0.3.x behavior
- because the safe-upgrade path never changes your configuration):
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
  --yes                  assume yes for all prompts (unattended)

Environment:
  UAEG_RELEASE_API_BASE / UAEG_RELEASE_DOWNLOAD_BASE  release channel
  overrides (mirrors, contract tests). Signatures are ALWAYS verified.
  UAEG_VERSION                                          default release for a
  standalone fresh install (same as --version X.Y.Z)

Exit codes:
  0 success                 3 no existing installation found
  1 error (refused/failed)  4 reserved
  2 failure -> rolled back  5 downgrade refused - on EVERY path (safe
  upgrade and the in-place/--repair flow alike, v0.4.1). Deliberate
  downgrades go through: sudo bash upgrade.sh --version X.Y.Z --allow-downgrade
USAGE
}

# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
need_value() {  # $1=option name, $2=remaining argc (incl. the option), $3=candidate
  # Fail with an actionable message instead of bash's raw "unbound
  # variable" when a value-taking option is last on the command line, and
  # treat an option-like token ("--cert --key x") as a MISSING value.
  if [[ "$2" -lt 2 || -z "${3:-}" || "$3" == -* ]]; then
    die "option $1 requires a value (see --help)"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start)            MODE_CMD="start"; shift ;;
    --stop)             MODE_CMD="stop"; shift ;;
    --restart)          MODE_CMD="restart"; shift ;;
    --status)           MODE_CMD="status"; shift ;;
    --check)            MODE_CMD="check"; shift ;;
    --dry-run)          MODE_CMD="dry-run"; shift ;;
    --rollback)
      MODE_CMD="rollback"
      # optional backup ID (timestamp prefix), forwarded to the updater as
      # --backup ID - same interface as `upgrade.sh --rollback [ID]`
      if [[ $# -ge 2 && "$2" != -* ]]; then ROLLBACK_ID="$2"; shift; fi
      shift ;;
    --repair)           MODE_CMD="repair"; FORCE_INPLACE=1; shift ;;
    --port)             need_value "$1" "$#" "${2:-}"; MCP_PORT="$2"; MCP_PORT_GIVEN=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --dashboard-port)   need_value "$1" "$#" "${2:-}"; DASHBOARD_PORT="$2"; DASHBOARD_PORT_GIVEN=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --dashboard-bind)   need_value "$1" "$#" "${2:-}"; DASHBOARD_BIND="$2"; DASHBOARD_BIND_GIVEN=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --domain)           need_value "$1" "$#" "${2:-}"; DOMAIN="$2"; TLS_MODE="certbot"; TLS_MODE_GIVEN=1; TLS_SRC_DOMAIN=1; PUBLIC_HOST_GIVEN=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --public-ip)        need_value "$1" "$#" "${2:-}"; PUBLIC_IP="$2"; PUBLIC_HOST_GIVEN=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --cert)             need_value "$1" "$#" "${2:-}"; CERT_FILE="$2"; TLS_MODE="manual"; TLS_MODE_GIVEN=1; TLS_SRC_MANUAL=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --key)              need_value "$1" "$#" "${2:-}"; KEY_FILE="$2"; TLS_MODE="manual"; TLS_MODE_GIVEN=1; TLS_SRC_MANUAL=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --self-signed)      TLS_MODE="self-signed"; TLS_MODE_GIVEN=1; TLS_SRC_SELFSIGNED=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift ;;
    --data-dir)         need_value "$1" "$#" "${2:-}"; DATA_DIR="$2"; TARGETING_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --config-dir)       need_value "$1" "$#" "${2:-}"; CONFIG_DIR="$2"; TARGETING_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --install-dir)      need_value "$1" "$#" "${2:-}"; INSTALL_DIR="$2"; TARGETING_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift 2 ;;
    --no-service)       NO_SERVICE=1; TARGETING_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift ;;
    --open-firewall)    OPEN_FIREWALL=1; CONFIG_FLAGS_GIVEN=1; BARE_INVOCATION=0; shift ;;
    --yes|-y)           ASSUME_YES=1; shift ;;
    --version)
      # Ambiguity resolved (contract v4): bare "--version" PRINTS this
      # file's version; "--version X.Y.Z" pins the target release for both
      # the fresh-install and the safe-upgrade paths.
      if [[ $# -ge 2 && "$2" != -* ]]; then
        FETCH_VERSION="$2"; shift 2
      else
        PRINT_VERSION_ONLY=1; shift
      fi ;;
    -h|--help)          usage; exit 0 ;;
    *)                  die "unknown option: $1 (see --help)" ;;
  esac
done

if [[ $PRINT_VERSION_ONLY -eq 1 ]]; then
  echo "UAEG unified installer v${UAEG_RELEASE_VERSION} (updater interface v${INSTALLER_INTERFACE})"
  exit 0
fi

if [[ -n "$MODE_CMD" && $CONFIG_FLAGS_GIVEN -eq 1 && "$MODE_CMD" != "dry-run" && "$MODE_CMD" != "repair" ]]; then
  die "configuration flags (--port, --dashboard-bind, --domain, ...) apply to the
install flow, not to --$MODE_CMD. For a read-only preview use --dry-run;
to APPLY configuration changes to an existing installation use --repair."
fi

# ----------------------------------------------------------------------
# TLS option contract (contract audit 2026-11-09). Everything below runs
# BEFORE any mutation: no privilege escalation, no apt, no service stop, no
# file write has happened yet. The canonical TLS interface is exactly:
#   --domain DOMAIN | --cert FILE --key FILE | --self-signed
# (there is NO --tls-mode / --cert-file / --key-file option; the v0.4.1
# recovery runbook shipped with those non-existent flags and is corrected
# by the same audit). One invocation selects ONE TLS source, and manual
# TLS requires the complete pair to exist - previously "--cert x" without
# "--key" was only detected AFTER the application had already been copied
# to the install directory (partial fresh install), and mixing e.g.
# "--domain --cert" silently took the last one.
# ----------------------------------------------------------------------
if (( TLS_SRC_DOMAIN + TLS_SRC_MANUAL + TLS_SRC_SELFSIGNED > 1 )); then
  die "conflicting TLS options: choose exactly ONE of
    --domain DOMAIN         (Let's Encrypt via certbot)
    --cert FILE --key FILE  (operator-provided certificate, manual mode)
    --self-signed           (local testing only)
(see --help)"
fi
if [[ $TLS_SRC_MANUAL -eq 1 ]]; then
  [[ -n "$CERT_FILE" && -n "$KEY_FILE" ]] || die "manual TLS requires BOTH --cert FILE and --key FILE (see --help)"
  [[ -f "$CERT_FILE" ]] || die "certificate file not found: $CERT_FILE"
  [[ -f "$KEY_FILE" ]] || die "key file not found: $KEY_FILE"
fi

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

# ----------------------------------------------------------------------
# Self-update protection (audit 2026-11-10, finding F4)
# ----------------------------------------------------------------------
# When started from INSIDE the UAEG service (an agent ran this installer via
# shell_exec / a background job), we sit in the unit's systemd cgroup and the
# service stop performed by this installer (or by the verified updater it
# launches) would kill THIS process too (KillMode=control-group; setsid does
# NOT escape a cgroup). Mutating modes re-exec into a transient systemd scope
# - a separate cgroup that survives the service stop - or refuse loudly.
# Read-only modes (status, check, dry-run) never touch the service.
selfupdate_guard() {
  [[ $NO_SERVICE -eq 1 ]] && return 0
  case "$MODE_CMD" in status|check|dry-run) return 0 ;; esac
  local cg=""
  cg="$(cat "${UAEG_CGROUP_FILE:-/proc/self/cgroup}" 2>/dev/null)" || return 0
  case "$cg" in
    *"/${SERVICE_NAME}.service"*)
      # Re-exec needs a REAL script file (SELF_FILE is the resolved
      # BASH_SOURCE); a stdin/eval invocation cannot be re-executed safely.
      if command -v systemd-run >/dev/null 2>&1 && [[ -f "$SELF_FILE" ]]; then
        say "self-update: running inside the ${SERVICE_NAME}.service cgroup;"
        say "self-update: re-executing in a transient systemd scope so the operation survives the service stop"
        exec systemd-run --scope --quiet --unit="uaeg-selfupdate-$$" bash "$SELF_FILE" "$@"
      fi
      die "refusing to run a mutating operation from inside the ${SERVICE_NAME}.service
cgroup: stopping the service would kill this installer mid-flight (systemd
KillMode=control-group; setsid does not escape the cgroup) and it cannot be
re-executed into a detached scope (no systemd-run, or the script is not
running from a file). Run it from an operator shell (SSH/console) instead.
   (nothing was changed)"
      ;;
  esac
  return 0
}
selfupdate_guard "$@"

# ----------------------------------------------------------------------
# Existing-installation detection (for the unified dispatch only; the
# install flow below keeps its own detection. Priority mirrors updater.sh:
# systemd unit > /usr/local/bin/uaeg symlink > default marker.)
# ----------------------------------------------------------------------
DISPATCH_UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
[[ -f "$DISPATCH_UNIT_FILE" ]] || DISPATCH_UNIT_FILE="/lib/systemd/system/${SERVICE_NAME}.service"
DISPATCH_INSTALL_DIR=""
if [[ -f "$DISPATCH_UNIT_FILE" ]]; then
  _unit_exec="$(sed -n 's/^ExecStart=//p' "$DISPATCH_UNIT_FILE" | head -1)"
  if [[ -n "$_unit_exec" ]]; then
    DISPATCH_INSTALL_DIR="$(dirname "$(dirname "$(dirname "$_unit_exec")")")"
  fi
fi
if [[ -z "$DISPATCH_INSTALL_DIR" && -L "$SYSTEM_UAEG_BIN" ]]; then
  _link_target="$(readlink -f "$SYSTEM_UAEG_BIN" 2>/dev/null || true)"
  if [[ "$_link_target" == */.venv/bin/uaeg ]]; then
    DISPATCH_INSTALL_DIR="$(dirname "$(dirname "$(dirname "$_link_target")")")"
  fi
fi
[[ -n "$DISPATCH_INSTALL_DIR" ]] || DISPATCH_INSTALL_DIR="$INSTALL_DIR"
IS_INSTALLED=0
if [[ -f "$DISPATCH_INSTALL_DIR/.uaeg-installed" ]] \
   || { [[ -f "$DISPATCH_INSTALL_DIR/pyproject.toml" ]] && [[ -x "$DISPATCH_INSTALL_DIR/.venv/bin/uaeg" ]]; }; then
  IS_INSTALLED=1
fi

# ----------------------------------------------------------------------
# Service-management commands (--start/--stop/--restart/--status)
# ----------------------------------------------------------------------
cmd_service() {
  local action="$1" rc=0
  if ! command -v systemctl >/dev/null 2>&1; then
    die "systemctl not found: --$action requires systemd. Containers and
--no-service installs should run the server directly:  uaeg serve"
  fi
  local unit="/etc/systemd/system/${SERVICE_NAME}.service"
  [[ -f "$unit" ]] || unit="/lib/systemd/system/${SERVICE_NAME}.service"
  if [[ ! -f "$unit" ]]; then
    if [[ $IS_INSTALLED -eq 1 ]]; then
      die "no systemd unit '${SERVICE_NAME}.service' found (installed with
--no-service?). Start the server directly:
  UAEG_CONFIG=$CONFIG_DIR/config.json UAEG_DATA_DIR=$DATA_DIR uaeg serve"
    fi
    die "UAEG is not installed. Install it first:  sudo bash install.sh"
  fi
  case "$action" in
    start)   say "starting ${SERVICE_NAME}"; systemctl start "$SERVICE_NAME" || rc=$? ;;
    stop)    say "stopping ${SERVICE_NAME}"; systemctl stop "$SERVICE_NAME" || rc=$? ;;
    restart) say "restarting ${SERVICE_NAME}"; systemctl restart "$SERVICE_NAME" || rc=$? ;;
    status)  systemctl status "$SERVICE_NAME" --no-pager -l || rc=$? ;;
  esac
  if [[ "$action" == "status" ]]; then
    if command -v curl >/dev/null 2>&1; then
      local hp
      hp="$(python3 - "$CONFIG_DIR/config.json" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    print(json.load(open(sys.argv[1]))["mcp"]["port"])
except Exception:
    pass
PY
)"
      if [[ "$hp" =~ ^[0-9]+$ ]] \
         && curl -sk --max-time 2 "https://127.0.0.1:${hp}/healthz" 2>/dev/null | grep -q '"ok"'; then
        say "health check: https://127.0.0.1:${hp}/healthz OK"
      fi
    fi
  else
    [[ $rc -eq 0 ]] && say "${SERVICE_NAME} ${action}: done" || warn "${SERVICE_NAME} ${action}: systemctl exited $rc"
  fi
  exit "$rc"
}

# ----------------------------------------------------------------------
# Embedded safe-upgrade bootstrap (mirrors upgrade.sh, interface v1)
# ----------------------------------------------------------------------
BOOTSTRAP_WORK=""
bootstrap_cleanup() { [[ -n "$BOOTSTRAP_WORK" && -d "$BOOTSTRAP_WORK" ]] && rm -rf "$BOOTSTRAP_WORK"; return 0; }

fetch_latest_tag() {
  curl -fsSL --max-time 30 \
    -H "Accept: application/vnd.github+json" \
    "$RELEASE_API_BASE/releases/latest" 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('tag_name',''))" \
    || echo ""
}

verify_manifest_signature() {  # $1=manifest $2=signature ; 0 = trusted
  local k
  for k in "$BOOTSTRAP_WORK/trusted"/*.pub; do
    if openssl pkeyutl -verify -pubin -inkey "$k" \
      -rawin -in "$1" -sigfile "$2" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

fetch_verified_updater() {  # $1=tag $2=version ; sets UPDATER_PATH/MANIFEST_PATH
  local tag="$1" ver="$2" manifest_url sig_url
  manifest_url="$RELEASE_DOWNLOAD_BASE/$tag/uaeg-manifest.json"
  sig_url="$RELEASE_DOWNLOAD_BASE/$tag/uaeg-manifest.json.sig"

  say "fetching release manifest: $manifest_url"
  curl -fsSL --max-time 120 -o "$BOOTSTRAP_WORK/manifest.json" "$manifest_url" \
    || die "could not download the release manifest for $tag
(network problem, or the release is missing uaeg-manifest.json - nothing was changed)"

  say "fetching manifest signature: $sig_url"
  curl -fsSL --max-time 120 -o "$BOOTSTRAP_WORK/manifest.json.sig" "$sig_url" \
    || die "could not download the manifest signature for $tag (incomplete release; nothing was changed)"

  say "verifying manifest signature (embedded trusted keyring)"
  if ! verify_manifest_signature "$BOOTSTRAP_WORK/manifest.json" "$BOOTSTRAP_WORK/manifest.json.sig"; then
    die "RELEASE MANIFEST SIGNATURE INVALID for $tag.
The manifest is not signed by ANY trusted UAEG release key.
Possible causes: release not published by the project, artifact tampering, or a
key rotation (see docs/BOOTSTRAP.md - Recovery). NOTHING was executed or changed.
Recovery (one-time bootstrap refresh):
  curl -fsSLO $RELEASE_LATEST_BASE/install.sh
  curl -fsSLO $RELEASE_LATEST_BASE/install.sh.sha256
  sha256sum -c install.sh.sha256"
  fi

  read -r UPDATER_REL UPDATER_SHA UPDATER_SIZE UPDATER_IFACE MANIFEST_VER \
    < <(python3 - "$BOOTSTRAP_WORK/manifest.json" <<'PY'
import json, sys
try:
    m = json.load(open(sys.argv[1], encoding="utf-8"))
except ValueError:
    sys.exit(3)
def fail(msg):
    print(f"ERR {msg}", file=sys.stderr); sys.exit(2)
if m.get("product") != "uaeg":
    fail("manifest product is not 'uaeg'")
up = m.get("updater") or {}
path, sha, size, iface = up.get("path"), up.get("sha256"), up.get("size"), up.get("interface")
ver = m.get("version", "")
import re
if not ver or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.-]*", ver):
    fail("manifest version missing/invalid")
if not path or "/" in path or path.startswith(".") or not path.endswith(".sh"):
    fail("manifest updater.path missing or unsafe (must be a bare .sh filename)")
if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
    fail("manifest updater.sha256 missing/invalid")
if not (isinstance(size, int) and 0 < size < 50_000_000):
    fail("manifest updater.size missing/invalid")
if not (isinstance(iface, int) and iface >= 1):
    fail("manifest updater.interface missing/invalid")
print(path, sha, size, iface, ver)
PY
  )
  if [[ -z "${UPDATER_REL:-}" || -z "${UPDATER_SHA:-}" || -z "${MANIFEST_VER:-}" || -z "${UPDATER_IFACE:-}" ]]; then
    die "the release manifest is malformed (no usable updater coordinates) - refusing to continue (nothing was changed)"
  fi
  if [[ "$MANIFEST_VER" != "$ver" ]]; then
    die "manifest version ($MANIFEST_VER) does not match the requested release ($ver) - refusing to continue"
  fi
  if (( UPDATER_IFACE > INSTALLER_INTERFACE )); then
    die "the updater in release $tag speaks interface v$UPDATER_IFACE, but this
installer supports up to v$INSTALLER_INTERFACE. Refresh the installer ONCE:
  curl -fsSLO $RELEASE_LATEST_BASE/install.sh
  curl -fsSLO $RELEASE_LATEST_BASE/install.sh.sha256
  sha256sum -c install.sh.sha256
(see docs/BOOTSTRAP.md - exceptional recovery; this is the only situation
that requires re-downloading this file)."
  fi

  say "downloading versioned updater: $UPDATER_REL ($tag)"
  curl -fsSL --max-time 120 -o "$BOOTSTRAP_WORK/updater.sh" \
    "$RELEASE_DOWNLOAD_BASE/$tag/$UPDATER_REL" \
    || die "could not download the updater $UPDATER_REL (nothing was changed)"

  say "verifying updater integrity (sha256 + size against the signed manifest)"
  local got_sha got_size
  got_sha="$(sha256sum "$BOOTSTRAP_WORK/updater.sh" | awk '{print $1}')"
  got_size="$(wc -c < "$BOOTSTRAP_WORK/updater.sh")"
  if [[ "$got_sha" != "$UPDATER_SHA" ]]; then
    die "UPDATER SHA-256 MISMATCH (expected $UPDATER_SHA, got $got_sha).
The downloaded updater does not match the signed manifest - refusing to
execute it. Nothing was changed."
  fi
  if [[ "$got_size" != "$UPDATER_SIZE" ]]; then
    die "updater size mismatch (expected $UPDATER_SIZE, got $got_size) - refusing to execute"
  fi

  UPDATER_PATH="$BOOTSTRAP_WORK/updater.sh"
  MANIFEST_PATH="$BOOTSTRAP_WORK/manifest.json"
}

run_bootstrap_mode() {  # $1 = upgrade | check | dry-run | rollback
  local bmode="$1"
  local cache_dir="${UAEG_UPGRADE_CACHE_DIR:-/var/lib/uaeg/upgrade}"
  local cached_updater="$cache_dir/last-updater.sh"
  local cached_manifest="$cache_dir/last-manifest.json"
  local updater_path="" manifest_path="" target_version="" target_tag=""
  local forward_args=()
  local rc=0

  command -v curl >/dev/null 2>&1 || die "curl is required (release discovery and artifact download)"
  command -v openssl >/dev/null 2>&1 || die "openssl is required (manifest signature verification)"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required (artifact integrity verification)"
  command -v python3 >/dev/null 2>&1 || die "python3 is required (manifest parsing)"

  # Targeting flags are valid updater flags: forward them so custom-path
  # installations are detected correctly by the updater.
  [[ -n "$FETCH_VERSION" ]] && forward_args+=(--version "$FETCH_VERSION")
  [[ $ASSUME_YES -eq 1 ]] && forward_args+=(--yes)
  [[ -n "${ROLLBACK_ID:-}" ]] && forward_args+=(--backup "$ROLLBACK_ID")
  [[ $TARGETING_FLAGS_GIVEN -eq 1 ]] && forward_args+=(--install-dir "$INSTALL_DIR" --data-dir "$DATA_DIR" --config-dir "$CONFIG_DIR")
  [[ $NO_SERVICE -eq 1 ]] && forward_args+=(--no-service)

  if [[ "$bmode" == "rollback" && -f "$cached_updater" ]]; then
    say "rollback: using the cached updater ($cached_updater)"
    updater_path="$cached_updater"
    [[ -f "$cached_manifest" ]] && manifest_path="$cached_manifest"
  else
    if [[ -n "$FETCH_VERSION" ]]; then
      target_version="${FETCH_VERSION#v}"
      target_tag="v$target_version"
    else
      say "resolving the latest published release from $RELEASE_API_BASE"
      target_tag="$(fetch_latest_tag)"
      [[ -n "$target_tag" ]] || die "could not determine the latest release
(network problem or no published releases). For offline rollback use:
  sudo bash install.sh --rollback   (uses the cached updater)"
      target_version="${target_tag#v}"
    fi
    BOOTSTRAP_WORK="$(mktemp -d /tmp/uaeg-bootstrap.XXXXXX)"
    chmod 700 "$BOOTSTRAP_WORK"
    trap bootstrap_cleanup EXIT INT TERM
    mkdir -p "$BOOTSTRAP_WORK/trusted"
    printf '%s\n' "$_TRUSTED_KEY_1" > "$BOOTSTRAP_WORK/trusted/1.pub"
    printf '%s\n' "$_TRUSTED_KEY_2" > "$BOOTSTRAP_WORK/trusted/2.pub"
    printf '%s\n' "$_TRUSTED_KEY_3" > "$BOOTSTRAP_WORK/trusted/3.pub"
    printf '%s\n' "$_TRUSTED_KEY_4" > "$BOOTSTRAP_WORK/trusted/4.pub"
    fetch_verified_updater "$target_tag" "$target_version"
    updater_path="$UPDATER_PATH"
    manifest_path="$MANIFEST_PATH"
  fi

  [[ -n "$updater_path" && -f "$updater_path" ]] || die "no updater available for this operation
(network failed and no cached updater exists at $cached_updater)."

  export UAEG_BOOTSTRAP="1"
  export UAEG_BOOTSTRAP_FILE="$SELF_FILE"
  [[ -n "$target_version" ]] && export UAEG_TARGET_VERSION="$target_version"
  [[ -n "$target_tag" ]] && export UAEG_RELEASE_TAG="$target_tag"
  [[ -n "$manifest_path" ]] && export UAEG_MANIFEST="$manifest_path"
  export UAEG_UPDATER="$updater_path"

  local mode_flag=""
  [[ "$bmode" == "check" ]] && mode_flag="--check"
  [[ "$bmode" == "dry-run" ]] && mode_flag="--dry-run"
  [[ "$bmode" == "rollback" ]] && mode_flag="--rollback"

  say "running the verified updater: $updater_path"
  if [[ -n "$mode_flag" ]]; then
    bash "$updater_path" $mode_flag ${forward_args[@]+"${forward_args[@]}"} || rc=$?
  else
    bash "$updater_path" ${forward_args[@]+"${forward_args[@]}"} || rc=$?
  fi

  bootstrap_cleanup
  BOOTSTRAP_WORK=""

  # Post-condition: this file (the operator's installer) must be untouched.
  local self_sha_after
  self_sha_after="$(sha256sum "$SELF_FILE" 2>/dev/null | awk '{print $1}' || echo unknown)"
  if [[ "$SELF_SHA_BEFORE" != "$self_sha_after" ]]; then
    warn "the installer file changed during the operation ($SELF_FILE).
The updater is not allowed to modify it - please verify the file or re-
download it once (docs/BOOTSTRAP.md - exceptional recovery)."
  fi

  exit "$rc"
}

# ----------------------------------------------------------------------
# Unified dispatch
# ----------------------------------------------------------------------
case "$MODE_CMD" in
  start|stop|restart|status)
    cmd_service "$MODE_CMD"
    ;;
  check)
    if [[ $IS_INSTALLED -eq 1 ]]; then
      say "existing installation detected - running the verified status check"
      run_bootstrap_mode "check"
    fi
    say "no existing UAEG installation detected"
    latest_rel="$(fetch_latest_tag 2>/dev/null || true)"
    [[ -n "$latest_rel" ]] && echo "  latest release : $latest_rel"
    echo "  install with   : sudo bash install.sh --domain <your-domain>"
    exit 3
    ;;
  dry-run)
    if [[ $IS_INSTALLED -eq 1 ]]; then
      if [[ $CONFIG_FLAGS_GIVEN -eq 1 ]]; then
        die "configuration flags cannot be previewed on an existing installation
with --dry-run (the safe upgrade never changes configuration). To APPLY
configuration changes use:  sudo bash install.sh --repair <flags>"
      fi
      say "existing installation detected - verified upgrade preview"
      run_bootstrap_mode "dry-run"
    fi
    echo
    printf '\033[1mFRESH INSTALL PLAN (dry-run - no changes)\033[0m\n'
    echo "  install dir    : $INSTALL_DIR"
    echo "  data dir       : $DATA_DIR"
    echo "  config dir     : $CONFIG_DIR"
    echo "  MCP port       : $MCP_PORT (public HTTPS)"
    echo "  dashboard      : $DASHBOARD_BIND:$DASHBOARD_PORT"
    echo "  TLS mode       : $TLS_MODE"
    [[ $NO_SERVICE -eq 1 ]] && echo "  service        : none (--no-service)" \
                            || echo "  service        : systemd unit '${SERVICE_NAME}.service'"
    [[ -n "$FETCH_VERSION" ]] && echo "  release        : v$FETCH_VERSION (pinned)" \
                              || echo "  release        : v$UAEG_RELEASE_VERSION (this file's release)"
    echo
    echo "run without --dry-run to install."
    exit 0
    ;;
  rollback)
    if [[ $IS_INSTALLED -eq 1 ]]; then
      run_bootstrap_mode "rollback"
    fi
    die "no UAEG installation detected - nothing to roll back"
    ;;
  repair)
    say "--repair: re-running the installer in place (persistent data preserved)"
    FORCE_INPLACE=1
    ;;
esac

# Default mode: fresh install, verified safe upgrade, or in-place flow.
if [[ "${UAEG_BOOTSTRAP:-0}" != "1" && $FORCE_INPLACE -eq 0 ]]; then
  if [[ $IS_INSTALLED -eq 1 && $BARE_INVOCATION -eq 1 ]]; then
    say "existing installation detected at $DISPATCH_INSTALL_DIR - safe upgrade via the verified release channel"
    say "(to re-run the installer in place instead, use: sudo bash install.sh --repair)"
    run_bootstrap_mode "upgrade"
  fi
else
  # Invoked inside the verified upgrade pipeline (by the updater), or --repair:
  # run the in-place installer flow below.
  :
fi

say "UAEG installer starting"
if [[ ! -f "$SCRIPT_DIR/pyproject.toml" ]]; then
  # Standalone bootstrap (FR-002): this script was downloaded on its own
  # (checksum-verified by the operator); fetch the matching release source.
  VERSION="${FETCH_VERSION:-${UAEG_VERSION:-$UAEG_RELEASE_VERSION}}"
  # v0.4.1: a standalone --repair must never fetch its own (possibly older)
  # pinned release over a NEWER installed application (incident 2026-11-08
  # variant: old downloaded installer + newer installation). Check the pin
  # against the installed version BEFORE downloading anything.
  STANDALONE_INST_VER="$(installed_app_version)"
  if [[ -n "$STANDALONE_INST_VER" ]] \
     && ver_gt "$STANDALONE_INST_VER" "${VERSION#v}" \
     && [[ "${UAEG_ALLOW_DOWNGRADE:-0}" != "1" ]]; then
    die "REFUSING to --repair with a standalone installer pinned to v${VERSION#v}
while the installed application is NEWER (v$STANDALONE_INST_VER at $INSTALL_DIR).
Deploying this file's source would be a silent downgrade - the exact mechanism
of the 2026-11-08 production incident (older config schema rejecting the
existing 'dashboard.tls' field). NOTHING was changed.

Recovery:
  a) safe upgrade to the latest release with THIS file:  sudo bash install.sh
  b) get the current installer once:
       curl -fsSLO $RELEASE_LATEST_BASE/install.sh
       curl -fsSLO $RELEASE_LATEST_BASE/install.sh.sha256
       sha256sum -c install.sh.sha256" 5
  fi
  say "standalone installer: fetching UAEG v${VERSION} source from GitHub"
  command -v curl >/dev/null 2>&1 || die "curl is required for standalone installation"
  TMP_SRC="$(mktemp -d /tmp/uaeg-bootstrap.XXXXXX)"
  TARBALL_URL="https://github.com/smahud/uaeg/archive/refs/tags/v${VERSION#v}.tar.gz"
  curl -fsSL "$TARBALL_URL" | tar -C "$TMP_SRC" -xz
  SCRIPT_DIR="$(echo "$TMP_SRC"/uaeg-*)"
  [[ -f "$SCRIPT_DIR/pyproject.toml" ]] || die "downloaded release archive is missing pyproject.toml"
  say "source ready: $SCRIPT_DIR"
fi

# ----------------------------------------------------------------------
# P0 preflight (v0.4.1): the in-place flow must never deploy older or
# configuration-incompatible source over an existing installation.
#
# Production incident 2026-11-08 (host.zerotier.my.id): `install.sh --repair`
# was run from a stale checkout whose src/uaeg/config.py predated the
# dashboard `tls` field (added in v0.3.0). The old source was copied over
# /opt/uaeg, the venv was re-pointed at it (pip install -e), and only THEN
# was the existing (v0.3.0+ schema) configuration validated ->
# "DashboardConfig.__init__() got an unexpected keyword argument 'tls'"
# -> failed repair, stopped service, rollback. Both guards below run
# BEFORE the service is stopped and before any application, venv or
# configuration byte is touched.
# ----------------------------------------------------------------------
preflight_inplace_safety() {
  local src_ver inst_ver
  inst_ver="$(installed_app_version)"
  [[ -n "$inst_ver" ]] || return 0   # no existing installation: fresh install

  src_ver="$(pyproject_version "$SCRIPT_DIR/pyproject.toml")"

  # -- guard 1: refuse to deploy older source over a newer installation --
  if [[ -n "$src_ver" ]] && ver_gt "$inst_ver" "$src_ver"; then
    if [[ "${UAEG_ALLOW_DOWNGRADE:-0}" == "1" ]]; then
      warn "UAEG_ALLOW_DOWNGRADE=1: deploying OLDER source (v$src_ver from $SCRIPT_DIR)
over the installed v$inst_ver, as explicitly requested by the operator"
    else
      die "REFUSING to deploy OLDER source over the installed application.

  installed application : v$inst_ver  ($INSTALL_DIR)
  source to be deployed : v$src_ver  ($SCRIPT_DIR)

This is exactly the mechanism of the 2026-11-08 production incident: an
older schema rejected the existing configuration ('dashboard.tls') AFTER
the application had already been replaced. NOTHING was changed.

Recovery:
  a) safe upgrade to a published release:  sudo bash install.sh [--version X.Y.Z]
  b) repair the installed version: run this installer from a checkout of
     v$inst_ver:   git fetch --tags && git checkout v$inst_ver
  c) intentional downgrade:  sudo bash upgrade.sh --version $src_ver --allow-downgrade" 5
    fi
  else
    say "preflight: source v${src_ver:-unknown} vs installed v$inst_ver - version order OK"
  fi

  # -- guard 2: the staged schema must accept the EXISTING configuration --
  local cfg_file="$CONFIG_DIR/config.json"
  [[ -f "$cfg_file" ]] || return 0
  local venv_py="$INSTALL_DIR/.venv/bin/python"
  [[ -x "$venv_py" ]] || return 0

  local stage probe out rc=0
  stage="$(mktemp -d /tmp/uaeg-preflight.XXXXXX)" \
    || die "could not create the preflight staging directory"
  chmod 700 "$stage"
  tar -C "$SCRIPT_DIR" -cf - \
    --exclude=.git --exclude=.venv --exclude='*.egg-info' \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=dist --exclude=build \
    . | tar -C "$stage" -xf -
  probe="$stage/config-probe.json"
  cp "$cfg_file" "$probe"

  # The probe imports uaeg.config FROM THE STAGED SOURCE (the injected
  # PYTHONPATH precedes site-packages and the editable install) using the
  # EXISTING venv interpreter with a sanitized environment: an operator's
  # PYTHONPATH or a stray package in the working directory must never
  # shadow the code that is about to be deployed. Only the exact incident
  # shape can fail here: newer-schema config vs older/different schema.
  out="$(env -u PYTHONPATH -u UAEG_CONFIG -u UAEG_DATA_DIR \
          PYTHONPATH="$stage/src" "$venv_py" -P - "$probe" "$stage/src" <<'PY' 2>&1
import sys
from pathlib import Path

import uaeg
import uaeg.config

expected_src = Path(sys.argv[2]).resolve()
origin = Path(uaeg.__file__).resolve()
if not origin.is_relative_to(expected_src):
    print(
        f"RUNTIME_MISMATCH: the probe interpreter loaded uaeg from {origin}, "
        f"not from the staged source {expected_src}. A stale package "
        "(PYTHONPATH, working directory or site-packages) is shadowing "
        "the source that is about to be deployed."
    )
    sys.exit(7)

try:
    uaeg.config.Config.load(sys.argv[1])  # load + validate with the STAGED schema
except uaeg.config.ConfigError as exc:
    print(f"CONFIG_INCOMPATIBLE: {exc}")
    sys.exit(6)

print(f"SCHEMA_OK uaeg={uaeg.__version__}")
PY
)" || rc=$?
  rm -rf "$stage"
  if [[ $rc -ne 0 || "$out" != SCHEMA_OK* ]]; then
    die "the existing configuration is NOT compatible with the source about to be deployed.

  source to be deployed : v${src_ver:-unknown} ($SCRIPT_DIR)
  existing configuration : $cfg_file

$out

NOTHING was changed: this check runs BEFORE the service is stopped and
before any application file is replaced. The source's configuration schema
does not accept a field that the existing configuration contains (the
2026-11-08 incident class: an older DashboardConfig rejected 'dashboard.tls').
Recovery:
  a) run this installer from a CURRENT checkout:
       git fetch --tags && git checkout v$inst_ver
  b) safe upgrade to a published release:  sudo bash install.sh
  c) deliberate downgrade to a schema-incompatible version additionally
     requires reconciling the configuration BY HAND first (the installer
     never edits the existing configuration): back it up, remove the
     fields the target version does not know, then re-run with
     UAEG_ALLOW_DOWNGRADE=1  (supported path: sudo bash upgrade.sh
     --version $src_ver --allow-downgrade)"
  fi
  say "preflight: existing configuration accepted by the staged schema ($out)"
}
preflight_inplace_safety

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
    crc=0
    confirm_ask "Continue anyway? [y/N] " || crc=$?
    if [[ $crc -eq 1 ]]; then
      die "aborted"
    fi
    if [[ $crc -ne 0 ]]; then
      die "no confirmation was received: stdin is not a terminal, there is no
controlling terminal (/dev/tty), and no answer was piped in. Run the
installer from an interactive terminal, or pass --yes."
    fi
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
    # v0.4.1: replacing application files under a live service must never
    # happen; a hung stop is a hard pre-mutation failure (was `|| true`).
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
      die "could not stop the running '$SERVICE_NAME' service - refusing to
replace application files under a live service. Investigate:
  systemctl status $SERVICE_NAME
  journalctl -u $SERVICE_NAME -n 50 --no-pager"
    fi
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

# System bootstrap convenience copy (upgrade contract v3/v4): operators can
# run `sudo uaeg-upgrade` in addition to their own downloaded upgrade.sh or
# install.sh. The operator's own bootstrap files are NEVER touched by any
# upgrade.
if [[ -f "$SCRIPT_DIR/upgrade.sh" ]]; then
  if ! cmp -s "$SCRIPT_DIR/upgrade.sh" /usr/local/sbin/uaeg-upgrade 2>/dev/null; then
    say "installing the system upgrade bootstrap: /usr/local/sbin/uaeg-upgrade"
    if cp "$SCRIPT_DIR/upgrade.sh" /usr/local/sbin/uaeg-upgrade.tmp \
       && chmod 0755 /usr/local/sbin/uaeg-upgrade.tmp \
       && mv -f /usr/local/sbin/uaeg-upgrade.tmp /usr/local/sbin/uaeg-upgrade; then
      :
    else
      warn "could not install /usr/local/sbin/uaeg-upgrade (non-fatal; bash upgrade.sh keeps working)"
    fi
  fi
fi

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
      # NOTE: this function runs inside a command substitution (a sub-
      # shell). Do NOT set DASHBOARD_PUBLIC here - the assignment would
      # be lost. The caller derives it from the resolved bind address.
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

# D10 (audit 2026-11-09): resolve_dashboard_bind runs in a subshell, so
# the historical `DASHBOARD_PUBLIC=1` inside it NEVER reached this shell.
# Since v0.3.0 this silently skipped the password prerequisite check and
# made the config writer omit dashboard.allow_public/dashboard.tls, so
# every `--dashboard-bind public` invocation (fresh install AND --repair)
# was ultimately refused by Config.validate with a confusing error after
# the application had already been deployed. Derive the public opt-in
# HERE, in the parent shell, from the resolved bind address.
case "$DASH_BIND_IP" in
  0.0.0.0|::) DASHBOARD_PUBLIC=1 ;;
esac

# ----------------------------------------------------------------------
# Public dashboard requires a password BEFORE anything is reconfigured
# ----------------------------------------------------------------------
if [[ $DASHBOARD_PUBLIC -eq 1 ]]; then
  if ! UAEG_DATA_DIR="$DATA_DIR" app_python -c \
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
    # Audit 2026-11-09 (silent-downgrade guard): an EXPLICITLY requested TLS
    # source (--cert/--key, --self-signed) must never be silently replaced
    # by self-signed because address detection failed on this host. Only
    # the implicit default (no TLS flags at all) may fall back to
    # self-signed - an explicit operator choice is kept as given.
    if [[ $TLS_MODE_GIVEN -eq 1 ]]; then
      warn "no --domain/--public-ip given; using detected address '$PUBLIC_HOST' (keeping the explicitly requested TLS mode: $TLS_MODE)"
    else
      warn "no --domain/--public-ip given; using detected address '$PUBLIC_HOST' (self-signed TLS only)"
      TLS_MODE="self-signed"
    fi
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
UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" app_python - "$CONFIG_FILE" \
  "$CONFIG_EXISTS" "$PUBLIC_HOST" "$MCP_PORT" "$MCP_PORT_GIVEN" \
  "$DASHBOARD_PORT" "$DASHBOARD_PORT_GIVEN" "$DASH_BIND_IP" "$DASHBOARD_BIND_GIVEN" \
  "$TLS_MODE" "$TLS_MODE_GIVEN" "$CERT_FILE" "$KEY_FILE" "$PUBLIC_HOST_GIVEN" \
  "$DASHBOARD_PUBLIC" "$INSTALL_DIR" <<'PY' \
  || die "configuration could not be written - see the error above"
import sys
from pathlib import Path

import uaeg
from uaeg.config import Config, ConfigError

# v0.4.1 invariant: the schema that validates and writes the configuration
# MUST belong to the application being deployed at $INSTALL_DIR - never a
# stale PYTHONPATH entry, a stray CWD package or an old site-packages copy
# (the interpreter itself is sanitized by app_python; this is the proof).
_origin = Path(uaeg.__file__).resolve()
_expected = Path(sys.argv[16]).resolve() / "src"
if not _origin.is_relative_to(_expected):
    print(
        f"CONFIG_ERROR: runtime/package mismatch - this interpreter loaded "
        f"uaeg from {_origin}, expected {_expected}. A stale package is "
        "shadowing the application; the configuration was NOT written. "
        "Re-run the installer with --repair from a current checkout.",
        file=sys.stderr,
    )
    sys.exit(9)

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
EFFECTIVE_PORTS="$(app_python - "$CONFIG_FILE" <<'PY'
import json, sys

cfg = json.load(open(sys.argv[1]))
print(cfg["mcp"]["port"])
print(cfg["dashboard"]["port"])
print(cfg.get("public_host", ""))
print(cfg.get("dashboard", {}).get("bind", "127.0.0.1"))
print(str(cfg.get("dashboard", {}).get("tls", False)).lower())
# Effective TLS mode from the (possibly preserved) configuration - the
# installer's own $TLS_MODE variable only reflects what THIS invocation
# requested, never what an upgrade kept in place (audit 2026-11-10, F3:
# preserved-certbot upgrades used to be summarized as "self-signed").
tls = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}
print(tls.get("mode", "self-signed"))
PY
)" || die "could not read the effective configuration"
EFFECTIVE_MCP_PORT="$(echo "$EFFECTIVE_PORTS" | sed -n 1p)"
EFFECTIVE_DASH_PORT="$(echo "$EFFECTIVE_PORTS" | sed -n 2p)"
EFFECTIVE_HOST="$(echo "$EFFECTIVE_PORTS" | sed -n 3p)"
EFFECTIVE_DASH_BIND="$(echo "$EFFECTIVE_PORTS" | sed -n 4p)"
EFFECTIVE_DASH_TLS="$(echo "$EFFECTIVE_PORTS" | sed -n 5p)"
EFFECTIVE_TLS_MODE="$(echo "$EFFECTIVE_PORTS" | sed -n 6p)"
# Fall back to the requested mode only when the read-back yielded nothing
# (should not happen: the config file is always written before this point).
[[ -z "$EFFECTIVE_TLS_MODE" ]] && EFFECTIVE_TLS_MODE="$TLS_MODE"

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

  # Dashboard listener proof (v0.4.1): the MCP health check proves the
  # process is serving and the listeners are supervised fail-together, but
  # the repair contract additionally requires POSITIVE evidence that the
  # dashboard listener answers on its effective bind address (the effective
  # configuration is authoritative, not this invocation's flags).
  DASH_PROBE_HOST="$EFFECTIVE_DASH_BIND"
  [[ "$DASH_PROBE_HOST" == "0.0.0.0" ]] && DASH_PROBE_HOST="127.0.0.1"
  if [[ "$DASH_PROBE_HOST" == "::" ]]; then DASH_PROBE_HOST="[::1]"; fi
  DASH_PROBE_SCHEME="http"
  [[ "$EFFECTIVE_DASH_TLS" == "true" ]] && DASH_PROBE_SCHEME="https"
  DASH_OK=0
  for _ in $(seq 1 15); do
    if curl -sk --max-time 2 -o /dev/null \
         "${DASH_PROBE_SCHEME}://${DASH_PROBE_HOST}:${EFFECTIVE_DASH_PORT}/login" 2>/dev/null; then
      DASH_OK=1
      break
    fi
    sleep 1
  done
  if [[ $DASH_OK -eq 0 ]]; then
    warn "the dashboard listener did not answer on ${DASH_PROBE_SCHEME}://${DASH_PROBE_HOST}:${EFFECTIVE_DASH_PORT}/login"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    die "installation incomplete - the dashboard listener is not reachable on its effective bind; rolling back"
  fi
  say "dashboard listener verified: ${DASH_PROBE_SCHEME}://${DASH_PROBE_HOST}:${EFFECTIVE_DASH_PORT}"
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
    case "$EFFECTIVE_DASH_BIND" in
      0.0.0.0|::)
        say "opening public dashboard port $EFFECTIVE_DASH_PORT/tcp in ufw"
        ufw allow "$EFFECTIVE_DASH_PORT/tcp" ;;
    esac
  else
    warn "ufw is active. Allow the MCP port yourself, e.g.:"
    warn "    ufw allow ${EFFECTIVE_MCP_PORT}/tcp"
    case "$EFFECTIVE_DASH_BIND" in
      0.0.0.0|::)
        warn "The dashboard is public - also allow its port:"
        warn "    ufw allow ${EFFECTIVE_DASH_PORT}/tcp" ;;
      *)
        warn "(the dashboard is LAN/loopback-private - do NOT open ${EFFECTIVE_DASH_PORT})" ;;
    esac
  fi
fi

# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
echo
printf '\033[1mUAEG installed successfully.\033[0m\n'
echo
EFFECTIVE_DASH_SCHEME="http"
[[ "$EFFECTIVE_DASH_TLS" == "true" ]] && EFFECTIVE_DASH_SCHEME="https"
case "$EFFECTIVE_DASH_BIND" in
  0.0.0.0|::)
    echo "  Dashboard    : ${EFFECTIVE_DASH_SCHEME}://${EFFECTIVE_HOST}:${EFFECTIVE_DASH_PORT} (public - password login + HTTPS)" ;;
  127.0.0.1|localhost)
    echo "  Dashboard    : ${EFFECTIVE_DASH_SCHEME}://127.0.0.1:${EFFECTIVE_DASH_PORT} (loopback only - NOT reachable from other machines)" ;;
  *)
    echo "  Dashboard    : ${EFFECTIVE_DASH_SCHEME}://${EFFECTIVE_DASH_BIND}:${EFFECTIVE_DASH_PORT} (LAN bind - reachable only from that interface)" ;;
esac
echo "  MCP endpoint : https://${EFFECTIVE_HOST}:${EFFECTIVE_MCP_PORT}/mcp"
echo "  Config       : $CONFIG_FILE"
echo "  Data         : $DATA_DIR"
echo "  TLS mode     : $EFFECTIVE_TLS_MODE"
[[ $NO_SERVICE -eq 0 ]] && echo "  Service      : systemctl status ${SERVICE_NAME}"
echo
echo "Next steps:"
case "$EFFECTIVE_DASH_BIND" in
  0.0.0.0|::) ;;
  *)
    echo "  0. (optional) enable dashboard login + remote access:"
    echo "       sudo uaeg pass"
    echo "       sudo bash install.sh --repair --dashboard-bind public  # + your TLS flags"
    echo "       sudo ufw allow ${EFFECTIVE_DASH_PORT}/tcp   # if a firewall is active" ;;
esac
echo "  1. Create an agent key:"
echo "       uaeg generate --name \"Claude\""
echo "  2. Configure your hosted agent's MCP connector:"
echo "       URL:                  https://${EFFECTIVE_HOST}:${EFFECTIVE_MCP_PORT}/mcp"
echo "       Authentication:       Bearer token (the generated uaeg_... secret)"
echo "  3. Verify health:"
echo "       uaeg doctor"
echo "  4. Future upgrades (this same file, downloaded once):"
echo "       sudo bash install.sh            # verified safe upgrade"
echo "       sudo bash install.sh --check    # status + available update"
echo
[[ "$TLS_MODE" == "self-signed" ]] && warn "self-signed TLS: most hosted platforms will reject this certificate. Use --domain (Let's Encrypt) for real agent connections."
echo "Docs: https://github.com/smahud/uaeg/tree/main/docs"
