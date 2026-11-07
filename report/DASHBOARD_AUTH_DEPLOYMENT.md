# UAEG Dashboard Authentication & Remote Access — Deployment Report

- **Date:** 2026-11-08
- **Baseline:** UAEG v0.2.0 (commit `111b3e2`, tag `v0.2.0`)
- **Delivered in:** commit **`af809db`** (`feat(dashboard): native password authentication, public HTTPS binding, uaeg pass — v0.3.0`)
- **Target environment:** Debian 13 VPS "MicroTix" (deployment runbook: [VPS_DEPLOYMENT_RUNBOOK.md](VPS_DEPLOYMENT_RUNBOOK.md))
- **Priority:** P0 — dashboard accessibility, authentication, data persistence

---

## 1. Root cause: why the dashboard could not be accessed remotely (v0.2.0)

Audit of the v0.2.0 codebase (config, server, installer, docs) identified four
independent causes — remote access was impossible **by design**, not by misconfiguration:

| # | Cause | Evidence (v0.2.0) |
|---|-------|-------------------|
| 1 | **Dashboard listener bound to `127.0.0.1` by default** | `DashboardConfig.bind = "127.0.0.1"` in `config.py`; installer default `--dashboard-bind loopback` |
| 2 | **Wildcard bind actively refused** | `Config.validate()`: *"Refusing to bind the dashboard to all interfaces: the dashboard has no login"* — `0.0.0.0` rejected unless `dashboard.allow_public=true`, which the installer never sets |
| 3 | **No authentication existed at all** | README v0.2.0: *"The dashboard has no login. Its network placement (LAN/loopback bind) is the only thing protecting it."* Every `/api/*` route (key generation, revocation, config, sessions, logs) was unauthenticated |
| 4 | **No TLS on the dashboard listener** | `server.py` v0.2.0 built the dashboard uvicorn listener without `ssl_certfile`/`ssl_keyfile`; only the MCP listener had TLS |

Consequently a remote browser hitting `https://<public-ip>:3903` could never reach a
 usable dashboard — and *should not have*, since doing so in v0.2.0 would have exposed
 full root-capable administration to the internet.

### Additional latent bug found during the audit

The v0.2.0 installer crashes with `PUBLIC_HOST: unbound variable` (`set -u`) on any
**upgrade** invocation that omits `--domain`/`--public-ip` — exactly the command an
operator would run to upgrade the MicroTix VPS. Fixed in `af809db` (`PUBLIC_HOST=""`
initialized in the defaults). The rollback trap was verified to restore config + app
when this failure fired during the upgrade simulation (§6).

---

## 2. Architecture changes (summary)

The one-process / two-listener architecture, the shared Runtime, the SQLite store and
the MCP bearer path are **unchanged**. Changes are additive:

```text
                       Internet / browser
                             |
                    https://<domain-or-ip>:3903   (dashboard.tls = true)
                             |
                      [ TLS-terminated uvicorn ]
                             |
                CIDR allowlist (optional, outermost, fail-closed)
                             |
                _DashAuthMiddleware  (NEW)
                  /login ........................ public (rate-limited POST)
                  everything else ............... session required
                    pages -> 302 /login          api -> 401 JSON
                  unsafe methods ................. CSRF token + Origin check
                  oversized bodies ............... 413
                             |
                      Starlette routes (unchanged)
```

New module `src/uaeg/dashauth.py` (≈470 lines) holds all auth primitives:

- **PasswordRecord** — salted **scrypt** (`hashlib.scrypt`, stdlib; n=32768, r=8, p=1,
  dklen=32, ~32 MiB per hash, ~80 ms on a small VPS) persisted as JSON at
  `/var/lib/uaeg/dashboard-pass.json` (0600, atomic tmp+fsync+`os.replace`+dir-fsync).
  Carries a random `generation` token.
- **PasswordManager** — hot-path accessor with mtime-based caching: `uaeg pass` takes
  effect **without a service restart**; a corrupt file fails closed (login required,
  verification impossible) and never downgrades to legacy mode.
- **SessionStore** — server-side opaque-token sessions (256-bit, client never supplies
  one → session fixation impossible), generation-bound, idle (30 min) + absolute (12 h)
  expiry, revocable individually and en masse.
