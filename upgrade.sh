#!/usr/bin/env bash
#
# UAEG permanent upgrade bootstrap — docs/UPGRADE_CONTRACT.md (contract v3).
#
# THIS FILE IS THE OPERATOR'S LONG-LIVED BOOTSTRAP. Download it ONCE; every
# future upgrade runs the SAME local file:
#
#     sudo bash upgrade.sh                 # latest stable release
#     sudo bash upgrade.sh --check         # read-only status
#     sudo bash upgrade.sh --dry-run       # show the plan, change nothing
#     sudo bash upgrade.sh --version X.Y.Z # pin an explicit release
#     sudo bash upgrade.sh --rollback      # restore the latest backup
#
# HOW IT WORKS (stable bootstrap -> verified versioned updater -> safe
# application upgrade):
#
#   1. Resolve the target release (latest published GitHub Release, or the
#      explicit --version). The DEFAULT source is the release channel -
#      never a mutable branch.
#   2. Download the release MANIFEST and its Ed25519 SIGNATURE.
#   3. Verify the signature against the trusted public key embedded below
#      (publisher authenticity), then verify the updater's SHA-256 against
#      the signed manifest (artifact integrity). Mismatched, missing or
#      untrusted artifacts are rejected - nothing is executed.
#   4. Execute the verified, versioned updater (uaeg-updater-<v>.sh) which
#      performs the actual safe upgrade: detect -> backup -> isolated
#      staging -> install -> verify zero data loss -> auto-rollback. The
#      updater implementation evolves release by release; this bootstrap
#      stays byte-stable through the documented interface (see
#      docs/BOOTSTRAP.md, interface v1).
#   5. Propagate the updater's exit status; clean temporary files.
#
# The bootstrap never modifies itself, never writes into the operator's
# directories, and never runs an unverified script.
#
# Offline operation: after the first successful upgrade the updater caches a
# verified copy of itself at /var/lib/uaeg/upgrade/last-updater.sh; --rollback
# (and --check) work offline from that cache. Trust-key rotation or an
# interface break would require a one-time bootstrap refresh - an EXCEPTIONAL
# recovery procedure documented in docs/BOOTSTRAP.md, not normal operation.
#
# Exit codes (propagated from the updater when it ran):
#   0 success                 3 no existing installation found
#   1 error (refused/failed)  4 reserved
#   2 failure -> rolled back  5 downgrade refused (use --allow-downgrade)
#
set -uo pipefail

# ----------------------------------------------------------------------
# Bootstrap identity (bumped ONLY for trust-key rotation or an interface
# break - never for routine releases; see docs/BOOTSTRAP.md).
# v2: rotated to the uaeg-release-2 signing key at v0.3.4 (the uaeg-release-1
# private key is no longer available to the publisher).
# v3: rotated to the uaeg-release-3 signing key at v0.4.0 (the uaeg-release-2
# private key is no longer available to the publisher either). The keyring
# still trusts ALL THREE keys so manifests of historical releases keep
# verifying for --version pins/downgrades.
# ----------------------------------------------------------------------
BOOTSTRAP_VERSION="3"
BOOTSTRAP_INTERFACE="1"        # max updater interface this bootstrap speaks
BOOTSTRAP_KEY_IDS="uaeg-release-1,uaeg-release-2,uaeg-release-3"

# ----------------------------------------------------------------------
# Release channel (env overrides exist for mirrors and the contract test
# suite; signature verification applies UNCONDITIONALLY).
# ----------------------------------------------------------------------
RELEASE_API_BASE="${UAEG_RELEASE_API_BASE:-https://api.github.com/repos/smahud/uaeg}"
RELEASE_DOWNLOAD_BASE="${UAEG_RELEASE_DOWNLOAD_BASE:-https://github.com/smahud/uaeg/releases/download}"

# ----------------------------------------------------------------------
# Trusted release-signing public keys (Ed25519, PEM).
# Key rotation policy: docs/BOOTSTRAP.md. Adding a key here is an
# exceptional, operator-visible bootstrap refresh - never silent.
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
# ---- TRUSTED KEYRING END ----

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
UAEG permanent upgrade bootstrap (docs/UPGRADE_CONTRACT.md, contract v3)

Download this file ONCE. Every future upgrade reuses the same local copy:
  sudo bash upgrade.sh

Modes:
  (default)              resolve target release -> verify signed manifest
                         -> run the versioned safe-upgrade updater
  --check                read-only: installation status + available target
  --dry-run              show the upgrade plan, change nothing
  --rollback [ID]        restore the latest upgrade backup (works offline
                         from the cached updater after the first upgrade)

