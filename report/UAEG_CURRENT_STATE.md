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

---

## 6. Round 2 — independent verification (2026-11-09, ~15:40–16:15 UTC)

A second recovery-verification session audited the round-1 patch-set
(`b428482` + docs commit `c956678`) from scratch, trusting nothing reported
and re-running everything executable:

| Check | Result (all executed this session) |
|---|---|
| Repo HEAD at round-2 start | `c956678` — round-1 patch-set + its SHA-recording docs commit; reference commit `30d047d` is 2 commits behind |
| Full pytest suite, fresh venv, pre-round-2 | **203 passed, 0 failed** (90 s) — round-1's claim reproduced exactly |
| Upgrade-contract suite, pre-round-2 | **290 PASS / 0 FAIL** — reproduced exactly |
| GitHub CI on `c956678` | completed / **success** (run 42557988917) |
| Incident command reproduction | `ERROR: unknown option: --tls-mode (see --help)`, exit 1, zero mutation — unchanged by design |
| Production probes (15:56:58 UTC, full TLS verification, no `-k`) | `healthz` HTTP 200, `version 0.4.1`, uptime 46 693 s; `/mcp` 401; **3903 connection refused**; chain `Verify return code: 0 (ok)`; SAN `host.zerotier.my.id`; cert valid 2026-11-07 → 2027-02-05 |
| Docs↔parser sweep beyond the TLS flags | install.sh flags in docs: contract-tested green. upgrade.sh forwarded flags (`--allow-downgrade`, `--yes`, …): accepted by design via the parser's forward-to-updater catch-all, and the updater's parser really accepts them (`updater.sh:233`). Config keys in `docs/CONFIGURATION.md`: all 20 exist in the schema. **One new defect of the incident's class found: upgrade.sh `--version`/`--source` missing values → raw bash error (D11) — fixed and locked this round** (5 new checks; post-patch suite: **208 passed / 0 failed**, contract **290 PASS / 0 FAIL**) |

**Timeline erratum (report hygiene):** round-1's §2 recorded a probe "at
~04:20 UTC with uptime 2067.5 s". That reading is internally inconsistent
with this round's measurement (15:43 UTC, uptime 45 895.6 s ⇒ last service
start ≈ 02:58 UTC): a later probe cannot show an earlier start time, so the
round-1 probe time was likely mis-transcribed. The authoritative
reconstruction from this round's direct measurements: the service
(re)started on v0.4.1 at ≈ 02:57–03:00 UTC and has been **continuously up
since** (46 693 s ≈ 13 h at 15:56:58 UTC). No operational conclusion
changes.

**Operator status (the actionable bottom line):** production completed
Step 1 (safe upgrade to v0.4.1) around 03:00 UTC, but as of 15:57 UTC the
runbook's Steps 2–4 have NOT been executed — ~13 h after revision 2 was
published. The continuous ~13 h uptime proves no restart/config change has
occurred, and 3903 remains refused. The corrected procedure is verified
end-to-end in the lab; only the on-box execution is missing.
