# UAEG — Release Decision

| | |
|---|---|
| **Decision** | **GO** (repository-side complete; production deployment is the delivered operator runbook) |
| **Release** | UAEG **v0.4.1** (production-incident closure: pre-mutation installer safety + key rotation 3→4) |
| **Audited release commit** | this tag `v0.4.1` (branch `main`) |
| **Decision record** | this file + [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md) (v0.4.1) |
| **Baseline audited** | `main` @ `1ab7a3e` / tag `v0.4.0` (post-publication-verified) + the live production state of host.zerotier.my.id (external probes) |
| **Target environment** | Dedicated, single-operator Debian VPS (MicroTix); trusted agents; native root execution (per PRD v1.0) |
| **Decided** | 2026-11-09 (UTC) |

## 1. Why GO

**Trigger (P0):** confirmed production incident 2026-11-08 —
`bash install.sh --repair` run from a stale mixed checkout (v0.4.0-era
installer + pre-v0.3.0 source) replaced the newer installed application
BEFORE validating the existing configuration, failed with
`DashboardConfig.__init__() got an unexpected keyword argument 'tls'`,
rolled back, and left the dashboard (P0-3) unrestored.

**Engineering gates — all passed:**

1. **Root cause established with a byte-exact reproduction** against the
   published v0.4.0 assets (not a mock): the exact production error, the
   mutate-first ordering, and the rollback — `evidence/incident_repro_v041.txt`.
2. **Fix proven at the same fidelity:** the v0.4.1 installer, placed in the
   identical stale checkout, refuses with exit 5 BEFORE any mutation;
   installed tree and configuration verified byte-identical (sha256) —
   plus 5 dedicated contract regressions (T-53..T-57) and 7 unit
   regressions pinning every layer of the defense.
3. **Full suite green:** contract **290 PASS / 0 FAIL** (T-01..T-57, real
   Ed25519 chain, local release server, v0.3.5-baseline migration);
   pytest **157 passed** (unit + e2e).
4. **No workaround violations:** strict config validation untouched (T-54
   proves the strict rejection still fires — the STALE SOURCE was the
   defect, never the operator's config); no self-signed production path;
   no password reset; existing safe-upgrade mechanism reused, not replaced.
5. **Separate defect fixed:** v0.4.0-as-published recovery hints pointed at
   a non-existent download URL (`latest-mismatch`) — corrected to the
   documented `releases/latest/download` alias.
6. **Release integrity:** manifest signed with the new `uaeg-release-4`
   (rotation 3→4 per the documented policy — the key-3 private key left
   this environment's control at v0.4.0); keyrings trust all four keys so
   historical pins/downgrades keep verifying; CI re-verifies signature +
   hashes before publishing.

**The environmental limit (unchanged, reported truthfully):** no VPS
credentials exist in this environment; production was NOT modified from
here. The dashboard restoration (P0-3) is a verified operator runbook
(`PRODUCTION_RECOVERY_V041.md`) — external probes confirm MCP healthy at
v0.4.0 and 3903 refused, so the runbook's preconditions hold exactly as
written.

## 2. Remaining operator actions (not engineering blockers)

| # | Action | Where |
|---|---|---|
| O-1 | Run the recovery runbook (upgrade to v0.4.1 + expose dashboard 3903 fail-closed) | `PRODUCTION_RECOVERY_V041.md` |
| O-2 | Secure the uaeg-release-4 private key out-of-band | key handover (same as key 3) |
| O-3 | Refresh or delete the stale clone that triggered the incident | §"Remaining risks" in FINAL_ACCEPTANCE.md |

## 3. Rollback / install-safety statement

- Safe upgrades and repairs keep the automatic backup → deploy → verify →
  rollback pipeline; `install.sh --rollback` restores the latest backup.
- NEW in v0.4.1: repairs from older or schema-incompatible sources are
  refused pre-mutation (exit 5 / `CONFIG_INCOMPATIBLE`); deliberate
  downgrades go through `upgrade.sh --version X.Y.Z --allow-downgrade`
  (and still require schema compatibility).
- A bootstrap/installer downloaded during v0.3.4–v0.4.0 will refuse v0.4.1+
  manifests with the documented key-rotation message; the one-time refresh
  is part of the recovery runbook (step 1).

## 4. Sign-off

- Audit method: reproduce-first (published assets), fix, regression-pin,
  full-suite, honest reporting of the production boundary.
- Decision: **GO** for v0.4.1 — the incident class is closed at the
  installer level; production restoration is one operator runbook away.
