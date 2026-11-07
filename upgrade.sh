#!/usr/bin/env bash
#
# UAEG safe-upgrade installer — implements docs/UPGRADE_CONTRACT.md (contract v2).
#
# ONE command is enough: it auto-detects the existing installation (install
# dir, data dir, config, systemd service), snapshots the pre-upgrade state
# (consistent SQLite backup + config + app), fetches the target release
# into an ISOLATED STAGING CLONE, runs the in-place upgrade, then VERIFIES
# zero data loss (keys identical, DB integrity, config preserved, dashboard
# password intact, service healthy). Any failure rolls the system back to
# the pre-upgrade state automatically.
#
# CONTRACT v2 (since v0.3.2): the operator's git working tree is NEVER read
# for content and NEVER mutated — no fetch-into-clone, no checkout, no
# reset, no clean. The release source is always materialized in a private
# staging directory (mktemp, mode 0700) and removed afterwards. This fixes
# the v0.3.1 incident where `git checkout <tag>` inside the operator's
# clone collided with the downloaded (untracked) upgrade.sh and was
# misreported as "tag not found" (see report/ROOT_CAUSE_ANALYSIS.md).
#
# Usage:
#   sudo bash upgrade.sh                 # upgrade to the latest release tag
#   sudo bash upgrade.sh --check         # read-only: installation status
#   sudo bash upgrade.sh --dry-run       # show the plan, change nothing
#   sudo bash upgrade.sh --rollback      # restore the latest upgrade backup
#   sudo bash upgrade.sh --version X.Y.Z # upgrade to a specific release tag
#   sudo bash upgrade.sh --main          # target origin/main (bleeding edge)
#   sudo bash upgrade.sh --source DIR    # upgrade from a local source tree
#   sudo bash upgrade.sh --yes           # non-interactive
#
# Exit codes:
#   0 success                 3 no existing installation found
#   1 error (refused/failed)  4 reserved (unused since contract v2)
#   2 failure -> rolled back  5 downgrade refused (use --allow-downgrade)
#
set -euo pipefail

# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------
MODE="upgrade"               # upgrade | check | dry-run | rollback
INSTALL_DIR=""               # detected below (flag > systemd unit > symlink > default)
DATA_DIR=""
CONFIG_DIR=""
CONFIG_FILE=""
SERVICE_NAME="uaeg"
NO_SERVICE=0                 # 1 = do not manage systemd (containers/tests)
HAS_SERVICE=0
BACKUPS_DIR=""               # default: /var/backups/uaeg
KEEP_BACKUPS=5
ASSUME_YES=0
ALLOW_DOWNGRADE=0
SKIP_SMOKE=0
OPT_VERSION=""
OPT_MAIN=0
OPT_SOURCE=""
OPT_INSTALLER=""
OPT_REPO="https://github.com/smahud/uaeg.git"
REPO_GIVEN=0                 # 1 = --repo was passed explicitly
REPO_URL=""                  # resolved by resolve_repo_url()
ROLLBACK_TARGET=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Runtime state (filled by detect_installation / during the run)
VENV_PYTHON=""
IS_INSTALLED=0
CUR_VERSION="0.0.0"
SOURCE_DIR=""
TARGET_VERSION=""
TARGET_REF=""
TARGET_SHA="n/a"
TMP_CLONE=""
BACKUP_ROOT=""
BACKUP_DIR=""
BACKUP_MADE=0
ROLLED_BACK=0
TS=""

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  {  # die <message> [exit-code]
  local rc="${2:-1}"
  printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2
  exit "$rc"
}

usage() {
  cat <<'USAGE'
UAEG safe-upgrade installer (docs/UPGRADE_CONTRACT.md)

Modes:
  (default)              upgrade in place: detect -> backup -> fetch -> upgrade
                         -> verify zero data loss -> auto-rollback on failure
  --check                read-only: show installation status and target version
  --dry-run              show the full upgrade plan, change nothing
  --rollback [ID]        restore data + config (and app, if retained) from the
                         latest upgrade backup, or from backup "ID"
                         (timestamp prefix or full path, given via --backup)

Target selection (upgrade mode):
  --version X.Y.Z        upgrade to release tag vX.Y.Z (default: latest tag)
  --main                 target origin/main instead of a release tag
  --source DIR           upgrade from a local source tree (offline installs)
  --repo URL             git remote to fetch the release from
                         (default: the operator clone's origin, else the
                         public repo). The release source is ALWAYS fetched
                         into an isolated staging directory - the operator's
                         git working tree is never read or modified.

Options:
  --installer PATH       installer to invoke (default: <source>/install.sh)
  --yes, -y              non-interactive; assume yes
  --allow-downgrade      permit downgrading to an older version
  --skip-smoke           skip the post-upgrade serve smoke test (weakens U-4)
  --install-dir PATH     override auto-detection [/opt/uaeg]
  --data-dir PATH        override auto-detection [/var/lib/uaeg]
  --config-dir PATH      override auto-detection [/etc/uaeg]
  --backups-dir PATH     backup root [/var/backups/uaeg]
  --keep-backups N       retain the N newest upgrade backups [5]
  --service-name NAME    systemd unit name [uaeg]
  --no-service           never touch systemd (containers/tests)
  -h, --help             show this help

Exit codes: 0 ok | 1 error | 2 rolled back | 3 not installed |
            4 reserved (unused: operator git trees are never mutated) |
            5 downgrade refused
USAGE
}

# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)             MODE="check"; shift ;;
    --dry-run)           MODE="dry-run"; shift ;;
    --rollback)          MODE="rollback"; shift ;;
    --backup)            ROLLBACK_TARGET="$2"; shift 2 ;;
    --version)           OPT_VERSION="$2"; shift 2 ;;
    --main)              OPT_MAIN=1; shift ;;
    --source)            OPT_SOURCE="$2"; shift 2 ;;
    --installer)         OPT_INSTALLER="$2"; shift 2 ;;
    --repo)              OPT_REPO="$2"; REPO_GIVEN=1; shift 2 ;;
    --yes|-y)            ASSUME_YES=1; shift ;;
    --allow-downgrade)   ALLOW_DOWNGRADE=1; shift ;;
    --skip-smoke)        SKIP_SMOKE=1; shift ;;
    --install-dir)       INSTALL_DIR="$2"; shift 2 ;;
    --data-dir)          DATA_DIR="$2"; shift 2 ;;
    --config-dir)        CONFIG_DIR="$2"; shift 2 ;;
    --backups-dir)       BACKUPS_DIR="$2"; shift 2 ;;
    --keep-backups)      KEEP_BACKUPS="$2"; [[ "$KEEP_BACKUPS" =~ ^[0-9]+$ ]] \
                           || die "--keep-backups harus berupa angka >= 0"; shift 2 ;;
    --service-name)      SERVICE_NAME="$2"; shift 2 ;;
    --no-service)        NO_SERVICE=1; shift ;;
    -h|--help)           usage; exit 0 ;;
    *)                   die "unknown option: $1 (see --help)" ;;
  esac
done

