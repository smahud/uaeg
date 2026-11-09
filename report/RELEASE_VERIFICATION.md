# Release Verification — v0.4.1

**Date:** 2026-11-09 · Tag `v0.4.1` (commit `fa08174`) · published by the
Release workflow (run 42552993400, **success**) after CI on the same commit
was fully green: Test matrix 3.11/3.12/3.13 + Install script smoke +
Safe-upgrade contract suite — all five jobs `success` (run 42552989187).

**Independent post-publication verification — strictly from the public
URLs** (`report/evidence/published_asset_e2e_v041.txt`):

1. `releases/latest` → `v0.4.1`; all **8 documented assets** present
   (install.sh + sha256, upgrade.sh + sha256, uaeg-updater-0.4.1.sh +
   sha256, uaeg-manifest.json + sig).
2. Every sha256 sidecar verifies against the downloaded bytes.
3. Manifest Ed25519 signature: **fails with the old key 3** (documented
   rotation refusal) and **verifies with `uaeg-release-4`**; manifest
   version 0.4.1; updater interface 1; asset hashes match.
4. `releases/latest/download/install.sh` (the URL used by the recovery
   runbook and by TROUBLESHOOTING §13) is **byte-identical to the v0.4.1
   asset** and pins `UAEG_RELEASE_VERSION="0.4.1"`.
5. Fresh install with the PUBLISHED installer (isolated dirs, no root):
   rc 0, v0.4.1 deployed; `uaeg version` → 0.4.1; served from the
   installed location: `/healthz` OK (v0.4.1), MCP `initialize` with a
   bearer key returns `serverInfo UAEG 0.4.1`, dashboard `/login` 200 on
   its effective (loopback) bind.
6. Production (external, unmodified): MCP `:3902` healthy at v0.4.0
   (401 without key), dashboard `:3903` refused — the operator runbook
   `PRODUCTION_RECOVERY_V041.md` is the remaining action (its step-1 URLs
   are verified above).

**Incident-closure evidence chain (all committed at the tag):**

- Root cause: `report/ROOT_CAUSE_ANALYSIS.md` — Insiden 3 (mutate-first
  ordering + stale mixed checkout + no version-order check on `--repair`).
- Reproduction with the PUBLISHED v0.4.0 assets: byte-exact production
  error + rollback — `report/evidence/incident_repro_v041.txt`.
- Fix proof: same stale checkout, v0.4.1 installer → **exit 5 before any
  mutation**, installed tree + configuration byte-identical.
- Regression pinning: T-53..T-57 (contract, 33 assertions) +
  `tests/test_unit_regression_v041.py` (7) — in the green CI run above.
- Suite totals at the tag: contract **290 PASS / 0 FAIL**; pytest
  **125 unit + 32 e2e passed**.

**Signing:** `uaeg-release-4` (rotation 3→4 per docs/BOOTSTRAP.md — the
key-3 private key left the publisher's control at v0.4.0). Keyrings in the
published install.sh/upgrade.sh trust all four keys; historical manifests
v0.3.3/v0.3.5/v0.4.0 still verify with their own keys
(`report/evidence/key_rotation_v041.txt`). The uaeg-release-4 private key
must be moved to secure operator-only storage (handed over out-of-band,
never committed).
