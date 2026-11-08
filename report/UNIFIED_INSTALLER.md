# Unified Installer — v0.4.0 design, implementation and evidence

**Date:** 2026-11-08 · **Mission item:** P0-2 "Unify installation and
upgrade" · **Status:** implemented and verified (258/258 contract assertions
incl. 14 new unified tests; 150/150 pytest).

---

## 1. Requirement → behavior map

| Required command | v0.4.0 behavior |
|---|---|
| `bash install.sh` | No installation → fresh install (all v0.3.x flags preserved). Existing installation + bare invocation (no flags, or only `--yes` / `--version X.Y.Z`) → **verified safe upgrade** to the latest stable release via the embedded bootstrap → signed-manifest-verified updater (U-13/U-14 chain unchanged) |
| `bash install.sh --restart` / `--start` / `--stop` | systemctl restart/start/stop of the `uaeg` unit; clean refusal (rc 1) with guidance when systemd or the unit is absent |
| `bash install.sh --status` | `systemctl status` + a live `/healthz` probe; falls back to an informative error on `--no-service` installs |
| `bash install.sh --check` | Installation status, version, keys, DB, backups + available target through the verified release channel (exit 3 if not installed) |
| `bash install.sh --dry-run` | Existing install → the updater's full upgrade plan; fresh host → the install plan (paths/ports/TLS/service). Zero changes either way |
| `bash install.sh --rollback` (optional ID) | Restores the latest upgrade backup (or `--backup ID`), preferring the cached verified updater → works offline after the first successful upgrade |
| `bash install.sh --repair` | Re-runs the installer in place: redeploys the app, rebuilds the venv, rewrites the unit; **persistent data (keys, DB, dashboard password, TLS material) and existing config preserved**; accepts first-install flags to re-apply configuration |
| `bash install.sh --help` | Full command + flag reference |
| `--version` ambiguity | **Resolved:** bare `--version` prints `UAEG unified installer v0.4.0 (updater interface v1)`; `--version X.Y.Z` pins the target release (fresh-install tag OR verified-upgrade target) |

All v0.3.x first-install flags keep their exact semantics (`--port`,
`--dashboard-port`, `--dashboard-bind`, `--domain`, `--public-ip`,
`--cert`/`--key`, `--self-signed`, `--data-dir`, `--config-dir`,
`--install-dir`, `--no-service`, `--open-firewall`, `--yes`, `UAEG_VERSION`).

## 2. Architecture (what changed, what did not)

```
                    ┌────────────────────────────────────────────┐
 sudo bash          │ install.sh  (unified, operator-owned)      │
 install.sh ───────►│                                            │
                    │ fresh host? ──► classic installer flow     │  (unchanged
                    │ bare + installed? ──┐                      │   from v0.3.x)
                    │ any config flag or │                      │
                    │ UAEG_BOOTSTRAP=1 ──┼─► in-place flow       │
                    │                     │                      │
                    │ --check/--dry-run/  ▼                      │
                    │ --rollback/default-safe-upgrade:           │
                    │   1. resolve target (GitHub release channel│
                    │      or --version pin)                     │
                    │   2. download manifest + Ed25519 signature │
                    │   3. VERIFY against the EMBEDDED keyring   │
                    │   4. download updater; VERIFY sha256+size  │
                    │   5. exec verified updater (interface v1)  │
                    └───────────────────┬────────────────────────┘
                                        ▼
              updater.sh (UNCHANGED engine): detect → backup →
              isolated staging → install.sh in-place → zero-data-loss
              verify → auto-rollback → cache updater → refresh
              /usr/local/sbin/uaeg-upgrade
```

- The embedded bootstrap is a faithful mirror of `upgrade.sh`'s verification
  chain (same keyring markers for the test suite, same manifest structural
  validation, same interface check, same self-SHA post-condition). The
  binding bootstrap↔updater interface stays **v1**; the safe-upgrade engine
  (`updater.sh`) is **not rewritten** — it is reused as-is.
- `upgrade.sh` remains published byte-compatible (its only diff: the
  keyring gains `uaeg-release-3` and BOOTSTRAP_VERSION bumps 2→3 — the
  change its own policy file prescribes for a rotation). Existing operators
  keep working; public documentation now points at `install.sh`.

### Loop prevention (critical design constraint)

The updater applies upgrades by invoking `<staged>/install.sh --yes
--install-dir ...`. Two independent guards keep the unified dispatcher out
of that path:

