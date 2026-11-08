# Focused Audit — v0.4.0 mission (unified installer + production dashboard)

**Date:** 2026-11-08 · **Auditor environment:** isolated sandbox (no VPS
access — GitHub PAT only) · **Baseline audited:** `main` @
`b08fcd6eccbe7d205dcce0d09bfa4ad5b9bf13db`, latest release `v0.3.5`
(verified by fresh clone; matches the mission brief exactly).

---

## 1. Scope and method

Fresh clone of `github.com/smahud/uaeg`; line-by-line review of the
install/upgrade surface (`install.sh`, `upgrade.sh`, `updater.sh`,
`release/*`, `.github/workflows/*`, `tests/test_upgrade_contract.sh`, the
`docs/` set, the `report/` audit trail), plus live external probes of the
production host. Every finding below is tied to evidence.

## 2. Baseline verification (mission §1)

| Item | Result |
|---|---|
| HEAD at audit start | `b08fcd6` — confirmed identical to the brief |
| Latest tag | `v0.3.5` — confirmed |
| Commits since v0.3.5 | exactly one: `b08fcd6` (docs: v0.3.5 post-publication verification) |
| Unified installer already implemented? | **NO.** `install.sh` at v0.3.5 is a fresh-install + naive in-place installer (641 lines); it has no subcommands and does not route upgrades through the verified bootstrap/updater chain. The mission's P0-2 was therefore implemented in this release (see UNIFIED_INSTALLER.md). |

## 3. Finding-by-finding

### F-1 (P0, confirmed) — Production dashboard still unreachable; two releases behind

Live external probes (report/evidence/production_probe_v040.txt, captured
2026-11-08T16:00Z):

- `host.zerotier.my.id` → 125.163.142.132 (live). `host.zeotier.my.id` has
  **no DNS record** — the authoritative hostname is **`host.zerotier.my.id`**
  (the "zeotier" spelling in an earlier message is a typo; no certificate or
  service exists under it).
- **Port 3903 (dashboard): TCP REFUSED** (RST) from the public internet —
  consistent with the previously established root cause RC-A (loopback-bound
  dashboard listener) and/or RC-B (firewall REJECT). An "open port" at the
  provider firewall cannot help: nothing answers on the public interface.
- **Port 3902 (MCP): OPEN and healthy** — UAEG **v0.3.3** (not v0.3.5),
  `/healthz` ok (uptime ≈ 5.3 h at probe time), valid Let's Encrypt
  certificate for `host.zerotier.my.id` (issued 2026-11-07, expires
  2027-02-05), `/mcp` correctly returns 401 without a key.
- Conclusion: the operator upgraded v0.3.2 → v0.3.3 since the last audit but
  **never executed the public-dashboard runbook steps** (password → bind
  0.0.0.0 + allow_public + tls → restart → `ufw allow 3903`). The P0 symptom
  persists unchanged. No VPS access exists in this environment, so the fix
  remains an operator action: see DASHBOARD_PRODUCTION_CHECK.md for the exact
  one-command runbook (now built on the unified `install.sh`) and the precise
  diagnostics required from the operator. This requirement is **BLOCKED on
  VPS access**, reported truthfully — no laboratory result is claimed as a
  production fix.

### F-2 (P0, confirmed) — Two public entrypoints; upgrade.sh required for upgrades

At v0.3.5 the documented flow requires downloading `upgrade.sh` (the
permanent bootstrap) separately from `install.sh`. `install.sh` re-run on an
existing installation performed an **unverified in-place upgrade** (no
signed-manifest verification, no consistent DB backup, no zero-data-loss
proof, no auto-rollback beyond app/config files). This contradicts the
mission requirement that `install.sh` be the sole public entrypoint selecting
a *safe* upgrade by default. **Fixed in v0.4.0** (UNIFIED_INSTALLER.md).

### F-3 (P1) — Release-signing private key `uaeg-release-2` is unavailable

`release/keys/` contains only public keys (by design); neither the repository
nor this environment holds the `uaeg-release-2` private key (searched; not
present — consistent with the v0.3.3-era audit note that keys must be moved
to operator storage). Consequence: without the key, v0.4.0 could not be
signed, and `release.yml` refuses to publish unverified content. Precedent:
the same situation occurred at v0.3.4 with `uaeg-release-1` and was resolved
by the documented key-rotation procedure. **Resolved by operator-approved
rotation to `uaeg-release-3`** (BOOTSTRAP.md §Key rotation; the new private
key is handed to the operator and never committed).