- **LoginThrottle** — per-source-IP sliding window: 5 failures / 15 min → 15 min
  lockout. Deliberately per-IP (never global) so a third party cannot lock the operator
  out of their own dashboard.

New config keys (backward compatible — old config files load unchanged):
`dashboard.tls` (bool, default false), `dashboard.session_idle_timeout_s` (1800),
`dashboard.session_absolute_timeout_s` (43200).

Dependencies: **none added** (3 runtime deps unchanged: mcp/starlette/uvicorn).

## 3. `uaeg pass` implementation

```
$ sudo uaeg pass
UAEG Dashboard Password Setup

Enter new password:
Confirm new password:

Password updated successfully.
  - it takes effect immediately (no service restart needed)
  - all existing dashboard sessions have been revoked
  - the MCP agent keys are NOT affected (separate credential)
```

All 12 behavioral requirements from the spec are implemented and tested
(`tests/test_e2e_dashboard_auth.py`):

1. Hidden input (`getpass`) — verified via detached-session subprocess tests.
2. Double entry, match required. 3. Mismatch → error + retry loop.
4. Empty rejected. 5. Minimum 12 chars enforced (max 1024 to bound scrypt cost).
6. Salted scrypt (stdlib). 7. No plaintext at rest (file content asserted in tests).
8. Never printed to logs/audit/CLI output (audit entries asserted secret-free).
9. Repeatable rotation. 10. No old password required (runs as local root).
11. Cancel (Ctrl+C/EOF) keeps the previous password (exit 130).
12. Atomic durable write (crash-injection test: `os.replace` failure leaves the old
    record intact, no tmp residue).

## 4. Login, sessions and endpoint protection

- **Login page** (`/login`, GET): UAEG-branded, dark theme consistent with the dashboard
  (same palette/typography), password input, Sign-in button with busy spinner, generic
  error area, guidance notes when no password exists yet or the file is corrupt.
  Screenshots: `report/evidence/uaeg_login_page.png`, `report/evidence/uaeg_login_error.png`.
- **Session flow**: browser → no valid session → login page → password verified →
  server-side session created (fresh token = rotation) → `uaeg_session` cookie
  (`HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` on HTTPS, `Max-Age` = idle timeout)
  → dashboard. Refresh keeps the session until expiry. **No password or bearer key is
  ever placed in localStorage**; the browser holds only the opaque cookie, and the UI
  keeps the CSRF token in a JS variable.
- **Coverage**: every page and API route requires a session — overview, keys
  (list/create/revoke/rotate/permissions), permissions, sessions, jobs (list/cancel/
  output), logs, server info, whoami. Pages redirect (302 `/login`), APIs return
  `401 Unauthorized`. Unauthenticated state-changing POSTs get 401 before the route
  runs (body never parsed). No WebSocket endpoints exist on the dashboard (verified by
  route audit); the MCP streamable-HTTP listener is untouched.
- **CSRF**: session-bound token (`X-CSRF-Token`, constant-time compare) required on
  every state-changing request; fetched via `GET /api/whoami` at UI boot and attached
  by the central `api()` JS helper. **Origin validation**: `Origin` (when present) must
  match `Host` on all unsafe methods, including `/login`.
- **Logout**: topbar Sign-out button → `POST /logout` → session revoked server-side,
  cookie cleared, next request redirected to login.
- **Password rotation**: new `generation` in the password file invalidates every
  existing session on the next request — no restart, no IPC.
- **Brute force**: per-IP lockout (§2), `429` + `Retry-After` with a generic message,
  successful/failed logins audited (`tool=dashboard_login`, `status=ok|denied|locked`,
  IP only — no password material; visible in the dashboard Logs tab and `uaeg` CLI).
- **Request limits**: login body capped at 64 KiB (enforced even for chunked requests
  without Content-Length); general dashboard body cap 1 MiB (413).

## 5. Dashboard listener: binding + TLS

- `dashboard.bind = 0.0.0.0` (and `::`, `*`) is now **supported and explicitly
  validated**: it requires `dashboard.allow_public=true` AND `dashboard.tls=true`
  (`Config.validate()`) AND an existing dashboard password — `server.serve()` refuses
  to start otherwise (fail closed, with the exact `sudo uaeg pass` remediation in the
  error). The runtime middleware independently denies admin access when no password
  exists, so even a mid-run password-file deletion cannot expose the dashboard.
