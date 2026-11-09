#!/usr/bin/env bash
#
# tests/test_upgrade_contract.sh — enforces docs/UPGRADE_CONTRACT.md (v1).
#
# Runs the REAL install.sh + upgrade.sh against fully isolated directories
# (no root, no systemd, no system paths) by applying the same test-only
# patches that .github/workflows/ci.yml uses for the installer smoke job:
# the root-check is disabled and apt is skipped. Everything else — backup,
# upgrade, verification, rollback — runs exactly as on the VPS.
#
# Contract matrix (docs/UPGRADE_CONTRACT.md, contract v2):
#   T-01     fresh install (prerequisite)
#   T-02     keys + operator config changes (prerequisite)
#   T-03     --check detects installation, version, paths       (U-2, U-10)
#   T-04     --dry-run shows plan, changes nothing              (U-11)
#   T-GUARD  public dashboard without password -> refuse        (U-8)
#   T-13     remote upgrade to latest tag (7.7.7) via staging   (U-1, U-2, U-12)
#             - operator clone HEAD/branch/status NOT modified
#   T-14     dirty git tree -> upgrade STILL safe, tree preserved (U-12 v2:
#             operator trees are never read or mutated)
#   T-05     --source upgrade: zero data loss + evidence        (U-1, U-3, U-4, U-8)
#   T-06     idempotent re-run                                   (U-7)
#   T-07     smoke-test evidence in RECORD/log                   (U-4)
#   T-08     broken release -> automatic rollback (exit 2)      (U-5)
#   T-09     manual --rollback restores data                     (U-6)
#   T-10     no installation -> refuse (exit 3)                 (U-2)
#   T-11     secrets never appear in logs/records               (U-9)
#   T-12     backup retention --keep-backups                     (U-7)
#   T-16     silent downgrade -> refuse (exit 5)                (U-12)
#   T-15     standalone script: dry-run resolves target         (U-2, U-11)
#   T-17     INCIDENT v0.3.1 regression: downloaded upgrade.sh inside
#             the operator clone (untracked, collides with the target
#             tag) -> upgrade must SUCCEED, tree untouched        (U-12 v2)
#   T-18     missing tag -> accurate message (never misreported
#             as a fetch/clone failure)                          (U-2, U-10)
#   T-19     standalone script (no local repo): FULL upgrade     (U-1, U-2)
#   T-32     INCIDENT v0.3.4 regression: answer "y" ON THE TERMINAL
#             while stdin is detached (</dev/null) -> the upgrade MUST
#             proceed, the prompt MUST display, and the refusal must
#             never falsely claim "cancelled by the operator"
#   T-33     INCIDENT v0.3.4 regression: updater executed with the
#             script on stdin (curl|bash shape) + "y" on the terminal
#             -> MUST proceed (no BASH_SOURCE crash, no false cancel)
#   T-34     explicit "n" on the terminal -> clean accurate refusal
#   T-35     no terminal + no answer -> accurate "no confirmation
#             was received" refusal (never a silent death)
#   T-36     no terminal + piped "y" -> accepted (automation preserved)
#   T-37     rollback confirmation: "n" aborts; "y" read from the
#             terminal while stdin is detached -> rollback proceeds
#   T-53..T-57 INCIDENT 2026-11-08 regressions: pre-mutation preflight
#             (downgrade exit 5 / schema probe / standalone pin /
#             PYTHONPATH sanitization) - zero mutation on refusal
#   T-38     confirm_ask helper (byte-exact from install.sh and
#             updater.sh): y / n / EOF / detached-stdin probes
#
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Self-heal a shallow/tagless checkout (e.g. actions/checkout default).
# This suite builds local git remotes FROM this repository (T-13/T-17) and
# checks out historical tags (T-17: v0.3.0). From a shallow clone those
# pushes are rejected ("shallow update not allowed") and the tags do not
# exist, which starves the test remotes. Best effort: unshallow + fetch
# tags from origin (requires network; harmless no-op when already complete).
# ---------------------------------------------------------------------------
if [[ -d "$REPO_DIR/.git" ]] \
   && [[ "$(git -C "$REPO_DIR" rev-parse --is-shallow-repository 2>/dev/null)" == "true" ]]; then
  echo "[setup] repositori shallow terdeteksi - mencoba unshallow + fetch tags"
  git -C "$REPO_DIR" fetch --unshallow --tags --quiet origin 2>/dev/null || true
fi
if [[ -z "$(git -C "$REPO_DIR" tag -l 'v*' 2>/dev/null)" && -d "$REPO_DIR/.git" ]]; then
  echo "[setup] tag tidak ditemukan - mencoba fetch tags dari origin"
  git -C "$REPO_DIR" fetch --tags --quiet origin 2>/dev/null || true
fi

BASE="$(mktemp -d /tmp/uaeg-upgtest.XXXXXX)"
SRC="$BASE/src-repo"          # patched copy of the repo  (no .git)
INSTALL="$BASE/opt/uaeg"
DATA="$BASE/var/lib/uaeg"
CONFIG="$BASE/etc/uaeg"
BACKUPS="$BASE/var/backups/uaeg"
TESTBIN="$BASE/bin"
VENV_UAEG="$INSTALL/.venv/bin/uaeg"
LOG="$BASE/run.log"
RUN_RC=9999

PASS=0; FAIL=0; FAILED=()

export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1

cleanup() {
  local rc=$?
  pkill -f "$INSTALL/.venv/bin/uaeg serve" 2>/dev/null || true
  pkill -f "$BASE/work/.venv" 2>/dev/null || true
  if [[ "${UAEG_TEST_KEEP:-0}" != "1" ]]; then rm -rf "$BASE"; fi
  printf '\n[suite selesai rc=%s | base=%s]\n' "$rc" "$BASE"
}
trap cleanup EXIT

t()    { printf '\n\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
no()   { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); FAILED+=("$*"); }
assert_eq()  { [[ "$1" == "$2" ]] && ok "$3" || no "$3 (got '$1', want '$2')"; }
assert_rc()  { [[ "$RUN_RC" == "$1" ]] && ok "$2" || no "$2 (rc=$RUN_RC, want $1)"; }
assert_file(){ [[ -e "$1" ]] && ok "$2" || no "$2 (missing: $1)"; }
assert_no()  { [[ ! -e "$1" ]] && ok "$2" || no "$2 (exists: $1)"; }
assert_in()  { grep -qF -- "$2" "$1" && ok "$3" || no "$3 ('$2' not found)"; }
assert_not_in() { ! grep -qF -- "$2" "$1" && ok "$3" || no "$3 ('$2' unexpectedly found in $1)"; }

run() {  # run <logfile> cmd... -> RUN_RC
  local logf="$1"; shift
  "$@" > "$logf" 2>&1
  RUN_RC=$?
}

# ---- test-only patches (identical spirit to ci.yml) ----------------------
patch_installsh() {  # $1 src -> stdout
  sed -e 's|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|' \
      -e 's|^apt-get update -qq$|:|' \
      -e 's|^apt-get install -y -qq python3 python3-venv openssl curl >/dev/null$|:|' \
      -e 's|^ln -sf "\$VENV_DIR/bin/uaeg" /usr/local/bin/uaeg$|ln -sf "$VENV_DIR/bin/uaeg" __TESTBIN__/uaeg|' \
      -e 's|/usr/local/sbin/uaeg-upgrade|__TESTSBIN__/uaeg-upgrade|g' \
      -e 's|DATA_DIR="/var/lib/uaeg"|DATA_DIR="__TESTBASE__/var/lib/uaeg"|' \
      -e 's|CONFIG_DIR="/etc/uaeg"|CONFIG_DIR="__TESTBASE__/etc/uaeg"|' \
      -e 's|INSTALL_DIR="/opt/uaeg"|INSTALL_DIR="__TESTBASE__/opt/uaeg"|' \
      "$1" | sed -e "s|__TESTBIN__|$TESTBIN|g" -e "s|__TESTSBIN__|$BASE/sbin|g" -e "s|__TESTBASE__|$BASE|g"
}
patch_updatersh() {  # $1 src -> stdout (root-check off + redirects for tests)
  sed -e 's|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|' \
      -e "s|^SYSTEM_BOOTSTRAP=\".*\"|SYSTEM_BOOTSTRAP=\"$BASE/sbin/uaeg-upgrade\"|" \
      -e "s|/usr/local/bin/uaeg|$TESTBIN/uaeg|g" \
      -e 's|CONFIG_DIR="/etc/uaeg"|CONFIG_DIR="__TESTBASE__/etc/uaeg"|' \
      -e 's|DATA_DIR="/var/lib/uaeg"|DATA_DIR="__TESTBASE__/var/lib/uaeg"|' \
      -e 's|INSTALL_DIR="/opt/uaeg"|INSTALL_DIR="__TESTBASE__/opt/uaeg"|' \
      "$1" | sed -e "s|__TESTBASE__|$BASE|g"
}
patch_bootstrapsh() {  # $1 src -> stdout (root-check off + TEST keyring swap)
  sed -e 's|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|' "$1" \
  | TESTKEY_PUB="$(cat "$TESTKEY_DIR/test-release.pub.pem")" awk '
      /^_TRUSTED_KEY_1="/ { printf "_TRUSTED_KEY_1=\"%s\"\n", ENVIRON["TESTKEY_PUB"]; skip=1; next }
      skip==1 && /"$/ { skip=0; next }
      skip==1 { next }
      { print }
  '
}
# patch_unifiedsh: the operator's ONE unified install.sh for contract-v4
# tests — installer redirects (patch_installsh) + the TEST bootstrap keyring
# (patch_bootstrapsh's swap), because the unified file carries the bootstrap.
patch_unifiedsh() {  # $1 src -> stdout
  patch_installsh "$1" \
  | TESTKEY_PUB="$(cat "$TESTKEY_DIR/test-release.pub.pem")" awk '
      /^_TRUSTED_KEY_1="/ { printf "_TRUSTED_KEY_1=\"%s\"\n", ENVIRON["TESTKEY_PUB"]; skip=1; next }
      skip==1 && /"$/ { skip=0; next }
      skip==1 { next }
      { print }
  '
}

# make_source <dir> <version>: copy the repo (no .git) with patched scripts
# and (optionally) a bumped version — simulates a NEW release tree.
make_source() {
  local dir="$1" ver="${2:-}"
  mkdir -p "$dir" "$TESTBIN" "$BASE/sbin"
  tar -C "$REPO_DIR" --exclude=.git --exclude=.venv --exclude=__pycache__ \
      --exclude=.pytest_cache --exclude=dist --exclude=build -cf - . \
    | tar -C "$dir" -xf -
  if [[ -n "$ver" ]]; then
    sed -i "s/^version[[:space:]]*=[[:space:]]*\".*\"/version = \"$ver\"/" "$dir/pyproject.toml"
    sed -i "s/^__version__[[:space:]]*=[[:space:]]*\".*\"/__version__ = \"$ver\"/" "$dir/src/uaeg/__init__.py"
  fi
  patch_installsh "$dir/install.sh" > "$dir/install-test.sh"
  patch_updatersh "$dir/updater.sh" > "$dir/updater-test.sh"
}

env_uaeg() { UAEG_CONFIG="$CONFIG/config.json" UAEG_DATA_DIR="$DATA" "$VENV_UAEG" "$@"; }

installed_version() { env_uaeg version 2>/dev/null | awk '{print $2}'; }

