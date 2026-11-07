# UAEG — Security Findings

Scope: security-relevant findings from the production-readiness audit of `smahud/uaeg`
(baseline `a7a381e`, v0.1.1) and their disposition in v0.2.0. Every finding lists the
affected component, evidence, severity (P0..P3), and disposition. **Confirmed defect**
means reproduced or proven from code; **risk** means a design property accepted and
documented rather than a defect.

## 1. Confirmed defects (all fixed in v0.2.0)

### SEC-01 — Dashboard CIDR allowlist failed open (P1)
- **Component**: `src/uaeg/dashboard.py` (`build_dashboard_asgi_app`), `src/uaeg/config.py` (validation gap).
- **Evidence**: with `network.allowed_dashboard_cidrs = ["192.168.99.0/not-a-cidr", "bogus"]` every entry failed to parse; the parsed list was empty; the CIDR middleware was then **not installed at all** and `GET /api/overview` from `8.8.8.8` returned HTTP 200 (REPRO-2, reproduced on v0.1.1). An operator who believed access was restricted had, in effect, no restriction.
- **Fix**: `Config.validate()` now rejects any invalid entry at startup (fail closed, clear error); the dashboard builder raises `ConfigError` on invalid entries as defense in depth (covers programmatic construction); the middleware blocks every path for out-of-list or non-IP peers.
- **Regression**: `test_config_invalid_cidr_fails_closed`, `test_config_all_invalid_cidrs_rejected`, `test_config_cidrs_wrong_type_rejected`, `test_dashboard_cidr_middleware_blocks_out_of_list`, e2e invalid-CIDR startup refusal.
- **Behavior note**: a config written by pre-0.2.0 with invalid entries will refuse to start after upgrade until the entries are fixed or nulled — intentional for a security control; documented in TROUBLESHOOTING.md.