### F-4 (P2) — `--version` flag ambiguity in install.sh

At v0.3.5 `install.sh --version X.Y.Z` selects a standalone-install release
tag, while conventional CLI semantics suggest "print version". **Fixed in
v0.4.0**: bare `--version` prints the installer version; `--version X.Y.Z`
pins the target release for both fresh install and verified upgrade
(U-16.4).

### F-5 (P2) — GitHub API rate-limit flakiness for unauthenticated clients

`fetch_latest_tag` (bootstrap) uses the unauthenticated GitHub Releases API.
Observed transient 403s from this sandbox's shared egress IP. Same behavior
as v0.3.3-v0.3.5 (not a regression); operators can pin `--version X.Y.Z` or
retry. Noted, not changed (keeping parity with the byte-stable bootstrap
design).

### F-6 (P3, verified non-issues)

- **Updater→installer invocation loop risk**: the updater invokes
  `<staged-source>/install.sh --yes --install-dir ...`. A naive unified
  dispatcher would re-enter the safe-upgrade path and loop. v0.4.0 guards
  this two ways (UAEG_BOOTSTRAP=1 environment check + "targeting flags imply
  in-place" rule). Proven by T-05/T-13/T-14 (standalone updater) and
  T-40..T-52 (bootstrap-invoked updater) all passing without loops.
- **`confirm_ask` byte-exactness** (contract T-38): preserved verbatim in the
  rewritten install.sh.
- **Test-suite sed compatibility** (ci.yml + contract suite patch the root
  check, apt lines, `ln -sf` line and `/usr/local/sbin/uaeg-upgrade` by exact
  line match): all patched line patterns preserved; verified by the green CI
  equivalent runs in this audit.

## 4. Compatibility assessment (mission §4)

| Preservation target | v0.4.0 behavior | Evidence |
|---|---|---|
| MCP agent keys | never written by any upgrade path; proven identical across upgrades | U-4 checks in every contract test; T-40/T-41/T-52 |
| Dashboard password | preserved (hash file never touched; public-bind-without-password upgrade refusal retained) | T-GUARD; U-4 check 7 |
| SQLite data | consistent online-backup snapshot before any change; quick_check + row-count proofs | T-05/T-08/T-48 |
| TLS certificates | untouched by safe upgrades (no TLS flags applied); certbot renewal hook preserved | U-8; install flow unchanged |
| Service configuration | preserved verbatim; unit rewritten only with identical parameterized paths | T-21/T-40 config assertions |
| Backups & rollback | unchanged machinery (`/var/backups/uaeg/upgrade-<ts>`, `--rollback`, retention) | T-08/T-09/T-48/T-49 |
| Historical tags/artifacts | untouched; keyring keeps keys 1+2 trusted so old releases still verify for pins/downgrades | T-26-adjacent rogue-key logic; keyring diff |
| Operator-local files | never modified by any upgrade (bootstrap self-SHA assertions) | T-22/T-23/T-40/T-41/T-52 |

Migration paths validated: fresh install (T-01), populated-state upgrade
(T-05/T-13), **v0.3.5 baseline → unified upgrade (T-52)**, and the
v0.3.3-era production shape (loopback dashboard + custom ports) is preserved
through upgrades (config assertions in T-21/T-40).

## 5. What was deliberately NOT done

- No changes to the safe-upgrade engine's semantics (`updater.sh` received
  only two additive, documented env fallbacks: `UAEG_REPO_URL`,
  `UAEG_BACKUPS_DIR` — flag > env > default; required by the contract suite
  to exercise the bare-invocation path without root).
- `upgrade.sh` keeps its full v0.3.x interface; the only diff is the
  key-rotation keyring addition + identity bump (BOOTSTRAP_VERSION 3),
  exactly as its own policy file prescribes for a rotation. Shipping it
  without key 3 would have produced a release whose own bootstrap cannot
  verify its own manifest — rejected as a defect.
- No dashboard password reset, no agent-key changes, no destructive action
  against production (no access; explicitly refused by design).

## 6. Verdict

The two P0 repository-side gaps (unified installer; release signing) are
closed and verified by 258 contract assertions + 150 pytest cases. The
production dashboard P0 is **diagnosed with fresh evidence and BLOCKED on
operator VPS access** — the exact runbook and diagnostics are in
DASHBOARD_PRODUCTION_CHECK.md.