# ----------------------------------------------------------------------
# Root check (must run as root: reads 0600 config, writes /var/backups)
# ----------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    say "menjalankan ulang sebagai root via sudo..."
    exec sudo "$0" "$@"
  fi
  die "skrip ini harus dijalankan sebagai root (coba: sudo bash upgrade.sh)"
fi

command -v python3 >/dev/null 2>&1 || die "python3 tidak ditemukan (dibutuhkan untuk verifikasi)"

# ----------------------------------------------------------------------
# systemd helpers (tolerant of hosts without systemd)
# ----------------------------------------------------------------------
svc_active() {
  [[ $NO_SERVICE -eq 1 ]] && return 1
  systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null
}
# NOTE: the svc_* mutators must ALWAYS return 0 - a bare `[[ ]] && { ...; }`
# would return 1 when NO_SERVICE=1 and kill the caller under `set -e`
# (this exact bug broke the rollback path before v0.3.1 was released).
svc_stop() {
  if [[ $NO_SERVICE -eq 0 ]]; then systemctl stop "$SERVICE_NAME" 2>/dev/null || true; fi
  return 0
}
svc_start() {
  if [[ $NO_SERVICE -eq 0 ]]; then systemctl start "$SERVICE_NAME" 2>/dev/null || true; fi
  return 0
}
svc_restart() {
  if [[ $NO_SERVICE -eq 0 ]]; then systemctl restart "$SERVICE_NAME" 2>/dev/null || true; fi
  return 0
}

# ----------------------------------------------------------------------
# Detection: find the existing installation (contract U-2)
# Priority: explicit flags > systemd unit > /usr/local/bin symlink > defaults
# ----------------------------------------------------------------------
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
[[ -f "$UNIT_FILE" ]] || UNIT_FILE="/lib/systemd/system/${SERVICE_NAME}.service"

detect_installation() {
  local unit_exec="" unit_cfg="" unit_data=""

  if [[ -f "$UNIT_FILE" ]]; then
    HAS_SERVICE=1
    unit_exec="$(sed -n 's/^ExecStart=//p' "$UNIT_FILE" | head -1)"
    unit_cfg="$(sed -n 's/^Environment=UAEG_CONFIG=//p' "$UNIT_FILE" | head -1)"
    unit_data="$(sed -n 's/^Environment=UAEG_DATA_DIR=//p' "$UNIT_FILE" | head -1)"
  fi

  # -- install dir ---------------------------------------------------
  if [[ -z "$INSTALL_DIR" ]]; then
    if [[ -n "$unit_exec" ]]; then
      # ExecStart=/opt/uaeg/.venv/bin/uaeg serve  ->  /opt/uaeg
      INSTALL_DIR="$(dirname "$(dirname "$(dirname "$unit_exec")")")"
    elif [[ -L /usr/local/bin/uaeg ]]; then
      local link_target
      link_target="$(readlink -f /usr/local/bin/uaeg 2>/dev/null || true)"
      # .../opt/uaeg/.venv/bin/uaeg  ->  /opt/uaeg
      if [[ "$link_target" == */.venv/bin/uaeg ]]; then
        INSTALL_DIR="$(dirname "$(dirname "$(dirname "$link_target")")")"
      fi
    fi
    [[ -n "$INSTALL_DIR" ]] || INSTALL_DIR="/opt/uaeg"
  fi

  # -- config dir / file ----------------------------------------------
  if [[ -z "$CONFIG_DIR" ]]; then
    if [[ -n "$unit_cfg" ]]; then
      CONFIG_DIR="$(dirname "$unit_cfg")"
      CONFIG_FILE="$unit_cfg"
    else
      CONFIG_DIR="/etc/uaeg"
    fi
  fi
  [[ -n "$CONFIG_FILE" ]] || CONFIG_FILE="$CONFIG_DIR/config.json"

  # -- data dir --------------------------------------------------------
  if [[ -z "$DATA_DIR" ]]; then
    if [[ -n "$unit_data" ]]; then
      DATA_DIR="$unit_data"
    else
      DATA_DIR="/var/lib/uaeg"
    fi
  fi

  VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"
  [[ -x "$VENV_PYTHON" ]] || VENV_PYTHON=""

  # -- installed? ------------------------------------------------------
  if [[ -f "$INSTALL_DIR/.uaeg-installed" ]] \
     || { [[ -f "$INSTALL_DIR/pyproject.toml" ]] && [[ -x "$INSTALL_DIR/.venv/bin/uaeg" ]]; }; then
    IS_INSTALLED=1
  fi

  if [[ $HAS_SERVICE -eq 0 && $NO_SERVICE -eq 0 ]]; then
    if command -v systemctl >/dev/null 2>&1 \
       && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\\.service"; then
      HAS_SERVICE=1
    fi
  fi
  if [[ $HAS_SERVICE -eq 0 && $NO_SERVICE -eq 0 ]]; then
    warn "unit systemd '${SERVICE_NAME}.service' tidak terdeteksi — melanjutkan tanpa manajemen service"
    NO_SERVICE=1
  fi

  # -- current version ---------------------------------------------------
  if [[ $IS_INSTALLED -eq 1 ]]; then
    local v=""
    if [[ -x "$INSTALL_DIR/.venv/bin/uaeg" ]]; then
      v="$(UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" \
            "$INSTALL_DIR/.venv/bin/uaeg" version 2>/dev/null | awk '{print $2}' || true)"
    fi
    if [[ -z "$v" ]] && [[ -f "$INSTALL_DIR/pyproject.toml" ]]; then
      v="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$INSTALL_DIR/pyproject.toml" | head -1)"
    fi
    if [[ -n "$v" ]]; then
      CUR_VERSION="$v"
    fi
  fi
  return 0
}

