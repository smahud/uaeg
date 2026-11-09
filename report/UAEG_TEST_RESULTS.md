# Test Results — Installer-Contract Audit (2026-11-09)

Environment: isolated Debian-family Linux sandbox (user `z`, Python 3.12.16,
bash, openssl, curl, git 2.47.3), repo checkout `main` @ `30d047d` +
this audit's patch. Every command below was actually executed in this
audit; nothing is quoted from prior sessions.

---

## 1. Baseline (before any change) — all green

| Command | Result |
|---|---|
| `python -m pytest tests/test_unit_core.py tests/test_unit_executor.py tests/test_unit_containment.py tests/test_unit_security_redaction.py tests/test_unit_server_hardening.py tests/test_dashboard_auth.py tests/test_unit_regression_v033.py tests/test_unit_regression_v034.py tests/test_unit_regression_v041.py` | **125 passed** (29.87s) |
| `python -m pytest tests/test_e2e_smoke.py tests/test_e2e_dashboard_auth.py tests/test_e2e_hardening.py` | **32 passed** (48.29s) |
| `bash tests/test_upgrade_contract.sh` | **290 PASS / 0 FAIL** (rc=0) |

## 2. Incident reproduction (defect evidence)

| Reproduction | Observed |
|---|---|
| Operator's exact command (`--repair --dashboard-bind public --tls-mode manual --cert-file … --key-file …`) | exit 1, `ERROR: unknown option: --tls-mode (see --help)`, **zero mutation** (no temp dirs, no files; dies before the root check) |
| `uaeg config set dashboard.bind 0.0.0.0` first (runbook revision-1 order, isolated env) | rc=1, "Refusing to bind…", value **not saved** (`dashboard.bind` stays `127.0.0.1`) — D4 confirmed |
| Corrected order (`allow_public` → `tls` → `bind`) | all rc=0, `dashboard.bind=0.0.0.0` saved — fix verified |
| D10 (before fix): e2e repair with canonical flags | `CONFIG_ERROR: Refusing to bind the dashboard to all interfaces…` → rollback; **the corrected command could not succeed on v0.4.1** |
| D10 semantics proof | `X=0; f(){ X=1; echo 0.0.0.0; }; IP="$(f)"; echo $X` → `0` (assignment lost in the command-substitution subshell) |

## 3. After the patch — full suite, all green

| Command | Result |
|---|---|
| `python -m pytest tests/` (all 14 files) | **203 passed, 0 failed** (94.25s) |
| `bash tests/test_upgrade_contract.sh` | **290 PASS / 0 FAIL** (rc=0) |
| `bash -n install.sh` | clean |
| `bash install.sh --help` | canonical flags present, ghost flags absent, no duplicated lines |

### 3.1 New regression suites (46 checks)

**`tests/test_installer_cli_contract.py` — 31 passed**

- incident invocation rejected pre-mutation, no side effects (the incident, locked)
- `--tls-mode` / `--cert-file` / `--key-file` are not options
- missing values on all 10 value-taking options → "requires a value"
- option-like value (`--cert --key x`) → missing value
- `--cert` alone / `--key` alone → "manual TLS requires BOTH"
- nonexistent cert/key files → pre-mutation rejection
- 4 conflicting-TLS-source combinations → "conflicting TLS options"
- canonical recovery invocation accepted (`--dry-run`: `TLS mode: manual`, `dashboard: public:3903`, `MCP port: 3902`)
- config flags + `--status` → rejected
- `--help`: canonical interface, no ghost flags, zero duplicated lines
- **documentation contract**: every `install.sh` flag in bash blocks of `README.md` + all `docs/*.md` + the recovery runbook exists in the parser; runbook code blocks never invoke ghost flags; the runbook's canonical invocation present verbatim; runbook's `config set` sequence binds LAST

**`tests/test_unit_regression_configset_pass.py` — 7 passed**

- wildcard-bind-first rejected and not persisted; corrected order saves
- manual TLS file validation at `config set` time (missing → rejected; real pair → accepted)
- `uaeg pass` setup: file written, never echoed, verifies
- rotation: generation bump (old sessions dead), old password invalid, new valid
- **MCP keys byte-identical + still authenticate after rotation** (separate credential domains)
- mismatch/short-password retry loop semantics
- password file byte-identical across config rewrites; DB `quick_check` ok

**`tests/test_e2e_installer_repair.py` — 8 passed** (the production recovery path, end-to-end)

1. fresh install (isolated dirs, rootless patched installer, loopback, manual TLS, ports 3902/3903)
2. persistent state created (password, agent key, custom `exec.default_timeout_s=333`)
3. **the exact corrected runbook command** (`--repair --dashboard-bind public --cert … --key …`) → exit 0, "public dashboard: password verified"
4. target config: `mcp.bind=0.0.0.0:3902`; `dashboard.bind=0.0.0.0:3903`, `allow_public=true`, `tls=true`; `tls.mode=manual` with the given files; unrelated values preserved
5. zero data loss: password file **byte-identical**, key rows identical, `quick_check` ok
6. served from the repaired install: HTTPS login page first; `/api/overview` 401 unauthenticated; correct password in (cookie `HttpOnly; Secure; SameSite=Lax`); wrong password 401 "Login failed."; logout (CSRF) revokes the session
7. **pre-repair MCP agent key still authenticates on 3902** (unauthenticated → 401/403)
8. both listeners operational simultaneously (`healthz` v0.4.1 + login page)

### 3.2 Real-browser verification (Chromium via agent-browser, local instance)

| Step | Result (screenshots: `report/evidence/browser_audit_20261109/`) |
|---|---|
| `http://…/login` | renders: title "UAEG · Login", heading "Sign in", password field, button (01-login-page.png) |
| wrong password | "Login failed." shown, no session (02-login-failed.png) |
| correct password | redirect to dashboard `#keys`, title "UAEG · Dashboard", tabs KEYS/PERMISSIONS/SESSIONS/LOGS/SERVER (03-dashboard.png) |
| "Sign out" | back to `/login`, title "UAEG · Login" (04-after-logout.png) |

## 4. Tests NOT executed in this audit (honest register)

| Not executed | Why |
|---|---|
| Any test **on the production VPS** (systemd/journalctl/ss/uaeg doctor, on-box preflight, real Let's Encrypt chain from the VPS, rollback drill on production backups) | no VPS credentials in this environment — the runbook's Step 2a-0 preflight performs these on the box; production acceptance is marked NOT VERIFIED accordingly |
| CI on GitHub Actions for the patch commit | kicked off by the push; status checked post-push and recorded in `UAEG_PRODUCTION_ACCEPTANCE.md` §6 (sandbox re-ran the same suites locally — §3) |
| Browser check against production `:3903` | endpoint still refused (repair not yet run by the operator) |
| `--dashboard-bind lan` private-address refusal path (install.sh `lan` branch) | pre-existing behavior untouched by this patch; contract suite covers `lan` resolution indirectly |

## 5. Verdict

All executable-in-scope suites are green: **203 + 290 checks, 0 failures**,
plus the real-browser matrix. The corrected recovery command is proven
end-to-end against a real installation with full data preservation. The
only unexecuted items require production-box access, which this environment
does not have.
