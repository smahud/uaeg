# Test Results — v0.4.0 (unified installer release)

**Date:** 2026-11-08 · **Environment:** isolated sandbox (Python 3.12.16,
bash 5.x, no root, no systemd — identical shape to the CI contract-suite
job) · **Suites:** `tests/test_upgrade_contract.sh` (contract) + pytest
unit/e2e + lab browser + lab MCP smoke + external production probes.

Historical suites for v0.3.x releases: see `report/TEST_EVIDENCE.md` and
`report/evidence/upgrade_contract_suite_v*.txt`.

---

## 1. Summary

| Suite | Result |
|---|---|
| Safe-upgrade contract suite (T-01..T-52, real Ed25519 chain, local release server) | **258 PASS / 0 FAIL** |
| pytest unit + e2e (core, executor, containment, security redaction, server hardening, dashboard auth, regression v033/v034, e2e smoke, e2e dashboard auth, e2e hardening) | **150 passed** (75.98 s) |
| Unified-installer dispatch smoke (help/version/check/dry-run/start/stop/status on an empty box) | PASS (manual, pre-suite) |
| Lab browser verification (real Chromium: login, wrong password, login, 5 tabs, logout, 0 console errors) | **10/10 PASS** — `evidence/lab_browser_verification_v040.txt` + `evidence/browser_v040/` |
| Lab MCP smoke (healthz, initialize with bearer key, 401 without key) | PASS — `evidence/lab_mcp_smoke_v040.txt` |
| Production external probes | captured — `evidence/production_probe_v040.txt` (dashboard P0 persists; see DASHBOARD_PRODUCTION_CHECK.md) |

Mission's ten required test areas → coverage:

| # | Required | Evidence |
|---|---|---|
| 1 | Existing unit and integration tests | 150/150 pytest; all 191 pre-existing contract assertions unmodified and green |
| 2 | Installer fresh-install smoke | T-01; CI installer job (fresh install + serve + MCP probe); sandbox lab install |
| 3 | Upgrade from v0.3.5 | **T-52**: real v0.3.5-tag tree installed, two keys generated, then bare `install.sh` → verified upgrade to a simulated newer release; keys identical |
| 4 | Multi-release reuse of an unchanged local install.sh | T-40 → T-41 (updater implementation CHANGED) → T-42 (pin) → T-52, operator file SHA-256 identical throughout |
| 5 | Signature verification + invalid-signature rejection | T-43 (tampered manifest), T-44 (foreign key), T-45 (tampered updater) — exit 1, nothing changed; positive verification in T-40..T-42, T-47, T-52 |
| 6 | Existing key/password preservation | U-4 machine proofs in every upgrade test (keys identical, password intact, config unchanged); T-51 (--repair preserves data) |
| 7 | Automatic and manual rollback | T-48 (broken release via bare install.sh → exit 2, auto-restore), T-49 (manual `--rollback` from cached updater); T-08/T-09/T-30/T-31 (pre-existing, still green) |
| 8 | Service-management commands | T-50 (`--start/--stop/--restart/--status` without systemd → clean rc-1 refusals); CI unified-command smoke (systemctl present, unit absent); `--status` health-probe code path exercised in the lab |
| 9 | Dashboard browser verification | Lab: 10/10 real-browser matrix (login page, wrong password refused, correct login, all tabs, logout, 0 console errors, 7 non-blank screenshots). **Production: BLOCKED on VPS access** — external probes only (login page unreachable: TCP refused) |
| 10 | MCP smoke after deployment | Lab: initialize + 401 matrix. Production: `/healthz` ok + 401 (external, no key) — v0.3.3 currently; post-v0.4.0 production verification pending the operator runbook |

## 2. Contract suite detail (new tests)

Setup (T-39): the release server is relaunched after T-31's offline test; a
patched operator `install.sh` (test keyring, redirected system paths) is
created once and its SHA recorded; a git remote whose tree carries a
runnable (patched) installer backs each simulated release.

| Test | Proves | Result |
|---|---|---|
| T-39 | `--help` lists all commands; bare `--version` prints v0.4.0 | PASS |
| T-40 | Bare invocation on an existing install → signature-verified safe upgrade; version bump; keys/config preserved; operator file SHA unchanged; `RECORD.json` shows `bootstrap_invoked=true` and `bootstrap_file` = the operator's install.sh | PASS |
| T-41 | The SAME file upgrades a release whose updater implementation changed; SHA still identical | PASS |
| T-42 | `install.sh --version 20.0.0` pinned verified upgrade | PASS |
| T-43 | Tampered manifest → `SIGNATURE INVALID`, exit 1, no backup, version unchanged | PASS |
| T-44 | Manifest signed by a foreign key → rejected | PASS |
| T-45 | Tampered updater bytes → `SHA-256 MISMATCH`, rejected | PASS |
| T-46 | Updater interface 99 → refused; refresh instructions point at install.sh | PASS |
| T-47 | `--check` shows status + "update available"; `--dry-run` shows the plan; no backups | PASS |
| T-48 | Broken release via bare install.sh → automatic rollback, exit 2, app back at previous version, keys intact | PASS |
| T-49 | `install.sh --rollback` uses the cached updater, restores pre-upgrade data | PASS |
| T-50 | `--start/--stop/--restart/--status` without a systemd unit → exit 1 with a clear systemd message | PASS |
| T-51 | `--repair` preserves keys/data, applies config flags (dashboard.port 3954→3956) | PASS |
| T-52 | v0.3.5 baseline install → bare unified upgrade → keys preserved; operator file SHA identical | PASS |

## 3. Issues found and fixed during this audit's own testing

1. `--rollback` initially did not forward the mode to the cached updater
   (the updater ran in upgrade mode; caught by T-49 returning exit 2).
   Fixed: the rollback subcommand now translates to `--rollback` for the
   updater, exactly as `upgrade.sh` forwards it.
2. Config-flag validation initially rejected `--repair` with first-install
   flags (caught by T-51). Fixed: `--repair` accepts configuration flags by
   design.
Both regressions were introduced and eliminated inside this audit; the
final suite state is fully green with the fixes in place.

3. The new CI unified-command smoke step failed twice on GitHub Actions
   before passing (test-design only, no product change): first it invoked
   the real unpatched updater as non-root (the updater correctly insists
   on root), then a log filename starting with `--` broke grep/cat. Fixed
   by ci.yml-only commits (`56173bd`, `00cc063`); the contract suite and
   all Python matrices were green on every commit including the tag.

## 4. Known environmental notes (not defects)

- Unauthenticated GitHub API calls from a shared egress IP can be
  rate-limited (403) transiently; the bootstrap then reports "could not
  determine the latest release" — identical to v0.3.3–v0.3.5 behavior;
  `--version X.Y.Z` pins bypass it.
- The contract suite runs without root/systemd by design; service-command
  tests assert the clean-refusal paths (real systemctl operation is covered
  by the production runbook's `--status` verification).
- The sandbox has no systemd, so `install.sh --status`'s systemctl branch is
  covered by code review + CI (systemctl exists on runners) rather than a
  live unit here; the dashboard/MCP behavior itself is covered by the real
  server lab runs.