db_keys() {  # "key_id:status" sorted — the zero-data-loss fingerprint
  python3 - "$DATA/uaeg.db" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    rows = con.execute("SELECT key_id, status FROM keys ORDER BY key_id").fetchall()
    print(";".join(f"{a}:{b}" for a, b in rows))
except Exception as exc:
    print(f"ERROR:{exc}")
PY
}

db_check() {
  python3 -c "
import sqlite3
con = sqlite3.connect('file:$DATA/uaeg.db?mode=ro', uri=True)
print(con.execute('PRAGMA quick_check').fetchone()[0])
"
}

cfg_field() {  # $1 = python expr over cfg
  python3 -c "
import json
cfg = json.load(open('$CONFIG/config.json'))
print($1)
"
}

backup_count() { ls -1d "$BACKUPS"/upgrade-* 2>/dev/null | wc -l; }

latest_record() {  # $1 = key
  python3 -c "
import glob, json
files = sorted(glob.glob('$BACKUPS/upgrade-*/RECORD.json'))
print(json.load(open(files[-1])).get('$1'))
" 2>/dev/null
}

REPO_VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$REPO_DIR/pyproject.toml" | head -1)"

UPGRADE_ARGS=(--yes --install-dir "$INSTALL" --data-dir "$DATA"
               --config-dir "$CONFIG" --backups-dir "$BACKUPS")

echo "=============================================================="
echo " UAEG safe-upgrade contract test suite"
echo " repo: $REPO_DIR (v$REPO_VERSION)   base: $BASE"
echo "=============================================================="

# ----------------------------------------------------------------------
t "T-01: fresh install via (patched) install.sh — isolated dirs"
# ----------------------------------------------------------------------
make_source "$SRC"
run "$LOG" bash "$SRC/install-test.sh" \
  --port 3952 --dashboard-port 3953 --dashboard-bind loopback \
  --public-ip 127.0.0.1 --self-signed \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 0 "T-01 installer selesai (rc)"
assert_file "$INSTALL/.uaeg-installed" "T-01 marker instalasi ada"
assert_eq "$(installed_version)" "$REPO_VERSION" "T-01 versi terpasang == $REPO_VERSION"

# ----------------------------------------------------------------------
t "T-02: dua agent key + perubahan config oleh operator"
# ----------------------------------------------------------------------
env_uaeg generate --name "agent-alpha" > "$BASE/key-alpha.txt" 2>&1
env_uaeg generate --name "agent-bravo" > "$BASE/key-bravo.txt" 2>&1
SECRET_A="$(grep -o 'uaeg_[A-Za-z0-9_-]*' "$BASE/key-alpha.txt" | head -1 || true)"
assert_eq "$(db_keys | awk -F';' '{print NF}')" "2" "T-02 ada 2 kunci agent"
env_uaeg config set dashboard.port 3954 >/dev/null 2>&1
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-02 operator mengubah dashboard.port -> 3954"
KEYS_BEFORE="$(db_keys)"
COUNTS_BEFORE="$(python3 -c "
import sqlite3
con = sqlite3.connect('file:$DATA/uaeg.db?mode=ro', uri=True)
print({t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('keys','jobs','sessions','exec_logs')})
")"

# ----------------------------------------------------------------------
t "T-03: upgrade.sh --check mendeteksi instalasi (U-2, U-10)"
# ----------------------------------------------------------------------
run "$LOG" bash "$SRC/updater-test.sh" --check "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-03 --check rc=0"
assert_in "$LOG" "$INSTALL" "T-03 menampilkan install dir"
assert_in "$LOG" "$REPO_VERSION" "T-03 menampilkan versi terpasang"
assert_in "$LOG" "$DATA" "T-03 menampilkan data dir"
assert_in "$LOG" "detected" "T-03 status terdeteksi"

# ----------------------------------------------------------------------
t "T-04: --dry-run menampilkan rencana tanpa perubahan (U-11)"
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC/updater-test.sh" --dry-run "${UPGRADE_ARGS[@]}" --source "$SRC" --installer "$SRC/install-test.sh"
assert_rc 0 "T-04 dry-run rc=0"
assert_in "$LOG" "UPGRADE PLAN" "T-04 menampilkan rencana"
assert_in "$LOG" "no changes" "T-04 menyatakan tidak ada perubahan"
assert_eq "$(backup_count)" "$N_BEFORE" "T-04 tidak membuat backup"

# ----------------------------------------------------------------------
t "T-GUARD: dashboard publik tanpa password -> upgrade ditolak (U-8)"
# ----------------------------------------------------------------------
python3 -c "
import json
p = '$CONFIG/config.json'
cfg = json.load(open(p))
cfg['dashboard']['bind'] = '0.0.0.0'
json.dump(cfg, open(p, 'w'), indent=2)
"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC/updater-test.sh" "${UPGRADE_ARGS[@]}" --source "$SRC" --installer "$SRC/install-test.sh"
assert_rc 1 "T-GUARD rc=1 (ditolak)"
assert_in "$LOG" "uaeg pass" "T-GUARD pesan mengarah ke 'sudo uaeg pass'"
assert_eq "$(backup_count)" "$N_BEFORE" "T-GUARD tidak membuat backup (belum ada yang berubah)"
python3 -c "
import json
p = '$CONFIG/config.json'
cfg = json.load(open(p))
cfg['dashboard']['bind'] = '127.0.0.1'
json.dump(cfg, open(p, 'w'), indent=2)
"
assert_eq "$(installed_version)" "$REPO_VERSION" "T-GUARD versi tidak berubah"

# ----------------------------------------------------------------------
t "T-13: remote upgrade ke tag terbaru (7.7.7) via staging terisolasi (U-1, U-2, U-12)"
# ----------------------------------------------------------------------
WORK="$BASE/work"; REMOTE="$BASE/remote.git"
git init --bare -q "$REMOTE"
git clone -q "$REPO_DIR" "$WORK"
# Branch-agnostic harness (2026-11-10 audit): when this suite runs from a
# non-main checkout (e.g. a PR branch), the clone's default branch is that
# branch and 'push origin main' fails with 'src refspec main does not match
# any'. Force a local main at HEAD so the staging remote always receives it.
git -C "$WORK" checkout -q -B main
git -C "$WORK" remote set-url origin "$REMOTE"
sed -i 's/^version[[:space:]]*=[[:space:]]*".*"/version = "7.7.7"/' "$WORK/pyproject.toml"
sed -i 's/^__version__[[:space:]]*=[[:space:]]*".*"/__version__ = "7.7.7"/' "$WORK/src/uaeg/__init__.py"
git -C "$WORK" -c user.email=ci@test -c user.name=ci commit -qam "test: bump 7.7.7"
git -C "$WORK" tag v7.7.7
git -C "$WORK" push -q origin main --tags
# the operator keeps a LOCAL (unpushed) commit on top of the tag: with the
# v0.3.1 design this was the tree upgrade.sh would check out / refuse on;
# contract v2 requires it to survive byte-for-byte untouched
git -C "$WORK" -c user.email=ci@test -c user.name=ci commit -q --allow-empty -m "local operator wip"
OP_HEAD_BEFORE="$(git -C "$WORK" rev-parse HEAD)"
patch_installsh "$WORK/install.sh" > "$WORK/install-test.sh"
patch_updatersh "$WORK/updater.sh" > "$WORK/updater-test.sh"
# capture AFTER the harness scripts exist, so the comparison isolates what
# the UPGRADE itself does to the tree
OP_STATUS_BEFORE="$(git -C "$WORK" status --porcelain)"
run "$LOG" bash "$WORK/updater-test.sh" "${UPGRADE_ARGS[@]}" --installer "$WORK/install-test.sh"
assert_rc 0 "T-13 remote upgrade rc=0"
assert_eq "$(installed_version)" "7.7.7" "T-13 versi sekarang 7.7.7"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$OP_HEAD_BEFORE" "T-13 clone operator TIDAK diubah (HEAD identik)"
assert_eq "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" "main" "T-13 clone operator tetap di branch main (bukan detached)"
assert_eq "$(git -C "$WORK" status --porcelain)" "$OP_STATUS_BEFORE" "T-13 status clone operator identik (tidak ada perubahan baru)"
assert_in "$LOG" "isolated staging" "T-13 sumber dari staging terisolasi"
assert_in "$LOG" "NOT touched" "T-13 script menyatakan clone operator tidak disentuh"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-13 kunci agent identik"
assert_eq "$(latest_record to_version)" "7.7.7" "T-13 RECORD to_version=7.7.7"
assert_eq "$(latest_record result)" "success" "T-13 RECORD result=success"
assert_eq "$(latest_record contract_version)" "3" "T-13 RECORD contract_version=3"

# ----------------------------------------------------------------------
t "T-14: git tree kotor -> TETAP aman; tree dipertahankan (U-12 kontrak v2)"
# ----------------------------------------------------------------------
# v0.3.1 refused this with exit 4. Contract v2: the operator tree is never
# read or mutated, so dirty files must not block the upgrade.
echo "local edit by operator" >> "$WORK/README.md"
run "$LOG" bash "$WORK/updater-test.sh" "${UPGRADE_ARGS[@]}" --installer "$WORK/install-test.sh"
assert_rc 0 "T-14 rc=0 (tree kotor tidak lagi menghalangi)"
assert_eq "$(installed_version)" "7.7.7" "T-14 versi tetap 7.7.7 (re-install/repair)"
assert_in "$WORK/README.md" "local edit by operator" "T-14 perubahan lokal operator dipertahankan"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-14 kunci agent identik"
git -C "$WORK" checkout -q -- README.md

# ----------------------------------------------------------------------
t "T-05: upgrade --source 9.9.9 — zero data loss + bukti (U-1, U-3, U-4, U-8)"
# ----------------------------------------------------------------------
SRC_NEXT="$BASE/src-next"
make_source "$SRC_NEXT" "9.9.9"
run "$LOG" bash "$SRC_NEXT/updater-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC_NEXT" --installer "$SRC_NEXT/install-test.sh"
assert_rc 0 "T-05 upgrade rc=0"
assert_eq "$(installed_version)" "9.9.9" "T-05 versi sekarang 9.9.9"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-05 kunci agent identik (key_id+status)"
assert_eq "$(db_check)" "ok" "T-05 integritas DB ok"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-05 config operator dipertahankan (port 3954)"
LATEST="$(ls -1d "$BACKUPS"/upgrade-* | sort | tail -1)"
assert_file "$LATEST/RECORD.json" "T-05 RECORD.json ada"
assert_file "$LATEST/before-state.json" "T-05 before-state.json ada (bukti audit)"
assert_file "$LATEST/after-state.json" "T-05 after-state.json ada (bukti audit)"
assert_file "$LATEST/install.log" "T-05 install.log ada"
assert_file "$LATEST/data/uaeg.db" "T-05 backup DB konsisten ada"
assert_no "$LATEST/app" "T-05 backup app/ dihapus setelah sukses terverifikasi"
assert_eq "$(latest_record from_version)" "7.7.7" "T-05 RECORD from_version=7.7.7"
assert_eq "$(latest_record to_version)" "9.9.9" "T-05 RECORD to_version=9.9.9"
assert_eq "$(latest_record result)" "success" "T-05 RECORD result=success"
assert_eq "$(latest_record verification)" "all-checks-passed" "T-05 RECORD verification=all-checks-passed"

