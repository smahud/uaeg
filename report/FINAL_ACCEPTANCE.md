# Final Acceptance — v0.4.1 (production-incident closure)

**Date:** 2026-11-09 · **Mission:** P0 closure of the 2026-11-08 production
incident (`install.sh --repair` from a stale checkout →
`DashboardConfig.__init__() got an unexpected keyword argument 'tls'` →
failed repair, rollback, dashboard 3903 still down) + release-key rotation
3→4 · **Overall verdict:** repository-side work COMPLETE and verified —
root cause reproduced byte-for-byte against the published v0.4.0 assets,
fix proven pre-mutation (exit 5 / CONFIG_INCOMPATIBLE, zero byte changes),
full suite green (290 contract PASS / 0 FAIL + 157 pytest). Production
deployment remains an operator action (no VPS credentials exist in this
environment — reported truthfully; runbook:
`PRODUCTION_RECOVERY_V041.md`).

---

## Definition of Done — item-by-item

| Item | Status | Evidence |
|---|---|---|
| P0-1: runtime/config-mismatch root cause traced with evidence | ✅ | `ROOT_CAUSE_ANALYSIS.md` "Insiden 3" (RC-1 mutate-first ordering, RC-2 stale mixed checkout — `--repair` exists only since v0.4.0 while the source predated v0.3.0, RC-3 no version-order check on the --repair path); reproduced exactly: `evidence/incident_repro_v041.txt` |
| P0-1: minimal fix + regression tests | ✅ | install.sh preflight (guard 1 version order → exit 5; guard 2 staged-schema probe → CONFIG_INCOMPATIBLE) + `app_python()` sanitized interpreter + writer origin proof + stop-service hard-fail; pinned by T-53..T-57 (33 assertions) + `test_unit_regression_v041.py` (7) |
| P0-2: installer transaction safety | ✅ | preflight runs BEFORE the service is stopped and before any file/venv/config byte is touched; hung `systemctl stop` is a hard pre-mutation failure; updater propagates `--allow-downgrade` consent; updater pipeline inherits both guards (it invokes the installer in-place) |
| P0-3: dashboard 3903 restoration | **fix delivered + verified in lab; deployment = operator** (unchanged constraint: no VPS access) | `PRODUCTION_RECOVERY_V041.md`: verified-channel upgrade to v0.4.1, fail-closed public-bind flow (password preserved, LE cert reused, no self-signed), ufw, external acceptance matrix, one-command rollback; installer now positively verifies the dashboard listener post-repair |
| No workaround violations (config `dashboard.tls` kept; no unknown-field loosening; no self-signed production; no password reset; no new orchestration) | ✅ | The fix touches only the installer's ORDER of operations and guards; `Config` strict validation unchanged (T-54 proves the strict rejection still fires); TLS/password flows untouched |
| Separate bug found & fixed | ✅ | v0.4.0-as-published recovery hints pointed at the non-existent `releases/download/latest-mismatch/` URL (4 sites) — fixed to the documented `releases/latest/download/` alias (`RELEASE_LATEST_BASE`, mirror-aware) |
| Release-key rotation 3→4 | ✅ | `release/keys/uaeg-release-4.pub.pem`; keyrings (install.sh, upgrade.sh) trust all four keys; BOOTSTRAP_VERSION 4; manifest v0.4.1 signed with key 4; historical manifests still verify (rotation cross-checks below) |
| Docs synchronized | ✅ | README (v0.4.1 note), BOOTSTRAP.md (rotation 3→4 + hardening note), INSTALL/usage text in installer; T-39 reads the version from pyproject (no more hard-coded pins) |
| Tests all green | ✅ | Contract **290 PASS / 0 FAIL** (T-01..T-57); pytest **125 unit + 32 e2e passed**; incident repro [A] fail → [B] refuse-with-zero-mutation |
| Production status reported truthfully | ✅ | This file + TEST_RESULTS.md §5: production NOT modified from this environment; MCP healthy at v0.4.0 (uptime 22 775 s at 02:41 UTC), 3903 refused; exact recovery steps delivered |

## Test totals

- **Contract suite: 290 PASS / 0 FAIL** (258 pre-existing assertions
  unmodified + 32 new incident assertions T-53..T-57), real OpenSSL
  Ed25519 chain against a local release server, v0.3.5-baseline migration
  included.
- **pytest: 157 passed** (125 unit incl. the 7 new v0.4.1 regressions +
  32 e2e: TLS, MCP smoke, dashboard auth, hardening).
- **Incident reproduction:** published-v0.4.0 failure reproduced
  byte-for-byte; v0.4.1 refusal verified with sha256 tree/config
  identity before/after.

## Release record (v0.4.1)

- Version: `0.4.1` (pyproject, `__init__`, installer pin) — patch bump:
  installer-safety fixes only; no interface change (contract v4 stands).
- Signing: **`uaeg-release-4`** (rotation 3→4; the release-3 private key
  was handed to the operator out-of-band at v0.4.0 and is not available to
  this environment — same documented procedure as the two previous
  rotations). Keyrings trust keys 1–4; historical releases keep verifying
  for pins/downgrades (cross-checks in `evidence/key_rotation_v041.txt`).
- Assets: the same eight documented assets, published by
  `.github/workflows/release.yml` after signature + hash re-verification.
- No manifest-covered file changed after signing (manifest is generated
  last, from the exact tagged bytes).

## Remaining risks & operator actions

1. **Run `PRODUCTION_RECOVERY_V041.md`** (upgrade to v0.4.1 + expose
   3903). Until then the dashboard stays down; MCP is unaffected.
2. Secure the uaeg-release-4 private key out-of-band (same as key 3).
3. The stale clone that triggered the incident should be deleted or
   `git fetch --tags && git checkout v0.4.1`-refreshed; v0.4.1 refuses
   repairs from it regardless (exit 5), but a clean tree avoids confusion.
