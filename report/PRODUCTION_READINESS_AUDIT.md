# UAEG — Production Readiness Audit

| | |
|---|---|
| **Audit scope** | Full repository audit, hardening and release preparation for the defined production environment (dedicated single-operator Debian 13 VPS, trusted agents, native root execution) |
| **Baseline audited** | `main` @ `a7a381eedcfbce741b1b4d3efe86f3bc9626e5a7` (tag `v0.1.1`) |
| **Result of this audit** | `main` @ the release commit of **v0.2.0** (see [RELEASE_DECISION.md](RELEASE_DECISION.md) for the exact SHA) |
| **Audit date** | 2026-11-07/08 (UTC) |
| **Auditor** | Principal-architect agent (execution agent per the master prompt); documentation cross-check delegated to a read-only sub-audit, integration and verification performed by the principal agent |

This report separates **verified facts** (reproduced or byte-verified), **findings** (with severity and disposition), **corrections**, **test results**, **remaining risks**, and **blocked work**.

---

## 1. Method

1. **Reconnaissance**: every source file, test, workflow, installer and document was read; the PRD (docs/PRD.md, authoritative spec) was mapped to implementation and tests (see [REQUIREMENT_TRACEABILITY.md](REQUIREMENT_TRACEABILITY.md)).
2. **Reproduction before fixing**: each suspected defect was reproduced with a standalone script (evidence captured verbatim) before any code change; fixes were then verified against the same scripts.
3. **Regression tests**: every material correction carries a dedicated regression test in `tests/`.
4. **Full-suite verification** on Python 3.12.16 and 3.13 (Debian 13 host). Python 3.11 is covered by the CI matrix but was not executable in the audit sandbox (not installed) — recorded as unverified locally.
5. **Acceptance runbook** AT-01..AT-19 executed to the extent the sandbox permits, with explicit NOT EXECUTED markers where a real VPS, root, systemd or a hosted MCP client is required (see [VPS_ACCEPTANCE_RESULTS.md](VPS_ACCEPTANCE_RESULTS.md)).

## 2. Baseline facts (verified)

- Repository `smahud/uaeg`, branch `main`, single commit history of 14 commits, clean worktree at start; tags `v0.1.0`, `v0.1.1`; version metadata consistent in code at 0.1.1 (`src/uaeg/__init__.py`, `pyproject.toml`, `install.sh`) but **not** in README/USAGE (stale 0.1.0).
- Code size at baseline: ~4,200 lines of Python + 390-line installer; 33 tests, all passing on 3.12/3.13 at baseline (verified).
- Dependencies pinned exactly (`mcp==1.27.0`, `starlette==0.50.0`, `uvicorn==0.44.0`); no other runtime dependencies; MIT license; `py.typed` present.
- CI workflow parses correctly and runs unit + e2e + installer-smoke on a 3.11/3.12/3.13 matrix (byte-verified; an early suspicion of a malformed `branches:` filter was disproven by hex-level inspection of the blob — see §5, note N-1).

## 3. Subsystem audit summary

| Subsystem | Verdict at baseline | Key evidence | Disposition |
|---|---|---|---|
| MCP Streamable HTTP lifecycle | Sound | Real TLS + protocol e2e tests (initialize → tools/list → tools/call → session id handling) | Kept; covered by extended e2e |
| Bearer auth / revocation / rotation | Auth & revocation immediate and per-request (good); rotation works; **revocation did not contain running processes**; CLI cancel/kill were stubs although documented as functional | `auth.py` per-request DB lookup (verified); stubs at `cli.py` (verified); e2e containment test | **Fixed** (containment + real CLI commands) — SEC-03 |
| Sync shell execution | Sound (timeouts, process-group kill, bounded capture, output drain to EOF) | `runner.py` review + unit tests incl. race fix from v0.1.1 | Kept |
| PTY sessions | Sound overall (ring buffer, resize, reconnect, terminate); minor zombie edge in reaper cancellation; no key ownership tracked | `pty.py` review | **Hardened** (reaper fix + key ownership) |
| Background jobs | Sound persistence model (shim + exit files, reattach, interrupted-after-reboot semantics verified by tests) | `jobs.py`/`_jobwrap.py` + tests | Kept; journal line now redacted |
| SQLite | WAL + busy timeout + serialized access (good); **no schema-version guard; corruption produced tracebacks** | `db.py` review; corruption repro (clean exit now) | **Hardened** (version guard, quick_check in doctor, clean startup error) |
| Dashboard isolation / CIDR | Bind restrictions good (wildcard refused without allow_public); **CIDR allowlist failed OPEN when all entries were invalid; invalid entries silently ignored** | REPRO-2: HTTP 200 served to arbitrary source with all-invalid list | **Fixed** (fail-closed validation) — SEC-01 |
| TLS | Enforced on the public listener, self-signed auto-generation, manual/certbot modes; **corrupt cert produced a raw traceback; documented dev escape hatch was dead code** | Code review + new fail-closed tests | **Fixed** (clean ConfigError; escape hatch functional) |
| Installer | Fresh install works (verified end-to-end in isolation); **upgrades overwrote operator configuration (FR-003 violation); no rollback on mid-upgrade failure; `--dashboard-bind lan` could select a public address; health check used default port instead of effective port** | REPRO-3 (config reset confirmed); failure-path repro (rollback verified working) | **Fixed** (config-preserving merge, rollback trap, RFC1918 preference, effective-port health check) |
| systemd lifecycle | Unit correct for the intended root deployment; `Restart=on-failure`; **half-alive service defeated restart supervision** (see SEC-02) | Unit file review; supervision tests | Fixed via listener supervision |
| Dependency / supply chain | Pinned exact versions; installer bootstrap fetches a versioned GitHub tarball with documented checksum verification; no telemetry | pyproject + INSTALL.md | Acceptable for MVP; reproducibility statement in RELEASE_DECISION |
| Documentation | Deep and mostly accurate; **10 findings rated WRONG** (procedures that would fail or silently lose state), 2 STALE versions, 14 minor | Full doc-vs-code cross-audit (26 findings) | **All fixed** (44 doc edits) |

