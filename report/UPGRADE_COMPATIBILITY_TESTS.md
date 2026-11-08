# Upgrade Compatibility Tests — v0.3.3 (contract v3)

**Suite:** `tests/test_upgrade_contract.sh` · **Result: 155 PASS / 0 FAIL**
(evidence: `report/evidence/upgrade_contract_suite_v033.txt`) · Runs in CI on
every push/PR (isolated dirs, no root/systemd; test-only patches identical in
spirit to the installer-smoke job).

The v0.3.2 clauses U-1..U-12 remain enforced (T-01..T-19, now exercised
against the versioned updater); v0.3.3 adds T-20..T-31 for the permanent
bootstrap. The signature/verification chain in every test is REAL OpenSSL
Ed25519 — only the release channel (a local HTTP server) and the keyring (a
test key substituted between marked lines, same technique CI already uses to
patch install.sh's root check) are simulated.

---

## Part 1 — the mandatory multi-release compatibility test (T-20..T-23, T-30)

The mission defines this as the most important acceptance test: the SAME
unchanged local bootstrap must upgrade across multiple releases, including
releases whose updater implementation CHANGED.

Sequence executed (fresh UAEG baseline installed by T-01):

1. **T-20** One operator-owned bootstrap created in an operator directory;
   its **SHA-256 recorded**. `--bootstrap-info` prints the interface and the
   test key's fingerprint (keyring substitution verified effective).
2. **Release A** (v11.0.0) is published to the simulated release channel:
   signed manifest + updater asset + git tag. `bash upgrade.sh` (the SAME
   file) → **T-21 PASS**: upgrade succeeds, manifest signature and updater
   integrity visibly verified, keys/config byte-identical, `RECORD.json`
   records `bootstrap_invoked=true`, `contract_version=3`.
3. **T-22 PASS**: the operator bootstrap file's **SHA-256 is unchanged**;
   the updater cached itself + manifest (offline capability); the system
   bootstrap copy exists at `/usr/local/sbin/uaeg-upgrade`; the updater
   explicitly reports the operator file "was NOT modified".
4. **Release B** (v12.0.0) ships a **genuinely changed updater
   implementation** (different code, visible marker). The SAME bootstrap —
   run from inside a dirty operator clone that itself carries an untracked
   `upgrade.sh` (the v0.3.1 incident shape) — upgrades to v12.0.0.
   **T-23 PASS**: the changed implementation demonstrably executed
   (`UPDATER-IMPL-B` marker in the log); **bootstrap SHA still identical**;
   keys identical; operator clone HEAD/status byte-identical (untracked file
   untouched).
5. **T-30**: a broken release C (signed correctly, app fails verification)
   → auto-rollback, exit 2, keys intact; then a good release D → **retry
   with the SAME bootstrap succeeds** (v14.0.0) and the **bootstrap SHA is
   still identical after failure + retry**.

**Conclusion:** the bootstrap is proven independent of the application
release cycle and of the updater implementation — one download, reused
indefinitely, exactly per the acceptance requirement.

## Part 2 — authenticity & integrity rejection matrix

| Test | Attack simulated | Required outcome | Result |
|---|---|---|---|
| T-24 | Manifest tampered (version byte flipped after signing) | Ed25519 verify fails → refuse, exit 1, nothing executed, no backup created | PASS |
| T-25 | Updater asset tampered (bytes appended after manifest signing) | SHA-256 mismatch vs signed manifest → refuse, exit 1 | PASS |
| T-26 | Manifest signed by a foreign (rogue) key | "not signed by a trusted UAEG release key" → refuse, exit 1 | PASS |
| T-27 | Updater declares interface v99 (newer than bootstrap's v1) | Refuse + one-time bootstrap refresh instructions; exit 1 | PASS |
| T-28 | Release exists but manifest asset missing | Clear "could not download the release manifest" error; exit 1; version unchanged | PASS |

## Part 3 — operational modes through the release channel

| Test | Scenario | Result |
|---|---|---|
| T-29 | `--check` and `--dry-run` via the release server | Both read-only (no backups created), correct target reported — PASS |
| T-31 | **Offline rollback** (release server killed; `--rollback`) | Uses the cached verified updater; data restored; exit 0 — PASS |

## Part 4 — retained v0.3.2 contract enforcement (T-01..T-19)

Fresh install (T-01), operator state (T-02), `--check` (T-03), `--dry-run`
(T-04), public-dashboard-without-password refusal (T-GUARD), remote upgrade
via isolated staging with operator-tree byte-identity (T-13), dirty tree
(T-14), `--source` zero-data-loss with full evidence set (T-05), idempotency
(T-06), smoke evidence (T-07), auto-rollback exit 2 (T-08), manual rollback
(T-09), no-install refusal exit 3 (T-10), secret non-leakage (T-11), backup
retention (T-12), downgrade refusal exit 5 (T-16), standalone dry-run (T-15),
the v0.3.1 incident regression with untracked colliding asset (T-17),
accurate missing-tag reporting (T-18), standalone full upgrade (T-19) — all
PASS against the versioned updater, with the v0.3.2 assertion set extended
for contract v3 (RECORD fields).

## Environment fidelity notes (honesty about the simulation)

- The release server is `python3 -m http.server` serving
  `/releases/latest` (JSON `tag_name`) and `/download/<tag>/<asset>`;
  the bootstrap's channel is pointed at it via the documented
  `UAEG_RELEASE_API_BASE` / `UAEG_RELEASE_DOWNLOAD_BASE` overrides (mirrors/
  tests). Signature and hash verification are unconditional — the overrides
  change WHERE metadata comes from, never WHETHER it is verified.
- Real GitHub publication fidelity is additionally covered by
  `release/verify_release.py` (locally, on the v0.3.3 tree: PASS — see
  `report/evidence/manifest_verification_v033.txt`) and by the release
  workflow's own verification + asset smoke checks at tag time.
- All upgrades in the suite run the REAL install path (venv + pip install)
  in isolated directories with the same test-only patches CI uses.