# ----------------------------------------------------------------------
t "T-06: idempoten — jalankan ulang, aman (U-7)"
# ----------------------------------------------------------------------
run "$LOG" bash "$SRC_NEXT/updater-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC_NEXT" --installer "$SRC_NEXT/install-test.sh"
assert_rc 0 "T-06 re-run rc=0"
assert_eq "$(installed_version)" "9.9.9" "T-06 versi tetap 9.9.9"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-06 kunci tetap identik"
assert_eq "$(backup_count)" "4" "T-06 backup keempat dibuat (T-13+T-14+T-05+T-06 = 4 direktori)"

# ----------------------------------------------------------------------
t "T-07: bukti smoke test / healthz tercatat (U-4)"
# ----------------------------------------------------------------------
LATEST="$(ls -1d "$BACKUPS"/upgrade-* | sort | tail -1)"
assert_file "$LATEST/smoke-serve.log" "T-07 smoke-serve.log ada"
assert_in "$LOG" "smoke test" "T-07 output menyebut smoke test"

# ----------------------------------------------------------------------
t "T-08: release rusak -> rollback OTOMATIS, exit 2 (U-5)"
# ----------------------------------------------------------------------
SRC_BROKEN="$BASE/src-broken"
make_source "$SRC_BROKEN" "9.9.9"
printf '\ndef broken(:\n' >> "$SRC_BROKEN/src/uaeg/server.py"
run "$LOG" bash "$SRC_BROKEN/updater-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC_BROKEN" --installer "$SRC_BROKEN/install-test.sh"
assert_rc 2 "T-08 rc=2 (gagal -> sudah di-rollback)"
assert_eq "$(installed_version)" "9.9.9" "T-08 aplikasi kembali ke 9.9.9 (berfungsi)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-08 kunci agent tetap utuh"
assert_eq "$(db_check)" "ok" "T-08 integritas DB ok"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-08 config tetap 3954"
assert_eq "$(latest_record result)" "rolled-back" "T-08 RECORD result=rolled-back"
assert_in "$LOG" "rollback verified" "T-08 rollback diverifikasi ulang (app lama hidup)"
LATEST="$(ls -1d "$BACKUPS"/upgrade-* | sort | tail -1)"
assert_file "$LATEST/app" "T-08 backup app/ dipertahankan untuk pemulihan manual"

# ----------------------------------------------------------------------
t "T-09: rollback manual memulihkan data pra-upgrade (U-6)"
# ----------------------------------------------------------------------
env_uaeg generate --name "agent-charlie" > /dev/null 2>&1
assert_eq "$(db_keys | awk -F';' '{print NF}')" "3" "T-09 kunci ketiga dibuat (3 kunci)"
run "$LOG" bash "$SRC_NEXT/updater-test.sh" --rollback --yes "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-09 --rollback rc=0"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-09 data kembali ke 2 kunci pra-backup"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-09 config tetap konsisten"
EVENTS="$(python3 -c "
import glob, json
files = sorted(glob.glob('$BACKUPS/upgrade-*/RECORD.json'))
r = json.load(open(files[-1]))
print('manual-rollback OK' if any('manual-rollback' in e for e in r.get('events', [])) else 'TIDAK-ADA')
")"
assert_eq "$EVENTS" "manual-rollback OK" "T-09 event manual-rollback tercatat di RECORD"

# ----------------------------------------------------------------------
t "T-10: tanpa instalasi -> menolak, exit 3 (U-2)"
# ----------------------------------------------------------------------
run "$LOG" bash "$SRC/updater-test.sh" --check \
  --install-dir "$BASE/noinstall" --data-dir "$BASE/nodata" \
  --config-dir "$BASE/noconfig" --backups-dir "$BACKUPS"
assert_rc 3 "T-10 rc=3 (tidak terinstal)"

# ----------------------------------------------------------------------
t "T-11: rahasia tidak pernah bocor ke log/record (U-9)"
# ----------------------------------------------------------------------
LEAK=0
while IFS= read -r f; do
  if grep -qF "$SECRET_A" "$f" 2>/dev/null; then LEAK=1; no "T-11 rahasia ditemukan di $f"; fi
done < <(find "$BACKUPS" -name '*.json' -o -name '*.log' 2>/dev/null)
[[ $LEAK -eq 0 ]] && ok "T-11 secret key tidak muncul di log/RECORD manapun"
LATEST="$(ls -1d "$BACKUPS"/upgrade-* | sort | tail -1)"
if grep -q '"verifier"' "$LATEST/before-state.json" 2>/dev/null; then
  no "T-11 before-state.json memuat verifier!"
else
  ok "T-11 state JSON tidak memuat verifier"
fi

# ----------------------------------------------------------------------
t "T-12: retensi backup --keep-backups (U-7)"
# ----------------------------------------------------------------------
run "$LOG" bash "$SRC_NEXT/updater-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC_NEXT" --installer "$SRC_NEXT/install-test.sh" --keep-backups 1
assert_rc 0 "T-12 upgrade dengan keep=1 rc=0"
assert_eq "$(backup_count)" "1" "T-12 hanya 1 backup tersisa"

# ----------------------------------------------------------------------
t "T-16: downgrade diam-diam -> ditolak exit 5 (U-12)"
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC/updater-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC" --installer "$SRC/install-test.sh"
assert_rc 5 "T-16 rc=5 (downgrade ditolak)"
assert_in "$LOG" "OLDER than" "T-16 pesan downgrade"
assert_eq "$(installed_version)" "9.9.9" "T-16 versi tidak berubah"
assert_eq "$(backup_count)" "$N_BEFORE" "T-16 tidak ada backup baru"

# ----------------------------------------------------------------------
t "T-15: skrip standalone (tanpa repo) — dry-run menemukan target (U-2, U-11)"
# ----------------------------------------------------------------------
STANDALONE="$BASE/standalone"
mkdir -p "$STANDALONE"
patch_updatersh "$REPO_DIR/updater.sh" > "$STANDALONE/updater-test.sh"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$STANDALONE/updater-test.sh" --dry-run "${UPGRADE_ARGS[@]}" --repo "$REMOTE"
assert_rc 0 "T-15 standalone dry-run rc=0"
assert_in "$LOG" "v7.7.7" "T-15 menemukan target v7.7.7 via ls-remote"
assert_eq "$(backup_count)" "$N_BEFORE" "T-15 tidak membuat backup"

# ----------------------------------------------------------------------
t "T-17: INSIDEN v0.3.1 — upgrade.sh terunduh DI DALAM clone operator (regresi)"
# ----------------------------------------------------------------------
# The exact MicroTix operator scenario: a clone whose local tags are stale,
# with the release assets downloaded INTO the working tree as untracked
# files that collide with the target tag's tracked upgrade.sh. The v0.3.1
# design ran `git checkout <tag>` in that tree, failed on the collision and
# misreported it as "tag not found". Contract v2 must upgrade successfully
# while leaving the operator's tree byte-for-byte untouched.
# ----------------------------------------------------------------------
INCREMOTE="$BASE/incident-remote.git"
INCWORK="$BASE/incident-work"
INC="$BASE/incident"
mkdir -p "$INC"
git init --bare -q "$INCREMOTE"
git clone -q "$REPO_DIR" "$INCWORK"
git -C "$INCWORK" checkout -q -B main   # branch-agnostic harness (see T-13)
git -C "$INCWORK" remote set-url origin "$INCREMOTE"
sed -i 's/^version[[:space:]]*=[[:space:]]*".*"/version = "10.0.0"/' "$INCWORK/pyproject.toml"
sed -i 's/^__version__[[:space:]]*=[[:space:]]*".*"/__version__ = "10.0.0"/' "$INCWORK/src/uaeg/__init__.py"
git -C "$INCWORK" -c user.email=ci@test -c user.name=ci commit -qam "test: bump 10.0.0"
git -C "$INCWORK" tag v10.0.0
git -C "$INCWORK" push -q origin main --tags
patch_installsh "$INCWORK/install.sh" > "$INCWORK/install-test.sh"
# operator clone at v0.3.0 (the incident state: upgrade.sh did not exist in
# that tree yet, so the downloaded asset is genuinely UNTRACKED), stale
# tags, origin = the remote
git clone -q "$REPO_DIR" "$INC/uaeg"
git -C "$INC/uaeg" remote set-url origin "$INCREMOTE"
git -C "$INC/uaeg" checkout -q v0.3.0
# ...release assets downloaded INTO the working tree (untracked!):
patch_updatersh "$REPO_DIR/updater.sh" > "$INC/uaeg/upgrade.sh"
( cd "$INC/uaeg" && sha256sum upgrade.sh > upgrade.sh.sha256 )
if ( cd "$INC/uaeg" && sha256sum -c upgrade.sh.sha256 >/dev/null 2>&1 ); then
  ok "T-17 checksum asset terverifikasi"
else
  no "T-17 checksum asset gagal"
fi
INC_HEAD_BEFORE="$(git -C "$INC/uaeg" rev-parse HEAD)"
INC_STATUS_BEFORE="$(git -C "$INC/uaeg" status --porcelain)"
run "$LOG" bash "$INC/uaeg/upgrade.sh" "${UPGRADE_ARGS[@]}" --installer "$INCWORK/install-test.sh"
assert_rc 0 "T-17 rc=0 (insiden v0.3.1 tidak terulang)"
assert_eq "$(installed_version)" "10.0.0" "T-17 versi sekarang 10.0.0"
assert_eq "$(git -C "$INC/uaeg" rev-parse HEAD)" "$INC_HEAD_BEFORE" "T-17 clone operator tidak berubah (HEAD identik)"
assert_eq "$(git -C "$INC/uaeg" status --porcelain)" "$INC_STATUS_BEFORE" "T-17 file terunduh tetap ada, tidak ada perubahan lain"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-17 kunci agent identik"
assert_in "$LOG" "NOT touched" "T-17 script menyatakan clone operator tidak disentuh"

# ----------------------------------------------------------------------
t "T-18: tag tidak ada -> pesan akurat, bukan misreporting (U-2, U-10)"
# ----------------------------------------------------------------------
# The v0.3.1 incident also misclassified failures. A tag that does not
# exist must be reported as a missing tag - never as a fetch/clone/network
# failure, and never with the old misleading "tidak ditemukan setelah fetch".
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC_NEXT/updater-test.sh" "${UPGRADE_ARGS[@]}" \
  --repo "$INCREMOTE" --installer "$INCWORK/install-test.sh" --version 0.0.42
assert_rc 1 "T-18 rc=1 (tag tidak ada -> ditolak)"
assert_in "$LOG" "not found on remote" "T-18 pesan menyebut tag tidak ada di remote"
if grep -q "not found after fetch" "$LOG" 2>/dev/null; then
  no "T-18 pesan misleading lama (v0.3.1) masih muncul"
else
  ok "T-18 pesan misleading lama (v0.3.1) tidak muncul"
fi
assert_eq "$(installed_version)" "10.0.0" "T-18 versi tidak berubah"
assert_eq "$(backup_count)" "$N_BEFORE" "T-18 tidak membuat backup (belum ada yang berubah)"

# ----------------------------------------------------------------------
t "T-19: skrip standalone (tanpa repo lokal) — upgrade PENUH dari remote (U-1, U-2)"
# ----------------------------------------------------------------------
STANDALONE="$BASE/standalone-full"
mkdir -p "$STANDALONE"
patch_updatersh "$REPO_DIR/updater.sh" > "$STANDALONE/upgrade.sh"
run "$LOG" bash "$STANDALONE/upgrade.sh" "${UPGRADE_ARGS[@]}" \
  --repo "$INCREMOTE" --installer "$INCWORK/install-test.sh"