### SEC-02 — Listener failure left a half-running service (P1)
- **Component**: `src/uaeg/server.py` (`_amain`).
- **Evidence**: a listener task dying with a regular exception was never observed; `await stop` blocked forever while the other listener kept serving; the process stayed alive so systemd considered the service healthy (REPRO-1b: 6 s after a simulated dashboard crash — process alive, MCP still serving, no restart). Bind-failures happened to exit via CPython's `SystemExit` task passthrough — an implementation detail, not a design guarantee.
- **Fix**: both listeners run as supervised tasks with done-callbacks; any failure (bind error converted from uvicorn's `sys.exit`, crash, unexpected return) stops the other listener, logs the cause, and exits non-zero so `Restart=on-failure` engages. Exceptions are always retrieved (no silent task deaths).
- **Regression**: `test_amain_returns_nonzero_when_listener_crashes`, `test_amain_returns_nonzero_on_bind_conflict`, e2e `test_server_exits_nonzero_on_listener_bind_conflict`.

### SEC-03 — Revocation did not contain the revoked key's processes; documented operator remediation was non-functional (P2)
- **Component**: `src/uaeg/cli.py` (stubs), `src/uaeg/dashboard.py`/`db.py` (revoke without termination), docs (USAGE/README/TROUBLESHOOTING promised working commands).
- **Evidence**: no termination call existed in any revoke path; `uaeg jobs cancel` / `uaeg sessions kill` printed an explanation and returned exit 1 (verified in code + doc audit). A revoked (possibly compromised) agent's shells and jobs kept running.
- **Fix**: revocation containment — `Runtime.terminate_key_resources` (in-server: graceful PTY close via SIGHUP→SIGKILL, job cancel via SIGTERM→SIGKILL) for the dashboard path; `uaeg.containment.terminate_key_processes` (out-of-process process-group signaling) for the CLI path. Opt-outs: CLI `--keep-processes`, dashboard body `{"keep_processes": true}`. Rotation is explicitly **not** containment (continuity). Real CLI `jobs cancel` / `sessions kill` implemented. Containment writes an auditable `revocation`/`contained` entry to exec_logs.
- **Regression**: `test_terminate_key_processes_kills_sessions_and_jobs`, `test_cancel_job_standalone`, `test_kill_session_standalone`, e2e `test_revocation_terminates_key_processes`, `test_revocation_keep_processes_opt_out`, `test_cli_jobs_cancel_and_sessions_kill`, `test_cli_key_rotation_and_revoke_containment`.

### SEC-04 — Secrets embedded in command text persisted verbatim (P2)
- **Component**: `src/uaeg/db.py` (`add_log`, `insert_job`, `insert_session`), `src/uaeg/executor/{jobs,pty}.py` (journal lines), `src/uaeg/dashboard.py` (session previews).
- **Evidence**: agents routinely embed third-party credentials in commands (`curl -H "Authorization: Bearer …"`, `git clone https://user:pass@…`). All of it landed verbatim in exec_logs (dashboard Logs tab), jobs/sessions tables (dashboard + CLI display), session preview tails and the systemd journal — violating the PRD section 13 line "No sensitive credentials in ordinary logs" for the journal, and exposing secrets to anything that can reach the dashboard.
- **Fix**: `security.redact_secrets()` (12 high-confidence rule families: auth headers, URL credentials, `uaeg_`/`ghp_`/`sk-`/`AKIA`/`xox*`/`AIza` tokens, JWTs, PEM private-key blocks, `password=`/`token=`/`api_key=`/`aws_secret_access_key=`-style assignments) applied at every persistence choke point. Best-effort by design; redaction errs toward masking.
- **Deliberately NOT redacted** (documented in SECURITY.md): raw job output files under `/var/lib/uaeg/jobs/<id>/` and the authenticated agent's own `job_output`/`shell_output` responses — the operator (root) retains full forensic access, the agent sees its own output, while LAN-dashboard and journal surfaces are sanitized.
- **Regression**: 10 pattern unit tests + persistence choke-point tests + full-path e2e (`test_audit_log_redaction_e2e`, incl. dashboard `/api/logs` verification).

### SEC-05 — Upgrades reset operator configuration, silently discarding security settings (P1)
- **Component**: `install.sh` configuration section (v0.1.1).
- **Evidence**: REPRO-3 — a documented upgrade reset `exec.default_timeout_s` 333→120, `restricted_paths`→`[]`, and `allowed_dashboard_cidrs` `["192.168.1.0/24"]`→`null`. Losing the CIDR allowlist silently reopens the dashboard access control the operator had configured; violates FR-003.
- **Fix**: upgrades load and preserve the existing config; only flags explicitly passed on the upgrade invocation are applied; validation precedes saving.
- **Regression**: config-preservation repro (FIXED) + installer exercised twice in the acceptance run.

### SEC-06 — Installer left broken state on mid-upgrade failure (P2)
- **Component**: `install.sh` (no rollback; default-port health check).
- **Evidence**: with `set -euo pipefail`, any failure after stopping the service (pip failure, invalid merged config, failed health check) exited leaving the service stopped and the app dir half-replaced. The health check polled the *default* port, so customized installs failed spuriously and would have triggered needless rollbacks/aborts.
- **Fix**: rollback EXIT trap (restores config backup, app-dir backup `/opt/uaeg.upgrade-bak-<ts>`, restarts the previous service when it was active); health check and summary use the *effective* values from the preserved config.
- **Regression**: forced-failure repro during development (config + app restored, service restarted); upgrade happy path re-verified.

### SEC-07 — `--dashboard-bind lan` could auto-bind the no-login dashboard to a public address (P2)
- **Component**: `install.sh` `resolve_dashboard_bind`.
- **Evidence**: the "lan" branch used the default-route source address (or `hostname -I | head -1`) — on single-NIC public VPSs (the common case) that is the **public** IP; docs claimed a private IP was chosen.
- **Fix**: RFC1918 addresses are preferred; if none exists and the only candidate is public, the installer refuses to auto-bind and instructs the operator (loopback / explicit private IP / add a private interface).
- **Regression**: installer logic exercised (loopback path) in acceptance run; public-refusal branch code-reviewed (cannot be exercised on this single-interface container without a public address).

### SEC-08 — Dead dev escape hatch / unclean fail states (P3, aggregate)
- `UAEG_DEV_INSECURE_HTTP=1` documented but unreachable (now functional, dev-only, loud warning); corrupt TLS material produced raw tracebacks (now clean `ConfigError`, exit 2); corrupt DB produced tracebacks (now clean error + recovery pointer + `PRAGMA quick_check` in doctor + schema-version guard); `Config.from_dict` crashed on unknown fields (now clear `ConfigError`); `True` passed port validation (now rejected).
- **Regression**: `test_server_clean_error_on_invalid_tls_cert`, `test_server_clean_error_on_corrupt_db`, `test_store_rejects_newer_schema_version`, `test_config_from_dict_rejects_unknown_fields`, `test_config_rejects_bool_port`.

## 2. Accepted risks (by design, documented — not defects)

| Risk | Rationale / documentation |
|---|---|
| Authenticated agents receive full root execution | Core product capability (PRD FR-010/FR-013, section 2); SECURITY.md warning block; dedicated disposable VPS is the intended environment |
| Advisory restricted paths are not a security boundary | PRD FR-014; enforced isolation out of MVP scope; LIMITATIONS.md |
| Dashboard has no login; network placement is the boundary | PRD FR-015/FR-018; wildcard bind refused; optional CIDR allowlist (fail-closed since v0.2.0); operator must not expose port 3903 |
| An open SSE stream authorized before revocation continues until the client closes | Per-request auth blocks all new requests; long-lived stream teardown is not implemented in the MVP (documented) |
| Job/session inspection is not key-scoped (any authenticated key can query any job/session id) | Trusted single-operator model (PRD section 3); ids are unguessable UUIDs; documented in LIMITATIONS.md |
| Raw job output files and agent-visible `job_output` are unredacted | Redaction targets UAEG's own logs/metadata; the operator is root; the agent sees its own output |
| Process-group signaling cannot terminate processes that escaped their group (setsid etc.) | Containment is best-effort; documented in LIMITATIONS.md |
| Self-signed TLS will be rejected by hosted clients | Documented everywhere; certbot/manual modes provided |
| Redaction is pattern-based (best effort) | Unrecognized secret shapes may pass; SECURITY.md advises `env` for secrets |

## 3. Verification of security controls (release gates 5)

- **Dashboard exposure**: wildcard bind refused by config validation (tested); CIDR allowlist enforced on every path with fail-closed parsing (tested); AT-15 denial behavior verified (403 for out-of-list source; bound to 127.0.0.1 only).
- **Authentication controls**: 401 + `WWW-Authenticate` for missing/invalid/revoked tokens (tested); verifier-only storage (tested); revocation immediate + containing (tested); rotation issues new credentials and kills nothing (tested).
- **No credentials in source, commits or reports**: verified — the audit introduced no secrets; test fixtures use generated material only.