# ----------------------------------------------------------------------
# State reader: consistent, secret-free snapshot of data + config
# (writes JSON to stdout; never prints verifiers or password hashes)
# ----------------------------------------------------------------------
read_state() {  # $1 = output file
  python3 - "$DATA_DIR/uaeg.db" "$CONFIG_FILE" "$DATA_DIR/dashboard-pass.json" > "$1" <<'PY'
import hashlib, json, os, sqlite3, sys

db_path, cfg_path, pass_path = sys.argv[1:4]
state = {
    "db_exists": os.path.exists(db_path),
    "quick_check": None, "schema_version": None,
    "keys": [], "counts": None,
    "config_exists": os.path.exists(cfg_path),
    "config_sha256": None, "effective": None,
    "password_configured": os.path.exists(pass_path),
}
if state["db_exists"]:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            state["quick_check"] = str(con.execute("PRAGMA quick_check").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            state["quick_check"] = f"failed: {exc}"
        try:
            row = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
            state["schema_version"] = int(row[0]) if row else None
        except sqlite3.DatabaseError:
            pass
        try:
            state["keys"] = [
                {"key_id": r[0], "name": r[1], "prefix": r[2], "status": r[3]}
                for r in con.execute(
                    "SELECT key_id, name, prefix, status FROM keys ORDER BY key_id")
            ]
        except sqlite3.DatabaseError:
            pass
        counts = {}
        for t in ("keys", "jobs", "sessions", "exec_logs"):
            try:
                counts[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except sqlite3.DatabaseError:
                counts[t] = None
        state["counts"] = counts
        con.close()
    except sqlite3.DatabaseError as exc:
        state["quick_check"] = f"failed: {exc}"
if state["config_exists"]:
    try:
        raw = open(cfg_path, encoding="utf-8").read()
        state["config_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        cfg = json.loads(raw)
        # absent key == application default (mirrors src/uaeg/config.py):
        # "effective configuration" must not false-positive when a version
        # adds an explicit default for a key an older config file lacks
        mcp = cfg.get("mcp") if isinstance(cfg.get("mcp"), dict) else {}
        dash = cfg.get("dashboard") if isinstance(cfg.get("dashboard"), dict) else {}
        tls = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}
        state["effective"] = {
            "mcp_port": mcp.get("port", 3902),
            "dash_port": dash.get("port", 3903),
            "dash_bind": dash.get("bind", "127.0.0.1"),
            "dash_tls": dash.get("tls", False),
            "allow_public": dash.get("allow_public", False),
            "tls_mode": tls.get("mode", "self-signed"),
            "public_host": cfg.get("public_host"),
        }
    except (OSError, ValueError) as exc:
        state["config_error"] = str(exc)
print(json.dumps(state, indent=2, sort_keys=True))
PY
}

state_field() {  # $1 = state json file, $2 = python expression over 's'
  python3 -c "import json,sys; s=json.load(open(sys.argv[1])); print(sys.argv[2] and eval(sys.argv[2]))" \
    "$1" "$2" 2>/dev/null || echo ""
}

# ----------------------------------------------------------------------
# Backup / restore (contract U-3, U-5, U-6)
# ----------------------------------------------------------------------
safe_rm() {
  case "$1" in
    ""|"/") die "menolak menghapus path berbahaya: '$1'" ;;
    /*) ;;   # normal absolute path
    *)  die "menolak menghapus path relatif: '$1' (harus absolut)" ;;
  esac
  rm -rf "$1"
}

make_backups() {
  BACKUP_ROOT="${BACKUPS_DIR:-/var/backups/uaeg}"
  mkdir -p "$BACKUP_ROOT"
  chmod 0700 "$BACKUP_ROOT"
}

create_backup() {  # requires: make_backups, TS
  BACKUP_DIR="$BACKUP_ROOT/upgrade-$TS"
  local n=0
  while [[ -e "$BACKUP_DIR" ]]; do
    n=$((n + 1))
    BACKUP_DIR="$BACKUP_ROOT/upgrade-$TS-$n"
  done
  mkdir -p "$BACKUP_DIR"
  chmod 0700 "$BACKUP_DIR"

  say "backup pra-upgrade (rollback safety): $BACKUP_DIR"

  # 1) consistent SQLite snapshot via the online backup API (service may
  #    keep running; the snapshot is transactionally consistent)
  mkdir -p "$BACKUP_DIR/data"
  if [[ -f "$DATA_DIR/uaeg.db" ]]; then
    python3 - "$DATA_DIR/uaeg.db" "$BACKUP_DIR/data/uaeg.db" <<'PY' \
      || die "backup database gagal - upgrade dibatalkan, TIDAK ADA yang berubah"
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
dst.close()
PY
  fi

  # 2) everything else in the data dir (jobs, sessions, tls, password hash);
  #    top-level entries only (cp -a recurses into dirs) - a recursive find
  #    would emit dirs AND their files, making cp copy nested files twice and
  #    abort with "will not overwrite just-created".
  #    The live db + its wal/shm are skipped (the snapshot above replaces them).
  if [[ -d "$DATA_DIR" ]]; then
    ( cd "$DATA_DIR" && find . -mindepth 1 -maxdepth 1 \
        ! -name uaeg.db ! -name uaeg.db-wal ! -name uaeg.db-shm \
        -exec cp -a --target-directory "$BACKUP_DIR/data/" {} + ) \
      || die "backup data dir gagal - upgrade dibatalkan, TIDAK ADA yang berubah"
  fi

  # 3) configuration
  if [[ -d "$CONFIG_DIR" ]]; then
    cp -a "$CONFIG_DIR" "$BACKUP_DIR/config" \
      || die "backup konfigurasi gagal - upgrade dibatalkan, TIDAK ADA yang berubah"
  fi

  # 4) the application itself (source + venv) for full rollback
  if [[ -d "$INSTALL_DIR" ]]; then
    cp -a "$INSTALL_DIR" "$BACKUP_DIR/app" \
      || die "backup aplikasi gagal - upgrade dibatalkan, TIDAK ADA yang berubah"
  fi

  BACKUP_MADE=1
}

restore_backup() {  # $1 = backup dir; restores data + config (+ app if present)
  local b="$1"
  [[ -n "$b" && -d "$b" ]] || die "backup tidak ditemukan: $b"

  say "mengembalikan state pra-upgrade dari: $b"
  svc_stop

  if [[ -d "$b/app" ]]; then
    safe_rm "$INSTALL_DIR"
    cp -a "$b/app" "$INSTALL_DIR" && say "aplikasi dipulihkan" \
      || warn "gagal memulihkan aplikasi dari $b/app - pulihkan manual"
  else
    warn "backup aplikasi tidak tersimpan pada backup ini (data+config saja)"
  fi

  if [[ -d "$b/data" ]]; then
    safe_rm "$DATA_DIR"
    cp -a "$b/data" "$DATA_DIR" && say "data dipulihkan ($DATA_DIR)" \
      || warn "gagal memulihkan data dari $b/data - pulihkan manual (service JANGAN dinyalakan dulu)"
  fi

  if [[ -d "$b/config" ]]; then
    mkdir -p "$CONFIG_DIR"
    cp -a "$b/config/." "$CONFIG_DIR/" && say "konfigurasi dipulihkan" \
      || warn "gagal memulihkan konfigurasi dari $b/config"
  fi

  svc_restart
}

prune_backups() {  # keep the newest $KEEP_BACKUPS upgrade dirs (always >= 1:
                   # retention must never remove the only recovery point)
  local keep="$KEEP_BACKUPS"
  [[ "$keep" =~ ^[0-9]+$ ]] || keep=5
  [[ "$keep" -ge 1 ]] || keep=1
  local old
  while IFS= read -r old; do
    warn "retensi: menghapus backup lama $old (keep=$keep)"
    safe_rm "$old"
  done < <(find "$BACKUP_ROOT" -maxdepth 1 -name 'upgrade-*' -type d 2>/dev/null | sort -r | tail -n +$((keep + 1)))
}

# ----------------------------------------------------------------------
# RECORD.json helpers (never contains secrets: no verifiers, no hashes)
# ----------------------------------------------------------------------
record_set() {  # $1 = file, remaining: key=value pairs
  local f="$1"; shift
  python3 - "$f" "$@" <<'PY'
import json, os, sys
path, pairs = sys.argv[1], sys.argv[2:]
rec = {}
if os.path.exists(path):
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except ValueError:
        rec = {}
for p in pairs:
    k, _, v = p.partition("=")
    if v == "true":
        v = True
    elif v == "false":
        v = False
    elif v.lstrip("-").isdigit():
        v = int(v)
    rec[k] = v
with open(path, "w", encoding="utf-8") as fh:
    json.dump(rec, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

record_event() {  # $1 = file, $2 = event text
  python3 - "$1" "$2" <<'PY'
import json, os, sys
path, text = sys.argv[1], sys.argv[2]
rec = {}
if os.path.exists(path):
    try:
        rec = json.load(open(path, encoding="utf-8"))
    except ValueError:
        rec = {}
rec.setdefault("events", []).append(text)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(rec, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

latest_backup_dir() {
  find "$BACKUP_ROOT" -maxdepth 1 -name 'upgrade-*' -type d 2>/dev/null | sort -r | sed -n 1p
}

# ----------------------------------------------------------------------
# Version helpers
# ----------------------------------------------------------------------
ver_gt() {  # true when $1 > $2 (semver-ish); equal is NOT greater
  [[ "$1" != "$2" && "$(printf '%s\n' "$1" "$2" | sort -V | sed -n 2p)" == "$1" ]]
}

pyproject_version() {  # $1 = source dir
  sed -n 's/^version[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$1/pyproject.toml" 2>/dev/null | head -1
}

# ----------------------------------------------------------------------
# Target source resolution (contract U-1, U-2) — CONTRACT v2:
# the release source is ALWAYS fetched into an isolated staging clone.
# The operator's git working tree is never read for content and never
# mutated (no fetch-into-clone, no checkout, no reset, no clean), so a
# downloaded upgrade.sh inside the operator's clone can never block an
# upgrade (v0.3.1 incident — see report/ROOT_CAUSE_ANALYSIS.md).
# ----------------------------------------------------------------------
resolve_repo_url() {
  # precedence: --repo flag > operator clone's origin > public default.
  # The operator's choice of clone origin is respected (mirrors/forks),
  # and the URL actually used is always printed and recorded.
  if [[ $REPO_GIVEN -eq 1 ]]; then
    REPO_URL="$OPT_REPO"
  elif [[ -d "$SCRIPT_DIR/.git" ]]; then
    local u=""
    u="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)" || true
    if [[ -n "$u" ]]; then
      REPO_URL="$u"
    else
      REPO_URL="$OPT_REPO"
    fi
  else
    REPO_URL="$OPT_REPO"
  fi
}

resolve_target_ref() {
  resolve_repo_url
  if [[ -n "$OPT_SOURCE" ]]; then return 0; fi   # --source decides the target, no ref needed
  if [[ -n "$OPT_VERSION" ]]; then
    TARGET_REF="v${OPT_VERSION#v}"
    return 0
  fi
  if [[ $OPT_MAIN -eq 1 ]]; then
    TARGET_REF="origin/main"
    return 0
  fi
  # Latest tag straight from the REMOTE (network truth, read-only).
  # Never resolved from the operator's possibly-stale local tags, and no
  # fetch is performed inside the operator's clone.
  TARGET_REF="$(timeout 30 git ls-remote --tags --refs "$REPO_URL" 'v*' 2>/dev/null \
                | awk -F/ '{print $NF}' | sort -V | tail -1 || true)"
  if [[ -z "$TARGET_REF" && -d "$SCRIPT_DIR/.git" ]]; then
    # offline fallback: local tags may be stale — say so explicitly
    TARGET_REF="$(git -C "$SCRIPT_DIR" tag -l 'v[0-9]*' | sort -V | tail -1)"
    [[ -n "$TARGET_REF" ]] && warn "ls-remote gagal - memakai tag lokal (mungkin kadaluarsa): $TARGET_REF"
  fi
  if [[ -z "$TARGET_REF" ]]; then
    TARGET_REF="origin/main"
    warn "tidak ada release tag ditemukan - menggunakan origin/main"
  fi
}

tag_exists_on_remote() {  # is TARGET_REF present on $REPO_URL?
  # Distinguishes "tag missing" (operator asked for a wrong version) from
  # "network/remote failure" so neither is misreported as the other
  # (the v0.3.1 incident misreported a checkout conflict as a missing tag).
  local out="" rc=0
  out="$(timeout 30 git ls-remote --tags --refs "$REPO_URL" 2>/dev/null)" || rc=$?
  if [[ $rc -ne 0 ]]; then
    die "git ls-remote gagal ke $REPO_URL (offline? remote salah?) - tidak ada yang berubah"
  fi
  printf '%s\n' "$out" | awk -v t="refs/tags/$TARGET_REF" '$2==t{f=1} END{exit !f}'
}

handle_stage_failure() {  # $1 = message, $2 = tolerance ("required"|"best-effort")
  if [[ -n "$TMP_CLONE" && -d "$TMP_CLONE" ]]; then rm -rf "$TMP_CLONE"; TMP_CLONE=""; fi
  if [[ "$2" == "best-effort" ]]; then
    warn "dry-run: $1"
    return 1
  fi
  die "$1"
}

materialize_source() {  # $1 = "required" | "best-effort" (dry-run tolerance)
  local tolerance="${1:-required}"
  resolve_repo_url

  if [[ -n "$OPT_SOURCE" ]]; then
    SOURCE_DIR="$OPT_SOURCE"
    [[ -f "$SOURCE_DIR/pyproject.toml" ]] \
      || die "--source tidak valid: pyproject.toml tidak ditemukan di $SOURCE_DIR"
    TARGET_REF="--source"
  else
    # Fetch the immutable release source into an ISOLATED STAGING clone.
    # The operator's working tree ($SCRIPT_DIR) is never touched: this is
    # the v0.3.2 fix for the v0.3.1 incident where `git checkout <tag>`
    # inside the operator's clone collided with the downloaded (untracked)
    # upgrade.sh and was misreported as "tag not found".
    command -v git >/dev/null 2>&1 || die "git tidak ditemukan (wajib untuk mengambil source rilis)"

    TMP_CLONE="$(mktemp -d /tmp/uaeg-upgrade-src.XXXXXX)"   # mode 0700
    if [[ "$TARGET_REF" == "origin/main" ]]; then
      say "meng-clone branch utama dari $REPO_URL ke staging terisolasi"
      if ! git clone --quiet --depth 1 "$REPO_URL" "$TMP_CLONE/uaeg" 2>/dev/null; then
        handle_stage_failure "git clone gagal dari $REPO_URL (offline? remote salah?) - tidak ada yang berubah" "$tolerance"
        return 1
      fi
    else
      # Verify the tag exists BEFORE cloning, so a missing tag is never
      # misreported as a clone/network failure (and vice versa).
      if ! tag_exists_on_remote; then
        handle_stage_failure "tag '$TARGET_REF' tidak ditemukan di remote $REPO_URL.
Daftar tag yang tersedia: git ls-remote --tags $REPO_URL" "$tolerance"
        return 1
      fi
      say "meng-clone tag $TARGET_REF dari $REPO_URL ke staging terisolasi"
      if ! git clone --quiet --depth 1 --branch "$TARGET_REF" "$REPO_URL" "$TMP_CLONE/uaeg" 2>/dev/null; then
        handle_stage_failure "git clone tag $TARGET_REF gagal dari $REPO_URL (offline? remote salah?) - tidak ada yang berubah" "$tolerance"
        return 1
      fi
      # Prove the staging clone is EXACTLY at the requested tag's commit.
      local want="" got=""
      want="$(git -C "$TMP_CLONE/uaeg" rev-parse "refs/tags/${TARGET_REF}^{commit}" 2>/dev/null || true)"
      got="$(git -C "$TMP_CLONE/uaeg" rev-parse HEAD 2>/dev/null || true)"
      if [[ -z "$want" || "$want" != "$got" ]]; then
        handle_stage_failure "staging clone tidak berada di commit tag $TARGET_REF (HEAD=$got, tag=$want) - tidak ada yang berubah" "$tolerance"
        return 1
      fi
    fi
    SOURCE_DIR="$TMP_CLONE/uaeg"

    if [[ -d "$SCRIPT_DIR/.git" ]]; then
      say "clone operator di $SCRIPT_DIR TIDAK disentuh (tetap pada kondisi semula)"
    fi
  fi

  TARGET_VERSION="$(pyproject_version "$SOURCE_DIR")"
  [[ -n "$TARGET_VERSION" ]] || die "tidak dapat membaca versi target dari $SOURCE_DIR/pyproject.toml"
  TARGET_SHA="$(git -C "$SOURCE_DIR" rev-parse --short HEAD 2>/dev/null || echo n/a)"

  [[ -f "$SOURCE_DIR/install.sh" ]] || [[ -n "$OPT_INSTALLER" ]] \
    || die "install.sh tidak ditemukan di source; gunakan --installer PATH"
}

# ----------------------------------------------------------------------
# Post-upgrade verification (contract U-4)
# ----------------------------------------------------------------------
verify_upgrade() {  # $1 = before-state.json ; returns 0 when everything passes
  local before="$1" after="$BACKUP_DIR/after-state.json" fails=0

  # 1) the new version is actually installed and importable
  local new_version=""
  if [[ -x "$INSTALL_DIR/.venv/bin/uaeg" ]]; then
    new_version="$(UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" \
      "$INSTALL_DIR/.venv/bin/uaeg" version 2>/dev/null | awk '{print $2}' || true)"
  fi
  if [[ "$new_version" == "$TARGET_VERSION" ]]; then
    say "verifikasi: versi terpasang = $new_version (sesuai target)"
  else
    warn "verifikasi: versi terpasang '$new_version' != target '$TARGET_VERSION'"
    return 1
  fi

  # 2) service / process health
  read_state "$after"
  local mcp_port dash_port dash_tls
  mcp_port="$(state_field "$after" "s['effective']['mcp_port']")"
  dash_port="$(state_field "$after" "s['effective']['dash_port']")"
  dash_tls="$(state_field "$after" "s['effective']['dash_tls']")"

  if [[ $NO_SERVICE -eq 0 ]]; then
    if svc_active; then
      say "verifikasi: service aktif"
    else
      warn "verifikasi: service TIDAK aktif"
      return 1
    fi
    if curl -sk --max-time 3 "https://127.0.0.1:${mcp_port}/healthz" 2>/dev/null | grep -q '"ok"'; then
      say "verifikasi: /healthz OK (port $mcp_port)"
    else
      warn "verifikasi: /healthz tidak menjawab di port $mcp_port"
      return 1
    fi
  elif [[ $SKIP_SMOKE -eq 0 ]]; then
    # no systemd: boot the server briefly and prove it serves (smoke test)
    if curl -sk --max-time 2 "https://127.0.0.1:${mcp_port}/healthz" 2>/dev/null | grep -q '"ok"'; then
      say "verifikasi: instance yang sudah berjalan menjawab /healthz (smoke dilewati)"
    else
      say "verifikasi: smoke test - menjalankan server sesaat (--no-service)"
      local smoke_log="$BACKUP_DIR/smoke-serve.log" smoke_pid=""
      UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" \
        "$INSTALL_DIR/.venv/bin/uaeg" serve >"$smoke_log" 2>&1 &
      smoke_pid=$!
      local healthy=0 i
      for i in $(seq 1 25); do
        if curl -sk --max-time 2 "https://127.0.0.1:${mcp_port}/healthz" 2>/dev/null | grep -q '"ok"'; then
          healthy=1; break
        fi
        sleep 1
      done
      kill "$smoke_pid" 2>/dev/null || true
      local w
      for w in 1 2 3 4 5; do
        kill -0 "$smoke_pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$smoke_pid" 2>/dev/null || true
      wait "$smoke_pid" 2>/dev/null || true
      if [[ $healthy -eq 1 ]]; then
        say "verifikasi: smoke test /healthz OK (port $mcp_port)"
      else
        warn "verifikasi: smoke test GAGAL (lihat $smoke_log)"
        return 1
      fi
    fi
  else
    warn "verifikasi: smoke test dilewati (--skip-smoke) - bukti kesehatan berkurang"
  fi

  # 3) login page still enforced when a password is configured (soft check)
  if [[ "$(state_field "$after" "s['password_configured']")" == "True" ]]; then
    local scheme="http"; [[ "$dash_tls" == "True" ]] && scheme="https"
    local code
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 3 "$scheme://127.0.0.1:${dash_port}/" 2>/dev/null || echo 000)"
    if [[ "$code" == "200" || "$code" == "302" ]]; then
      say "verifikasi: halaman dashboard menjawab ($code)"
    else
      warn "verifikasi: halaman dashboard menjawab $code (diharapkan 200/302)"
      return 1
    fi
  fi

  # 4) zero data loss: compare before/after states
  say "verifikasi: perbandingan state zero-data-loss"
  python3 - "$before" "$after" <<'PY' || return 1
import json, sys

before, after = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  GAGAL {name}  ({detail})")

b_keys = {(k["key_id"], k["status"]) for k in before.get("keys") or []}
a_keys = {(k["key_id"], k["status"]) for k in after.get("keys") or []}
check("agent keys identik (key_id + status)", b_keys == a_keys,
      f"sebelum={sorted(b_keys)} sesudah={sorted(a_keys)}")

check("integritas database (quick_check)", after.get("quick_check") == "ok",
      f"hasil={after.get('quick_check')!r}")

bc, ac = before.get("counts") or {}, after.get("counts") or {}
check("jumlah keys tidak berkurang",
      bc.get("keys") is None or (ac.get("keys") is not None and ac["keys"] == bc["keys"]),
      f"sebelum={bc.get('keys')} sesudah={ac.get('keys')}")
for t in ("jobs", "sessions", "exec_logs"):
    check(f"jumlah {t} tidak berkurang",
          bc.get(t) is None or (ac.get(t) is not None and ac[t] >= bc[t]),
          f"sebelum={bc.get(t)} sesudah={ac.get(t)}")

b_eff, a_eff = before.get("effective") or {}, after.get("effective") or {}
for f in ("mcp_port", "dash_port", "dash_bind", "dash_tls", "allow_public",
          "tls_mode", "public_host"):
    check(f"config {f} tidak berubah", b_eff.get(f) == a_eff.get(f),
          f"sebelum={b_eff.get(f)!r} sesudah={a_eff.get(f)!r}")

if before.get("password_configured"):
    check("password dashboard tetap terkonfigurasi",
          after.get("password_configured") is True)

print()
if failures:
    print(f"VERIFIKASI GAGAL: {len(failures)} check tidak lolos")
    sys.exit(1)
print("semua check zero-data-loss lolos")
PY
  return 0
}

# ----------------------------------------------------------------------
# Rollback (contract U-5)
# ----------------------------------------------------------------------
perform_rollback() {
  [[ $ROLLED_BACK -eq 1 ]] && return 0
  ROLLED_BACK=1
  warn "UPGRADE GAGAL - mengembalikan seluruh state pra-upgrade secara otomatis"
  restore_backup "$BACKUP_DIR"

  # prove the restored state actually works
  local check_json="$BACKUP_DIR/rollback-state.json"
  read_state "$check_json"
  local restored_ok=1
  if [[ -x "$INSTALL_DIR/.venv/bin/uaeg" ]]; then
    local v
    v="$(UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" \
      "$INSTALL_DIR/.venv/bin/uaeg" version 2>/dev/null | awk '{print $2}' || true)"
    if [[ "$v" == "$CUR_VERSION" ]]; then
      say "rollback terverifikasi: aplikasi kembali ke v$v"
    else
      warn "rollback: versi terbaca '$v' (diharapkan $CUR_VERSION)"
      restored_ok=0
    fi
  fi
  if [[ $NO_SERVICE -eq 0 ]]; then
    if svc_active; then
      say "rollback terverifikasi: service aktif kembali"
    else
      warn "rollback: service tidak aktif - jalankan manual: systemctl start $SERVICE_NAME"
      restored_ok=0
    fi
  elif [[ $SKIP_SMOKE -eq 0 ]]; then
    # no systemd: prove the RESTORED app can actually serve
    local mcp_port_rb
    mcp_port_rb="$(state_field "$check_json" "s['effective']['mcp_port']")"
    if [[ "$mcp_port_rb" =~ ^[0-9]+$ ]]; then
      UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" \
        "$INSTALL_DIR/.venv/bin/uaeg" serve >"$BACKUP_DIR/rollback-smoke.log" 2>&1 &
      local rsmoke_pid=$!
      local rhealthy=0 ri
      for ri in $(seq 1 20); do
        if curl -sk --max-time 2 "https://127.0.0.1:${mcp_port_rb}/healthz" 2>/dev/null | grep -q '"ok"'; then
          rhealthy=1; break
        fi
        sleep 1
      done
      kill "$rsmoke_pid" 2>/dev/null || true
      for ri in 1 2 3 4 5; do kill -0 "$rsmoke_pid" 2>/dev/null || break; sleep 1; done
      kill -9 "$rsmoke_pid" 2>/dev/null || true
      wait "$rsmoke_pid" 2>/dev/null || true
      if [[ $rhealthy -eq 1 ]]; then
        say "rollback terverifikasi: aplikasi lama menjawab /healthz"
      else
        warn "rollback: aplikasi terpulihkan tidak menjawab /healthz (lihat $BACKUP_DIR/rollback-smoke.log)"
        restored_ok=0
      fi
    fi
  fi
  if [[ -f "$BACKUP_DIR/RECORD.json" ]]; then
    record_set "$BACKUP_DIR/RECORD.json" "result=rolled-back" \
      "reason=upgrade gagal; state pra-upgrade dipulihkan otomatis" \
      "rollback_verified=$( [[ $restored_ok -eq 1 ]] && echo true || echo false )"
  fi
  warn "backup lengkap tetap tersimpan di $BACKUP_DIR (bukti + pemulihan manual)"
}

on_exit() {
  local rc=$?
  trap - EXIT INT TERM
  [[ -n "$TMP_CLONE" && -d "$TMP_CLONE" ]] && rm -rf "$TMP_CLONE"
  if [[ $rc -ne 0 && "$MODE" == "upgrade" && $BACKUP_MADE -eq 1 \
        && $ROLLED_BACK -eq 0 ]]; then
    perform_rollback
    rc=2
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ----------------------------------------------------------------------
# MODE: --check  (read-only status; contract U-11)
# ----------------------------------------------------------------------
run_check() {
  echo
  printf '\033[1mStatus instalasi UAEG\033[0m\n'
  echo "  instalasi      : $INSTALL_DIR $([[ $IS_INSTALLED -eq 1 ]] && echo '(terdeteksi)' || echo '(TIDAK terdeteksi)')"
  echo "  versi          : $CUR_VERSION"
  echo "  data           : $DATA_DIR"
  echo "  konfigurasi    : $CONFIG_FILE"
  echo "  service        : $([[ $NO_SERVICE -eq 1 ]] && echo 'tanpa systemd (--no-service)' || echo "$SERVICE_NAME ($(svc_active && echo aktif || echo nonaktif))")"
  if [[ $IS_INSTALLED -eq 1 ]]; then
    local st="$BACKUP_ROOT/.check-state.json"
    read_state "$st"
    echo "  database       : $(state_field "$st" "s['db_exists']")  quick_check=$(state_field "$st" "s['quick_check']")"
    echo "  keys           : aktif=$(state_field "$st" "len([k for k in s['keys'] if k['status']=='active'])")  revoked=$(state_field "$st" "len([k for k in s['keys'] if k['status']=='revoked'])")"
    echo "  dashboard      : bind=$(state_field "$st" "s['effective']['dash_bind']")  port=$(state_field "$st" "s['effective']['dash_port']")  tls=$(state_field "$st" "s['effective']['dash_tls']")"
    echo "  password dash  : $(state_field "$st" "s['password_configured']")"
    rm -f "$st"
  fi
  echo
  printf '\033[1mRiwayat upgrade\033[0m\n'
  local last
  last="$(latest_backup_dir)"
  if [[ -n "$last" && -f "$last/RECORD.json" ]]; then
    python3 -c "
import json
r = json.load(open('$last/RECORD.json'))
print(f\"  terakhir       : {r.get('finished_at','?')}  {r.get('from_version','?')} -> {r.get('to_version','?')}  hasil={r.get('result','?')}\")
"
  else
    echo "  (belum ada riwayat upgrade tercatat)"
  fi
  local nb
  nb="$(find "$BACKUP_ROOT" -maxdepth 1 -name 'upgrade-*' -type d 2>/dev/null | wc -l)"
  echo "  backup tersedia: $nb (di $BACKUP_ROOT)"
  echo
  printf '\033[1mTarget upgrade\033[0m\n'
  resolve_target_ref
  if [[ "$TARGET_REF" == origin/main ]]; then
    echo "  target         : origin/main (tidak ada tag rilis)"
  else
    echo "  target         : $TARGET_REF"
    if ver_gt "${TARGET_REF#v}" "$CUR_VERSION"; then
      echo "  status         : update tersedia ($CUR_VERSION -> ${TARGET_REF#v})"
    elif [[ "${TARGET_REF#v}" == "$CUR_VERSION" ]]; then
      echo "  status         : sudah pada versi rilis terbaru"
    else
      echo "  status         : versi terpasang lebih baru dari tag ($CUR_VERSION > ${TARGET_REF#v})"
    fi
  fi
  echo
  [[ $IS_INSTALLED -eq 1 ]] || exit 3
}

# ----------------------------------------------------------------------
# MODE: --rollback  (contract U-6)
# ----------------------------------------------------------------------
run_rollback() {
  [[ $IS_INSTALLED -eq 1 ]] || die "tidak ada instalasi UAEG terdeteksi - tidak ada yang di-rollback"
  local target="$ROLLBACK_TARGET"
  if [[ -z "$target" ]]; then
    target="$(latest_backup_dir)"
    [[ -n "$target" ]] || die "tidak ada backup upgrade ditemukan di $BACKUP_ROOT"
  elif [[ ! -d "$target" ]]; then
    # allow a timestamp prefix or a bare dir name
    target="$BACKUP_ROOT/upgrade-${target#upgrade-}"
    [[ -d "$target" ]] || target="$(find "$BACKUP_ROOT" -maxdepth 1 -name "upgrade-*$(basename "$ROLLBACK_TARGET")*" -type d 2>/dev/null | sort -r | sed -n 1p)"
    [[ -n "$target" && -d "$target" ]] || die "backup tidak ditemukan untuk '$ROLLBACK_TARGET' (lihat: ls $BACKUP_ROOT)"
  fi

  echo
  printf '\033[1mRollback manual\033[0m\n'
  echo "  backup   : $target"
  if [[ -f "$target/RECORD.json" ]]; then
    python3 -c "
import json
r = json.load(open('$target/RECORD.json'))
print(f\"  asal     : {r.get('from_version','?')} -> {r.get('to_version','?')}  hasil={r.get('result','?')}\")
"
  fi
  echo "  aplikasi : $([[ -d "$target/app" ]] && echo 'ikut dipulihkan' || echo 'TIDAK tersimpan (data+config saja)')"
  if [[ ! -d "$target/app" && -f "$target/RECORD.json" ]]; then
    # honest recovery path: the app itself is re-installable from the
    # immutable release tag recorded in this backup (contract U-6/U-7)
    local from_v=""
    from_v="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('from_version','?'))" \
               "$target/RECORD.json" 2>/dev/null || true)"
    if [[ -n "$from_v" && "$from_v" != "?" ]]; then
      echo "             (pemulihan aplikasi: install.sh dari tag v$from_v yang immutable,"
      echo "              lalu --rollback untuk data+config dari backup ini)"
    fi
  fi
  echo
  warn "rollback mengganti data & konfigurasi dengan kondisi PRA-UPGRADE."
  warn "perubahan yang dibuat SETELAH backup itu (key baru, job baru) akan hilang."
  if [[ $ASSUME_YES -ne 1 ]]; then
    read -r -p "Lanjutkan rollback? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || die "dibatalkan"
  fi

  restore_backup "$target"
  if [[ -f "$target/RECORD.json" ]]; then
    record_event "$target/RECORD.json" \
      "manual-rollback pada $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      || warn "tidak dapat mencatat event manual-rollback di RECORD.json"
  fi

  say "rollback selesai. Verifikasi:"
  UAEG_CONFIG="$CONFIG_FILE" UAEG_DATA_DIR="$DATA_DIR" \
    "$INSTALL_DIR/.venv/bin/uaeg" doctor 2>/dev/null || warn "uaeg doctor melaporkan masalah - periksa manual"
  echo
  echo "  service : systemctl status $SERVICE_NAME"
  echo "  keys    : sudo uaeg keys list"
  echo
}

# ----------------------------------------------------------------------
# MODE: upgrade / dry-run (contract U-1 .. U-12)
# ----------------------------------------------------------------------
run_upgrade() {
  # ---- guards --------------------------------------------------------
  if [[ $IS_INSTALLED -eq 0 ]]; then
    die "instalasi UAEG tidak terdeteksi di $INSTALL_DIR.
upgrade.sh khusus untuk UPGRADE instalasi yang sudah ada.
Instalasi baru: git clone $OPT_REPO && cd uaeg && sudo ./install.sh --domain <domain-anda>" 3
  fi

  # public dashboard without a password must never pass through an upgrade
  local pre="$BACKUP_ROOT/.pre-state.json"
  read_state "$pre"
  local bind allow_pub pw
  bind="$(state_field "$pre" "s['effective']['dash_bind']")"
  allow_pub="$(state_field "$pre" "s['effective']['allow_public']")"
  pw="$(state_field "$pre" "s['password_configured']")"
  if [[ "$bind" == "0.0.0.0" || "$bind" == "::" || "$allow_pub" == "True" ]]; then
    if [[ "$pw" != "True" ]]; then
      rm -f "$pre"
      die "dashboard terikat publik ($bind) tetapi password belum dikonfigurasi.
 demi keamanan upgrade ditolak. Jalankan dulu:  sudo uaeg pass  (interaktif)
 lalu jalankan ulang upgrade.sh. Password dashboard independen dari agent key MCP."
    fi
  fi

  # ---- resolve target --------------------------------------------------
  resolve_target_ref
  if [[ "$MODE" == "dry-run" ]]; then
    # Stage the target source too (isolated, read-only for the operator), so
    # the plan shows the REAL target version + commit — without changing
    # anything. If the source cannot be fetched (e.g. offline), degrade to a
    # plan without the version instead of failing the dry-run.
    if ! materialize_source best-effort; then
      TARGET_VERSION="(tidak terbaca)"
      TARGET_SHA="n/a"
    fi
    echo
    printf '\033[1mRENCANA UPGRADE (dry-run - tidak ada perubahan)\033[0m\n'
    echo "  instalasi   : $INSTALL_DIR (v$CUR_VERSION)"
    echo "  target      : v$TARGET_VERSION ($TARGET_REF, commit $TARGET_SHA)"
    if [[ -n "$OPT_SOURCE" ]]; then
      echo "  sumber      : $OPT_SOURCE (lokal, --source)"
    else
      echo "  sumber      : $REPO_URL (staging terisolasi; clone operator tidak disentuh)"
    fi
    echo "  data        : $DATA_DIR"
    echo "  konfigurasi : $CONFIG_FILE (dipertahankan apa adanya - tidak diubah)"
    echo "  backup      : $BACKUP_ROOT/upgrade-<ts> (DB konsisten + config + aplikasi)"
    echo "  layanan     : $([[ $NO_SERVICE -eq 1 ]] && echo 'tanpa systemd' || echo "restart terkontrol $SERVICE_NAME (downtime ±15-60 detik)")"
    echo
    echo "jalankan tanpa --dry-run untuk mengeksekusi."
    rm -f "$pre"
    return 0
  fi

  materialize_source required

  # ---- version guards ---------------------------------------------------
  if ver_gt "$CUR_VERSION" "$TARGET_VERSION" && [[ $ALLOW_DOWNGRADE -eq 0 ]]; then
    die "target v$TARGET_VERSION LEBIH TUA daripada terpasang v$CUR_VERSION (downgrade).
Jika memang disengaja: sudo bash upgrade.sh --version $TARGET_VERSION --allow-downgrade" 5
  fi
  if [[ "$CUR_VERSION" == "$TARGET_VERSION" ]]; then
    say "versi target sama dengan terpasang (v$CUR_VERSION) - melanjutkan sebagai re-install/repair"
  fi

  # ---- disk space guard ---------------------------------------------------
  local need_kb=51200 app_kb=0 data_kb=0 avail_kb=0
  if [[ -d "$INSTALL_DIR" ]]; then
    app_kb="$(du -sk "$INSTALL_DIR" 2>/dev/null | awk '{print $1}')" || app_kb=0
  fi
  if [[ -d "$DATA_DIR" ]]; then
    data_kb="$(du -sk "$DATA_DIR" 2>/dev/null | awk '{print $1}')" || data_kb=0
  fi
  avail_kb="$(df -Pk "$BACKUP_ROOT" 2>/dev/null | awk 'NR==2{print $4}')"
  need_kb=$(( (app_kb + data_kb) * 12 / 10 + 51200 ))
  if [[ ! "$avail_kb" =~ ^[0-9]+$ ]] || [[ "$avail_kb" -lt "$need_kb" ]]; then
    die "ruang disk tidak cukup untuk backup di $BACKUP_ROOT: butuh ~$((need_kb / 1024)) MiB, tersedia $((avail_kb / 1024)) MiB"
  fi

  # ---- plan + confirm -------------------------------------------------------
  local n_keys
  n_keys="$(state_field "$pre" "len(s['keys'])")"
  echo
  printf '\033[1mRENCANA UPGRADE\033[0m\n'
  echo "  instalasi   : $INSTALL_DIR (v$CUR_VERSION)"
  echo "  target      : v$TARGET_VERSION ($TARGET_REF, commit $TARGET_SHA)"
  echo "  data        : $DATA_DIR ($n_keys kunci agent)"
  echo "  konfigurasi : $CONFIG_FILE (dipertahankan apa adanya - tidak diubah)"
  echo "  backup      : $BACKUP_ROOT/upgrade-$TS (DB konsisten + config + aplikasi)"
  echo "  layanan     : $([[ $NO_SERVICE -eq 1 ]] && echo 'tanpa systemd' || echo "restart terkontrol $SERVICE_NAME (downtime ±15-60 detik)")"
  echo
  if [[ $ASSUME_YES -ne 1 ]]; then
    read -r -p "Lanjutkan upgrade ke v$TARGET_VERSION? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || { rm -f "$pre"; die "dibatalkan oleh operator"; }
  fi

  # ---- backup (U-3) ----------------------------------------------------------
  create_backup
  cp "$pre" "$BACKUP_DIR/before-state.json"
  rm -f "$pre"
  record_set "$BACKUP_DIR/RECORD.json" \
    "tool=upgrade.sh" "contract_version=2" "mode=upgrade" \
    "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "from_version=$CUR_VERSION" "to_version=$TARGET_VERSION" \
    "target_ref=$TARGET_REF" "target_git_sha=$TARGET_SHA" \
    "repo_url=$REPO_URL" \
    "source_dir=$SOURCE_DIR" "install_dir=$INSTALL_DIR" "data_dir=$DATA_DIR" \
    "config_file=$CONFIG_FILE" "service_name=$SERVICE_NAME" \
    "service_managed=$( [[ $NO_SERVICE -eq 0 ]] && echo true || echo false )" \
    "service_active_before=$(svc_active && echo true || echo false)" \
    "result=in-progress"

  # ---- execute the upgrade -----------------------------------------------------
  local installer="${OPT_INSTALLER:-$SOURCE_DIR/install.sh}"
  say "menjalankan installer: $installer (mode upgrade, konfigurasi lama dipertahankan)"
  local install_args=(--yes --install-dir "$INSTALL_DIR" --data-dir "$DATA_DIR"
                      --config-dir "$CONFIG_DIR")
  [[ $NO_SERVICE -eq 1 ]] && install_args+=(--no-service)
  if ! bash "$installer" "${install_args[@]}" 2>&1 | tee "$BACKUP_DIR/install.log"; then
    warn "installer gagal (log: $BACKUP_DIR/install.log)"
    return 1   # on_exit trap performs the rollback
  fi

  # ---- verify (U-4) --------------------------------------------------------------
  if ! verify_upgrade "$BACKUP_DIR/before-state.json"; then
    warn "verifikasi pasca-upgrade GAGAL"
    return 1   # on_exit trap performs the rollback
  fi

  # ---- success: record, clean, prune ------------------------------------------------
  record_set "$BACKUP_DIR/RECORD.json" \
    "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "result=success" \
    "verification=all-checks-passed" \
    "service_active_after=$(svc_active && echo true || echo false)"
  if [[ -d "$BACKUP_DIR/app" ]]; then
    safe_rm "$BACKUP_DIR/app"
    record_set "$BACKUP_DIR/RECORD.json" "app_backup_retained=false"
  fi
  prune_backups

  echo
  printf '\033[1mUPGRADE BERHASIL: v%s -> v%s\033[0m\n' "$CUR_VERSION" "$TARGET_VERSION"
  echo
  echo "  terverifikasi : versi, service/healthz, agent keys identik, integritas DB,"
  echo "                  konfigurasi dipertahankan, password dashboard utuh"
  echo "  backup        : $BACKUP_DIR (data + config; rollback: sudo bash upgrade.sh --rollback)"
  echo "  log installer : $BACKUP_DIR/install.log"
  if [[ -d "$SCRIPT_DIR/.git" ]]; then
    echo "  catatan       : clone operator $SCRIPT_DIR TIDAK diubah (sumber diambil"
    echo "                  dari staging terisolasi $REPO_URL)"
  fi
  echo
  echo "Langkah berikutnya:"
  echo "  uaeg version && sudo uaeg doctor"
  echo "  sudo uaeg keys list   # pastikan key agent Anda masih lengkap"
  [[ $NO_SERVICE -eq 0 ]] && echo "  systemctl status $SERVICE_NAME"
  echo
}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
main() {
  say "UAEG safe-upgrade installer (contract: docs/UPGRADE_CONTRACT.md, v2)"

  detect_installation
  make_backups
  TS="$(date +%Y%m%d-%H%M%S)"

  case "$MODE" in
    check)     run_check ;;
    rollback)  run_rollback ;;
    dry-run)   run_upgrade ;;
    upgrade)   run_upgrade ;;
    *)         die "mode tidak dikenal: $MODE" ;;
  esac
}

main