assert_rc 0 "T-19 standalone upgrade rc=0"
assert_eq "$(installed_version)" "10.0.0" "T-19 versi 10.0.0 (re-install dari standalone)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-19 kunci agent identik"
assert_in "$LOG" "isolated staging" "T-19 sumber dari staging terisolasi"

# ----------------------------------------------------------------------
# CONTRACT v3 — PERMANENT BOOTSTRAP TESTS (T-20 .. T-34)
#
# These tests prove the core requirement of the permanent upgrade
# architecture: ONE operator-owned upgrade.sh (bootstrap) upgrades across
# MULTIPLE releases - including releases whose UPDATER IMPLEMENTATION
# CHANGED - while the bootstrap file itself stays byte-identical, artifacts
# are signature-verified, and data survives.
#
# A local "release server" (python http.server) stands in for GitHub
# Releases: UAEG_RELEASE_API_BASE / UAEG_RELEASE_DOWNLOAD_BASE point the
# (test-patched) bootstrap at it, and a test-only Ed25519 key is swapped
# into the bootstrap's keyring. The verification chain is otherwise REAL:
# manifest download -> Ed25519 signature check -> sha256 + size check ->
# updater execution.
# ----------------------------------------------------------------------
t "T-20: setup — test signing key, release server, operator bootstrap"
TESTKEY_DIR="$BASE/testkeys"
mkdir -p "$TESTKEY_DIR"
openssl genpkey -algorithm ed25519 -out "$TESTKEY_DIR/test-release.key" 2>/dev/null
openssl pkey -in "$TESTKEY_DIR/test-release.key" -pubout -out "$TESTKEY_DIR/test-release.pub.pem" 2>/dev/null
# a SECOND, untrusted key (for the wrong-publisher test)
openssl genpkey -algorithm ed25519 -out "$TESTKEY_DIR/rogue.key" 2>/dev/null
openssl pkey -in "$TESTKEY_DIR/rogue.key" -pubout -out "$TESTKEY_DIR/rogue.pub.pem" 2>/dev/null
assert_file "$TESTKEY_DIR/test-release.pub.pem" "T-20 kunci uji dibuat"

RELROOT="$BASE/release-server"
mkdir -p "$RELROOT/releases" "$RELROOT/download"
RELPORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
python3 -m http.server "$RELPORT" --bind 127.0.0.1 --directory "$RELROOT" >/dev/null 2>&1 &
RELSRV_PID=$!
sleep 1
if curl -fsS "http://127.0.0.1:$RELPORT/releases/latest" >/dev/null 2>&1; then
  ok "T-20 release server hidup (port $RELPORT)"
else
  printf '{"tag_name":"none"}\n' > "$RELROOT/releases/latest"  # placeholder
  if curl -fsS "http://127.0.0.1:$RELPORT/releases/latest" >/dev/null 2>&1; then
    ok "T-20 release server hidup (port $RELPORT)"
  else
    no "T-20 release server tidak hidup"
  fi
fi

# The operator's permanent bootstrap: ONE copy, kept in an operator dir.
OPERATOR_DIR="$BASE/operator"
mkdir -p "$OPERATOR_DIR"
patch_bootstrapsh "$REPO_DIR/upgrade.sh" > "$OPERATOR_DIR/upgrade.sh"
BOOTSTRAP_SHA="$(sha256sum "$OPERATOR_DIR/upgrade.sh" | awk '{print $1}')"
assert_file "$OPERATOR_DIR/upgrade.sh" "T-20 bootstrap operator dibuat (sha ${BOOTSTRAP_SHA:0:12}…)"

export UAEG_RELEASE_API_BASE="http://127.0.0.1:$RELPORT"
export UAEG_RELEASE_DOWNLOAD_BASE="http://127.0.0.1:$RELPORT/download"
export UAEG_UPGRADE_CACHE_DIR="$BASE/var/lib/uaeg/upgrade"

run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --bootstrap-info
assert_rc 0 "T-20 --bootstrap-info rc=0"
assert_in "$LOG" "updater interface" "T-20 info menampilkan interface"
assert_in "$LOG" "trusted key" "T-20 info menampilkan trusted key"
TEST_FP="$(printf '%s' "$(cat "$TESTKEY_DIR/test-release.pub.pem")" | sha256sum | awk '{print substr($1,1,16)}')"
assert_in "$LOG" "$TEST_FP" "T-20 keyring bootstrap == kunci uji (fingerprint cocok)"

sign_manifest() {  # $1 manifest $2 sig $3 key
  openssl pkeyutl -sign -inkey "$3" -rawin -in "$1" -out "$2" 2>/dev/null
}

make_release() {  # $1 = tree (contains updater-test.sh)  $2 = version  [$3 = key]
  local tag="v$2"
  local rdir="$RELROOT/download/$tag"
  local asset="uaeg-updater-$2.sh"
  local key="${3:-$TESTKEY_DIR/test-release.key}"
  mkdir -p "$rdir"
  cp "$1/updater-test.sh" "$rdir/$asset"
  python3 - "$rdir" "$asset" "$2" <<'PY'
import hashlib, json, os, sys
rdir, asset, ver = sys.argv[1:4]
data = open(os.path.join(rdir, asset), "rb").read()
manifest = {
    "product": "uaeg",
    "version": ver,
    "updater": {
        "path": asset,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "interface": 1,
    },
    "assets": {},
}
open(os.path.join(rdir, "uaeg-manifest.json"), "w", encoding="utf-8").write(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY
  sign_manifest "$rdir/uaeg-manifest.json" "$rdir/uaeg-manifest.json.sig" "$key"
}
set_latest_tag() { printf '{"tag_name":"%s"}\n' "v$1" > "$RELROOT/releases/latest"; }

# make_release_repo <gitdir> <remote> <version>: build a local git remote whose
# tag v<version> carries a tree at that version (the suite's established
# T-13 pattern, factored out).
make_release_repo() {
  local gdir="$1" remote="$2" ver="$3"
  git clone -q "$REPO_DIR" "$gdir"
  git -C "$gdir" checkout -q -B main   # branch-agnostic harness (see T-13)
  git -C "$gdir" remote set-url origin "$remote"
  sed -i "s/^version[[:space:]]*=[[:space:]]*\".*\"/version = \"$ver\"/" "$gdir/pyproject.toml"
  sed -i "s/^__version__[[:space:]]*=[[:space:]]*\".*\"/__version__ = \"$ver\"/" "$gdir/src/uaeg/__init__.py"
  git -C "$gdir" -c user.email=ci@test -c user.name=ci commit -qam "release $ver"
  git -C "$gdir" tag "v$ver"
  git -C "$gdir" push -q origin main --tags
}

# Build RELEASE A (v11.0.0): a patched asset tree + a local git remote at that version
WORK_A="$BASE/work-a"; REMOTE_A="$BASE/remote-a.git"; GIT_A_DIR="$BASE/gita"
make_source "$WORK_A" "11.0.0"
git init --bare -q "$REMOTE_A"
make_release_repo "$GIT_A_DIR" "$REMOTE_A" "11.0.0"
make_release "$WORK_A" "11.0.0"
set_latest_tag "11.0.0"

# ----------------------------------------------------------------------
t "T-21: bootstrap upgrade #1 -> release A (v11.0.0) — signature-verified"
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_A" \
  --installer "$WORK_A/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-21 bootstrap upgrade ke 11.0.0 rc=0"
assert_eq "$(installed_version)" "11.0.0" "T-21 versi sekarang 11.0.0"
assert_in "$LOG" "verifying manifest signature" "T-21 manifest signature diverifikasi"
assert_in "$LOG" "verifying updater integrity" "T-21 integritas updater diverifikasi"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-21 kunci agent identik"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-21 config operator dipertahankan"
assert_eq "$(latest_record bootstrap_invoked)" "True" "T-21 RECORD bootstrap_invoked=true"
assert_eq "$(latest_record contract_version)" "3" "T-21 RECORD contract_version=3"

# ----------------------------------------------------------------------
t "T-22: bootstrap file TIDAK berubah + cache updater + system copy (kontrak inti)"
# ----------------------------------------------------------------------
assert_eq "$(sha256sum "$OPERATOR_DIR/upgrade.sh" | awk '{print $1}')" "$BOOTSTRAP_SHA" \
  "T-22 SHA-256 bootstrap operator identik (file tidak pernah ditimpa)"
assert_file "$UAEG_UPGRADE_CACHE_DIR/last-updater.sh" "T-22 updater ter-cache (rollback offline)"
assert_file "$UAEG_UPGRADE_CACHE_DIR/last-manifest.json" "T-22 manifest ter-cache"
assert_file "$BASE/sbin/uaeg-upgrade" "T-22 system bootstrap copy terpasang (/usr/local/sbin/uaeg-upgrade)"
assert_in "$LOG" "NOT modified" "T-22 updater menyatakan bootstrap operator tidak diubah"

# ----------------------------------------------------------------------
t "T-23: RELEASE B dengan UPDATER IMPLEMENTATION BERUBAH — bootstrap yang sama"
# ----------------------------------------------------------------------
# The heart of the permanent-bootstrap contract: release B ships a DIFFERENT
# updater implementation; the operator's unchanged bootstrap must fetch it,
# verify it, and run it successfully.
WORK_B="$BASE/work-b"; REMOTE_B="$BASE/remote-b.git"; GIT_B_DIR="$BASE/gitb"
make_source "$WORK_B" "12.0.0"
# genuinely different updater implementation (visible marker line)
sed -i 's|^main$|say "UPDATER-IMPL-B (changed implementation)"\nmain|' "$WORK_B/updater-test.sh"
git init --bare -q "$REMOTE_B"
make_release_repo "$GIT_B_DIR" "$REMOTE_B" "12.0.0"
make_release "$WORK_B" "12.0.0"
set_latest_tag "12.0.0"

# run the SAME bootstrap from INSIDE the operator's (dirty, untracked-
# upgrade.sh-carrying) clone — also the v0.3.1 incident shape at the
# bootstrap level
INC2="$BASE/incident2"; mkdir -p "$INC2"
git clone -q "$REPO_DIR" "$INC2/uaeg"
git -C "$INC2/uaeg" remote set-url origin "$REMOTE_B"
git -C "$INC2/uaeg" checkout -q v0.3.0
cp "$OPERATOR_DIR/upgrade.sh" "$INC2/uaeg/upgrade.sh"   # untracked, collides with tag's file
INC2_HEAD_BEFORE="$(git -C "$INC2/uaeg" rev-parse HEAD)"
INC2_STATUS_BEFORE="$(git -C "$INC2/uaeg" status --porcelain)"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_B" \
  --installer "$WORK_B/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-23 bootstrap (file yang sama) upgrade ke 12.0.0 rc=0"
assert_eq "$(installed_version)" "12.0.0" "T-23 versi sekarang 12.0.0 (updater impl B)"
assert_in "$LOG" "UPDATER-IMPL-B" "T-23 updater implementation B benar-benar dijalankan"
assert_eq "$(sha256sum "$OPERATOR_DIR/upgrade.sh" | awk '{print $1}')" "$BOOTSTRAP_SHA" \
  "T-23 SHA-256 bootstrap operator MASIH identik (lintas rilis + lintas implementasi updater)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-23 kunci agent identik"
assert_eq "$(git -C "$INC2/uaeg" rev-parse HEAD)" "$INC2_HEAD_BEFORE" "T-23 clone operator tidak berubah (HEAD)"
assert_eq "$(git -C "$INC2/uaeg" status --porcelain)" "$INC2_STATUS_BEFORE" "T-23 file untracked operator tetap utuh"

# ----------------------------------------------------------------------
t "T-24: manifest yang di-tamper -> DITOLAK (autentisitas publisher)"
# ----------------------------------------------------------------------
python3 - "$RELROOT/download/v11.0.0/uaeg-manifest.json" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read().replace('"version": "11.0.0"', '"version": "12.9.9"')
open(p, "w", encoding="utf-8").write(s)
PY
set_latest_tag "11.0.0"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_A" \
  --installer "$WORK_A/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 1 "T-24 rc=1 (manifest tampered -> ditolak)"
assert_in "$LOG" "SIGNATURE INVALID" "T-24 pesan signature invalid"
assert_eq "$(installed_version)" "12.0.0" "T-24 versi tidak berubah (12.0.0)"
assert_eq "$(backup_count)" "$N_BEFORE" "T-24 tidak ada backup baru (tidak ada yang dieksekusi)"

# ----------------------------------------------------------------------
t "T-25: updater yang di-tamper (sha mismatch) -> DITOLAK (integritas artifact)"
# ----------------------------------------------------------------------
# restore the v11.0.0 manifest to its signed form, then tamper the updater bytes
python3 - "$RELROOT/download/v11.0.0/uaeg-manifest.json" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read().replace('"version": "12.9.9"', '"version": "11.0.0"')
open(p, "w", encoding="utf-8").write(s)
PY
printf '\n# tampered\n' >> "$RELROOT/download/v11.0.0/uaeg-updater-11.0.0.sh"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_A" \
  --installer "$WORK_A/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 1 "T-25 rc=1 (updater sha mismatch -> ditolak)"
assert_in "$LOG" "SHA-256 MISMATCH" "T-25 pesan sha mismatch"
assert_eq "$(installed_version)" "12.0.0" "T-25 versi tidak berubah"

# ----------------------------------------------------------------------
t "T-26: manifest ditandatangani kunci asing -> DITOLAK (publisher asing)"
# ----------------------------------------------------------------------
# re-sign the (restored) manifest with the ROGUE key; the bootstrap's
# keyring only trusts the test key
sign_manifest "$RELROOT/download/v11.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v11.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/rogue.key"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_A" \
  --installer "$WORK_A/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 1 "T-26 rc=1 (publisher asing -> ditolak)"
assert_in "$LOG" "not signed by" "T-26 pesan publisher tidak dipercaya (ditolak)"
assert_eq "$(installed_version)" "12.0.0" "T-26 versi tidak berubah"
# restore the trusted signature for later tests
sign_manifest "$RELROOT/download/v11.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v11.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/test-release.key"

# ----------------------------------------------------------------------
t "T-27: updater interface terlalu baru -> bootstrap menolak dengan instruksi refresh"
# ----------------------------------------------------------------------
python3 - "$RELROOT/download/v12.0.0/uaeg-manifest.json" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p, encoding="utf-8"))
m["updater"]["interface"] = 99
open(p, "w", encoding="utf-8").write(json.dumps(m, indent=2, sort_keys=True) + "\n")
PY
sign_manifest "$RELROOT/download/v12.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v12.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/test-release.key"
set_latest_tag "12.0.0"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_B" \
  --installer "$WORK_B/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 1 "T-27 rc=1 (interface baru -> tolak)"
