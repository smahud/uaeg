# UAEG Current State — Installer-Contract Audit & Production Recovery (2026-11-09)

**Audit executor:** autonomous agent session (GitHub access + isolated Debian
test sandbox; **no VPS credentials** — production observations below are
external, read-only probes).
**Audit baseline:** repository `main` @ `30d047d04729a3d703f00c8bcd3fc288ff7bb4f8`
(reference commit of the mission, confirmed still HEAD of `origin/main` at
audit start, 2026-11-09 ~02:55 UTC).

---

## 1. Repository state (verified facts)

| Item | Value | Evidence |
|---|---|---|
| `origin/main` HEAD at audit start | `30d047d04729a3d703f00c8bcd3fc288ff7bb4f8` | `git ls-remote`, `git rev-parse HEAD` |
| Relationship to latest tag | `v0.4.1-1-g30d047d` (1 docs-only commit after the release) | `git describe --tags` |
| Tag `v0.4.1` | `fa08174` ("release: v0.4.1 — pre-mutation repair safety (incident 2026-11-08), release-key rotation 3->4") | `git log` |
| Source version (`pyproject.toml`) | `0.4.1` | file read |
| Installer self-version (`UAEG_RELEASE_VERSION`) | `0.4.1` | `install.sh` line 79 |
| Python package version | `uaeg 0.4.1` | `uaeg version` in the test venv |
| Release-signing keyring | keys 1–4 embedded (current: `uaeg-release-4`) | `install.sh` trusted-keyring block |
| Baseline test status BEFORE any change | unit 125 passed · e2e 32 passed · upgrade-contract 290 PASS / 0 FAIL | executed in this audit (commands in `UAEG_TEST_RESULTS.md`) |

## 2. Production state (external read-only probes, 2026-11-09 ~04:20 UTC)

| Check | Result |
|---|---|
| DNS `host.zerotier.my.id` | resolves → `125.163.142.132` |
| `https://host.zerotier.my.id:3902/healthz` | **HTTP 200**, full TLS verification (no `-k`): `{"status":"ok","service":"UAEG","version":"0.4.1","uptime_seconds":2067.5}` |
| `https://host.zerotier.my.id:3902/mcp` without key | **HTTP 401** (MCP auth intact) |
| TLS certificate on 3902 | subject `CN=host.zerotier.my.id` · issuer `Let's Encrypt (CN=YE2)` · valid 2026-11-07 → **2027-02-05** · SAN `DNS:host.zerotier.my.id` · chain verify: `Verify return code: 0 (ok)` |
| `https://host.zerotier.my.id:3903/login` | **connection refused** (fast RST — nothing listening publicly / firewall REJECT). **This is the remaining P0.** |

Interpretation (facts, not assumptions): the MCP plane is healthy and already
runs **v0.4.1** with a browser-trusted Let's Encrypt chain — i.e. the operator
has completed Step 1 (safe upgrade) of the recovery runbook since the
incident; service uptime at probe time was ≈34 min. The dashboard exposure
(Step 2) has NOT succeeded yet — consistent with the reported incident: the
operator's repair died at `unknown option: --tls-mode`, and (found by this
audit, see `UAEG_ROOT_CAUSE_ANALYSIS.md` §D10) the released v0.4.1 installer
would have refused the corrected invocation too.

## 3. What this audit changed

Code/doc/test changes are on `main` (see `UAEG_PATCH_CHANGELOG.md` for the
full list and rationale):

- `report/PRODUCTION_RECOVERY_V041.md` → revision 2 (correct canonical
  flags, corrected `uaeg config set` order, real TLS verification without
  `-k`, preflight block, honest D10 caveat).
- `report/DASHBOARD_ROOT_CAUSE.md` → same `config set` order errata.
- `install.sh` → pre-mutation TLS-flag contract (missing values, incomplete
  `--cert/--key` pair, missing files, conflicting TLS sources), `--help`
  dedup, **D10 fix** (`--dashboard-bind public` actually sets
  `allow_public`/`tls` now), silent-TLS-downgrade guard (D9).
- 3 new regression suites (46 new checks): `tests/test_installer_cli_contract.py`
  (31), `tests/test_unit_regression_configset_pass.py` (7),
  `tests/test_e2e_installer_repair.py` (8) + CI wiring for all of them.

## 4. Not verifiable from this environment (honest limits)

- On-VPS state: `systemctl`, `journalctl`, `ss`, `/root/uaeg` staleness,
  `uaeg status`/`doctor`, backup presence, effective config — **no VPS
  access**. The runbook's new preflight block (Step 2a-0) performs these
  checks on the box.
- Whether the production `/etc/uaeg/config.json` already contains
  `dashboard.allow_public=true` (determines whether the *released* v0.4.1
  installer could complete Step 2b despite D10 — see the runbook's D10
  caveat and the guaranteed manual alternative).

## 5. Production status

**MCP: OPERATIONAL (verified externally, trusted TLS, v0.4.1, auth intact).**
**Dashboard: NOT YET RESTORED (3903 refused) — blocked on operator execution
of the corrected Step 2 + Step 3 of the runbook.** Full acceptance matrix:
`UAEG_PRODUCTION_ACCEPTANCE.md`.
