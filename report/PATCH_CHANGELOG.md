# Patch Changelog — v0.3.2 → v0.3.3

**Date:** 2026-11-08 · **Commit range:** v0.3.2 (9da477e) → v0.3.3 ·
Every change below is covered by a regression test or the extended contract
suite unless marked otherwise.

---

## A. Execution-subsystem fixes (src/uaeg/executor/)

### pty.py
- **B-1 (P1): non-blocking PTY master + queued-input drain.** The master fd
  is set `O_NONBLOCK` at open. `TerminalSession.write()` never blocks: a
  short write or `EAGAIN` queues the remainder in a bounded backlog
  (≤ `_MAX_PENDING_INPUT` = 1 MiB) which `SessionManager` drains via
  `loop.add_writer` when the tty becomes writable; exceeding the backlog
  raises a descriptive `SessionError` (backpressure) instead of hanging the
  service. `_on_readable` treats `BlockingIOError` as a spurious event, not
  an exit. Fixes the whole-service freeze when input is sent to a suspended
  (Ctrl-Z) session. Test: `test_pty_write_to_suspended_session_does_not_block_loop`,
  `test_pty_write_normal_session_delivers_input`.
- **B-6: snapshot iteration in `SessionManager.list()`** (dict-mutation race
  with termination workers).
- **B-7: atomic session close.** `_mark_exited` now performs the
  closed-check-then-set + fd swap under a per-session `threading.Lock`; the
  master fd is set to -1 so no other path can reuse the number; the close
  callback removes both the reader and the input-drain writer and clears the
  backlog.
- **B-8: `read_since` non-positive limit** returns no data with a stable
  (never backwards) cursor.

### jobs.py
- **B-3: cancellation escalation on real liveness.** `cancel()` decides
  SIGTERM→SIGKILL escalation from actual process-group liveness (new
  zombie-aware `_group_alive()` scanning `/proc`), not from the DB status it
  had just written. Test: `test_job_cancel_kills_term_ignoring_command`.
- **B-4: boot-id stamping.** `spawn()` writes the kernel boot id into the
  job dir; `reattach()` marks previous-boot jobs `interrupted`
  unconditionally (pid-recycling guard); legacy rows without a stamp fall
  back to pid liveness. Tests: `test_reattach_marks_previous_boot_job_interrupted`,
  `test_spawn_writes_current_boot_id`.
- **B-5: watcher exception guard.** `_watch` catches and logs non-cancel
  exceptions and keeps polling. Test: `test_job_watcher_survives_check_exception`.

## B. Auth / containment / storage fixes

- **B-2 (containment.py):** `key_resources()` uses `store.open_sessions()`
  (unbounded, filtered server-side conceptually) instead of
  `list_sessions(limit=1000)`. Test:
  `test_containment_key_resources_sees_old_open_sessions` (1100 noise rows).
- **B-12 (cli.py + dashboard.py): key rotation now creates the replacement
  key BEFORE revoking the old one** in both the CLI and dashboard paths — a
  crash can no longer leave zero working keys. Test:
  `test_rotation_failure_keeps_old_key_active`.
- **B-9 (security.py):** new redaction rule for short-form CLI password
  flags (`mysql -ppassword123`, `psql -whunter2`) with an anti-false-positive
  guard (value must contain a digit/symbol or be ≥10 chars; ordinary flags
  like `-print`/`-append` untouched). Tests: parametrized
  `test_redaction_short_flag_passwords`, `test_redaction_preserves_ordinary_flags`.
- **B-10 (cli.py):** `_key_exists()` added; `uaeg config get` distinguishes
  existing-but-null keys from unknown keys (the dead `missing_ok` branch is
  gone). Test: `test_dig_key_exists_semantics`.
- **B-11 (db.py + server.py):** `Store.trim_exec_logs(keep=20_000)` runs once
  at startup; **always commits** (a zero-row delete without commit left a
  dangling write transaction that locked the database for every other writer
  — caught by the v0.3.3 test run itself and fixed). Test:
  `test_trim_exec_logs_retention`.
- **B-13:** dead code removed (`auth.UNPROTECTED_PATHS`, `db._row_to_dict`),
  paths.py docstring corrected (sessions/ is reserved; records live in the
  DB).

## C. MCP tool layer (mcp_tools.py)

- **B-8 follow-ups:** `shell_output` clamps `limit_bytes` to [0, 8 MiB] at
  the tool boundary; `job_wait` clamps `timeout_seconds` to [0, 600] with a
  numeric-coercion fallback (an agent can no longer pin an HTTP worker
  indefinitely). Test: `test_job_wait_timeout_is_clamped_by_tool`.

## D. Dashboard backend (dashboard.py)

- **F-5:** `GET /api/whoami` returns a proper legacy-mode payload
  (`authenticated: false`, guidance) instead of `KeyError` → 500. Test:
  `test_whoami_legacy_mode_returns_payload`.