assert_in "$LOG" "interface" "T-27 pesan menyebut interface"
assert_in "$LOG" "bootstrap" "T-27 pesan menjelaskan refresh bootstrap (prosedur istimewa)"
assert_eq "$(installed_version)" "12.0.0" "T-27 versi tidak berubah"
# restore interface 1
python3 - "$RELROOT/download/v12.0.0/uaeg-manifest.json" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p, encoding="utf-8"))
m["updater"]["interface"] = 1
open(p, "w", encoding="utf-8").write(json.dumps(m, indent=2, sort_keys=True) + "\n")
PY
sign_manifest "$RELROOT/download/v12.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v12.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/test-release.key"

# ----------------------------------------------------------------------
t "T-28: release tanpa manifest -> pesan jelas, tidak ada yang berubah"
# ----------------------------------------------------------------------
mkdir -p "$RELROOT/download/v0.0.9"
set_latest_tag "0.0.9"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_A" \
  --installer "$WORK_A/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 1 "T-28 rc=1 (manifest hilang -> ditolak)"
assert_in "$LOG" "could not download the release manifest" "T-28 pesan manifest hilang"
assert_eq "$(installed_version)" "12.0.0" "T-28 versi tidak berubah"
set_latest_tag "12.0.0"

# ----------------------------------------------------------------------
t "T-29: bootstrap --check dan --dry-run melalui kanal rilis (U-10, U-11)"
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --check --repo "$REMOTE_B" \
  --installer "$WORK_B/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-29 --check rc=0"
assert_in "$LOG" "12.0.0" "T-29 --check menampilkan versi"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --dry-run --repo "$REMOTE_B" \
  --installer "$WORK_B/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-29 --dry-run rc=0"
assert_in "$LOG" "UPGRADE PLAN" "T-29 --dry-run menampilkan rencana"
assert_eq "$(backup_count)" "$N_BEFORE" "T-29 --check/--dry-run tidak membuat backup"

# ----------------------------------------------------------------------
t "T-30: release rusak -> rollback OTOMATIS via bootstrap; RETRY dengan bootstrap yang sama"
# ----------------------------------------------------------------------
WORK_C="$BASE/work-c"; REMOTE_C="$BASE/remote-c.git"; GIT_C_DIR="$BASE/gitc"
make_source "$WORK_C" "13.0.0"
printf '\ndef broken(:\n' >> "$WORK_C/src/uaeg/server.py"
git init --bare -q "$REMOTE_C"
make_release_repo "$GIT_C_DIR" "$REMOTE_C" "13.0.0"
printf '\ndef broken(:\n' >> "$GIT_C_DIR/src/uaeg/server.py"
git -C "$GIT_C_DIR" -c user.email=ci@test -c user.name=ci commit -qam "release C (broken)"
git -C "$GIT_C_DIR" tag -f v13.0.0 >/dev/null 2>&1 || git -C "$GIT_C_DIR" tag v13.0.0
# force-move the tag to the broken commit on the remote
git -C "$GIT_C_DIR" push -q origin main --tags --force
make_release "$WORK_C" "13.0.0"
set_latest_tag "13.0.0"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_C" \
  --installer "$WORK_C/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 2 "T-30 rc=2 (release rusak -> rollback otomatis via bootstrap)"
assert_eq "$(installed_version)" "12.0.0" "T-30 aplikasi kembali ke 12.0.0"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-30 kunci agent tetap utuh"

# retry with a GOOD release D using the SAME bootstrap
WORK_D="$BASE/work-d"; REMOTE_D="$BASE/remote-d.git"; GIT_D_DIR="$BASE/gitd"
make_source "$WORK_D" "14.0.0"
git init --bare -q "$REMOTE_D"
make_release_repo "$GIT_D_DIR" "$REMOTE_D" "14.0.0"
make_release "$WORK_D" "14.0.0"
set_latest_tag "14.0.0"
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --repo "$REMOTE_D" \
  --installer "$WORK_D/install-test.sh" "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-30 retry dengan bootstrap yang sama -> 14.0.0 rc=0"
assert_eq "$(installed_version)" "14.0.0" "T-30 versi sekarang 14.0.0"
assert_eq "$(sha256sum "$OPERATOR_DIR/upgrade.sh" | awk '{print $1}')" "$BOOTSTRAP_SHA" \
  "T-30 SHA-256 bootstrap operator MASIH identik setelah gagal+retry"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-30 kunci agent identik setelah retry"

# ----------------------------------------------------------------------
t "T-31: rollback OFFLINE (server rilis mati) via updater ter-cache"
# ----------------------------------------------------------------------
kill "$RELSRV_PID" 2>/dev/null
sleep 0.5
run "$LOG" bash "$OPERATOR_DIR/upgrade.sh" --rollback --yes "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-31 rollback offline rc=0 (cached updater)"
assert_in "$LOG" "cached updater" "T-31 menggunakan updater ter-cache"
# The latest backup belongs to the SUCCESSFUL 14.0.0 upgrade: by contract
# (U-3/U-7) success backups retain data+config only, so the application
# stays at 14.0.0 while the DATA returns to the pre-upgrade state. App
# recovery from a success backup = install.sh from the immutable tag.
assert_eq "$(installed_version)" "14.0.0" "T-31 app tetap 14.0.0 (backup sukses tidak menyimpan app — kontrak U-7)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-31 data pra-upgrade dipulihkan (kunci identik)"

# ----------------------------------------------------------------------
# T-32 .. T-38 — INSIDEN v0.3.4 (2026-11-08): konfirmasi interaktif y/N
# dibaca dari STDIN, bukan dari keyboard operator. Saat stdin bukan
# keyboard (terputus, </dev/null, skrip lewat pipe ala `curl | bash`,
# sudo/CI), jawaban "y" tidak pernah sampai ke skrip dan v0.3.4:
#   - salah lapor "cancelled by the operator" padahal operator menjawab y
#   - ATAU mati diam-diam (set -e + read EOF) tanpa pesan error sama sekali
#   - ATAU crash BASH_SOURCE unbound saat skrip dieksekusi via stdin
# Fix v0.3.5: confirm_ask() membaca keyboard operator (stdin jika tty,
# lalu /dev/tty, lalu stdin terpipah untuk otomasi) dan melaporkan
# penolakan dengan alasan yang BENAR. Test memakai pty sungguhan
# (script(1)) sebagai terminal operator.
# ----------------------------------------------------------------------
command -v script >/dev/null 2>&1 \
  || no "T-32..T-38 memerlukan script(1) dari util-linux di lingkungan test"
command -v setsid >/dev/null 2>&1 \
  || no "T-32..T-38 memerlukan setsid(1) dari util-linux di lingkungan test"
CONFIRM_ARGS=(--install-dir "$INSTALL" --data-dir "$DATA"
               --config-dir "$CONFIG" --backups-dir "$BACKUPS")

# ----------------------------------------------------------------------
t "T-32: INSIDEN v0.3.4 — jawab 'y' di terminal saat stdin terputus -> upgrade HARUS berjalan"
# ----------------------------------------------------------------------
# Bentuk persis insiden operator: prompt tampil, operator mengetik y,
# stdin skrip terputus (< /dev/null). Konfirmasi wajib dibaca dari
# /dev/tty (keyboard operator), bukan stdin.
SRC_C1="$BASE/src-c1"
make_source "$SRC_C1" "15.0.0"
printf 'y\n' | script -qec \
  "bash $SRC_C1/updater-test.sh --source $SRC_C1 --installer $SRC_C1/install-test.sh ${CONFIRM_ARGS[*]} < /dev/null" \
  /dev/null > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-32 rc=0 (jawaban y dibaca dari terminal, bukan stdin)"
assert_eq "$(installed_version)" "15.0.0" "T-32 versi sekarang 15.0.0"
assert_in "$LOG" "Continue with the upgrade to v15.0.0?" "T-32 prompt konfirmasi TAMPIL di terminal"
assert_not_in "$LOG" "cancelled by the operator" "T-32 tidak ada false 'cancelled by the operator'"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-32 kunci agent identik (zero data loss)"
assert_eq "$(latest_record result)" "success" "T-32 RECORD result=success"

