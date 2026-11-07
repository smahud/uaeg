# UAEG — Requirement Traceability

Source of truth: `docs/PRD.md` (PRD v1.0 — MVP Baseline). Every FR and the section 13/14
security/non-functional requirements are traced to implementation, tests and acceptance
evidence. Status legend: **F** = fully implemented and verified · **F\*** = implemented and
verified in-sandbox; final acceptance leg requires the authorized VPS run · **D** = documentation
obligation met.

Baseline audited: `a7a381e` (v0.1.1). Release: v0.2.0 (this commit).

## Functional requirements

| FR | Requirement | Implementation | Automated tests | Acceptance evidence | Status |
|----|-------------|----------------|------------------|---------------------|--------|
| FR-001 | Git installation with prerequisites check, deps, build, systemd registration, port/binding config, persistent config+SQLite, health verification, URL + next steps | `install.sh` (OS check, python≥3.11 check, apt deps, tar install, venv, systemd unit generation, health check loop, summary); `uaeg init` fallback | installer exercised end-to-end in isolation (AT-01 executor; CI `installer` job) | AT-01 ADAPTED (systemd leg NOT EXECUTED in container) | F\* |
| FR-002 | One-line installer from a versioned release with integrity verification; no unverified mutable script as root | `install.sh` standalone bootstrap (fetches tagged tarball, `--version`/`UAEG_VERSION` pinning); documented SHA-256 workflow in `docs/INSTALL.md` | bootstrap path executed during audit debugging (network fetch of release archive verified) | documented; operator checksum step is procedural | F |
| FR-003 | Configuration (public host, MCP port, dashboard bind/port, TLS mode, data dir, execution defaults) persists across restarts **and upgrades** | `config.py` (validated schema, atomic save); **v0.2.0: installer preserves existing config on upgrade, applies only explicit flags** | `test_config_roundtrip`, config-preservation repro + e2e restart persistence; AT-02/AT-16 | AT-02 PASS, AT-16 PASS; upgrade repro FIXED | **F** (was violated at v0.1.1 — fixed) |
| FR-004 | Standards-compatible Streamable HTTP MCP endpoint `/mcp` with full lifecycle | `app.py` (FastMCP, JSON responses, stateful sessions), `mcp_tools.py` (13 tools) | e2e protocol tests: initialize/tools list+call/session id, 13/13 tools | AT-04 protocol-equivalent executed; hosted-client leg NOT EXECUTED | F\* |
| FR-005 | Bearer auth: reject missing/invalid, CSPRNG keys, verifier-only storage, multiple keys, immediate revocation, HTTPS with client-trusted cert | `security.py` (256-bit secrets, SHA-256 verifiers), `auth.py` (per-request verification, 401 + WWW-Authenticate), `server.py` (mandatory TLS) | `test_mcp_requires_auth`, `test_key_revocation_and_second_key`, `test_full_mcp_flow`, rotation e2e | AT-03/AT-12/AT-13 PASS | F |
| FR-006 | Documented integration instructions; no false compatibility claims; built-in terminal not redirected | README + USAGE platform-compatibility sections (stdio-only / no custom headers / locked catalogs declared unsupported); tools require explicit calls | AT-19 honesty check (grep-based, in runbook executor) | AT-19 PASS | F |
| FR-007 | `shell_exec`: root exec, cwd, env, exit code, stdout/stderr, timeout, async jobs, output pagination | `executor/runner.py` (timeout→process-group kill, bounded capture), `executor/jobs.py` (detached jobs), MCP tool | `test_run_command_*`, `test_job_*`, e2e `shell_exec` flows, high-volume pagination test | AT-05/AT-07/AT-11 PASS | F |
| FR-008 | `shell_open/input/output/close`: native PTY, interactive I/O, session ids, resize, status, reconnection, explicit termination | `executor/pty.py` (real pty, session leader, ring buffer + seq cursor, SIGWINCH) | `test_pty_*`, e2e PTY flow | AT-10 PASS | F |
| FR-009 | Background jobs independent of HTTP lifetime; unique ID, pid, status, timestamps, exit code, output, cancellation; honest interrupted-after-reboot state | `executor/jobs.py` + `_jobwrap.py` (detached shim, pid/exit files), `db.py` jobs table | `test_job_lifecycle/failure/cancel/survives_manager_shutdown/reattach/interrupted_after_reboot_like_kill`, e2e restart-survival | AT-11 PASS | F |
| FR-010 | OS capabilities: packages, runtimes, files, git, builds, systemd, docker, network config, processes | Root systemd unit (intentional, PRD FR-013); execution subsystem is a full shell | AT-07 (files) PASS, AT-08 (build+test) PASS; AT-06 (apt) NOT EXECUTED (root); AT-09 NOT EXECUTED (systemd) | AT-06/09 outstanding | F\* |
| FR-011 | `uaeg generate [--name]`; unique key, default permission "all"; secret displayed exactly once | `cli.py cmd_generate`, `security.new_key` | `test_secret_format`, `test_new_key_unique`; AT-03 checks one-time display | AT-03 PASS | F |
| FR-012 | `uaeg keys list/revoke/rotate`, `status`, `doctor`; metadata (ID, name, prefix, created, last used, status, permission mode); no recoverable plaintext keys | `cli.py`, `db.py` keys table (verifier only), doctor/status commands; **v0.2.0: revocation containment** | `test_store_keys_lifecycle`, e2e revocation + rotation tests | AT-12 PASS (incl. containment) | F |
| FR-013 | Default permission mode "all": authenticated keys execute root commands unless changed | `db.py` permission_mode; `mcp_tools.py` enforcement-free default; `runtime.check_restricted_cwd` advisory gate for restricted keys | e2e (all flows run unrestricted by default) | AT-05 | F |
| FR-014 | Optional advisory restricted directories; communicated as usage rules; explicitly not a security boundary | `runtime.usage_rules()` + `check_restricted_cwd()`; warnings in doctor/config; SECURITY.md/LIMITATIONS.md statements | config validation warning test; docs | — | F |
| FR-015 | HTTP dashboard on private LAN interface + custom port; no login; never on all interfaces by default; network-level restriction; loopback fallback | `dashboard.py` + `config.py` (wildcard refused without explicit `allow_public`), optional CIDR allowlist (v0.2.0: fail-closed), installer LAN/RFC1918 logic | `test_config_validation_rules`, CIDR unit + middleware tests, `test_dashboard_api` | AT-14 ADAPTED, AT-15 ADAPTED (CIDR 403 verified) | F\* |
| FR-016 | Dashboard: Keys (generate/revoke/metadata/permissions), Permissions (mode + advisory paths), Sessions (active+history, jobs, terminate), Logs (timestamps, identity, command metadata, status, exit code, output inspection), Server (endpoints, health, version, network/TLS status) | `dashboard.py` 15 routes + `dashboard_ui.py` (5 tabs; API surface byte-audited against the UI) | `test_dashboard_api`, containment e2e via dashboard | AT-14/AT-17 PASS | F |
| FR-017 | Direct HTTPS with domain or public IP + configurable port; certbot automation when possible; trusted cert for IP installs; **never silent insecure HTTP fallback** | `server.py` `_build_ssl_context` (refuses to start without valid certs; explicit `UAEG_DEV_INSECURE_HTTP=1` dev-only escape hatch), installer certbot/manual/self-signed provisioning + renewal hook | fail-closed TLS tests (corrupt cert → clean exit 2); healthz over TLS everywhere | AT-01/AT-02 (TLS served); certbot drill NOT EXECUTED (needs DNS+port 80) | F\* |
| FR-018 | Network separation: MCP public+authenticated; dashboard private, network-restricted, no login; no shared public unauthenticated admin route | `server.py` dual listeners, `auth.py` scope-limited to `/mcp`, dashboard bind validation + CIDR middleware | auth scope tests, CIDR middleware tests, AT-15 | AT-15 ADAPTED | F\* |
| FR-019 | systemd service with automatic restart; state outside the install dir | generated unit (`Restart=on-failure`), `/etc/uaeg` + `/var/lib/uaeg` layout; **v0.2.0: listener failures exit non-zero so restart supervision actually works** | supervision tests (bind conflict, crash → exit 1) | AT-09 NOT EXECUTED (no systemd in container) | F\* |
| FR-020 | `uaeg status`, `uaeg doctor`, health endpoint, service logs, clear errors, documented recovery | `cli.py` status/doctor (v0.2.0: DB integrity, SAN, clean root explanation), `/healthz`, `logging_util` + TROUBLESHOOTING.md recovery procedures (incl. DB corruption) | doctor exercised in AT-01; corruption clean-error test | AT-01 ADAPTED | F |

