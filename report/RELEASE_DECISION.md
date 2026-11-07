# UAEG — Release Decision

| | |
|---|---|
| **Decision** | **CONDITIONAL GO** |
| **Release** | UAEG **v0.2.0** (production-hardening release) |
| **Audited release commit** | `35b0d8dc9317de914ba0136459a69dd33c523ee3` (tag `v0.2.0`, branch `main`) |
| **Decision record** | This file (committed immediately after the release commit) |
| **Baseline audited** | `a7a381eedcfbce741b1b4d3efe86f3bc9626e5a7` (v0.1.1) |
| **Target environment** | Dedicated, single-operator Debian 13 VPS; trusted agents; native root execution (per PRD v1.0) |
| **Decided** | 2026-11-08 (UTC) |

## 1. Why CONDITIONAL GO (not GO, not NO-GO)

**Engineering gates — all passed:**

1. **PRD traceability**: every FR-001..FR-020 and the section 13/14 baselines are implemented and traceable (see [REQUIREMENT_TRACEABILITY.md](REQUIREMENT_TRACEABILITY.md)); the one spec violation found (FR-003 config persistence across upgrades) is fixed and regression-covered.
2. **Automated tests**: 74 tests (41 added in this release) — **0 failures on Python 3.12.16 and 3.13**; every material fix carries a dedicated regression test; all four defect reproductions re-run to FIXED ([TEST_EVIDENCE.md](TEST_EVIDENCE.md)). Python 3.11 runs in the CI matrix (not executable in the audit sandbox).
3. **Findings**: P0 = 0; P1 = 3, all fixed; P2 = 7, all fixed; P3 = 7, fixed or accepted-and-documented. **No unresolved P0/P1 findings.** ([SECURITY_FINDINGS.md](SECURITY_FINDINGS.md), [PRODUCTION_READINESS_AUDIT.md](PRODUCTION_READINESS_AUDIT.md))
4. **Dashboard exposure & authentication controls verified**: wildcard bind refused; CIDR allowlist fail-closed and enforced on every path (403 for out-of-list peers); 401 + WWW-Authenticate for missing/invalid/revoked credentials; verifier-only key storage; revocation immediate and containing; rotation issues new credentials.
5. **Upgrade, rollback, backup and recovery tested**: config-preserving upgrade (repro FIXED), automatic rollback on forced installer failure (config + app + service restored), restart persistence (AT-16), SQLite corruption → clean error + documented move-aside recovery + doctor integrity check.
6. **Documentation matches the implementation**: 44 corrections applied across README and all 8 docs; version metadata single-sourced at 0.2.0 and enforced by a test; no stale version references remain.
7. **Versioned, reproducible release produced**: tag `v0.2.0` on the audited commit; exact-pinned dependencies (`mcp==1.27.0`, `starlette==0.50.0`, `uvicorn==0.44.0`); installer bootstrap fetches the tagged archive with the documented checksum-verification workflow.

**The one gate not fully discharged — PRD section 15 (acceptance on an actual target VPS):**
AT-01..AT-19 were executed on a genuine Debian 13 userspace, but in a container without
root, systemd, a public IP/DNS, a hosted MCP platform or a second LAN host:
**12 PASS · 4 ADAPTED · 3 NOT EXECUTED · 0 FAIL** ([VPS_ACCEPTANCE_RESULTS.md](VPS_ACCEPTANCE_RESULTS.md)).
Per the audit mission's own rule, the unexecuted legs are reported as NOT EXECUTED — never
as passed. Until they run on an authorized VPS, the product must not be *labeled*
production-ready per the PRD's acceptance clause, even though all engineering work is done
and verified. Hence **CONDITIONAL GO**: v0.2.0 is released as the production-hardening
candidate; final acceptance is a documented operator action away.

## 2. Remaining blockers (all environmental, none engineering)

| # | Blocker | Required action |
|---|---|---|
| B-1 | AT-04 hosted MCP client connection | Configure a compatible hosted platform with URL + `Authorization: Bearer` header against the VPS; verify initialize/tools/`server_info`, `LAST USED` advancing |
| B-2 | AT-06 root package installation | `apt-get update && apt-get install -y jq` via the agent on the root VPS |
| B-3 | AT-09 systemd lifecycle | Install with service registration; agent-created test unit start/stop/cleanup |
| B-4 | AT-05 root context | `whoami` → `root` on the standard install |
| B-5 | AT-14/AT-15 LAN legs | Dashboard from an approved LAN workstation; denial from an unapproved source |
| B-6 | Certbot drill | Domain install (port 80 + DNS) and one renewal (deploy hook restarts UAEG) |

## 3. Exact next actions if release is to be finalized

1. Operator provisions the authorized disposable Debian 13 VPS with a DNS record (e.g. `gw.example.com`).
2. Install: `git clone https://github.com/smahud/uaeg.git && cd uaeg && sudo ./install.sh --domain gw.example.com` (record `git rev-parse HEAD` from the clone before installing; expect `35b0d8d…` / tag `v0.2.0`).
3. Run the runbook `docs/ACCEPTANCE.md` AT-01..AT-19 end-to-end and fill the results table; the sandbox-executed matrix in [VPS_ACCEPTANCE_RESULTS.md](VPS_ACCEPTANCE_RESULTS.md) lists exactly which legs remain.
4. If all rows pass: update the results table, flip the decision to **GO** in this file, tag the acceptance evidence — no code changes expected.
5. If any row fails: file the failure with logs (`journalctl -u uaeg`) against the relevant subsystem; the audit's reproduction-first method (see [PRODUCTION_READINESS_AUDIT.md](PRODUCTION_READINESS_AUDIT.md) §1) applies.

## 4. Rollback / install-safety statement

Upgrades from v0.1.x: the v0.2.0 installer preserves the existing configuration (FR-003)
and rolls back automatically on failure. One intentional behavior change to communicate:
configurations containing **invalid** `network.allowed_dashboard_cidrs` entries (silently
ignored by v0.1.x) now prevent startup until fixed or nulled — fail-closed by design
(documented in TROUBLESHOOTING.md §"startup fails with invalid CIDR").

## 5. Sign-off

- Execution agent (audit, corrections, integration, verification): full-repo audit at `a7a381e`; corrections as commit `35b0d8d`; 148 green test executions (74 × 2 interpreters); 4/4 reproductions converted to FIXED; acceptance runbook executed to sandbox limits with honest NOT EXECUTED markers.
- Decision: **CONDITIONAL GO** — release v0.2.0 as the production-readiness candidate; final GO requires the outstanding VPS acceptance legs (B-1..B-6).