1. `UAEG_BOOTSTRAP=1` in the environment (set by both `upgrade.sh` and the
   embedded flow) forces the in-place installer flow.
2. Any targeting flag (`--install-dir`, `--data-dir`, `--config-dir`,
   `--no-service`) or configuration flag implies the in-place flow — the
   safe-upgrade path is only selected by the bare invocation.

Verified from both directions: standalone-updater tests (T-05/T-13/T-14,
no bootstrap env) and bootstrap-invoked tests (T-40..T-52) all pass with no
re-entrancy.

### Why the keyring lives in install.sh

"Operator downloads install.sh once" must hold even when no sibling
`upgrade.sh` exists and no system bootstrap was ever installed (e.g. a
v0.3.2-era box). Embedding the trusted keyring makes the single file
self-sufficient for every future upgrade; a future trust-key rotation or
interface break then requires the documented one-time re-download — the
same exceptional procedure as before, now centered on `install.sh`
(BOOTSTRAP.md §Key rotation, and the failure messages themselves print the
refresh commands).

## 3. Files changed

| File | Change |
|---|---|
| `install.sh` | unified rewrite (1143 lines): dispatch layer + embedded bootstrap + unchanged install flow; `UAEG_RELEASE_VERSION=0.4.0`; CI/test sed patterns and `confirm_ask` preserved byte-exact |
| `upgrade.sh` | keyring-only rotation (adds `_TRUSTED_KEY_3`, BOOTSTRAP_VERSION 3, key-id list); no behavioral change |
| `updater.sh` | two additive env fallbacks (`UAEG_REPO_URL`, `UAEG_BACKUPS_DIR`; flag > env > default) + usage note — required by the suite, useful for mirrors |
| `release/keys/uaeg-release-3.pub.pem` | new public key (private key delivered to the operator, never committed) |
| `tests/test_upgrade_contract.sh` | +`patch_unifiedsh`/`make_release_repo_uni` helpers; +T-39..T-52; patch helpers now redirect the updater's auto-detection defaults so bare-invocation upgrades resolve the test installation |
| `.github/workflows/ci.yml` | installer job gains a unified-command smoke step (bare `--version`, `--start` refusal, `--dry-run`) |
| `pyproject.toml`, `src/uaeg/__init__.py` | version 0.4.0 |
| docs | README, INSTALL, BOOTSTRAP, UPGRADE_CONTRACT (contract v4, clause U-16) |

## 4. Verification summary (details in TEST_RESULTS.md)

- **Fresh install** from the unified file: T-01 (suite) + sandbox lab install.
- **Bare-run selection**: install-absent vs install-present dispatch — T-01 vs
  T-40; plus the standalone smoke of `--check`/`--dry-run`/`--version`.
- **Safe upgrade, signature-verified**: T-40 (RECORD.json proves
  `bootstrap_file` = the operator's install.sh and `bootstrap_invoked=true`).
- **Multi-release reuse of the unchanged file**: T-40 → T-41 (updater
  implementation CHANGED between releases) → T-42 (pin) → T-52 (different
  baseline) with SHA-256 of the operator file identical throughout.
- **Rejections**: tampered manifest (T-43), foreign key (T-44), tampered
  updater (T-45), too-new interface with install.sh refresh instructions
  (T-46) — exit 1, nothing changed, no backups created.
- **Rollback**: automatic on a broken release via bare install.sh (T-48,
  exit 2, data intact) and manual `--rollback` from the cached updater
  (T-49).
- **Service commands**: T-50 (clean refusals without systemd) + CI smoke
  step (runner has systemctl but no unit).
- **Data/credentials preservation**: keys identical and config preserved in
  every upgrade test; `--repair` preserves data while applying flags (T-51);
  **v0.3.5-baseline upgrade with keys preserved (T-52)**.
- **Regression safety**: all 191 pre-existing assertions pass unmodified.

## 5. Operator experience after v0.4.0

```bash
# once, ever:
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh.sha256
sha256sum -c install.sh.sha256

sudo bash install.sh --domain gw.example.com   # fresh install
sudo bash install.sh                           # every future upgrade
sudo bash install.sh --check                   # status + available update
sudo bash install.sh --rollback                # recover from an upgrade
sudo bash install.sh --repair                  # repair without data loss
sudo bash install.sh --status                  # service status
```

Exceptional, documented one-time refresh (BOOTSTRAP.md): a release-signing
key rotation (happens at v0.4.0: key 2 → key 3) or a future updater
interface break — the failure messages print the exact re-download commands.