- **TLS**: `dashboard.tls=true` serves the dashboard over HTTPS using the *same
  certificate material* as the MCP listener (`tls.mode` = certbot / manual /
  self-signed, auto-generated on demand in self-signed mode, pre-validated at startup
  with a clear `ConfigError` if unloadable). No reverse proxy, no new components.
- **Port** stays whatever is configured (default 3903).
- **Backward compatibility**: loopback/LAN binds behave exactly as before. Without a
  password the dashboard runs in *legacy mode* (v0.2.0 behavior) on private binds only;
  with a password, login is enforced on every bind. `runtime.dashboard_url()` and the
  startup log report the correct scheme.
- **Installer**: `--dashboard-bind public` sets bind/allow_public/tls **after verifying
  that a dashboard password exists** — it aborts with instructions otherwise, so the
  installer can never publish an unauthenticated dashboard. Firewall hints and the
  summary output updated accordingly.

## 6. Backup, migration and zero-data-loss evidence

- **No schema migration was needed** (SQLite schema stays v1; the password lives in its
  own file). No destructive operation exists anywhere in the change set.
- **Full upgrade simulation executed** (`/home/z/my-project/scripts/upgrade_sim.sh`,
  replicating the MicroTix path): fresh **v0.2.0** install (loopback, self-signed,
  ports 3962/3963) → 2 agent keys generated → `exec.default_timeout_s=90` customization
  → v0.2.0 server verified healthy (MCP + legacy dashboard) → **in-place upgrade to the
  v0.3.0 working tree** (installer backup + config-preserve path) → verified:

  | Check | Result |
  |---|---|
  | Keys after upgrade | **2 → 2 (preserved)** — both still authenticate via MCP initialize |
  | Custom config (`exec.default_timeout_s=90`) | preserved |
  | Dashboard bind/port / TLS mode | preserved (127.0.0.1:3963, self-signed) |
  | v0.3.0 server health (`/healthz`) | ok |
  | Legacy dashboard (no password yet) | served, as on v0.2.0 |
  | `uaeg pass` on the upgraded install | succeeds; login enforced **without restart** |
  | `/api/keys` without session | 401 |
  | Login → session → key visible | ok |
  | Rollback trap (when the latent v0.2.0 bug fired) | config + app restored correctly |

- **Persistence across restart**: `test_restart_preserves_password_keys_and_mcp`
  restarts the whole server and re-verifies password enforcement, key presence and MCP
  bearer authentication; `quick_check` = ok afterwards.
- The dashboard password is a **separate credential** from MCP agent keys: no auth code
  path touches the `keys` table, and rotating the password is covered by an explicit
  test asserting the agent key still authenticates.
- No backup or secret was committed to GitHub (password file lives under
  `/var/lib/uaeg`, outside the repo; `.gitignore` already excludes nothing needed).

## 7. Automated test results (full suite: **113 passed**)

Executed in the build sandbox on Python 3.12.16 (`python -m pytest tests/`):

| Suite | Tests | Covers |
|---|---|---|
| `test_unit_core.py` (updated) | incl. new wildcard/TLS/session-timeout validation cases | config guard rails |
| `test_dashboard_auth.py` (new) | 28 | scrypt round-trip, no-plaintext-at-rest, atomic-write crash injection, corrupt-file fail-closed, hot reload, session lifecycle/idle/absolute/generation, throttle lockout + per-IP isolation, legacy mode, 302/401 semantics on every API, cookie flags (HttpOnly/SameSite/Secure-on-HTTPS), CSRF + Origin, 413, lockout 429, secret-free audit, CIDR+auth composition |
| `test_e2e_dashboard_auth.py` (new) | 11 | real `uaeg pass` subprocesses (first setup, mismatch retry, short/empty rejection, EOF cancel keeps old password, rotation), live-server login flow, logout, lockout over real HTTP, rotation without restart, **public bind refused without password**, **HTTPS dashboard reached over the non-loopback interface (21.0.16.172) with Secure cookie + plain-HTTP refused**, restart persistence, MCP keys still authenticating, DB integrity |
| Existing suites (core, executor, containment, redaction, server hardening, e2e smoke, e2e hardening) | 74 | no regressions |