# ----------------------------------------------------------------------
t "T-33: INSIDEN v0.3.4 — skrip dieksekusi via stdin (curl|bash) + 'y' di terminal -> HARUS berjalan"
# ----------------------------------------------------------------------
SRC_C2="$BASE/src-c2"
make_source "$SRC_C2" "16.0.0"
printf 'y\n' | script -qec \
  "cat $SRC_C2/updater-test.sh | bash -s -- --source $SRC_C2 --installer $SRC_C2/install-test.sh ${CONFIRM_ARGS[*]}" \
  /dev/null > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-33 rc=0 (bash -s tanpa crash BASH_SOURCE; jawaban dari /dev/tty)"
assert_eq "$(installed_version)" "16.0.0" "T-33 versi sekarang 16.0.0"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-33 kunci agent identik"
assert_not_in "$LOG" "unbound variable" "T-33 tidak ada crash BASH_SOURCE"

# ----------------------------------------------------------------------
t "T-34: jawab 'n' di terminal -> penolakan bersih dan akurat"
# ----------------------------------------------------------------------
V_BEFORE="$(installed_version)"; N_BEFORE="$(backup_count)"
printf 'n\n' | script -qec \
  "bash $SRC_C2/updater-test.sh --source $SRC_C2 --installer $SRC_C2/install-test.sh ${CONFIRM_ARGS[*]} < /dev/null" \
  /dev/null > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-34 rc=1 (penolakan eksplisit oleh operator)"
assert_in "$LOG" "cancelled by the operator" "T-34 pesan penolakan akurat untuk jawaban n"
assert_eq "$(installed_version)" "$V_BEFORE" "T-34 versi tidak berubah"
assert_eq "$(backup_count)" "$N_BEFORE" "T-34 tidak ada backup baru"

# ----------------------------------------------------------------------
t "T-35: tanpa terminal + tanpa jawaban -> penolakan dengan alasan BENAR (bukan 'cancelled')"
# ----------------------------------------------------------------------
# v0.3.4 pada bentuk ini mati diam-diam (set -e membunuh skrip saat read
# EOF) tanpa pesan apa pun. Fix: pesan harus menyatakan jawaban tidak
# terbaca + arahkan ke --yes.
setsid bash "$SRC_C2/updater-test.sh" --source "$SRC_C2" \
  --installer "$SRC_C2/install-test.sh" "${CONFIRM_ARGS[@]}" \
  < /dev/null > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-35 rc=1 (ditolak, bukan lanjut diam-diam)"
assert_in "$LOG" "no confirmation was received" "T-35 pesan menyatakan jawaban tidak dapat dibaca"
assert_in "$LOG" "--yes" "T-35 pesan mengarah ke --yes untuk unattended"
assert_not_in "$LOG" "cancelled by the operator" "T-35 tidak salah lapor 'cancelled by the operator'"
assert_eq "$(installed_version)" "$V_BEFORE" "T-35 versi tidak berubah"
assert_eq "$(backup_count)" "$N_BEFORE" "T-35 tidak ada backup baru"

# ----------------------------------------------------------------------
t "T-36: otomasi tanpa terminal — 'y' lewat pipe tetap diterima"
# ----------------------------------------------------------------------
SRC_C3="$BASE/src-c3"
make_source "$SRC_C3" "17.0.0"
printf 'y\n' | setsid bash "$SRC_C3/updater-test.sh" --source "$SRC_C3" \
  --installer "$SRC_C3/install-test.sh" "${CONFIRM_ARGS[@]}" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-36 rc=0 (jawaban terpipah diterima — jalur otomasi terjaga)"
assert_eq "$(installed_version)" "17.0.0" "T-36 versi sekarang 17.0.0"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-36 kunci agent identik"

# ----------------------------------------------------------------------
t "T-37: konfirmasi rollback — 'n' -> aborted; 'y' via terminal saat stdin terputus -> rollback berjalan"
# ----------------------------------------------------------------------
printf 'n\n' | script -qec \
  "bash $SRC_C3/updater-test.sh --rollback ${CONFIRM_ARGS[*]} < /dev/null" \
  /dev/null > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-37 rc=1 ('n' pada rollback -> aborted)"
assert_in "$LOG" "aborted" "T-37 pesan aborted"
printf 'y\n' | script -qec \
  "bash $SRC_C3/updater-test.sh --rollback ${CONFIRM_ARGS[*]} < /dev/null" \
  /dev/null > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-37 rc=0 (jawaban y via terminal -> rollback berjalan)"
assert_in "$LOG" "Continue with the rollback?" "T-37 prompt rollback tampil"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-37 data pra-upgrade dipulihkan (kunci identik)"

# ----------------------------------------------------------------------
t "T-38: helper confirm_ask (diekstrak byte-exact dari install.sh & updater.sh)"
# ----------------------------------------------------------------------
for f in "$REPO_DIR/updater.sh" "$REPO_DIR/install.sh"; do
  name="$(basename "$f" .sh)"
  sed -n '/^confirm_ask()/,/^}/p' "$f" > "$BASE/ask-$name.sh"
  echo "confirm_ask 'P? [y/N] ' >/dev/null" >> "$BASE/ask-$name.sh"
  grep -q '^confirm_ask()' "$BASE/ask-$name.sh" \
    || { no "T-38 helper confirm_ask tidak ditemukan di $f"; continue; }
  printf 'y\n' | setsid bash "$BASE/ask-$name.sh" >/dev/null 2>&1; r1=$?
  printf 'n\n' | setsid bash "$BASE/ask-$name.sh" >/dev/null 2>&1; r2=$?
  setsid bash "$BASE/ask-$name.sh" < /dev/null >/dev/null 2>&1; r3=$?
  printf 'y\n' | script -qec "bash $BASE/ask-$name.sh < /dev/null" /dev/null >/dev/null 2>&1; r4=$?
  assert_eq "$r1" "0" "T-38 $name: 'y' terpipah -> ya (0)"
  assert_eq "$r2" "1" "T-38 $name: 'n' terpipah -> tidak (1)"
  assert_eq "$r3" "2" "T-38 $name: EOF tanpa terminal -> tidak ada jawaban (2)"
  assert_eq "$r4" "0" "T-38 $name: stdin terputus + tty -> 'y' terbaca (0)"
done

# ----------------------------------------------------------------------
# T-39 .. T-52 — KONTRAK v4: install.sh adalah ENTRYPOINT TERPADU
# (install + upgrade + service management, docs/UPGRADE_CONTRACT.md U-16).
# Operator mengunduh install.sh SEKALI; file yang sama dipakai untuk semua
# upgrade berikutnya via bootstrap ter-embed (keyring + verifikasi
# manifest + updater terverifikasi - rantai sama dengan upgrade.sh).
# ----------------------------------------------------------------------
t "T-39: setup — rilis server (hidup lagi), install.sh terpadu operator, rilis E"
# T-31 mematikan server rilis; nyalakan lagi untuk kanal terpadu.
RELPORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
python3 -m http.server "$RELPORT" --bind 127.0.0.1 --directory "$RELROOT" >/dev/null 2>&1 &
RELSRV_PID=$!
sleep 1
curl -fsS "http://127.0.0.1:$RELPORT/releases/latest" >/dev/null 2>&1 \
  && ok "T-39 release server hidup lagi (port $RELPORT)" \
  || no "T-39 release server tidak hidup"
export UAEG_RELEASE_API_BASE="http://127.0.0.1:$RELPORT"
export UAEG_RELEASE_DOWNLOAD_BASE="http://127.0.0.1:$RELPORT/download"
export UAEG_BACKUPS_DIR="$BACKUPS"
export UAEG_SYSTEM_UAEG_BIN="$TESTBIN/uaeg"

# File operator: SATU install.sh terpadu (patched + keyring uji), SHA dicatat.
OPERATOR_UNI="$BASE/operator-unified"; mkdir -p "$OPERATOR_UNI"
patch_unifiedsh "$REPO_DIR/install.sh" > "$OPERATOR_UNI/install.sh"
UNI_SHA="$(sha256sum "$OPERATOR_UNI/install.sh" | awk '{print $1}')"
assert_file "$OPERATOR_UNI/install.sh" "T-39 install.sh terpadu operator dibuat (sha ${UNI_SHA:0:12}…)"

# make_release_repo_uni <gitdir> <remote> <version>: remote git yang POHONNYA
# membawa install.sh ter-patch (agar updater bisa menjalankannya tanpa root).
make_release_repo_uni() {
  local gdir="$1" remote="$2" ver="$3"
  git clone -q "$REPO_DIR" "$gdir"
  git -C "$gdir" checkout -q -B main   # branch-agnostic harness (see T-13)
  git -C "$gdir" remote set-url origin "$remote"
  sed -i "s/^version[[:space:]]*=[[:space:]]*\".*\"/version = \"$ver\"/" "$gdir/pyproject.toml"
  sed -i "s/^__version__[[:space:]]*=[[:space:]]*\".*\"/__version__ = \"$ver\"/" "$gdir/src/uaeg/__init__.py"
  patch_installsh "$gdir/install.sh" > "$gdir/install.sh.tmp" && mv "$gdir/install.sh.tmp" "$gdir/install.sh"
  git -C "$gdir" -c user.email=ci@test -c user.name=ci commit -qam "release $ver (test-patched installer)"
  git -C "$gdir" tag "v$ver"
  git -C "$gdir" push -q origin main --tags
}

# Rilis E (v18.0.0)
WORK_E="$BASE/work-e"; REMOTE_E="$BASE/remote-e.git"; GIT_E_DIR="$BASE/gite"
make_source "$WORK_E" "18.0.0"
git init --bare -q "$REMOTE_E"
make_release_repo_uni "$GIT_E_DIR" "$REMOTE_E" "18.0.0"
make_release "$WORK_E" "18.0.0"
set_latest_tag "18.0.0"

run "$LOG" bash "$OPERATOR_UNI/install.sh" --help
assert_rc 0 "T-39 --help rc=0"
assert_in "$LOG" "--check" "T-39 help memuat --check"
assert_in "$LOG" "--rollback" "T-39 help memuat --rollback"
assert_in "$LOG" "--repair" "T-39 help memuat --repair"
assert_in "$LOG" "--start" "T-39 help memuat --start"
run "$LOG" bash "$OPERATOR_UNI/install.sh" --version
assert_rc 0 "T-39 --version (tanpa argumen) rc=0"
assert_in "$LOG" "v$REPO_VERSION" "T-39 --version mencetak versi installer (v$REPO_VERSION)"

# ----------------------------------------------------------------------
t "T-40: BARE install.sh pada instalasi ada -> SAFE UPGRADE terverifikasi (U-16)"
# ----------------------------------------------------------------------
V_BEFORE="$(installed_version)"
export UAEG_REPO_URL="$REMOTE_E"
N_BEFORE="$(backup_count)"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-40 bare install.sh upgrade ke 18.0.0 rc=0 (dari $V_BEFORE)"
assert_eq "$(installed_version)" "18.0.0" "T-40 versi sekarang 18.0.0"
assert_in "$LOG" "verifying manifest signature" "T-40 manifest signature diverifikasi (bootstrap ter-embed)"
assert_in "$LOG" "verifying updater integrity" "T-40 integritas updater diverifikasi"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-40 kunci agent identik"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-40 config operator dipertahankan"
assert_eq "$(sha256sum "$OPERATOR_UNI/install.sh" | awk '{print $1}')" "$UNI_SHA" \
  "T-40 SHA-256 install.sh operator TIDAK berubah"