## 4. The seven specifically suspected issues

| # | Suspected issue | Status at baseline | Evidence | Severity | Disposition |
|---|---|---|---|---|---|
| 1 | Configuration overwrite during upgrade | **CONFIRMED** | REPRO-3: after a documented upgrade, `exec.default_timeout_s` 333→120, `restricted_paths` `["/srv/important"]`→`[]`, `allowed_dashboard_cidrs` `["192.168.1.0/24"]`→`null`. Also violated FR-003 and 4 doc claims | **P1** | FIXED: installer now loads and preserves the existing config; only explicitly passed flags override; regression-covered (repro + e2e) |
| 2 | Listener failure without full service termination | **CONFIRMED (partial)** | Bind-failure already exited (CPython `SystemExit` passthrough) — verified. But a listener dying with a *regular* exception left the service half-alive indefinitely: REPRO-1b (6 s after dashboard crash: process alive, MCP serving, systemd unaware) | **P1** | FIXED: supervised serve tasks; any listener failure stops both, exit code 1, clean logs; regression-covered |
| 3 | Invalid CIDR entries weakening dashboard controls | **CONFIRMED** | REPRO-2: with `allowed_dashboard_cidrs: ["192.168.99.0/not-a-cidr","bogus"]` the parsed list was empty → middleware not installed → `GET /api/overview` from `8.8.8.8` → HTTP 200 | **P1** | FIXED: `Config.validate()` rejects invalid entries at startup; dashboard builder fails closed as defense in depth; middleware blocks all paths for out-of-list peers; regression-covered |
| 4 | Audit-log exposure of secrets embedded in command text | **CONFIRMED** | Code evidence: `add_log` persisted `meta.command` and `output_preview` verbatim; journal lines logged full commands; jobs/sessions tables stored raw commands (dashboard/CLI display) | **P2** | FIXED: `security.redact_secrets()` applied at the persistence choke points (`add_log`, `insert_job`, `insert_session`, session previews, journal lines). Raw output files and agent-visible `job_output` deliberately unredacted (documented). Regression-covered incl. full-path e2e |
| 5 | Incomplete process termination after key revocation | **CONFIRMED** | No termination call anywhere in revoke paths; additionally the documented operator remediation (`uaeg jobs cancel`, `uaeg sessions kill`) was a stub returning exit 1 | **P2** | FIXED: revocation containment terminates the key's live PTY sessions and cancels its running jobs (dashboard + CLI), opt-out `--keep-processes` / `{"keep_processes": true}`; CLI cancel/kill implemented for real; rotation explicitly not containment; regression-covered (unit + e2e) |
| 6 | Installer failure halfway through an upgrade | **CONFIRMED** | With `set -e`, failure after the service was stopped left a stopped service and half-replaced app dir; health check failure aborted without recovery; health check polled the *default* port (custom installs failed spuriously) | **P2** | FIXED: rollback EXIT trap restores config + app dir + restarts the previous service; app-dir backup; effective-port health check; verified by forced-failure repro |
| 7 | Version inconsistencies between documentation and package metadata | **CONFIRMED** | README "(v0.1.0)", USAGE example `uaeg 0.1.0` vs package 0.1.1 | **P3** | FIXED: single source of truth at 0.2.0; new `test_version_metadata_consistency` enforces pyproject == `__version__` == installer == README |

## 5. Additional findings (not on the suspect list)