Target selection:
  --version X.Y.Z        upgrade to release tag vX.Y.Z (default: latest
                         published release - never a mutable branch)
  --main                 explicit opt-in: target origin/main via the latest
                         updater (bleeding edge; not the default source)
  --source DIR           upgrade from a local source tree (offline). Runs
                         DIR/updater.sh when present (operator-trusted local
                         file), else fetches the latest updater and forwards
                         --source to it.

Forwarded to the updater (full list: --help of the fetched updater):
  --repo URL  --installer PATH  --yes  --allow-downgrade  --skip-smoke
  --install-dir/--data-dir/--config-dir/--backups-dir PATH
  --keep-backups N  --service-name NAME  --no-service  --backup ID

Bootstrap introspection:
  --bootstrap-info       print bootstrap version, interface and key id

Environment:
  UAEG_RELEASE_API_BASE / UAEG_RELEASE_DOWNLOAD_BASE  release channel
  overrides (mirrors, contract tests). Signatures are ALWAYS verified.
USAGE
}

# ----------------------------------------------------------------------
# Argument parsing (bootstrap-consumed flags; everything else is forwarded)
# ----------------------------------------------------------------------
BOOT_MODE="upgrade"          # upgrade | check | dry-run | rollback | info
OPT_VERSION=""
OPT_MAIN=0
OPT_SOURCE=""
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)          BOOT_MODE="check"; shift ;;
    --dry-run)        BOOT_MODE="dry-run"; shift ;;
    --rollback)       BOOT_MODE="rollback"; FORWARD_ARGS+=("$1"); shift ;;
    --version)        OPT_VERSION="${2#v}"; FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
    --main)           OPT_MAIN=1; FORWARD_ARGS+=("$1"); shift ;;
    --source)         OPT_SOURCE="$2"; FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
    --bootstrap-info) BOOT_MODE="info"; shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                FORWARD_ARGS+=("$1"); shift ;;
  esac
done

# ----------------------------------------------------------------------
# Root check (the updater needs root for /opt, /etc, /var and systemd)
# ----------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    say "re-running as root via sudo..."
    exec sudo "$0" "$@"
  fi
  die "this script must run as root (try: sudo bash upgrade.sh)"
fi

command -v curl >/dev/null 2>&1 || die "curl is required (release discovery and artifact download)"
command -v openssl >/dev/null 2>&1 || die "openssl is required (manifest signature verification)"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required (artifact integrity verification)"
command -v python3 >/dev/null 2>&1 || die "python3 is required (manifest parsing)"
command -v bash >/dev/null 2>&1 || die "bash is required"

BOOTSTRAP_FILE="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
BOOTSTRAP_SHA_BEFORE="$(sha256sum "$BOOTSTRAP_FILE" 2>/dev/null | awk '{print $1}' || echo unknown)"

# ----------------------------------------------------------------------
# Bootstrap info mode
# ----------------------------------------------------------------------
if [[ "$BOOT_MODE" == "info" ]]; then
  _fp() { printf '%s' "$1" | sha256sum | awk '{print substr($1,1,16)}'; }
  cat <<INFO
UAEG upgrade bootstrap
  bootstrap version : $BOOTSTRAP_VERSION
  updater interface : v$BOOTSTRAP_INTERFACE (max supported)
  trusted keys      : $BOOTSTRAP_KEY_IDS (fingerprints $(_fp "$_TRUSTED_KEY_1"), $(_fp "$_TRUSTED_KEY_2"), $(_fp "$_TRUSTED_KEY_3"))
  release channel   : $RELEASE_API_BASE
  this file         : $BOOTSTRAP_FILE (sha256 ${BOOTSTRAP_SHA_BEFORE:0:16}…)
  contract          : docs/UPGRADE_CONTRACT.md (v3), docs/BOOTSTRAP.md
INFO
  exit 0
fi

# ----------------------------------------------------------------------
# Temp workspace (0700); cleaned on any exit path
# ----------------------------------------------------------------------
WORK="$(mktemp -d /tmp/uaeg-bootstrap.XXXXXX)"
chmod 700 "$WORK"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

# ----------------------------------------------------------------------
# Trust: write the trusted public key(s) and verify a manifest signature
# against ANY of them (a release signed by any trusted key is accepted).
# ----------------------------------------------------------------------
KEY_DIR="$WORK/trusted"
mkdir -p "$KEY_DIR"
printf '%s\n' "$_TRUSTED_KEY_1" > "$KEY_DIR/1.pub"
printf '%s\n' "$_TRUSTED_KEY_2" > "$KEY_DIR/2.pub"
printf '%s\n' "$_TRUSTED_KEY_3" > "$KEY_DIR/3.pub"

