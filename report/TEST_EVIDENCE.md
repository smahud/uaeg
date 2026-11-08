# Test Evidence — v0.3.3

**Date:** 2026-11-08 · **Environment:** isolated sandbox (Debian, Python
3.12.16, bash 5.2, OpenSSL 3.5.7), real venv/pip installs, real TLS, real
browser (Chromium 157 via Playwright), real Ed25519 signatures. Raw logs
under `report/evidence/`.

---

## 1. Automated matrix — summary

| Suite | Result | Evidence file |
|---|---|---|
| Python tests (`pytest tests/`) | **133 passed / 0 failed** (113 pre-existing + 20 new regression) | `evidence/pytest_v033.txt` |
| Upgrade contract (`tests/test_upgrade_contract.sh`) | **155 PASS / 0 FAIL** (T-01..T-19 + T-20..T-31) | `evidence/upgrade_contract_suite_v033.txt` |
| Release manifest verification (`release/verify_release.py`) | **PASS** (signature + 3 asset hashes + interface) | `evidence/manifest_verification_v033.txt` |
| Syntax gates | `bash -n` clean on upgrade.sh / updater.sh / install.sh / contract suite | (session log) |

### Python coverage by area

- **Auth & dashboard:** login/logout/session expiry/cookie/CSRF/rate-limit/
  password rotation (e2e, real TLS); public-bind fail-closed guards;
  legacy-mode whoami (new); sessions-endpoint race (new); dashboard API
  auth on every route.
- **Executor:** sync runner (timeouts, caps, cwd/env), PTY open/input/
  output/resize/close/sessions, **non-blocking write into a SIGSTOPed
  session (new B-1 regression)**, backlog cap, background jobs
  (spawn/status/output/wait/cancel/**TERM-ignoring kill (new B-3)**,
  reattach/**previous-boot interruption (new B-4)**, watcher survival (new
  B-5)), containment (**>1000-row open-session visibility (new B-2)**),
  rotation failure ordering (new B-12), exec_logs retention (new B-11).
- **Security:** redaction matrix incl. short-flag passwords (new B-9);
  constant-time compare; secret non-leakage.
- **MCP e2e:** full protocol flow (initialize → tools/list → tools/call)
  with real TLS + bearer keys, revocation immediacy, restart survival,
  hardening scenarios (concurrent clients, high-volume output, CLI parity).

### Contract coverage

See UPGRADE_COMPATIBILITY_TESTS.md for the full clause→test map (U-1..U-14).

## 2. Real-browser verification (fixed dashboard, v0.3.3)

Lab mirroring production intent: `dashboard.bind=0.0.0.0`, TLS, password
login; fresh browser profile (no cached credentials); Chromium 157.

**Result matrix** (`evidence/lab_browser_verification_v033.txt`):

| Check | Result |
|---|---|
| Unauthenticated `/` | 302 → `/login` ✓ |
| Login page renders (password form) | ✓ |
| Wrong password | 401 API + visible error, stays on /login ✓ |
| Correct login | redirect to `/` dashboard ✓ |
| All 5 tabs render (keys/permissions/sessions/logs/server) | ✓ |
| URL hash follows tab selection (F-7) | `#server` ✓ |
| Key creation via UI → one-time secret modal → acknowledgment | ✓ (modal open; key row present) |
| `/api/whoami` | 200 ✓ |
| Logout (Sign out button) | POST /logout → `/login` ✓ |
| **Console errors** | **0** (favicon now 200 — was 404 on v0.3.2) |
| Page errors / failed requests | 0 / 0 |

Screenshots: `evidence/uaeg_v033_dashboard_keys.png`,
`evidence/uaeg_v033_dashboard_server.png`,
`evidence/uaeg_v033_after_logout.png` (content-verified non-blank:
21.8% / 68.3% / 8.0% non-background pixels).

## 3. Production probes (live evidence)

- `evidence/production_network_probe_v033.txt` — DNS, port matrix
  (22/80/443 open; 3902 refused→open transition; 3903 refused), control
  probes proving sandbox egress.
- `evidence/production_mcp_probe_v033.txt` — live `/healthz` (UAEG v0.3.2,
  uptime 7625 s), unauthenticated `/mcp` → 401 (auth enforced), Let's
  Encrypt certificate details (valid to 2027-02-05, SAN match).

## 4. Negative/failure-injection results (from the contract suite)

Tampered manifest → refused · tampered updater → refused · rogue-key
signature → refused · interface-too-new → refused with instructions ·
missing manifest → clear error · broken release → auto-rollback (exit 2) ·
retry after failure with same bootstrap → success · offline rollback →
success · silent downgrade → refused (exit 5) · dirty/untracked operator
tree → upgrade succeeds, tree byte-identical · secrets never in logs/records.

## 5. Known test-environment deviations (honesty)

- The contract suite disables the root check and apt via sed (same as the
  project's pre-existing CI pattern); all other machinery (backup, staging,
  install, verification, rollback, signatures) runs exactly as production.
- The release channel + signing key are simulated locally in tests (real
  OpenSSL Ed25519); the production channel is GitHub Releases, verified
  again by `release/verify_release.py` locally and by the release workflow
  at publish time.
- Browser tests ignore the LAB's self-signed TLS error (production uses a
  valid Let's Encrypt certificate — verified live on the MCP listener).