assert_eq "$(latest_record bootstrap_invoked)" "True" "T-40 RECORD bootstrap_invoked=true"
assert_eq "$(latest_record bootstrap_file)" "$OPERATOR_UNI/install.sh" "T-40 RECORD bootstrap_file == install.sh operator"
assert_eq "$(latest_record to_version)" "18.0.0" "T-40 RECORD to_version=18.0.0"

# ----------------------------------------------------------------------
t "T-41: MULTI-RELEASE — file install.sh yang sama, rilis F dengan UPDATER BERUBAH"
# ----------------------------------------------------------------------
WORK_F="$BASE/work-f"; REMOTE_F="$BASE/remote-f.git"; GIT_F_DIR="$BASE/gitf"
make_source "$WORK_F" "19.0.0"
sed -i 's|^main$|say "UPDATER-IMPL-F (changed implementation)"\nmain|' "$WORK_F/updater-test.sh"
git init --bare -q "$REMOTE_F"
make_release_repo_uni "$GIT_F_DIR" "$REMOTE_F" "19.0.0"
make_release "$WORK_F" "19.0.0"
set_latest_tag "19.0.0"
export UAEG_REPO_URL="$REMOTE_F"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-41 file yang sama upgrade ke 19.0.0 rc=0"
assert_eq "$(installed_version)" "19.0.0" "T-41 versi sekarang 19.0.0"
assert_in "$LOG" "UPDATER-IMPL-F" "T-41 updater implementasi F benar-benar dijalankan"
assert_eq "$(sha256sum "$OPERATOR_UNI/install.sh" | awk '{print $1}')" "$UNI_SHA" \
  "T-41 SHA-256 install.sh operator MASIH identik (reuse multi-rilis)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-41 kunci agent identik"

# ----------------------------------------------------------------------
t "T-42: install.sh --version X.Y.Z pada instalasi ada -> upgrade PINNED"
# ----------------------------------------------------------------------
WORK_G="$BASE/work-g"; REMOTE_G="$BASE/remote-g.git"; GIT_G_DIR="$BASE/gitg"
make_source "$WORK_G" "20.0.0"
git init --bare -q "$REMOTE_G"
make_release_repo_uni "$GIT_G_DIR" "$REMOTE_G" "20.0.0"
make_release "$WORK_G" "20.0.0"
export UAEG_REPO_URL="$REMOTE_G"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" --version 20.0.0 > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-42 --version 20.0.0 rc=0"
assert_eq "$(installed_version)" "20.0.0" "T-42 versi sekarang 20.0.0 (pin terverifikasi)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-42 kunci agent identik"

# ----------------------------------------------------------------------
t "T-43: manifest di-tamper -> install.sh bare MENOLAK (U-14 via installer)"
# ----------------------------------------------------------------------
python3 - "$RELROOT/download/v18.0.0/uaeg-manifest.json" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read().replace('"version": "18.0.0"', '"version": "12.9.9"')
open(p, "w", encoding="utf-8").write(s)
PY
set_latest_tag "18.0.0"
export UAEG_REPO_URL="$REMOTE_E"
N_BEFORE="$(backup_count)"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-43 rc=1 (manifest tampered -> ditolak oleh installer terpadu)"
assert_in "$LOG" "SIGNATURE INVALID" "T-43 pesan signature invalid"
assert_eq "$(installed_version)" "20.0.0" "T-43 versi tidak berubah"
assert_eq "$(backup_count)" "$N_BEFORE" "T-43 tidak ada backup baru"
python3 - "$RELROOT/download/v18.0.0/uaeg-manifest.json" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read().replace('"version": "12.9.9"', '"version": "18.0.0"')
open(p, "w", encoding="utf-8").write(s)
PY

# ----------------------------------------------------------------------
t "T-44: manifest kunci asing -> install.sh bare MENOLAK"
# ----------------------------------------------------------------------
sign_manifest "$RELROOT/download/v18.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v18.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/rogue.key"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-44 rc=1 (publisher asing -> ditolak)"
assert_in "$LOG" "not signed by" "T-44 pesan publisher tidak dipercaya"
assert_eq "$(installed_version)" "20.0.0" "T-44 versi tidak berubah"
sign_manifest "$RELROOT/download/v18.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v18.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/test-release.key"

# ----------------------------------------------------------------------
t "T-45: updater di-tamper (sha mismatch) -> install.sh bare MENOLAK"
# ----------------------------------------------------------------------
printf '\n# tampered\n' >> "$RELROOT/download/v18.0.0/uaeg-updater-18.0.0.sh"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-45 rc=1 (updater sha mismatch -> ditolak)"
assert_in "$LOG" "SHA-256 MISMATCH" "T-45 pesan sha mismatch"
assert_eq "$(installed_version)" "20.0.0" "T-45 versi tidak berubah"
cp "$WORK_E/updater-test.sh" "$RELROOT/download/v18.0.0/uaeg-updater-18.0.0.sh"

# ----------------------------------------------------------------------
t "T-46: updater interface terlalu baru -> install.sh menolak + instruksi refresh"
# ----------------------------------------------------------------------
python3 - "$RELROOT/download/v19.0.0/uaeg-manifest.json" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p, encoding="utf-8"))
m["updater"]["interface"] = 99
open(p, "w", encoding="utf-8").write(json.dumps(m, indent=2, sort_keys=True) + "\n")
PY
sign_manifest "$RELROOT/download/v19.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v19.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/test-release.key"
set_latest_tag "19.0.0"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 1 "T-46 rc=1 (interface baru -> tolak)"
assert_in "$LOG" "interface" "T-46 pesan menyebut interface"
assert_in "$LOG" "install.sh" "T-46 instruksi refresh mengarah ke install.sh"
assert_eq "$(installed_version)" "20.0.0" "T-46 versi tidak berubah"
python3 - "$RELROOT/download/v19.0.0/uaeg-manifest.json" <<'PY'
import json, sys
p = sys.argv[1]
m = json.load(open(p, encoding="utf-8"))
m["updater"]["interface"] = 1
open(p, "w", encoding="utf-8").write(json.dumps(m, indent=2, sort_keys=True) + "\n")
PY
sign_manifest "$RELROOT/download/v19.0.0/uaeg-manifest.json" \
  "$RELROOT/download/v19.0.0/uaeg-manifest.json.sig" "$TESTKEY_DIR/test-release.key"

# ----------------------------------------------------------------------
t "T-47: install.sh --check dan --dry-run melalui kanal rilis (U-10, U-11)"
# ----------------------------------------------------------------------
WORK_H="$BASE/work-h"; REMOTE_H="$BASE/remote-h.git"; GIT_H_DIR="$BASE/gith"
make_source "$WORK_H" "21.0.0"
git init --bare -q "$REMOTE_H"
make_release_repo_uni "$GIT_H_DIR" "$REMOTE_H" "21.0.0"
make_release "$WORK_H" "21.0.0"
set_latest_tag "21.0.0"
export UAEG_REPO_URL="$REMOTE_H"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$OPERATOR_UNI/install.sh" --check
assert_rc 0 "T-47 --check rc=0"
assert_in "$LOG" "20.0.0" "T-47 --check menampilkan versi terpasang"
assert_in "$LOG" "update available" "T-47 --check menandai pembaruan tersedia"
run "$LOG" bash "$OPERATOR_UNI/install.sh" --dry-run
assert_rc 0 "T-47 --dry-run rc=0"
assert_in "$LOG" "UPGRADE PLAN" "T-47 --dry-run menampilkan rencana upgrade"
assert_eq "$(backup_count)" "$N_BEFORE" "T-47 --check/--dry-run tidak membuat backup"

# ----------------------------------------------------------------------
t "T-48: rilis rusak via install.sh bare -> ROLLBACK OTOMATIS (rc 2)"
# ----------------------------------------------------------------------
WORK_I="$BASE/work-i"; REMOTE_I="$BASE/remote-i.git"; GIT_I_DIR="$BASE/giti"
make_source "$WORK_I" "22.0.0"
printf '\ndef broken(:\n' >> "$WORK_I/src/uaeg/server.py"
git init --bare -q "$REMOTE_I"
make_release_repo_uni "$GIT_I_DIR" "$REMOTE_I" "22.0.0"
printf '\ndef broken(:\n' >> "$GIT_I_DIR/src/uaeg/server.py"
git -C "$GIT_I_DIR" -c user.email=ci@test -c user.name=ci commit -qam "release I (broken)"
git -C "$GIT_I_DIR" tag -f v22.0.0 >/dev/null 2>&1 || git -C "$GIT_I_DIR" tag v22.0.0
git -C "$GIT_I_DIR" push -q origin main --tags --force
make_release "$WORK_I" "22.0.0"
set_latest_tag "22.0.0"
export UAEG_REPO_URL="$REMOTE_I"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 2 "T-48 rc=2 (rilis rusak -> rollback otomatis via installer terpadu)"
assert_eq "$(installed_version)" "20.0.0" "T-48 aplikasi kembali ke 20.0.0"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-48 kunci agent tetap utuh"

# ----------------------------------------------------------------------
t "T-49: install.sh --rollback (updater ter-cache, manual)"
# ----------------------------------------------------------------------
run "$LOG" bash "$OPERATOR_UNI/install.sh" --rollback --yes
assert_rc 0 "T-49 --rollback rc=0"
assert_in "$LOG" "cached updater" "T-49 menggunakan updater ter-cache"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-49 data pra-upgrade dipulihkan (kunci identik)"

# ----------------------------------------------------------------------
t "T-50: perintah service tanpa systemd/unit -> penolakan bersih (rc 1)"
# ----------------------------------------------------------------------
for sc in --start --stop --restart --status; do
  run "$LOG" bash "$OPERATOR_UNI/install.sh" "$sc"
  if [[ "$RUN_RC" -eq 1 ]] && grep -q "systemd" "$LOG"; then
    ok "T-50 $sc -> rc=1 + pesan systemd yang jelas"
  else
    no "T-50 $sc -> rc=$RUN_RC (harus 1 dengan pesan systemd; log: $(head -c 120 "$LOG"))"
  fi
done

# ----------------------------------------------------------------------
t "T-51: --repair mempertahankan data + menerapkan flag config (in-place)"
# ----------------------------------------------------------------------
WORK_J="$BASE/work-j"
make_source "$WORK_J" "23.0.0"
run "$LOG" bash "$WORK_J/install-test.sh" --repair \
  --port 3952 --dashboard-port 3956 --dashboard-bind loopback \
  --public-ip 127.0.0.1 --self-signed \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 0 "T-51 --repair rc=0"
assert_eq "$(installed_version)" "23.0.0" "T-51 versi sekarang 23.0.0 (sumber lokal)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-51 kunci agent identik (data dipertahankan)"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3956" "T-51 flag config diterapkan (3954 -> 3956)"
# kembalikan port untuk konsistensi lingkungan berikutnya
env_uaeg config set dashboard.port 3954 >/dev/null 2>&1

