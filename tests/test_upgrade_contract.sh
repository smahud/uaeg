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
#
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
assert_in()  { grep -qF "$2" "$1" && ok "$3" || no "$3 ('$2' not found)"; }

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
      "$1" | sed -e "s|__TESTBIN__|$TESTBIN|g"
}
patch_upgradesh() {  # $1 src -> stdout
  sed -e 's|^if \[\[ \$EUID -ne 0 \]\]; then|if false; then|' "$1"
}

# make_source <dir> <version>: copy the repo (no .git) with patched scripts
# and (optionally) a bumped version — simulates a NEW release tree.
make_source() {
  local dir="$1" ver="${2:-}"
  mkdir -p "$dir" "$TESTBIN"
  tar -C "$REPO_DIR" --exclude=.git --exclude=.venv --exclude=__pycache__ \
      --exclude=.pytest_cache --exclude=dist --exclude=build -cf - . \
    | tar -C "$dir" -xf -
  if [[ -n "$ver" ]]; then
    sed -i "s/^version[[:space:]]*=[[:space:]]*\".*\"/version = \"$ver\"/" "$dir/pyproject.toml"
    sed -i "s/^__version__[[:space:]]*=[[:space:]]*\".*\"/__version__ = \"$ver\"/" "$dir/src/uaeg/__init__.py"
  fi
  patch_installsh "$dir/install.sh" > "$dir/install-test.sh"
  patch_upgradesh "$dir/upgrade.sh" > "$dir/upgrade-test.sh"
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
run "$LOG" bash "$SRC/upgrade-test.sh" --check "${UPGRADE_ARGS[@]}"
assert_rc 0 "T-03 --check rc=0"
assert_in "$LOG" "$INSTALL" "T-03 menampilkan install dir"
assert_in "$LOG" "$REPO_VERSION" "T-03 menampilkan versi terpasang"
assert_in "$LOG" "$DATA" "T-03 menampilkan data dir"
assert_in "$LOG" "terdeteksi" "T-03 status terdeteksi"

# ----------------------------------------------------------------------
t "T-04: --dry-run menampilkan rencana tanpa perubahan (U-11)"
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC/upgrade-test.sh" --dry-run "${UPGRADE_ARGS[@]}" --source "$SRC" --installer "$SRC/install-test.sh"
assert_rc 0 "T-04 dry-run rc=0"
assert_in "$LOG" "RENCANA UPGRADE" "T-04 menampilkan rencana"
assert_in "$LOG" "tidak ada perubahan" "T-04 menyatakan tidak ada perubahan"
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
run "$LOG" bash "$SRC/upgrade-test.sh" "${UPGRADE_ARGS[@]}" --source "$SRC" --installer "$SRC/install-test.sh"
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
OP_STATUS_BEFORE="$(git -C "$WORK" status --porcelain)"
patch_installsh "$WORK/install.sh" > "$WORK/install-test.sh"
patch_upgradesh "$WORK/upgrade.sh" > "$WORK/upgrade-test.sh"
run "$LOG" bash "$WORK/upgrade-test.sh" "${UPGRADE_ARGS[@]}" --installer "$WORK/install-test.sh"
assert_rc 0 "T-13 remote upgrade rc=0"
assert_eq "$(installed_version)" "7.7.7" "T-13 versi sekarang 7.7.7"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$OP_HEAD_BEFORE" "T-13 clone operator TIDAK diubah (HEAD identik)"
assert_eq "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" "main" "T-13 clone operator tetap di branch main (bukan detached)"
assert_eq "$(git -C "$WORK" status --porcelain)" "$OP_STATUS_BEFORE" "T-13 status clone operator identik (tidak ada perubahan baru)"
assert_in "$LOG" "staging terisolasi" "T-13 sumber dari staging terisolasi"
assert_in "$LOG" "TIDAK disentuh" "T-13 script menyatakan clone operator tidak disentuh"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-13 kunci agent identik"
assert_eq "$(latest_record to_version)" "7.7.7" "T-13 RECORD to_version=7.7.7"
assert_eq "$(latest_record result)" "success" "T-13 RECORD result=success"
assert_eq "$(latest_record contract_version)" "2" "T-13 RECORD contract_version=2"

# ----------------------------------------------------------------------
t "T-14: git tree kotor -> TETAP aman; tree dipertahankan (U-12 kontrak v2)"
# ----------------------------------------------------------------------
# v0.3.1 refused this with exit 4. Contract v2: the operator tree is never
# read or mutated, so dirty files must not block the upgrade.
echo "local edit by operator" >> "$WORK/README.md"
run "$LOG" bash "$WORK/upgrade-test.sh" "${UPGRADE_ARGS[@]}" --installer "$WORK/install-test.sh"
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
run "$LOG" bash "$SRC_NEXT/upgrade-test.sh" "${UPGRADE_ARGS[@]}" \
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
run "$LOG" bash "$SRC_NEXT/upgrade-test.sh" "${UPGRADE_ARGS[@]}" \
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
run "$LOG" bash "$SRC_BROKEN/upgrade-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC_BROKEN" --installer "$SRC_BROKEN/install-test.sh"
assert_rc 2 "T-08 rc=2 (gagal -> sudah di-rollback)"
assert_eq "$(installed_version)" "9.9.9" "T-08 aplikasi kembali ke 9.9.9 (berfungsi)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-08 kunci agent tetap utuh"
assert_eq "$(db_check)" "ok" "T-08 integritas DB ok"
assert_eq "$(cfg_field "cfg['dashboard']['port']")" "3954" "T-08 config tetap 3954"
assert_eq "$(latest_record result)" "rolled-back" "T-08 RECORD result=rolled-back"
assert_in "$LOG" "rollback terverifikasi" "T-08 rollback diverifikasi ulang (app lama hidup)"
LATEST="$(ls -1d "$BACKUPS"/upgrade-* | sort | tail -1)"
assert_file "$LATEST/app" "T-08 backup app/ dipertahankan untuk pemulihan manual"

# ----------------------------------------------------------------------
t "T-09: rollback manual memulihkan data pra-upgrade (U-6)"
# ----------------------------------------------------------------------
env_uaeg generate --name "agent-charlie" > /dev/null 2>&1
assert_eq "$(db_keys | awk -F';' '{print NF}')" "3" "T-09 kunci ketiga dibuat (3 kunci)"
run "$LOG" bash "$SRC_NEXT/upgrade-test.sh" --rollback --yes "${UPGRADE_ARGS[@]}"
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
run "$LOG" bash "$SRC/upgrade-test.sh" --check \
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
run "$LOG" bash "$SRC_NEXT/upgrade-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC_NEXT" --installer "$SRC_NEXT/install-test.sh" --keep-backups 1
assert_rc 0 "T-12 upgrade dengan keep=1 rc=0"
assert_eq "$(backup_count)" "1" "T-12 hanya 1 backup tersisa"

# ----------------------------------------------------------------------
t "T-16: downgrade diam-diam -> ditolak exit 5 (U-12)"
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC/upgrade-test.sh" "${UPGRADE_ARGS[@]}" \
  --source "$SRC" --installer "$SRC/install-test.sh"
assert_rc 5 "T-16 rc=5 (downgrade ditolak)"
assert_in "$LOG" "LEBIH TUA" "T-16 pesan downgrade"
assert_eq "$(installed_version)" "9.9.9" "T-16 versi tidak berubah"
assert_eq "$(backup_count)" "$N_BEFORE" "T-16 tidak ada backup baru"

# ----------------------------------------------------------------------
t "T-15: skrip standalone (tanpa repo) — dry-run menemukan target (U-2, U-11)"
# ----------------------------------------------------------------------
STANDALONE="$BASE/standalone"
mkdir -p "$STANDALONE"
patch_upgradesh "$REPO_DIR/upgrade.sh" > "$STANDALONE/upgrade-test.sh"
N_BEFORE="$(backup_count)"
run "$LOG" bash "$STANDALONE/upgrade-test.sh" --dry-run "${UPGRADE_ARGS[@]}" --repo "$REMOTE"
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
patch_upgradesh "$REPO_DIR/upgrade.sh" > "$INC/uaeg/upgrade.sh"
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
assert_in "$LOG" "TIDAK disentuh" "T-17 script menyatakan clone operator tidak disentuh"

# ----------------------------------------------------------------------
t "T-18: tag tidak ada -> pesan akurat, bukan misreporting (U-2, U-10)"
# ----------------------------------------------------------------------
# The v0.3.1 incident also misclassified failures. A tag that does not
# exist must be reported as a missing tag - never as a fetch/clone/network
# failure, and never with the old misleading "tidak ditemukan setelah fetch".
# ----------------------------------------------------------------------
N_BEFORE="$(backup_count)"
run "$LOG" bash "$SRC_NEXT/upgrade-test.sh" "${UPGRADE_ARGS[@]}" \
  --repo "$INCREMOTE" --installer "$INCWORK/install-test.sh" --version 0.0.42
assert_rc 1 "T-18 rc=1 (tag tidak ada -> ditolak)"
assert_in "$LOG" "tidak ditemukan di remote" "T-18 pesan menyebut tag tidak ada di remote"
if grep -q "tidak ditemukan setelah fetch" "$LOG" 2>/dev/null; then
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
patch_upgradesh "$REPO_DIR/upgrade.sh" > "$STANDALONE/upgrade.sh"
run "$LOG" bash "$STANDALONE/upgrade.sh" "${UPGRADE_ARGS[@]}" \
  --repo "$INCREMOTE" --installer "$INCWORK/install-test.sh"
assert_rc 0 "T-19 standalone upgrade rc=0"
assert_eq "$(installed_version)" "10.0.0" "T-19 versi 10.0.0 (re-install dari standalone)"
assert_eq "$(db_keys)" "$KEYS_BEFORE" "T-19 kunci agent identik"
assert_in "$LOG" "staging terisolasi" "T-19 sumber dari staging terisolasi"

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
