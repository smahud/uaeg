# Final Acceptance — v0.4.0 (unified installer + focused completion audit)

**Date:** 2026-11-08 · **Mission:** unified installer as the sole public
entrypoint, production dashboard resolution, compatibility for v0.3.2–v0.3.5,
verified release · **Overall verdict:** repository-side work COMPLETE and
verified; **production dashboard fix BLOCKED on VPS access** (operator
runbook delivered; no VPS credentials exist in this environment — reported
truthfully).

---

## Definition of Done — item-by-item (mission §9)

| Item | Status | Evidence |
|---|---|---|
| Dashboard production issue resolved and verified | **Repo-side: root cause re-confirmed with fresh evidence; fix = BLOCKED (operator)** | `evidence/production_probe_v040.txt`: 3903 refused, 3902 healthy at v0.3.3, LE cert valid. Runbook: DASHBOARD_PRODUCTION_CHECK.md §4 (one `install.sh` file, no password reset). Lab dashboard behavior fully verified in a real browser (10/10) |
| `install.sh` is the only required public installation/upgrade file | ✅ | UNIFIED_INSTALLER.md; embedded trusted keyring + verification chain; `upgrade.sh` kept only as a compatibility asset |
| No-argument execution selects install or upgrade correctly | ✅ | T-01 (fresh) / T-40 (bare → verified upgrade); dispatch rules in UPGRADE_CONTRACT.md U-16 |
| Service-management flags work | ✅ | `--start/--stop/--restart/--status` (T-50 clean refusals without systemd; CI smoke with systemctl present; `--status` health probe) |
| Existing credentials and data remain intact | ✅ | U-4 machine proofs in every upgrade test; T-51 (`--repair`); T-52 (v0.3.5 baseline, keys preserved); password never reset anywhere |
| Multi-release bootstrap reuse passes | ✅ | Same operator file across releases E→F(changed updater)→G(pin)→v24 with SHA-256 unchanged (T-40..T-42, T-52) |
| Release verification passes | ✅ | `release/verify_release.py`: signature (uaeg-release-3) + asset hashes + interface consistency all OK; rotation cross-checks in `evidence/key_rotation_v040.txt` |
| README and docs are synchronized | ✅ | README, INSTALL, BOOTSTRAP, UPGRADE_CONTRACT (contract v4 / U-16) all describe the unified flow; obsolete `upgrade.sh`-download instructions replaced (old runbook marked superseded) |
| Changes committed and pushed | ✅ | `main` + tag `v0.4.0` pushed; release published by `release.yml` (verified from public URLs — see §Release) |
| Production status is reported truthfully | ✅ | This file: production NOT modified, dashboard NOT claimed fixed; exact diagnostics listed |

## Test totals

- **Contract suite: 258 PASS / 0 FAIL** (191 pre-existing assertions
  unmodified + 67 new unified-installer assertions T-39..T-52), real
  OpenSSL Ed25519 chain against a local release server.
- **pytest: 150 passed** (unit + e2e incl. dashboard auth, TLS, MCP smoke,
  hardening, regression suites).
- **Real-browser lab matrix: 10/10** (login page, wrong-password refusal,
  login, 5 tabs, logout, 0 console errors; 7 non-blank screenshots).
- **Lab MCP smoke:** healthz ok (v0.4.0), MCP initialize with bearer key,
  401 without key.

## Release record (v0.4.0)

- Version: `0.4.0` (pyproject, `__init__`, installer pin) — minor bump: new
  backwards-compatible operator interface (contract v4 / U-16).
- Signing: **`uaeg-release-3`** (rotation 2→3; the release-2 private key is
  no longer available to the publisher — same situation and procedure as the
  v0.3.4 rotation). Keyring now trusts keys 1+2+3 in both `install.sh` and
  `upgrade.sh`, so historical releases keep verifying for pins/downgrades
  (proven in `evidence/key_rotation_v040.txt`).
- Assets published by `.github/workflows/release.yml` after re-verification:
  `install.sh`(+sha256), `upgrade.sh`(+sha256),
  `uaeg-updater-0.4.0.sh`(+sha256), `uaeg-manifest.json`(+sig) — eight
  assets, same set as previous releases.
- The **uaeg-release-3 private key is handed to the operator out-of-band**
  (never committed); it must be moved to secure operator-only storage. If it
  cannot be secured, the documented rotation procedure applies again.

## Remaining risks & operator actions

1. **Execute the production runbook** (DASHBOARD_PRODUCTION_CHECK.md §4):
   one-time verified download of `install.sh`, `sudo bash install.sh`
   (v0.3.3 → v0.4.0 safe upgrade with automatic rollback protection), then
   the public-dashboard steps (password only if not yet set — never reset;
   bind/allow_public/tls; restart; `ufw allow 3903`). ~5 minutes. The audit
   environment will re-verify ports 3903/3902 externally on request.
2. **Secure the `uaeg-release-3` private key** (delivered with this report).
   Losing it forces another documented rotation at the next release.
3. **Key-rotation friction is real but bounded:** bootstraps downloaded
   during v0.3.4/v0.3.5 will refuse v0.4.0+ manifests with the explicit
   rotation message and need the one-time `install.sh` download (which the
   v0.4.0 migration requires anyway). Production (v0.3.3-era bootstrap)
   already required this refresh since v0.3.4. No data is affected.
4. Unauthenticated GitHub API rate limits can transiently block
   "latest release" resolution (unchanged from v0.3.3–v0.3.5);
   `--version X.Y.Z` pins work around it.
5. `install.sh --status` relies on the systemd unit; `--no-service`
   installations get a clear refusal message with the manual command
   (documented).

## Explicit non-claims (evidence policy)

- No claim that production was modified: it was not (no VPS access).
- No claim that the production dashboard renders: it is unreachable
  (TCP refused) until the operator runbook is executed.
- No claim that the production upgrade to v0.4.0 happened: production still
  reports v0.3.3 (measured 2026-11-08T16:00Z).
- Every number above maps to a log/evidence file under `report/evidence/`
  or to a suite run in this audit (TEST_RESULTS.md).