| ID | Finding | Severity | Disposition |
|---|---|---|---|
| F-08 | `--dashboard-bind lan` picked the default-route source address — on single-NIC public VPSs this is the **public** IP, binding the no-login dashboard to the public interface (docs claimed "private IP") | **P2** | FIXED: RFC1918 preference + refusal to auto-bind public; documented |
| F-09 | `UAEG_DEV_INSECURE_HTTP=1` was documented but dead code (unreachable) | P3 | FIXED: functional dev-only escape hatch (loud warning); documented |
| F-10 | PTY reaper cancellation could orphan a not-yet-reaped child (zombie edge) | P3 | FIXED: reaper survives until the child is reaped; grace-period SIGKILL |
| F-11 | `async def JobManager.shutdown()` was never awaited (watchers not cancelled on graceful shutdown) | P3 | FIXED |
| F-12 | Corrupt SQLite DB produced a traceback at startup; no integrity check anywhere | P2 | FIXED: clean exit-code-2 error + recovery pointer; `PRAGMA quick_check` in doctor; schema-version guard |
| F-13 | Corrupt/unreadable TLS material produced a raw `ssl.SSLError` traceback | P3 | FIXED: clean ConfigError |
| F-14 | `Config.from_dict` raised raw `TypeError` on unknown fields; `True` passed port validation as port 1 | P3 | FIXED |
| F-15 | `uaeg jobs output --offset -N` produced wrong slices | P3 | FIXED (clamped) |
| F-16 | SAN never reported by doctor/dashboard despite doc claims; doctor table/`version` check claims mismatched reality | P3 | FIXED (SAN reported; docs aligned) |
| F-17 | 10 WRONG / 2 STALE / 14 minor documentation findings (upgrade promises, stub commands documented as functional, reboot claim, `git -C /opt/uaeg` step, illustrative outputs, CI claims, env-precedence formula, "five tables", etc.) | P2 (aggregate) | FIXED: 44 surgical doc edits across 8 documents + README |
| N-1 | Process note: during the audit, tool-output rendering intermittently displayed the CI file's `branches: [main]` as `branches: ain]`, creating a false impression of an upstream CI defect. Byte-level and git-blob-hash verification proved the committed file is correct (`[main]`); no CI defect exists. Recorded to keep the audit honest. | — | No action required |

**Classification totals**: P0 = 0 · P1 = 3 · P2 = 7 · P3 = 7. All P1/P2 confirmed defects are **fixed with regression tests**; P3 items fixed or explicitly accepted as documented behavior. No unresolved P0/P1 findings remain (release gate 4).

## 6. Corrections made (code)

- `src/uaeg/server.py` — listener supervision with fail-fast exit codes; functional `UAEG_DEV_INSECURE_HTTP`; clean DB-corruption and TLS-load errors; awaited job shutdown.
- `src/uaeg/security.py` — `redact_secrets()` (12 high-confidence rule families).
- `src/uaeg/db.py` — redaction at persistence choke points; `find_key_by_verifier`; schema-version guard; `quick_check()`.
- `src/uaeg/config.py` — CIDR fail-closed validation; robust `from_dict`; bool-port rejection; `load(str|Path)`.
- `src/uaeg/dashboard.py` — per-entry CIDR fail-closed build; dead middleware removed; revocation containment endpoint behavior; redacted session previews.
- `src/uaeg/containment.py` (new) — out-of-process containment, job cancel and session kill primitives.
- `src/uaeg/runtime.py` — `terminate_key_resources` (in-server containment); SAN in `tls_info`; awaited shutdown.
- `src/uaeg/executor/pty.py` — key ownership on sessions; zombie-safe reaper; redacted journal lines.
- `src/uaeg/executor/jobs.py` — `cancel_by_key`; redacted journal lines.
- `src/uaeg/cli.py` — real `jobs cancel` / `sessions kill`; revoke containment with `--keep-processes`; doctor SAN + DB integrity; offset clamp.
- `install.sh` — flag-given tracking; config-preserving upgrade merge; rollback trap with app-dir backup; RFC1918 LAN detection; effective-value health check/summary; version 0.2.0.
- Public APIs preserved: all 13 MCP tools, their signatures and result shapes are unchanged; dashboard API is a strict superset (revoke now also returns a `containment` object); CLI gained flags but kept existing syntax.

## 7. Test results

See [TEST_EVIDENCE.md](TEST_EVIDENCE.md) for full detail: **74 tests (41 new), 0 failures, on Python 3.12.16 and 3.13**; 4/4 defect reproductions converted to FIXED.

## 8. Remaining risks and blocked work

- **Blocked (environment)**: AT-04 (hosted platform client), AT-06 (apt install), AT-09 (systemd service management) and the root-context/LAN legs of AT-01/05/14/15 require an authorized root Debian 13 VPS and a hosted MCP platform — NOT EXECUTED in this audit (see [VPS_ACCEPTANCE_RESULTS.md](VPS_ACCEPTANCE_RESULTS.md)). This is the only release gate not fully discharged.
- **Accepted risks (documented, by design)**: full root execution for authenticated agents; advisory-only restricted paths; no-login dashboard protected solely by network placement (+ optional CIDR allowlist); raw job output files and agent-visible `job_output` unredacted; job/session inspection not key-scoped (trusted-operator model); process-group signaling cannot catch processes that escaped into their own session/group.
- **Not verified locally**: Python 3.11 execution (CI matrix covers it); certbot issuance/renewal drill (needs port 80 + DNS; renewal hook reviewed, restart-on-renewal path code-verified).