- **F-6:** `/api/sessions` skips sessions that vanished between `list()` and
  `get()` instead of 500-ing the endpoint. Test:
  `test_sessions_endpoint_skips_vanished_session`.
- **F-10:** dashboard HTML served with `Cache-Control: no-store`.
- **Favicon route** `/favicon.ico` (inline SVG, 200, cacheable) — removes the
  console 404 on every page load.

## E. Dashboard frontend (dashboard_ui.py)

- **F-1 (P2):** connection indicator is now only green when EVERY endpoint
  of the refresh cycle succeeded (`markHealthy()`/`lastTabError`); a passing
  `/api/overview` can no longer mask a failing tab. All five loaders
  migrated.
- **F-2:** `api()` transparently re-fetches the CSRF token once on a 403 for
  state-changing calls and retries — a transient boot-time whoami failure no
  longer degrades the dashboard to read-only (and logout revocation now
  always reaches the server).
- **F-3:** key-generation in-flight guard covers the Enter-key path — double
  submit can no longer create two keys and destroy the first one-time
  secret.
- **F-4:** job-output viewer uses a request-generation counter — stale
  responses from a closed viewer can no longer double-append output.
- **F-7:** tab switches update the URL hash via `history.replaceState`.
- **F-8:** login lockout message surfaces the server's `Retry-After`
  (minutes) instead of "a moment".
- **F-9:** permission-mode toggle reloads the keys table instead of mutating
  a DOM node a periodic re-render may have detached.
- Favicon `<link>` in both templates.

## F. Permanent upgrade architecture (contract v3)

- **`upgrade.sh` REWRITTEN as the stable operator bootstrap** (~380 lines):
  release-channel target resolution (GitHub Releases API — never `main` by
  default), manifest download, **Ed25519 signature verification against the
  embedded trusted keyring**, structural manifest validation (safe updater
  path, version match, interface ≤ supported), updater download with
  **SHA-256 + size verification against the signed manifest**, execution with
  the documented interface env, exit-code propagation, temp cleanup,
  post-run self-hash assertion. `--bootstrap-info` introspection. Offline
  `--rollback`/`--check` from the cached updater. All v0.3.2 flags preserved.
- **`updater.sh` NEW — the versioned safe-upgrade updater** (the v0.3.2
  machinery, contract v3): bootstrap-interface handshake, `UAEG_RELEASE_TAG`
  target trust, self-caching to the upgrade cache (0700, atomic) on success,
  best-effort system bootstrap refresh at `/usr/local/sbin/uaeg-upgrade`,
  extended `RECORD.json` (contract 3, interface, bootstrap provenance). Fully
  usable standalone.
- **Release signing:** `release/keys/uaeg-release-1.pub.pem` (committed);
  `release/make_release.py` (build+sign manifests; private key never
  committed); `release/verify_release.py` (independent verification used by
  CI and operators); signed manifest for v0.3.3 under
  `release/manifests/v0.3.3/`.
- **`.github/workflows/release.yml`:** verifies the manifest signature and
  every asset hash BEFORE publishing; publishes 8 assets (install.sh,
  upgrade.sh, uaeg-updater-0.3.3.sh + sidecars + manifest + signature);
  smoke-checks all documented URLs.
- **`install.sh`:** pins 0.3.3; installs the system bootstrap convenience
  copy `/usr/local/sbin/uaeg-upgrade`.
- **CI (`ci.yml`):** installer smoke patches the new system-bootstrap path;
  regression suite added to the unit-test step.

## G. Tests

- `tests/test_unit_regression_v033.py` — 20 regression tests (B-1..B-12,
  F-5, F-6).
- `tests/test_upgrade_contract.sh` — migrated to the updater (T-01..T-19)
  + new permanent-bootstrap series T-20..T-31 (signature/tamper/foreign-key/
  interface/missing-manifest rejections, multi-release compatibility with a
  CHANGED updater implementation, offline rollback, retry-after-failure).

## H. Documentation

- `docs/BOOTSTRAP.md` (new): architecture, binding interface v1, trust
  model, key-rotation policy, migration, test coverage.
- `docs/UPGRADE_CONTRACT.md`: contract v3 with new clauses U-13 (permanent
  bootstrap) and U-14 (verified authenticity/integrity).
- `docs/INSTALL.md`, `README.md`: one-time bootstrap download flow.
- `docs/SECURITY.md`: release-authenticity section.
- `docs/TROUBLESHOOTING.md`: signature/key-rotation recovery, offline
  rollback.
- `docs/LIMITATIONS.md`: job-output-files-uncapped note (B-11 remainder).

## I. Version

`0.3.2` → `0.3.3` (pyproject.toml, `__version__`, installer pin, README).
