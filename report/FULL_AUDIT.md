# Full End-to-End Audit — UAEG v0.3.3


---

# AUDIT ROUND 2 — v0.3.4 (2026-11-08)

(Supersedes nothing: this round extends the v0.3.3 audit below with a second full-system pass.)

**Baseline:** v0.3.3 (fff55c3, published release verified independently)
→ working tree at v0.3.4. **Method:** four parallel deep sub-audits
(executor/jobs/PTY; dashboard/dashauth/UI; MCP server/auth/tools;
CLI/db/config/docs — three completed, the fourth covered by the principal)
integrated and re-verified; every confirmed defect fixed at root cause with
a pinning regression test; full matrix re-run on the final state.

## R2.1 Verified defects found and FIXED in v0.3.4

| ID | Severity | Subsystem | Defect (all reproduced before fixing) |
|----|----------|-----------|----------------------------------------|
| V-1 | **P1** | executor/pty.py | When a session's process exited, the session was dropped from the registry in the same event-loop batch as the final `feed()`: the ring-buffer tail was orphaned and `shell_output` returned "unknown or expired session_id" — the agent could NEVER read the final output chunk. Simultaneously the `waitpid` in `_mark_exited` discarded the child's status, so `exit_code` stayed `None` forever. Reproduced at 200 ms and 5 ms polling cadences with `bash -c 'echo FINAL_OUTPUT_123; exit 7'`. |
| D-1 | **P1** | dashauth.py + dashboard.py | Deleting `<data_dir>/dashboard-pass.json` at runtime silently DISABLED all dashboard authentication (the auth middleware re-checks `is_configured()` per request and the file's absence read as "never configured"), including on public `0.0.0.0` binds. Since authenticated agents run as root by design, any agent could convert the operator's password-protected dashboard into an unauthenticated key-issuance endpoint with one `rm`. Reproduced in-process at ASGI level: pre-deletion `/api/keys` → 401; post-deletion → 200 + full SPA + key creation. |
| V-2 | P2 | executor/jobs.py | The detached job shim (`Popen`) was never reaped: every finished/killed job left a zombie until an unrelated later `subprocess` call happened to reap it. Reproduced: `/proc/<pid>/stat` state `Z` persisting after job completion. |
| V-3 | P2 | executor/jobs.py | The jobs row was inserted AFTER spawning the process: a failed `insert_job` (disk full, db locked) orphaned a live, unmanageable process and leaked a raw `sqlite3.OperationalError` to the MCP client (only `JobError` is mapped to a clean tool error). Reproduced by monkeypatching `insert_job` to raise; `pgrep` showed the orphan running `sleep 45`. |
| V-4 | P2 | executor/pty.py | `shell_close` always reported `exit_code: null` — both `waitpid` call sites in `_terminate` discarded the status. |
| MCP-D1 | P2 | mcp_tools/pty/jobs | No key-scoped ownership: any valid key could read, inject input into, resize, close or cancel another key's sessions/jobs, and `shell_sessions`/`job_list` exposed every key's resources (cross-key PTY input injection). Documented as a single-operator MVP limitation — now enforced (see LIMITATIONS.md #9) at the manager layer with the dashboard/CLI remaining operator-level. |
| D-2 | P3 | dashboard.py | The 1 MiB request-body cap only inspected the `Content-Length` header; chunked request bodies bypassed it into uncapped `request.json()` reads and unbounded persisted fields. Reproduced: 2 MiB chunked POST accepted (201); same body with Content-Length correctly 413. |
| MCP-D2 | P3 | pty.py | `shell_resize` accepted unvalidated cols/rows (0/1/1001+ pushed to `TIOCSWINSZ`; negatives/>65535 raised a raw `struct.error`). |
| MCP-D3 | P3 | mcp_tools.py | `job_list(limit=-1)` dumped the entire jobs table (SQLite treats negative LIMIT as unlimited). |
| MCP-D4 | P3 | mcp_tools.py | `shell_exec(timeout_seconds=-5)` killed the process group in ~1 ms and reported `timed_out` — no lower/upper bound (inconsistent with the `job_wait` clamp whose rationale is documented in the same file). |
| D-5 | P3 | mcp_tools.py | Module docstring claimed "Every tool call is recorded in the execution audit log" — 8 of 13 tools wrote no audit rows. Docstring corrected to the truthful set. |
| D-6 | P3 | config.py | `mcp_url()` advertised `https://` while `UAEG_DEV_INSECURE_HTTP=1` serves plain HTTP (dev-only cosmetic inconsistency). |
| D-7 | P3 | config.py | `Config.from_dict` silently swallowed a non-object section (`"tls": "certbot"`) and reverted the WHOLE section to defaults (fail-open configuration). Now a load-time `ConfigError`. |
| C-2 | P3 | dashboard.py | `runtime.tls_info()` (blocking `openssl` subprocess, 10 s timeout) ran on the event loop inside the `/api/server` handler — could freeze both listeners. Moved to `asyncio.to_thread`. |
| C-3 | P3 | pty.py | `write()`/`resize()` leaked raw `OSError` (EIO/EBADF race on a just-closed master) as internal tool errors instead of `SessionError("session is closed")`. |
| C-4 | P3 | pty.py | `Task.cancel()` called from a `to_thread` worker (not thread-safe) — reaper cancellation moved onto the loop thread. |
| Docs | P3 | docs/LIMITATIONS.md | Item #3 still described the pre-v0.3.0 "dashboard has no login" state — contradicted the shipped password login. Rewritten to the actual contract. Dead code removed (`paths.ensure_data_dirs`, `Store.find_key_by_verifier`, `Session.expires_at`). |

## R2.2 Release engineering change (v0.3.4)

**Signing-key rotation `uaeg-release-1` → `uaeg-release-2`.** The v0.3.3
manifest's signature was independently verified against the committed public
key (proving the key pair existed), but the private half is not available to
this audit round (not in the repository, git history, sandbox, or GitHub —
verified by blob-scan). Per docs/BOOTSTRAP.md's documented exceptional
procedure, v0.3.4 is signed by a newly generated `uaeg-release-2`; the
bootstrap keyring (BOOTSTRAP_VERSION=2) trusts BOTH keys so historical
releases (≤ v0.3.3) still verify for `--version` pins. Holders of a
v0.3.3-era bootstrap get the documented fail-closed "signature invalid /
key rotation" refusal and re-download the bootstrap ONCE. The private key
`uaeg-release-2` is delivered to the operator out-of-band (never committed).

## R2.3 Test evidence (v0.3.4 — final state, before tagging)

- Python tests: **118 passed / 0 failed** unit+regression (101 pre-existing,
  17 new in `tests/test_unit_regression_v034.py`) and **32 passed** e2e
  (smoke + dashboard auth + hardening) — `report/evidence/pytest_v034.txt`,
  `report/evidence/e2e_v034.txt`.
- Upgrade contract suite: **155 PASS / 0 FAIL** (T-01..T-31 incl. the
  v0.3.1 incident regression T-17 and the permanent-bootstrap series on the
  rotated keyring) — `report/evidence/upgrade_contract_suite_v034.txt`.
- Operator-incident reproduction against the REAL published v0.3.3 release
  (real GitHub API, real Ed25519 signature, real updater bytes, EUID=0 via
  root user namespace; only the installer's apt step is skipped as in CI):
  **18/18 PASS** — the downloaded bootstrap upgrades a v0.3.0 installation
  to v0.3.3 with zero data loss while the operator's dirty clone (untracked,
  colliding `upgrade.sh` inside it) stays byte-for-byte untouched —
  `report/evidence/incident_repro_real_v033.txt`.

## R2.4 Verified-clean areas this round (no defects)

db.py (WAL/RLock/redaction/schema guard), cli.py (incl. the full `uaeg pass`
contract), auth.py bearer chain, server.py listener supervision and TLS
fail-closed paths, dashboard routing table (every route requires auth;
no traversal), dashboard_ui.py XSS escaping (all `innerHTML` paths pass
`esc()`), runner.py core path, MCP Streamable HTTP lifecycle on the pinned
mcp==1.27.0, revocation containment, installer/updater/bootstrap shell
logic (unchanged this round, contract suite green).



**Date:** 2026-11-08 · **Scope:** complete repository audit at v0.3.2
(9da477e) → patched and re-verified as v0.3.3 · **Method:** layered
reconnaissance (repo, live lab reproduction, real-browser testing, network
probes of production), two independent sub-audits (backend, frontend)
integrated and re-verified by the principal, automated test matrix.

This report supersedes the v0.3.2 FULL_AUDIT.md (preserved in git history at
tag v0.3.2).

---

## 1. Baseline established

- Repository: github.com/smahud/uaeg, branch `main`, HEAD `9da477e`
  ("docs(report): post-publication verification of v0.3.2") — no commits
  after the v0.3.2 tag; latest release v0.3.2 (published 2026-11-07T23:34Z)
  with 4 assets (install.sh, upgrade.sh + sidecars).
- Prior audit trail (v0.3.1 upgrade incident root cause + fix) verified
  accurate; v0.3.2 contract suite reproduced locally: 95 PASS / 0 FAIL.
- Production (live): UAEG **v0.3.2** at host.zerotier.my.id — see
  DASHBOARD_ROOT_CAUSE.md for the incident analysis.

## 2. MCP tool registry verification

All 13 documented tools registered, exactly (verified programmatically
against the real FastMCP registry on the pinned mcp==1.27.0):
`server_info`, `shell_exec`, `shell_open`, `shell_input`, `shell_output`,
`shell_resize`, `shell_close`, `shell_sessions`, `job_status`, `job_output`,
`job_wait`, `job_cancel`, `job_list`. No missing, no extra tools. Bearer
verification runs ahead of MCP session handling (app.py wraps
`streamable_http_app()` in `BearerAuthMiddleware`); `/mcp/` (trailing slash)
307→401 — no bypass; only `/healthz` and `/` are public by design.
Production confirms the contract live: unauthenticated `GET /mcp` → 401.

## 3. Findings (integrated from the backend + frontend sub-audits, each
re-verified by the principal; reproductions re-run against patched code)

### Release blockers found (both fixed in v0.3.3)

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| B-1 | **P1** | Blocking `os.write` on the PTY master ran on the event loop: one `shell_input` into a suspended session (agent sends Ctrl-Z — an advertised feature) froze the ENTIRE service (both listeners), often permanently; no in-band recovery. REPRODUCED twice before the fix. | Master fd now `O_NONBLOCK`; undeliverable input is queued (≤1 MiB backpressure cap with a clear error) and drained by an event-loop writer. Regression test proves the loop stays responsive into a SIGSTOPed session and that the backlog cap raises cleanly. |
| — | P0(process) | Production dashboard unreachable: loopback-bound dashboard + firewall (see DASHBOARD_ROOT_CAUSE.md). | Operator runbook + v0.3.3 docs/install hardening; not a code defect. |

### Security-contract degradations (fixed)

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| B-2 | P2 | CLI revocation containment only saw the newest 1000 session rows — a revoked key's older-but-open PTY survived `uaeg keys revoke` (FR-012 regression). REPRODUCED. | `containment.key_resources()` now queries `open_sessions()` (no LIMIT). Regression test with 1100 noise rows. |
| B-3 | P2 | `JobManager.cancel()` never escalated to SIGKILL — the break condition read the DB status it had just written ('cancelled' is final), so TERM-ignoring commands (nohup, `trap '' TERM`) kept running while reported 'cancelled'. REPRODUCED. | Escalation now decided by actual process-group liveness (zombie-aware `/proc` scan). Regression test kills a `trap '' TERM` tree. |
| B-4 | P2 | No boot-id stamp: after a reboot a recycled pid could keep a dead job 'running' forever, and containment/cancel would signal an UNRELATED root process group. | Jobs stamp `/proc/sys/kernel/random/boot_id` at spawn; `reattach()` marks previous-boot jobs 'interrupted' unconditionally (legacy rows fall back to pid-liveness). Two regression tests. |

### Correctness / robustness (fixed)

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| B-5 | P3 | Job watcher died silently on a transient DB error → job stuck 'running' with no reconciliation. | Watcher catches, logs, keeps polling. Test with a failing store. |
| B-6 | P3 | `SessionManager.list()` iterated the live dict while termination threads mutated it → `RuntimeError: dictionary changed size`. | Snapshot iteration. |
| B-7 | P3 | `_mark_exited` check-then-set race across threads could double-close the master fd (recycled fd → unrelated descriptor killed). | Per-session close lock + fd swap to -1. |
| B-8 | P3 | `shell_output(limit_bytes<0)` produced a backwards cursor and re-delivered old output; `job_wait` accepted unbounded timeouts (HTTP worker pinned). | Clamp at tool layer + 600 s server-side cap; negative limit = no-op with stable cursor. |
| B-9 | P3 | Redaction missed short-form DB password flags (`mysql -ppassword123`, `psql -whunter2`) in the audit trail. REPRODUCED. | New redaction rule with anti-false-positive guard (digit/symbol or ≥10 chars). |
| B-10 | P3 | `uaeg config get tls.cert` reported a legitimate null key as "unknown config key" (`_dig`'s missing_ok was dead code). | `_key_exists()` distinguishes present-null from unknown. |
| B-11 | P3 | `exec_logs` grew without bound (≤4 KiB preview per tool call). | Startup retention pass keeps the newest 20 000 rows (`Store.trim_exec_logs`; the v0.3.3 test run caught and fixed a zero-row-commit dangling-write-lock in it). Job output files remain uncapped — documented in LIMITATIONS.md. |
| B-12 | P3 | Key rotation revoked BEFORE creating the replacement: a crash between commits left zero working keys and the new secret lost. | Create-then-revoke (safer failure direction), CLI + dashboard. |
| B-13 | P3 | Dead/misleading code: `auth.UNPROTECTED_PATHS` (wrong content), `db._row_to_dict`, paths.py docstring drift. | Removed / corrected. |
| F-1 | P2 | Dashboard showed a green "updated" indicator while a tab's endpoint kept failing (stale "live" tables behind a healthy facade) — the worst possible state during incident triage. | Per-cycle truth: the indicator only goes green when every endpoint of the cycle succeeded. |
| F-2 | P3 | CSRF token fetched once at boot; a transient whoami failure silently degraded the dashboard to read-only (every action 403) until reload — and logout's server-side revocation never ran. | `api()` re-fetches the token once on 403 and retries. |
| F-3 | P3 | Enter-key double-submit on key generation created two keys; the first one-time secret was lost forever. | In-flight guard. |
| F-4 | P3 | Job-output viewer double-appended output across modal close/reopen (stale response guard incomplete). | Request-generation counter. |
| F-5 | P3 | `GET /api/whoami` 500'd (`KeyError: 'state'`) on every page load in legacy no-password mode. REPRODUCED. | Legacy payload (`authenticated: false` + guidance). |
| F-6 | P3 | `/api/sessions` could 500 when a PTY closed between list() and get() — the exact trigger of F-1's masking. | Per-session try/except. |
| F-7..F-10 | P4 | Hash not updated on tab switch (reload loses tab); lockout message hid the 15-min Retry-After; mode-toggle mutated a possibly-detached DOM node; dashboard HTML served without `Cache-Control: no-store`. | All fixed. |
| — | P4 | `/favicon.ico` 404 in the console on every page load. | Inline-SVG favicon route (200, cacheable). |

### Verified-correct areas (no action)

- Bearer-auth surface: hash-then-lookup verification, per-request revocation
  (same Mcp-Session-Id → 401 after revoke), constant-time compares,
  no secret/token leakage in any response path; SQL fully parameterized;
  SQLite usage sound (WAL, busy_timeout, schema-version guard, atomic writes).
- Job state machine honest under crashes/restarts (`running →
  completed/failed/cancelled/interrupted`); `runner.py` correct (timeout kill
  of process group, capped buffers, EOF drain).
- Both-listener fail-together supervision; fail-closed config validation.
- Frontend XSS-clean: every operator/attacker-influenced string passes
  `esc()`/`textContent`; only server-generated integers interpolate raw.
- One-time secrets shown exactly once, undismissable modal, refresh paused
  while open; destructive actions confirmed; empty states on all tabs.

## 4. Test evidence (v0.3.3)

- Python suite: **133 passed / 0 failed** (113 pre-existing + 20 new regression
  tests covering the findings above).
- Upgrade contract suite: **155 PASS / 0 FAIL** (60 migrated/extended
  assertions from T-01..T-19 on the versioned updater + 95 new assertions in
  the T-20..T-31 permanent-bootstrap series; see
  UPGRADE_COMPATIBILITY_TESTS.md).
- Real-browser verification of the fixed dashboard: login → dashboard → all
  tabs → key creation (secret modal) → logout; **0 console errors**, favicon
  200, no failed requests (`report/evidence/lab_browser_verification_v033.txt`
  + 3 screenshots).

## 5. Residual risks (accepted / documented, none release-blocking)

| Risk | Level | Notes |
|------|-------|-------|
| Agent execution = full root, no sandbox | Accepted (PRD FR-010/FR-013) | Mitigated by keys/revocation/audit; advisory paths are not a boundary |
| Background-job stdout/stderr files uncapped on disk | Accepted (documented, LIMITATIONS.md) | Sync exec is capped; jobs are operator-visible via dashboard/CLI |
| Production still runs v0.3.2 until the operator upgrades | Open (operator action) | Runbook in PRODUCTION_DEPLOYMENT.md; v0.3.2's B-1 freeze bug is one Ctrl-Z away |
| scrypt verification cost (~80 ms) on weak VPS hardware | Accepted | Documented; DoS-blunted by per-IP lockout |
