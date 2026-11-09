# Patch Changelog — Installer-Contract Audit (2026-11-09)

Baseline: `main` @ `30d047d04729a3d703f00c8bcd3fc288ff7bb4f8` (= `v0.4.1-1`,
docs-only commit after the release tag).
Patch-set commit: **`b4284822adf2f3c46e82baaa90307e3938d3ed5e`**
("fix(install): enforce TLS CLI contract pre-mutation; repair
--dashboard-bind public; correct recovery runbook"), pushed to
`origin/main` 2026-11-09 and verified via `git ls-remote`.

All changes are minimal and defect-driven. No architecture change, no
configuration-framework change, no new dependency, no data migration.

---

## 1. Files changed

| File | Change | Defects |
|---|---|---|
| `install.sh` | `--help`: removed duplicated `--repair` safety paragraph | D5 |
| `install.sh` | Argument parsing: `need_value()` guard on every value-taking option (clear "requires a value" error; option-like tokens treated as missing values) | D6 |
| `install.sh` | Post-parse TLS contract block (before the root check — i.e. before sudo exec, apt, service control, any file write): reject conflicting TLS sources (`--domain` + `--cert/--key`, `--self-signed` + either); require the complete `--cert`/`--key` pair; require both files to exist | D7, D8 |
| `install.sh` | D10 fix: `DASHBOARD_PUBLIC` is now derived in the parent shell from the resolved bind address (`case "$DASH_BIND_IP" in 0.0.0.0\|::)`); the dead subshell assignment was removed with an explanatory comment. `--dashboard-bind public` now genuinely sets `dashboard.allow_public=true` + `dashboard.tls=true` (and the password prerequisite check runs) | D10 |
| `install.sh` | Silent-downgrade guard: an explicitly requested TLS source is never replaced by the self-signed fallback when address detection fails | D9 |
| `report/PRODUCTION_RECOVERY_V041.md` | Revision 2: canonical `--cert`/`--key` invocation; corrected `uaeg config set` order (`allow_public` → `tls` → `bind` LAST) with explanation; new preflight block (Step 2a-0: service state, version, `uaeg status`, cert dates/SAN, cert↔key modulus match, disk space); Step 4d uses full TLS verification (no `-k`); "What NOT to do" no longer references `--tls-mode`; honest D10 caveat for the released v0.4.1 installer | D1, D2, D3, D4 |
| `report/DASHBOARD_ROOT_CAUSE.md` | Same `config set` ordering errata in §6 | D4 |
| `tests/test_installer_cli_contract.py` | NEW (31 checks): incident reproduction (pre-mutation rejection + no side effects), ghost flags rejected, missing values, incomplete pair, missing files, TLS conflicts, canonical acceptance via `--dry-run`, `--help` accuracy + no duplicates, and the **documentation contract** (every `install.sh` flag in bash blocks of `docs/*.md`, `README.md`, the runbook must exist in the parser; runbook never invokes ghost flags in code blocks; runbook's `config set` order binds LAST) | D1–D8 locks |
| `tests/test_unit_regression_configset_pass.py` | NEW (7 checks): `config set` ordering contract (wildcard first = rejected & not saved; correct order = saved), manual-TLS file validation at set time, `uaeg pass` setup + rotation (generation bump revokes sessions; MCP keys byte-identical and still authenticating; DB `quick_check` ok), mismatch/length rules, password file byte-identical across config rewrites | D4 + auth matrix |
| `tests/test_e2e_installer_repair.py` | NEW (8 checks): full production-shape recovery against a real isolated installation — fresh install → password + agent key + custom config value → **the exact corrected runbook repair command** → target config asserted (`mcp 0.0.0.0:3902`, `dashboard 0.0.0.0:3903 + allow_public + tls`, `tls.mode=manual`) → zero data loss (password file byte-identical, keys identical, DB ok) → serve from the repaired install → HTTPS login page first, API 401 unauthenticated, correct/wrong password, cookie flags, logout revocation, **pre-repair MCP key still authenticates on 3902**, both listeners live simultaneously | D10 lock + acceptance |
| `.github/workflows/ci.yml` | New test files wired into the `test` job; new step for the installer-repair e2e | — |
| `report/evidence/browser_audit_20261109/*.png` | NEW: real-Chromium evidence (login page, login-failed, dashboard, after-logout) | — |
| `report/UAEG_*.md` (5 files) | NEW: this audit's contract reports | — |

## 2. Design decisions

**No CLI aliases added (`--tls-mode`/`--cert-file`/`--key-file` stay
non-options).** The audit charter's preferred approach — retain the
canonical `--cert`/`--key` interface and correct the documentation — was
adopted. Rationale: (a) the flags never existed, so no working invocation
in the wild depends on them; (b) `--tls-mode` would introduce a redundant
way to express what the TLS-source flags already encode, and its failure
mode ("--tls-mode manual" without `--cert/--key") is exactly the ambiguity
this incident exposed; (c) the doc↔parser contract test now makes future
drift impossible, which addresses the actual root cause. Unsupported
arguments continue to be rejected **before any service or filesystem
mutation** — now also for the newly validated misuse classes (missing
values, incomplete pair, conflicts).

**The later in-flow checks were kept** (defense in depth): the manual-TLS
file existence/parseability checks remain in the TLS-provisioning section;
the new parse-time checks merely front-run them with clearer, earlier
errors.

**No release tag cut.** The release pipeline signs manifests with the
project's private release key, which this audit environment does not hold.
The fixes land on `main`; the runbook tells the operator how to proceed on
the released v0.4.1 (manual path — always works) or from `main` (installer
path — D10 fixed) until the maintainer cuts v0.4.2.

## 3. Compatibility assessment

- Canonical invocations are unchanged: `--domain`, `--cert/--key`,
  `--self-signed`, `--dashboard-bind`, ports, paths, all commands — the
  full upgrade-contract suite (290 checks, incl. every documented bootstrap
  and updater flow) passes unchanged.
- Behavior changes are strictly fail-closed error paths for previously
  undefined/ambiguous input (missing values, half pairs, conflicting TLS
  sources) and one genuine bug fix (public bind now works as documented
  since v0.3.0).
- CI's installer smoke job and the contract suite's `sed` patches target
  lines that this patch does not touch (verified by re-running both).

## 4. What was intentionally NOT changed

- The single-operator login system, session/CSRF/Origin/throttle design
  (verified correct by the existing 559-check suite + this audit's
  additions; no rewrite warranted).
- `updater.sh` / `upgrade.sh` (no defect found in the audit scope; contract
  suite green).
- The systemd unit, venv layout, database schema, key material handling.
- Historical reports other than the two recovery-relevant corrections
  above (archives stay as written).

## 5. Commit

- Baseline: `30d047d04729a3d703f00c8bcd3fc288ff7bb4f8`
- **Patch-set commit: `b4284822adf2f3c46e82baaa90307e3938d3ed5e`** —
  pushed to `origin/main`, verified via `git ls-remote`.
- CI run for the patch set:
  https://github.com/smahud/uaeg/actions/runs/42557757475
  (result recorded in `report/UAEG_PRODUCTION_ACCEPTANCE.md` §6).
- This report itself and its SHA-recording sibling were amended in a
  follow-up docs commit on `main` (a SHA cannot be embedded in the commit
  it identifies); final HEAD: see `git log -1`.

---

## 6. Round-2 patch-set (2026-11-09, independent re-audit)

Baseline: `main` @ `c9566785c262c0554323ab3ba0d47da342b70cf1` (round-1
patch-set + its SHA-recording docs commit). Every round-1 claim was
re-verified from scratch before this patch was written (203 pytest + 290
contract checks green, CI green on `c956678`, incident reproduced,
production re-probed — see `UAEG_TEST_RESULTS.md` §6 and
`UAEG_CURRENT_STATE.md` §6). All changes are again minimal and
defect-driven; nothing outside the confirmed defect class was touched.

| File | Change | Defect |
|---|---|---|
| `upgrade.sh` | `need_value()` guard on `--version` and `--source` — an actionable `option … requires a value (see --help)` at parse time (before the root check, before any file write, before any network use); option-like tokens (`--version --check`) are treated as missing values. Supersedes round-1 §4's "no defect found" statement for `upgrade.sh`. | D11 |
| `tests/test_installer_cli_contract.py` | +5 checks (31 → 36): `upgrade.sh` missing-value rejection on `--version`/`--source` (pre-root, zero side effects, no raw bash error), option-like-value rejection, and the positive path — a complete `--version 0.4.1 --bootstrap-info` on a root-gate-disabled copy exits 0 before any network/updater activity, proving the guard never rejects valid invocations. | D11 lock |
| `report/evidence/*_round2_20261109.txt` (4 files) | NEW: full-suite re-run evidence (208 passed / 0 failed), contract-suite re-run (290 PASS / 0 FAIL), fresh production probe (15:56:58 UTC), incident + D11 before/after reproduction transcript. | — |
| `report/UAEG_*.md` (5 files) | Round-2 sections appended: independent verification record, D11, timeline erratum, refreshed production state and delivery record. | — |
| `report/PRODUCTION_RECOVERY_V041.md` | Production-state table refreshed (15:57 UTC probes; v0.4.1 confirmed running since ≈ 03:00 UTC) + explicit Step-1-complete / Steps-2–4-pending status note. | — |

Verification performed BEFORE pushing (this session, fresh venv):

- `pytest tests/` → **208 passed, 0 failed** (203 round-1 + 5 new)
- `bash tests/test_upgrade_contract.sh` → **290 PASS / 0 FAIL**
- Manual D11 before/after reproduction (evidence file above)
- `bash -n` clean on the patched `upgrade.sh` (run as part of the suite's
  script executions)

`updater.sh` intentionally NOT touched (signed release asset; the
same-class papercut there is fail-safe and documented as a known
limitation for the next signed release — `UAEG_ROOT_CAUSE_ANALYSIS.md`
§5). No CI workflow change needed: the new checks live in an existing,
already-wired test file.

Round-2 patch-set commit: **`2c3b66db1f90aad17cb597a2369dcc9eca9b87d4`** —
pushed to `origin/main` and verified via `git ls-remote`; GitHub CI run
42653244177: **all 5 jobs success** (Python 3.11/3.12/3.13 test matrix,
upgrade-contract suite, installer smoke). This SHA-recording amendment
itself is the follow-up docs commit on `main` (a SHA cannot be embedded
in the commit it identifies); final HEAD: see `git log -1`.