# ----------------------------------------------------------------------
t "T-52: UPGRADE DARI BASELINE v0.3.5 -> installer terpadu (kompatibilitas)"
# ----------------------------------------------------------------------
# Instal ulang dari POHON TAG v0.3.5 ASLI (installer lama, tanpa subperintah)
# lalu upgrade dengan install.sh terpadu bare. Membuktikan jalur migrasi
# operator rilis 0.3.2-0.3.5: kunci + data selamat, kontrak tetap berlaku.
rm -rf "$INSTALL" "$DATA" "$CONFIG" "$TESTBIN/uaeg"
SRC_035="$BASE/src-035"
mkdir -p "$SRC_035"
git -C "$REPO_DIR" archive --format=tar "v0.3.5" | tar -C "$SRC_035" -xf -
patch_installsh "$SRC_035/install.sh" > "$SRC_035/install-test.sh"
run "$LOG" bash "$SRC_035/install-test.sh" \
  --port 3952 --dashboard-port 3953 --dashboard-bind loopback \
  --public-ip 127.0.0.1 --self-signed \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 0 "T-52 fresh install dari tag v0.3.5 rc=0"
assert_eq "$(installed_version)" "0.3.5" "T-52 baseline terpasang: 0.3.5"
env_uaeg generate --name "migrate-alpha" > "$BASE/key-migrate.txt" 2>&1
env_uaeg generate --name "migrate-bravo" > "$BASE/key-migrate2.txt" 2>&1
KEYS_035="$(db_keys)"
WORK_K="$BASE/work-k"; REMOTE_K="$BASE/remote-k.git"; GIT_K_DIR="$BASE/gitk"
make_source "$WORK_K" "24.0.0"
git init --bare -q "$REMOTE_K"
make_release_repo_uni "$GIT_K_DIR" "$REMOTE_K" "24.0.0"
make_release "$WORK_K" "24.0.0"
set_latest_tag "24.0.0"
export UAEG_REPO_URL="$REMOTE_K"
printf 'y\n' | bash "$OPERATOR_UNI/install.sh" > "$LOG" 2>&1
RUN_RC=$?
assert_rc 0 "T-52 upgrade v0.3.5 -> 24.0.0 via install.sh bare rc=0"
assert_eq "$(installed_version)" "24.0.0" "T-52 versi sekarang 24.0.0"
assert_eq "$(db_keys)" "$KEYS_035" "T-52 kunci agent dari baseline 0.3.5 identik"
assert_eq "$(sha256sum "$OPERATOR_UNI/install.sh" | awk '{print $1}')" "$UNI_SHA" \
  "T-52 SHA-256 install.sh operator MASIH identik (lintas baseline)"

# ----------------------------------------------------------------------
# T-53 .. T-57 — INSIDEN PRODUKSI 2026-11-08 (host.zerotier.my.id):
# `install.sh --repair` dari checkout LAMA menimpa aplikasi BARU, KEMUDIAN
# skema lama menolak konfigurasi yang ada (dashboard.tls) -> CONFIG_ERROR,
# service mati, rollback. v0.4.1: preflight pre-mutation menolak lebih
# dulu (guard 1: urutan versi, exit 5; guard 2: probe skema) dan tidak
# ada satu byte pun berubah.
# ----------------------------------------------------------------------
tree_hash() {  # deterministic content hash of the installed app tree
  ( cd "$1" && find . -type f ! -path './.venv/*' -print0 | sort -z \
      | xargs -0 sha256sum | sha256sum | awk '{print $1}' )
}

t "T-53: INSIDEN 2026-11-08 — --repair dari sumber LAMA atas instalasi BARU -> tolak exit 5 SEBELUM mutasi"
# ----------------------------------------------------------------------
# Installed: 24.0.0 (dari T-52). Source: repo asli v$REPO_VERSION (LEBIH
# LAMA) - persis bentuk produksi (checkout basi v0.2.x + aplikasi v0.4.0).
INSTALL_HASH_BEFORE="$(tree_hash "$INSTALL")"
CFG_SHA_BEFORE="$(sha256sum "$CONFIG/config.json" | awk '{print $1}')"
KEYS_BEFORE_53="$(db_keys)"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC/install-test.sh" --repair \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 5 "T-53 rc=5 (downgrade pre-mutation ditolak)"
assert_in "$LOG" "REFUSING to deploy OLDER source" "T-53 pesan penolakan guard 1"
assert_in "$LOG" "NOTHING was changed" "T-53 penolakan menyatakan tidak ada perubahan"
assert_eq "$(installed_version)" "24.0.0" "T-53 versi terpasang tidak berubah"
assert_eq "$(tree_hash "$INSTALL")" "$INSTALL_HASH_BEFORE" "T-53 pohon aplikasi identik byte-per-byte"
assert_eq "$(sha256sum "$CONFIG/config.json" | awk '{print $1}')" "$CFG_SHA_BEFORE" "T-53 konfigurasi identik"
assert_eq "$(db_keys)" "$KEYS_BEFORE_53" "T-53 kunci agent identik"
assert_eq "$(backup_count)" "$N_BEFORE" "T-53 tidak ada backup baru"
assert_eq "$(ls -1d "$INSTALL".upgrade-bak-* 2>/dev/null | wc -l)" "0" \
  "T-53 tidak ada backup aplikasi baru (rollback-safety belum dibuat)"

t "T-54: INSIDEN (varian versi-sama) — skema lama menolak konfigurasi ada -> CONFIG_INCOMPATIBLE, nol mutasi"
# ----------------------------------------------------------------------
# Version strings LIE in the real world (mixed trees, hand-edited
# pyproject). Source with the SAME version as installed but with the
# dashboard `tls` field REMOVED (pre-v0.3.0 schema) - guard 1 passes,
# guard 2 must catch it via the staged-schema probe BEFORE any mutation.
SRC_INCOMPAT="$BASE/src-incompat"
make_source "$SRC_INCOMPAT" "24.0.0"
sed -i '/^    tls: bool = False$/d' "$SRC_INCOMPAT/src/uaeg/config.py"
grep -q 'tls: bool' "$SRC_INCOMPAT/src/uaeg/config.py" \
  && no "T-54 setup: field tls harus terhapus dari salinan" \
  || ok "T-54 setup: salinan sumber tanpa field tls (skema lama)"
assert_in "$CONFIG/config.json" "\"tls\"" "T-54 konfigurasi ada memuat dashboard.tls"
run "$LOG" bash "$SRC_INCOMPAT/install-test.sh" --repair \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 1 "T-54 rc=1 (inkompatibilitas skema ditolak)"
assert_in "$LOG" "NOT compatible" "T-54 pesan CONFIG_INCOMPATIBLE"
assert_in "$LOG" "unexpected keyword argument 'tls'" "T-54 penyebab persis insiden disebut"
assert_in "$LOG" "NOTHING was changed" "T-54 penolakan pre-mutation"
assert_eq "$(installed_version)" "24.0.0" "T-54 versi tidak berubah"
assert_eq "$(tree_hash "$INSTALL")" "$INSTALL_HASH_BEFORE" "T-54 pohon aplikasi identik"
assert_eq "$(sha256sum "$CONFIG/config.json" | awk '{print $1}')" "$CFG_SHA_BEFORE" "T-54 konfigurasi identik"

t "T-55: installer STANDALONE ter-pin lama + instalasi lebih baru -> tolak SEBELUM unduh (exit 5)"
# ----------------------------------------------------------------------
# The standalone variant of the incident: an old downloaded install.sh
# (no pyproject.toml beside it) pinned to its own release. The refusal
# must fire BEFORE any download - proven by pointing the release channel
# at a dead URL: a fetch attempt would fail with a network error instead
# of the downgrade refusal.
STANDALONE_OLD="$BASE/standalone-old"
mkdir -p "$STANDALONE_OLD"
cp "$SRC/install-test.sh" "$STANDALONE_OLD/install.sh"
run "$LOG" env UAEG_RELEASE_API_BASE="http://127.0.0.1:1/dead" \
  UAEG_RELEASE_DOWNLOAD_BASE="http://127.0.0.1:1/dead" \
  bash "$STANDALONE_OLD/install.sh" --repair \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 5 "T-55 rc=5 (pin lama ditolak pre-download)"
assert_in "$LOG" "REFUSING to --repair with a standalone installer" "T-55 pesan penolakan standalone"
assert_not_in "$LOG" "could not determine the latest release" "T-55 tidak sempat menyentuh jaringan"
assert_eq "$(installed_version)" "24.0.0" "T-55 versi tidak berubah"

t "T-56: UAEG_ALLOW_DOWNGRADE=1 + sumber lama yang KOMPATIBEL skema -> repair berjalan (jalur resmi)"
# ----------------------------------------------------------------------
# The documented escape hatch: explicit operator consent deploys older
# source whose schema still ACCEPTS the existing configuration. The
# full repair must succeed, preserve keys + operator config, and land
# on the older version.
DASH_PORT_BEFORE="$(cfg_field "cfg['dashboard']['port']")"
run "$LOG" env UAEG_ALLOW_DOWNGRADE=1 bash "$SRC/install-test.sh" --repair \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 0 "T-56 rc=0 (downgrade tersurat selesai)"
assert_in "$LOG" "UAEG_ALLOW_DOWNGRADE=1: deploying OLDER source" "T-56 peringatan persetujuan tampil"
assert_in "$LOG" "preflight: existing configuration accepted by the staged schema" "T-56 probe skema lolos"
assert_eq "$(installed_version)" "$REPO_VERSION" "T-56 versi sekarang v$REPO_VERSION (sumber lama)"
assert_eq "$(db_keys)" "$KEYS_BEFORE_53" "T-56 kunci agent identik"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "$DASH_PORT_BEFORE" "T-56 konfigurasi operator dipertahankan"

t "T-57: PYTHONPATH busuk (paket uaeg LAMA) tidak bisa membayangi — sanitasi interpreter v0.4.1"
# ----------------------------------------------------------------------
# The stale-module vector: an operator exports PYTHONPATH pointing at a
# pre-v0.3.0 uaeg package (no dashboard tls field). The v0.4.1 preflight
# probe and the config writer run with a sanitized interpreter
# (env -u PYTHONPATH + -P), so the shadow must be ignored and the repair
# must succeed with the CORRECT schema.
STALE_SHADOW="$BASE/stale-shadow"
mkdir -p "$STALE_SHADOW"
tar -C "$SRC" --exclude=.git -cf - src | tar -C "$STALE_SHADOW" -xf -
sed -i '/^    tls: bool = False$/d' "$STALE_SHADOW/src/uaeg/config.py"
# package importable as top-level `uaeg` via PYTHONPATH
run "$LOG" env PYTHONPATH="$STALE_SHADOW/src" \
  bash "$SRC/install-test.sh" --repair \
  --install-dir "$INSTALL" --config-dir "$CONFIG" --data-dir "$DATA" \
  --no-service --yes
assert_rc 0 "T-57 rc=0 (repair kebal PYTHONPATH busuk)"
assert_in "$LOG" "preflight: existing configuration accepted by the staged schema" "T-57 probe memakai sumber ter-staging, bukan bayangan"
assert_eq "$(installed_version)" "$REPO_VERSION" "T-57 versi $REPO_VERSION"
assert_in "$CONFIG/config.json" "\"tls\"" "T-57 konfigurasi tetap membawa dashboard.tls (skema benar yang menulis)"

# matikan server rilis yang dinyalakan lagi di T-39
kill "$RELSRV_PID" 2>/dev/null || true

# ----------------------------------------------------------------------
echo
echo "=============================================================="
printf ' HASIL: \033[1;32m%d PASS\033[0m / \033[1;31m%d FAIL\033[0m\n' "$PASS" "$FAIL"
if [[ $FAIL -gt 0 ]]; then
  printf ' Gagal:\n'
  printf '  - %s\n' "${FAILED[@]}"
fi
echo "=============================================================="
[[ $FAIL -eq 0 ]]