verify_manifest_signature() {  # $1=manifest $2=signature ; 0 = trusted
  local k
  for k in "$KEY_DIR"/*.pub; do
    if openssl pkeyutl -verify -pubin -inkey "$k" \
      -rawin -in "$1" -sigfile "$2" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

# ----------------------------------------------------------------------
# Release discovery (GitHub Releases API; never a mutable branch by default)
# ----------------------------------------------------------------------
fetch_latest_tag() {
  curl -fsSL --max-time 30 \
    -H "Accept: application/vnd.github+json" \
    "$RELEASE_API_BASE/releases/latest" 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('tag_name',''))" \
    || echo ""
}

# ----------------------------------------------------------------------
# Manifest download + verification + updater fetch
# ----------------------------------------------------------------------
CACHE_DIR="${UAEG_UPGRADE_CACHE_DIR:-/var/lib/uaeg/upgrade}"
CACHED_UPDATER="$CACHE_DIR/last-updater.sh"
CACHED_MANIFEST="$CACHE_DIR/last-manifest.json"

download() {  # $1=url $2=dest
  curl -fsSL --max-time 120 -o "$2" "$1"
}

fetch_verified_updater() {  # sets UPDATER_PATH + MANIFEST_PATH; dies on any failure
  local tag="$1" ver="$2" manifest_url sig_url
  manifest_url="$RELEASE_DOWNLOAD_BASE/$tag/uaeg-manifest.json"
  sig_url="$RELEASE_DOWNLOAD_BASE/$tag/uaeg-manifest.json.sig"

  say "fetching release manifest: $manifest_url"
  download "$manifest_url" "$WORK/manifest.json" \
    || die "could not download the release manifest for $tag
(network problem, or the release is missing uaeg-manifest.json - nothing was changed)"

  say "fetching manifest signature: $sig_url"
  download "$sig_url" "$WORK/manifest.json.sig" \
    || die "could not download the manifest signature for $tag (incomplete release; nothing was changed)"

  say "verifying manifest signature (trusted publishers: $BOOTSTRAP_KEY_IDS)"
  if ! verify_manifest_signature "$WORK/manifest.json" "$WORK/manifest.json.sig"; then
    die "RELEASE MANIFEST SIGNATURE INVALID for $tag.
The manifest is not signed by ANY trusted UAEG release key ($BOOTSTRAP_KEY_IDS).
Possible causes: release not published by the project, artifact tampering, or a
key rotation (see docs/BOOTSTRAP.md - Recovery). NOTHING was executed or changed."
  fi

  # Structural validation + updater coordinates from the signed manifest.
  read -r UPDATER_REL UPDATER_SHA UPDATER_SIZE UPDATER_IFACE MANIFEST_VER \
    < <(python3 - "$WORK/manifest.json" <<'PY'
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
  if [[ -z "$UPDATER_REL" || -z "$UPDATER_SHA" || -z "$MANIFEST_VER" ]]; then
    die "the release manifest is malformed (no usable updater coordinates) - refusing to continue (nothing was changed)"
  fi

  if [[ "$MANIFEST_VER" != "$ver" ]]; then
    die "manifest version ($MANIFEST_VER) does not match the requested release ($ver) - refusing to continue"
  fi
  if (( UPDATER_IFACE > BOOTSTRAP_INTERFACE )); then
    die "the updater in release $tag speaks interface v$UPDATER_IFACE, but this
bootstrap supports up to v$BOOTSTRAP_INTERFACE. Refresh the bootstrap ONCE:
  curl -fsSLO $RELEASE_DOWNLOAD_BASE/latest-mismatch/upgrade.sh
(see docs/BOOTSTRAP.md - exceptional recovery; this is the only situation
that requires re-downloading the bootstrap)."
  fi

  say "downloading versioned updater: $UPDATER_REL ($tag)"
  download "$RELEASE_DOWNLOAD_BASE/$tag/$UPDATER_REL" "$WORK/updater.sh" \
    || die "could not download the updater $UPDATER_REL (nothing was changed)"

  say "verifying updater integrity (sha256 + size against the signed manifest)"
  local got_sha got_size
  got_sha="$(sha256sum "$WORK/updater.sh" | awk '{print $1}')"
  got_size="$(wc -c < "$WORK/updater.sh")"
  if [[ "$got_sha" != "$UPDATER_SHA" ]]; then
    die "UPDATER SHA-256 MISMATCH (expected $UPDATER_SHA, got $got_sha).
The downloaded updater does not match the signed manifest - refusing to
execute it. Nothing was changed."
  fi
  if [[ "$got_size" != "$UPDATER_SIZE" ]]; then
    die "updater size mismatch (expected $UPDATER_SIZE, got $got_size) - refusing to execute"
  fi

  UPDATER_PATH="$WORK/updater.sh"
  MANIFEST_PATH="$WORK/manifest.json"
}

# ----------------------------------------------------------------------
# Target resolution + updater acquisition per mode
# ----------------------------------------------------------------------
UPDATER_PATH=""
MANIFEST_PATH=""
TARGET_VERSION=""
TARGET_TAG=""

if [[ -n "$OPT_SOURCE" && -f "$OPT_SOURCE/updater.sh" ]]; then
  # Explicit operator-trusted local source tree (same trust level as the
  # v0.3.2 contract's --source, which ran <source>/install.sh directly).
  warn "--source: running the LOCAL updater $OPT_SOURCE/updater.sh (operator-trusted, not release-verified)"
  UPDATER_PATH="$OPT_SOURCE/updater.sh"
  MANIFEST_PATH=""
elif [[ "$BOOT_MODE" == "rollback" && -f "$CACHED_UPDATER" ]]; then
  # Rollback is a local operation: prefer the cached, previously-verified
  # updater so it works with no network at all.
  say "rollback: using the cached updater ($CACHED_UPDATER)"
  UPDATER_PATH="$CACHED_UPDATER"
  [[ -f "$CACHED_MANIFEST" ]] && MANIFEST_PATH="$CACHED_MANIFEST"
else
  # Online release resolution.
  if [[ -n "$OPT_VERSION" ]]; then
    TARGET_VERSION="$OPT_VERSION"
    TARGET_TAG="v$OPT_VERSION"
  else
    say "resolving the latest published release from $RELEASE_API_BASE"
    TARGET_TAG="$(fetch_latest_tag)"
    [[ -n "$TARGET_TAG" ]] || die "could not determine the latest release
(network problem or no published releases). For offline rollback use:
  sudo bash upgrade.sh --rollback   (uses the cached updater)"
    TARGET_VERSION="${TARGET_TAG#v}"
  fi
  fetch_verified_updater "$TARGET_TAG" "$TARGET_VERSION"
fi

[[ -n "$UPDATER_PATH" && -f "$UPDATER_PATH" ]] || die "no updater available for this operation
(network failed and no cached updater exists at $CACHED_UPDATER)."

# ----------------------------------------------------------------------
# Execute the verified updater (interface v1: env + forwarded argv)
# ----------------------------------------------------------------------
export UAEG_BOOTSTRAP="1"
export UAEG_BOOTSTRAP_FILE="$BOOTSTRAP_FILE"
[[ -n "$TARGET_VERSION" ]] && export UAEG_TARGET_VERSION="$TARGET_VERSION"
[[ -n "$TARGET_TAG" ]] && export UAEG_RELEASE_TAG="$TARGET_TAG"
[[ -n "$MANIFEST_PATH" ]] && export UAEG_MANIFEST="$MANIFEST_PATH"
export UAEG_UPDATER="$UPDATER_PATH"

# The selected MODE must reach the updater: --check / --dry-run change its
# behavior; the default (upgrade) and --rollback need no extra flag.
MODE_FLAG=""
[[ "$BOOT_MODE" == "check" ]] && MODE_FLAG="--check"
[[ "$BOOT_MODE" == "dry-run" ]] && MODE_FLAG="--dry-run"

say "running the verified updater: $UPDATER_PATH"
if [[ -n "$MODE_FLAG" ]]; then
  bash "$UPDATER_PATH" $MODE_FLAG ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"}
else
  bash "$UPDATER_PATH" ${FORWARD_ARGS+"${FORWARD_ARGS[@]}"}
fi
rc=$?

# ----------------------------------------------------------------------
# Post-conditions: the operator's bootstrap file must be untouched.
# ----------------------------------------------------------------------
BOOTSTRAP_SHA_AFTER="$(sha256sum "$BOOTSTRAP_FILE" 2>/dev/null | awk '{print $1}' || echo unknown)"
if [[ "$BOOTSTRAP_SHA_BEFORE" != "$BOOTSTRAP_SHA_AFTER" ]]; then
  warn "the bootstrap file changed during the upgrade ($BOOTSTRAP_FILE).
The updater is not allowed to modify it - please verify the file or re-
download it once (docs/BOOTSTRAP.md - exceptional recovery)."
fi

exit "$rc"
