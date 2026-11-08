# Final Acceptance — v0.3.3

**Date:** 2026-11-08 · **Mission:** UAEG production recovery, end-to-end
audit & permanent upgrade bootstrap · **Overall verdict:** repository-side
work COMPLETE and verified; production deployment PENDING operator execution
of the runbook (no VPS access from the audit environment — explicitly
reported, not silently claimed).

---

## Definition of Done — item-by-item

### Dashboard

| Item | Status | Evidence |
|---|---|---|
| Root cause established and documented | ✅ | DASHBOARD_ROOT_CAUSE.md (loopback-bound dashboard + firewall; MCP healthy live; UI ruled out by lab browser test) |
| Public HTTPS endpoint reachable from external network | ⏳ OPERATOR | Runbook §3 (PRODUCTION_DEPLOYMENT.md); blocked only by VPS access |
| Login page renders correctly | ✅ (lab, published code) | evidence/lab_browser_verification_v033.txt |
| Authentication succeeds with existing credentials | ✅ (lab; password preserved through upgrades by contract U-4) | browser evidence + contract T-05/T-21 |
| Dashboard renders after login | ✅ (lab) | screenshots (non-blank verified) |
| Implemented menus/actions function | ✅ (lab) | all 5 tabs + key creation with one-time secret modal |
| Browser console has no release-blocking errors | ✅ (lab) | 0 console errors (favicon 404 fixed) |
| Dashboard API endpoints enforce authentication | ✅ | e2e test suite (23 auth tests) + live production 401 pattern on MCP |
| Logout and session expiration work | ✅ | browser evidence (logout → /login) + e2e expiry tests |

### Backend

| Item | Status | Evidence |
|---|---|---|
| MCP endpoint remains operational | ✅ (live production v0.3.2; v0.3.3 in release) | evidence/production_mcp_probe_v033.txt |
| All registered MCP tools pass integration tests | ✅ | 13/13 registry verified + e2e suites (133 pytest PASS) |
| Existing agent keys remain valid | ✅ (upgrade preserves keys by construction; verified in every contract upgrade test) | T-05/T-13/T-19/T-21/T-23/T-30 |
| PTY and background jobs function correctly | ✅ (improved: B-1/B-3/B-4/B-5/B-8 fixed + regression tests) | test_unit_regression_v033.py |
| Database integrity verified | ✅ | quick_check in every U-4 verification + doctor |
| No unresolved release-blocking security defects remain | ✅ | SECURITY_REVIEW.md (none blocking; accepted risks listed) |

### Upgrade

| Item | Status | Evidence |
|---|---|---|
| Existing installations can migrate to the permanent bootstrap | ✅ | one-command transition documented + T-21; operator clone never touched (T-17/T-23) |
| One local upgrade.sh reusable indefinitely | ✅ | **multi-release test T-23**: same file, SHA-256 unchanged, across releases A→B (CHANGED updater implementation)→retry D |
| Bootstrap automatically fetches the selected versioned updater | ✅ | T-21/T-23 (release-channel resolution + verified download) |
| Artifact authenticity and integrity verified | ✅ | T-24..T-28 (tamper/foreign-key/interface/missing rejections) + live asset verification |
| Multi-release compatibility tests pass | ✅ | UPGRADE_COMPATIBILITY_TESTS.md (155/155) |
| Local bootstrap not overwritten during normal upgrades | ✅ | SHA assertions in T-22/T-23/T-30 + post-run self-check in the bootstrap |
| Backup and rollback work | ✅ | T-08/T-09/T-30 (auto + manual) + T-31 (offline) |
| Configuration, credentials, persistent data survive | ✅ | U-4 zero-data-loss proof on every upgrade test |
| Upgrade failures safely recoverable | ✅ | T-30 (failed release → rollback → retry with same bootstrap succeeds) |

### Release and production

| Item | Status | Evidence |
|---|---|---|
| All release-blocking tests pass | ✅ | 133 pytest + 155 contract + browser verification |
| Release assets independently verified | ✅ | evidence/release_assets_v033.txt (public URLs, checksums, Ed25519, manifest consistency) |
| GitHub release published | ✅ | v0.3.3 @ 2026-11-08T03:21:50Z, 8 assets, workflow success |
| Production deployment verified or explicitly marked pending | ⏳ **PENDING (operator)** | PRODUCTION_DEPLOYMENT.md — exact runbook; blocked by absent VPS credentials |
| All reports committed under report/ | ✅ | this file + 9 companions + evidence/ |
| Final acceptance contains evidence and remaining risks | ✅ | below |

## Key numbers

- **Repository:** HEAD `fff55c3` (main), tag `v0.3.3`, release published
  with 8 verified assets.
- **Tests:** 133/133 pytest (20 new regressions) · 155/155 upgrade-contract
  assertions (95 new bootstrap assertions) · real-browser matrix 12/12 ·
  release asset chain 8/8.
- **Audit findings:** 23 (1×P1, 4×P2, 12×P3, 6×P4) — **all fixed** in
  v0.3.3 except the production-configuration gap (operator runbook) and
  documented accepted risks.
- **Production (live measurements):** UAEG v0.3.2, MCP healthy (401 auth
  enforced, valid Let's Encrypt cert), dashboard unreachable publicly.

## Remaining risks & operator actions

1. **Execute the production runbook** (PRODUCTION_DEPLOYMENT.md §2–§3):
   one-time bootstrap download + `sudo bash upgrade.sh` (→ v0.3.3), then
   enable the public dashboard (password → bind/allow_public/tls → restart
   → firewall). ~5 minutes, with automatic rollback protection during the
   upgrade.
2. Until upgraded, the production v0.3.2 host carries the B-1 freeze defect
   (one Ctrl-Z + input away from a full-service freeze) — upgrade promptly.
3. **Safeguard the release-signing private key** (`uaeg-release-1`): it was
   generated in the audit environment and must be moved to secure
   operator-only storage; it is required for signing future releases
   (`release/make_release.py --key ...`). If it cannot be secured, rotate
   per docs/BOOTSTRAP.md (dual-sign window → new key) — an exceptional,
   documented procedure.
4. Background-job output files remain uncapped (documented,
   LIMITATIONS.md #13) — monitor long-running verbose jobs.
5. CI run for `fff55c3` (main): **completed, conclusion SUCCESS — all jobs
   green** (Python 3.11/3.12/3.13 matrix, safe-upgrade contract suite,
   installer smoke; the release-verification job on the tag also passed).

## Explicit non-claims (evidence policy)

- No claim that production was modified: it was not (no VPS access).
- No claim that the production dashboard renders: its public reachability
  awaits the operator runbook; the CODE's dashboard behavior is fully
  verified in a real browser.
- No claim of tests not actually run: every number above maps to a log file
  under `report/evidence/` or a workflow run.
