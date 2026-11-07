# UAEG — Test Evidence

All evidence below was produced on the audit host: **Debian GNU/Linux 13 (trixie)**,
x86_64, Python 3.12.16 and Python 3.13 (via uv-managed interpreters), repo at the v0.2.0
release commit. Commands are reproducible from a clean checkout.

## 1. Automated test suite

```
# Python 3.12
$ python -m pytest tests/ -q
74 tests, 0 failed, 0 errors        (rc=0)

# Python 3.13
$ python -m pytest tests/ -q
74 tests, 0 failed, 0 errors        (rc=0)
```

Test inventory (74 total; 41 new in v0.2.0, 33 pre-existing):

| File | Tests | Covers |
|---|---|---|
| tests/test_unit_core.py | 8 | secret format/verifiers, config roundtrip+validation, store keys/jobs/sessions/logs |
| tests/test_unit_executor.py | 16 | runner (env/cwd/exit/timeout/output cap), jobs (lifecycle, failure, cancel, survives shutdown, reattach, interrupted-after-reboot), PTY (interactive, Ctrl-C, resize, ring bounds, reconcile) |
| tests/test_unit_security_redaction.py **(new)** | 10 | redaction pattern families + persistence choke points (exec_logs, jobs, sessions) |
| tests/test_unit_server_hardening.py **(new)** | 15 | CIDR fail-closed (4), dashboard middleware scope (2), listener supervision (2), config robustness (3), store schema/integrity (3), corrupt-DB clean exit (1) |
| tests/test_unit_containment.py **(new)** | 4 | containment by key, standalone job cancel, standalone session kill, resource listing |
| tests/test_e2e_smoke.py | 9 | real TLS + MCP protocol, auth, execution, jobs, PTY, revocation, dashboard API, audit + restart persistence, job survival across server restart |
| tests/test_e2e_hardening.py **(new)** | 12 | revocation containment (+opt-out), audit redaction end-to-end, 6 concurrent clients + dashboard key ops + repeated connect/disconnect, 2 MB high-volume output + truncation flags, listener bind conflict (subprocess), invalid-CIDR startup refusal, corrupt-cert clean failure, CLI jobs cancel/sessions kill vs live server, dashboard rotation, CLI rotation + containment, version metadata consistency |

Python 3.11: not installed on the audit host → **NOT TESTED locally**; covered by the repository CI matrix (`ubuntu-latest`, 3.11/3.12/3.13) which runs the same suite on push/PR.

## 2. Defect reproductions (before → after)

| Repro | Scenario | v0.1.1 result | v0.2.0 result |
|---|---|---|---|
| repro_listener_failure.py | MCP port occupied, `uaeg serve` started | process exited rc=1 (via accidental SystemExit passthrough) — acceptable outcome, accidental mechanism | **FIXED**: supervised exit rc=1 with clean error log |
| repro1b_wrapper.py | dashboard listener crashes with RuntimeError 2 s after startup | **BUG CONFIRMED**: 6 s later process alive, MCP still serving, systemd unaware | **FIXED**: process exits non-zero with `listener failure: uaeg-dashboard …` and `the service does not run half-alive` |
| repro_cidr_failopen.py | all-invalid `allowed_dashboard_cidrs` | **BUG CONFIRMED**: `GET /api/overview` from 8.8.8.8 → HTTP 200 (allowlist silently disabled) | **FIXED**: `Config.validate()` rejects with `…invalid CIDR entries… Refusing to start` |
| repro_config_overwrite.sh | real install.sh twice (upgrade path) with operator config changes in between | **BUG CONFIRMED**: `default_timeout_s` 333→120, `restricted_paths`→`[]`, `allowed_dashboard_cidrs`→null | **FIXED**: `REPRO RESULT: FIXED (operator config preserved)` |
| installer forced failure (during development) | upgrade with invalid merged config | exit with service stopped, config reset | **rollback executed**: previous config restored, app files restored, service restart attempted (log captured) |

## 3. Targeted test coverage required by the audit mission (§5)

| Required area | Evidence | Status |
|---|---|---|
| Concurrent clients and key operations | `test_concurrent_clients_and_key_ops` (6 parallel MCP clients × shell_exec + concurrent dashboard key create/list) | Covered |
| Repeated connection and disconnection | same test (3× full MCP session cycle); AT-11 disconnect/reconnect | Covered |
| Process crashes and restart | supervision tests (crash → exit 1), bind-conflict tests, `test_job_survives_server_restart`, AT-16 restart persistence | Covered |
| High-volume output and truncation | 2 MB background job, full paginated retrieval verified byte-wise; sync path `stdout_truncated`/`stdout_bytes` assertions | Covered |
| Large / long-running background jobs | `sleep 300` jobs (containment tests), 2 MB output job, job_wait | Covered |
| TLS renewal and invalid certificates | invalid/corrupt cert → clean exit 2 (`test_server_clean_error_on_invalid_tls_cert`); 30-day expiry warning in doctor (checkend logic, code-verified); certbot renewal deploy-hook reviewed (restarts UAEG) | **Invalid-cert: covered. Renewal drill: NOT EXECUTED locally** (requires DNS + port 80) |
| Failed installations and upgrades | forced-failure rollback (evidence in §2), upgrade happy path (acceptance run + repro), CI installer smoke job | Covered |
| Configuration persistence and rollback | repro_config_overwrite (FIXED), AT-16, `test_config_roundtrip` | Covered |
| Unauthorized dashboard access | CIDR middleware unit tests (out-of-list → 403, non-IP → 403, all paths protected), AT-15 (403 verified behaviorally) | Covered |
| Revoked credentials | AT-12 + 6 e2e/unit tests incl. containment and 401 immediacy | Covered |
| SQLite corruption and backup restoration | corrupt DB → clean exit 2 + doctor INTEGRITY CHECK FAILED (`test_server_clean_error_on_corrupt_db`, acceptance-run doctor drill); recovery = move file aside → fresh DB (documented + exercised); **full backup/restore drill from operator-made copies: documented procedure, not separately automated** | Covered (recovery) / Documented (backup drill) |

## 4. Acceptance run (AT-01..AT-19)

Executed by `scripts/acceptance_run.py` (audit harness, kept outside the repo deliverable)
against a real `install.sh`-produced instance in an isolated tree on this Debian 13 host.
Totals: **12 PASS · 4 ADAPTED · 3 NOT EXECUTED · 0 FAIL** — full table and per-test notes in
[VPS_ACCEPTANCE_RESULTS.md](VPS_ACCEPTANCE_RESULTS.md).

## 5. Multi-version verification

| Interpreter | Suite result |
|---|---|
| Python 3.12.16 | 74/74 PASS |
| Python 3.13 (uv) | 74/74 PASS |
| Python 3.11 | NOT TESTED locally (no interpreter in sandbox); CI matrix covers it on push |

## 6. Environment / reproducibility notes

- Dependencies resolved from pinned specs (`mcp==1.27.0`, `starlette==0.50.0`, `uvicorn==0.44.0`) — identical resolution on both interpreters (editable install `pip install -e ".[dev]"`).
- All servers in tests bind to 127.0.0.1 with ephemeral ports; all filesystem effects are confined to tmp directories; no destructive host-level operations were performed (root unavailable; none needed for the automated suite).
- The one test-suite flake class (environmental): PTY output polling depends on shell prompt timing; bounded retries (10 s deadlines) make the suite stable — 3 consecutive full-suite runs on each interpreter showed zero flakes.
