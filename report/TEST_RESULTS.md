# Test Results — v0.4.1 (production-incident closure release)

**Date:** 2026-11-09 · **Environment:** isolated sandbox (Python 3.12.16,
bash 5.x, no root, no systemd — identical shape to the CI contract-suite
job) · **Suites:** `tests/test_upgrade_contract.sh` (contract, T-01..T-57)
+ pytest unit/e2e + incident reproduction against the PUBLISHED v0.4.0
assets + external production probes.

Historical suites for earlier releases: `report/TEST_EVIDENCE.md` and the
v0.4.0 section preserved in git history (`git show v0.4.0:report/TEST_RESULTS.md`).

---

## 1. Summary

| Suite | Result |
|---|---|
| Safe-upgrade contract suite (T-01..T-57, real Ed25519 chain, local release server) | **290 PASS / 0 FAIL** |
| pytest unit (core, executor, containment, security redaction, server hardening, dashboard auth, regression v033/v034/**v041**) | **125 passed** (36.6 s) |
| pytest e2e (smoke, dashboard auth, hardening) | **32 passed** (102 s) |
| **Incident reproduction** (published v0.4.0 installer vs v0.4.1 fix, stale mixed checkout) | **reproduced + refused pre-mutation** — `evidence/incident_repro_v041.txt` |
| Production external probes (2026-11-09 02:41 UTC) | MCP `:3902` healthy **v0.4.0** (uptime 22 775 s); dashboard `:3903` still refused — operator runbook: `PRODUCTION_RECOVERY_V041.md` |

## 2. New in v0.4.1 — incident regressions (T-53..T-57, 33 assertions)

All five pin the 2026-11-08 production incident class
(`report/ROOT_CAUSE_ANALYSIS.md`, "Insiden 3"):

| Test | Scenario (isolated dirs, patched installer — no root/systemd) | Result |
|---|---|---|
| **T-53** | `--repair` from an OLDER source over a NEWER install (installed 24.0.0, source 0.4.1) | **exit 5** refusal; "REFUSING to deploy OLDER source"; version, installed-tree hash, config sha, agent keys and backup count ALL unchanged |
| **T-54** | SAME version strings, but the source's `DashboardConfig` has the `tls` field removed (mixed-tree shape) | **exit 1** `CONFIG_INCOMPATIBLE`, message contains the exact production cause (`unexpected keyword argument 'tls'`); zero mutation (tree + config verified) |
| **T-55** | Standalone installer pinned to its own older release, release channel pointed at a dead URL | **exit 5** BEFORE any download (the refusal, not a network error, is what fires) |
| **T-56** | `UAEG_ALLOW_DOWNGRADE=1` + schema-COMPATIBLE older source | repair **succeeds** (rc 0) with the consent warning; agent keys and the operator's `dashboard.port` preserved; lands on the older version |
| **T-57** | `PYTHONPATH` pointing at a pre-v0.3.0 `uaeg` package (stale-module shadow) | repair **succeeds** via the sanitized interpreter (`env -u PYTHONPATH … -P`); the config is written by the CORRECT schema and still carries `dashboard.tls` |

Unit-level pinning (`tests/test_unit_regression_v041.py`, 7 tests): the
production-shaped config (MCP `0.0.0.0:3902`, dashboard `0.0.0.0:3903`,
`dashboard.tls=true`, manual TLS material) loads on the current schema; a
faithful pre-v0.3.0 `DashboardConfig` replica rejects it with exactly the
production `TypeError`; the wrapped `ConfigError` names the offending
field; the preflight probe contract (staged-source import origin +
`Config.load`) passes against the real repository source.

## 3. Incident reproduction against the published v0.4.0 (fail-first proof)

`report/evidence/incident_repro_v041.txt` — full logs:

- Baseline: fresh install of the **published v0.4.0** (`git archive
  v0.4.0`), config carries `dashboard.tls` (schema field since v0.3.0).
- **[A] The incident:** `bash install.sh --repair` from a stale mixed
  checkout (v0.4.0-era `install.sh` + pre-v0.3.0 `src/uaeg/config.py`) →
  `CONFIG_ERROR: existing configuration is invalid: configuration contains
  an unknown or mistyped field: DashboardConfig.__init__() got an
  unexpected keyword argument 'tls'` → rollback engaged → rc 1. The exact
  production error, byte-for-byte, including the mutate-first ordering.
- **[B] The fix:** the v0.4.1 installer placed in the SAME stale checkout,
  same command → `REFUSING to deploy OLDER source … NOTHING was changed`,
  **rc 5**, installed tree and configuration **byte-identical** before and
  after (sha256-verified).

## 4. Full-suite evidence

- Contract suite final line: `HASIL: 290 PASS / 0 FAIL` (runtime ≈ 21 min,
  includes fresh installs, verified remote upgrades via a local release
  server with a real Ed25519 signing chain, rollback, key-rotation
  refusals, and the v0.3.5-baseline migration T-51/T-52).
- pytest: `125 passed` (unit) · `32 passed` (e2e) — no skips, no flaky
  reruns; the single initial failure (README version metadata) was fixed
  by synchronizing README/CI/T-39 to v0.4.1 and re-run green.

## 5. Production state (external probes, not modified by this release)

| Probe (2026-11-09 02:41 UTC) | Result |
|---|---|
| `GET https://host.zerotier.my.id:3902/healthz` | `{"status":"ok","service":"UAEG","version":"0.4.0","uptime_seconds":22775.2}` |
| `GET https://host.zerotier.my.id:3902/mcp` (no key) | 401 (fail-closed auth intact) |
| `https://host.zerotier.my.id:3903/login` | connection refused (P0-3 remains; runbook delivered) |

The healthy v0.4.0 MCP service with ~6.3 h uptime is consistent with the
incident timeline (v0.4.0 deployed → failed `--repair` → rollback restored
v0.4.0 → service restarted; the dashboard was never re-exposed). Recovery:
`report/PRODUCTION_RECOVERY_V041.md`.

## 6. Known environmental notes (not defects)

- The contract suite and reproduction apply the documented test-only
  patches (root check disabled, `apt` skipped, system paths redirected) —
  identical to the CI installer job; every other code path runs unpatched.
- systemd-dependent behavior (the hard-fail on a hung `systemctl stop`,
  the supervised restart) is exercised only on a real host; the sandbox has
  no systemd. The code paths are simple, reviewed, and their failure
  semantics (die before mutation) are covered by the preflight tests.