## Section 13 — security baseline

| Requirement | Status | Evidence |
|---|---|---|
| HTTPS for public MCP access | F | mandatory TLS; dev escape hatch explicit+loud; fail-closed tests |
| Strong, independently generated API keys | F | CSPRNG 256-bit, unit-tested format |
| No anonymous public execution | F | per-request bearer auth on `/mcp` only |
| Immediate key revocation | F | per-request DB lookup; **v0.2.0 adds process containment** |
| Private dashboard network binding | F | wildcard refusal + CIDR allowlist (v0.2.0 fail-closed) + installer RFC1918 logic |
| Basic execution audit records | F | exec_logs with identity/tool/status/exit/duration/preview |
| Protection of key material in storage | F | SHA-256 verifiers only, 0600 db/config |
| No sensitive credentials in ordinary logs | **F (v0.2.0)** | redaction layer at all persistence choke points; env values never logged (AT-17-verified) |

## Section 14 — non-functional requirements

| Requirement | Status | Evidence |
|---|---|---|
| Debian 13 support | F\* | audit host is Debian 13 (trixie); OS check in installer; systemd leg pending VPS |
| Concurrent authenticated MCP connections | F | `test_concurrent_clients_and_key_ops` (6 parallel clients + dashboard key ops) |
| Multiple agent keys | F | AT-13 + unit + e2e multi-key tests |
| No session identifier collisions | F | UUID4 hex ids (design) + collision-free across all tests |
| Bounded in-memory output buffers | F | `_CappedBuffer` (1 MiB/stream), PTY ring buffer cap; truncation flags tested |
| Persist job metadata and available output | F | files + SQLite; restart survival tests |
| Disconnected clients don't kill background jobs | F | AT-11 + `test_job_survives_manager_shutdown`/server restart |
| Clear diagnostic errors | F (v0.2.0) | fail-closed clean errors for config/TLS/DB/listeners; doctor guidance |
| Install/remove without destroying unrelated VPS resources | F | uninstaller keeps config/data unless `--purge`; installer confined to its paths |
| Minimal dependencies/operation | F | 3 pinned runtime deps; single process; single SQLite file |

## Acceptance tests (section 15) mapping

AT-01..AT-19 → implementation/test/acceptance detail in
[VPS_ACCEPTANCE_RESULTS.md](VPS_ACCEPTANCE_RESULTS.md) and [TEST_EVIDENCE.md](TEST_EVIDENCE.md).
Definition of Done (section 17) items 1–5, 7, 8 are met by the evidence above; item 6
("required acceptance tests pass") is **partially met** pending the authorized real-VPS run —
see [RELEASE_DECISION.md](RELEASE_DECISION.md).