Spec §10 matrix mapping: *"uaeg pass first setup / mismatch / change / hash persistence
/ login correct / login incorrect / brute-force / dashboard without session / with
session / API without session / logout / rotation revokes sessions / wildcard bind /
public without password / HTTPS external access / MCP keys preserved / agent connection
/ restart & upgrade / database integrity"* — **all covered by the suites above**
(rollback additionally covered by the installer simulation in §6).
UI wiring (CSRF header injection, 401 redirect, logout) verified in a real Chromium
session (screenshots in `report/evidence/`: login page, login error, dashboard, and key
creation through the UI — which proves the CSRF path end-to-end).

## 8. Real external access test

Sandbox-external simulation (non-loopback interface `eth0` 21.0.16.172, self-signed
TLS, wildcard bind + password): login page served over HTTPS, `Secure` cookie issued,
dashboard accessible with session, plain-HTTP refused, MCP unaffected — see
`test_public_https_dashboard_from_non_loopback_interface`.

**Not yet performed from the real MicroTix VPS public IP/domain** — requires the
operator-run deployment (runbook §steps 10-11). The final GO below is conditional on
that verification, per the spec's final directive.

## 9. Deployment

- Code: commit **`af809db`** on `main` (this repository), version bumped to **v0.3.0**
  (pyproject / `__version__` / installer release constant kept consistent — enforced by
  `test_version_metadata_consistency`).
- CI: new suites added to the matrix (3.11–3.13); installer smoke unchanged (still
  passes: fresh install keeps the dashboard private by default).
- **VPS rollout (MicroTix) is operator-executed** following
  [VPS_DEPLOYMENT_RUNBOOK.md](VPS_DEPLOYMENT_RUNBOOK.md): backup → deploy → verify keys
  & MCP → interactive `sudo uaeg pass` (password chosen and typed by the operator;
  never auto-generated or printed by tooling) → enable public HTTPS → external browser
  verification. The runbook includes the rollback procedure.

## 10. Remaining risks

1. **Single-factor password on a root-capable admin surface.** A compromised dashboard
   password equals full agent-key control. Mitigations present: HTTPS enforced for
   public binds, lockout, scrypt, sessions revocation. Optional hardening: keep
   `network.allowed_dashboard_cidrs` even when public (operator IP allowlist), or keep
   the dashboard private and tunnel.
2. **Self-signed TLS** on the dashboard will trigger browser warnings if the operator
   has not provisioned a trusted certificate (certbot/manual) — the spec's requirement
   of trusted HTTPS for external access means the MicroTix deployment should use a
   domain + Let's Encrypt (already supported by the installer).
3. **Sessions are in-memory**: a service restart logs the operator back in (no
   persistence of sessions by design — maximal revocability).
4. **Per-IP lockout only**: a distributed attacker gets 5 tries per IP per window;
   each attempt costs ~80 ms of scrypt on the server. Acceptable for a single-operator
   tool; noted as residual.
5. **Legacy mode** (private bind, no password) intentionally preserves v0.2.0 exposure
   semantics on LAN — operators should run `sudo uaeg pass` regardless (doctor reminds
   them).
6. UAEG's core threat model is unchanged: an authenticated agent still has full root
   (FR-010/FR-013/FR-014/FR-020).

## 11. Final decision

**CONDITIONAL GO** for the MicroTix VPS rollout:

- ✅ `sudo uaeg pass` interactive, double entry, hidden, rotatable
- ✅ Login page before the dashboard; login/logout/session expiry/rotation semantics
- ✅ All dashboard endpoints protected (pages + API), CSRF + Origin
- ✅ `0.0.0.0` binding supported; refused without password or TLS (fail closed)
- ✅ HTTPS dashboard (same cert infra as MCP); non-loopback external simulation passed
- ✅ Existing MCP keys/verifiers/config/DB preserved across upgrade, rotation, restart
  (simulation + tests); no destructive migration exists
- ✅ Repository updated (commit `af809db`); automated tests (113) green; reports in `report/`
- ⏳ **Operator-executed steps before production sign-off:** deploy to MicroTix, set
  the password interactively, enable the public HTTPS bind, and verify access from an
  external network (runbook steps 1–13, including `uaeg doctor` and the acceptance
  checklist). Per the final directive, production readiness is declared only after
  those verifications pass on the real VPS.

*No passwords, bearer tokens, session cookies or other secrets appear in this report.*
