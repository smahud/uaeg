# Root Cause Analysis — Installer-Contract Defect & Dashboard Restoration Blockers (2026-11-09)

Incident window: 2026-11-08 → 2026-11-09 · Environment: MicroTix Debian VPS
(`host.zerotier.my.id`) · Scope: `install.sh` CLI contract, recovery
runbook, dashboard public-bind path.

---

## 1. The reported incident (verified, reproduced)

The operator ran the runbook's repair command:

```bash
bash install.sh --repair \
  --dashboard-bind public \
  --tls-mode manual \
  --cert-file /etc/letsencrypt/live/host.zerotier.my.id/fullchain.pem \
  --key-file  /etc/letsencrypt/live/host.zerotier.my.id/privkey.pem
```

Result: `ERROR: unknown option: --tls-mode (see --help)`, exit 1.

**Reproduced in this audit** against `main` @ `30d047d`: same message, exit 1,
zero mutation (parse-time death precedes the root check, apt, service stop
and every file write — proven by `tests/test_installer_cli_contract.py::
test_incident_unknown_tls_mode_flag_rejected_before_mutation`).

### Immediate cause (D1)

`report/PRODUCTION_RECOVERY_V041.md` (revision 1) documented an installer
invocation with three flags that **do not exist**. The parser's real TLS
interface has always been (since the unified installer):

```
--domain DOMAIN            # certbot / Let's Encrypt
--cert FILE --key FILE     # manual, operator-provided material
--self-signed              # local testing
```

There is no `--tls-mode`, `--cert-file` or `--key-file` in any released
version (verified: `git log -S` across the tags; every release's `install.sh`
case-statement).

### Why it could happen (systemic cause)

The runbook was written **without an executable contract between the
documentation and the parser**. Nothing failed when prose and code diverged:
no test compared documented invocations with the parser's accepted options.
This audit closes that hole with
`tests/test_installer_cli_contract.py::test_live_docs_use_only_real_installer_flags`,
which extracts every `install.sh` flag used in bash blocks across
`docs/*.md`, `README.md` and the recovery runbook, and fails on any flag the
parser does not accept.

## 2. Deeper defects found by this audit (the "do not stop at the first
error" mandate)

Each defect below was **reproduced** before being fixed (details and evidence
commands in `UAEG_PATCH_CHANGELOG.md` / `UAEG_TEST_RESULTS.md`):

### D10 (critical, latent since v0.3.0) — `--dashboard-bind public` never worked through the installer

`resolve_dashboard_bind()` set `DASHBOARD_PUBLIC=1` **inside a command
substitution** (`DASH_BIND_IP="$(resolve_dashboard_bind)"`), i.e. in a
subshell — the assignment was silently lost. Consequences in every version
v0.3.0 → v0.4.1:

1. the "public dashboard requires a password" prerequisite check was
   **skipped** (fail-open on the check);
2. the config writer omitted `dashboard.allow_public=true` and
   `dashboard.tls=true`;
3. `Config.validate()` then refused the wildcard bind ("Refusing to bind the
   dashboard to all interfaces without an explicit opt-in") — **after** the
   service had been stopped and the application already redeployed, forcing
   a rollback.

So even the *correctly-spelled* runbook command would have failed on the
released v0.4.1 installer. Reproduced by
`tests/test_e2e_installer_repair.py` (the test failed with exactly this
CONFIG_ERROR before the fix, passes after). Introduced by commit `af809db`
("feat(dashboard): native password authentication, public HTTPS binding,
uaeg pass — v0.3.0"); `git log -S` confirms the broken assignment is present
in every tag since.

**Why every prior test missed it:** CI's installer job and the upgrade
contract suite only exercise `--dashboard-bind loopback` / `--self-signed`;
the dashboard-auth e2e suites build configs directly in Python and bypass
`install.sh` entirely. No test ever drove `--dashboard-bind public` through
the real installer. That exact path is now regression-locked end-to-end.

### D4 (high) — the runbook's manual alternative silently left the dashboard on loopback

Every `uaeg config set` re-validates the **whole** configuration before
saving. The runbook (and `report/DASHBOARD_ROOT_CAUSE.md` §6) ordered the
sequence:

```bash
uaeg config set dashboard.bind 0.0.0.0      # FIRST -> rejected, NOT saved
uaeg config set dashboard.allow_public true
uaeg config set dashboard.tls true
```

The first command fails validation ("Refusing to bind … without an explicit
opt-in"), rc=1, value not persisted — an operator following the runbook
verbatim ends with `dashboard.bind=127.0.0.1`, restarts, and the dashboard
stays private with **no error pointing at the cause**. Verified
empirically; corrected order (`allow_public` → `tls` → `bind`, LAST) works
and is now locked by
`tests/test_unit_regression_configset_pass.py` and a runbook-structure test.

### D7 (medium) — `--cert` without `--key` died only after the app was deployed

The pair check (`manual TLS requires both --cert and --key`) lived in the
TLS-provisioning section, which runs **after** the application is copied to
`/opt/uaeg` and the venv rebuilt. On a fresh install this left a partial
installation (app present, no config, no unit). Now validated at parse
time, before any mutation, together with file existence.

### D8 (medium) — conflicting TLS sources silently last-won

`--domain gw.example.com --cert x --key y` (or `--self-signed --cert …`)
did not error: the last flag silently selected the TLS mode. Now rejected
pre-mutation with an explicit "choose exactly ONE" error.

### D9 (medium) — silent TLS downgrade when address detection fails

On a repair with explicit `--cert/--key` but no `--domain/--public-ip`, if
`ip route get 1.1.1.1` failed (no default route), the installer silently
rewrote `TLS_MODE="self-signed"` and would have written
`tls.mode=self-signed` into the configuration — the exact "silent
configuration downgrade" class the mission forbids. Now: an explicit TLS
source is never replaced by the self-signed fallback.

### D6 (low) — missing option values produced raw bash errors

`bash install.sh --cert` (as the last argument) died with
`line NNN: $2: unbound variable` instead of an actionable message. Now:
`ERROR: option --cert requires a value (see --help)`.

### D5 (low) — duplicated `--repair` paragraph in `--help`

A copy-paste artifact printed the v0.4.1 safety paragraph twice. Deduped;
a test now fails on any duplicated help line.

### D3 (low) — `curl -sk` used as production TLS "verification"

Runbook Step 4d "verified" the MCP endpoint with `-k` (validation
disabled) — not proof of trusted TLS. Replaced with full-verification
checks; this audit's external probes demonstrate the correct method
(`Verify return code: 0 (ok)` against the system trust store).

## 3. Why the failure was safe (defense that worked)

Credit where due — the v0.4.1 parser's parse-time rejection is what made
the D1 failure **safe**: the operator's broken command changed nothing
(proven). Likewise the v0.4.1 pre-mutation guards (older-source refusal,
schema-compat probe) are real and contract-tested (T-53/T-54/T-55, 290
checks green). The incident's blast radius was "recovery blocked", not
"data lost" — keys, database and password were never at risk from D1.

## 4. Root-cause statement

**Primary:** the recovery runbook documented an installer CLI contract that
never existed, and no automated contract bound documentation to the parser
(D1–D3). **Contributing:** the documented recovery paths had never been
executed end-to-end by any test — the installer's public-bind branch had
been broken since its introduction (D10), and the documented manual
alternative had an order-dependent silent failure (D4). The combination
meant *neither* documented path to `0.0.0.0:3903` could succeed as written.

**Fix strategy:** minimal, evidence-driven corrections that keep the
canonical interface (`--cert`/`--key` — no compatibility aliases added;
decision rationale in `UAEG_PATCH_CHANGELOG.md` §2) plus a permanent
doc↔parser contract test and an end-to-end repair regression that drives
the exact production recovery command shape.

---

## 5. Round-2 findings (2026-11-09, independent re-audit)

A second session re-ran the entire verification (all claims reproduced:
203 pytest + 290 contract checks green pre-round-2) and swept the remaining
CLI/doc surfaces beyond the TLS flags. One additional defect of the
incident's class was found, plus one report-hygiene erratum:

### D11 (low, fixed this round) — upgrade.sh missing values: raw bash error

`bash upgrade.sh --version` (or `--source`) as the last argument died with
bash's raw `line 152: 2: unbound variable` instead of an actionable
message — the exact D6 class round 1 fixed in `install.sh`, missed in the
sibling bootstrap (round 1's changelog §4 had recorded "updater.sh /
upgrade.sh: no defect found in the audit scope" — that finding is
superseded for `upgrade.sh` by this round).

Why it matters despite being fail-safe: `install.sh`'s own
downgrade-refusal messages — the documented recovery path for the exit-5
class — direct operators to
`sudo bash upgrade.sh --version X.Y.Z --allow-downgrade`, and
`docs/INSTALL.md` / `docs/TROUBLESHOOTING.md` document `--version X.Y.Z`
and `--source DIR`. An operator who truncates the command now gets the
same actionable "requires a value" error class as `install.sh`.

Fixed with the same `need_value()` guard at parse time (before the root
check, before any file write, before any network use). Locked by 5 new
checks in `tests/test_installer_cli_contract.py`: missing-value rejection
(pre-root, zero side effects, no raw bash error) on both options,
option-like-value rejection, and the positive proof that a complete
`--version X.Y.Z --bootstrap-info` passes the parser and exits cleanly
before any network or updater activity.

**Known limitation (deferred to the next release):** `updater.sh` — the
signed, versioned release asset — has the same unguarded `$2` shifts for
its value-taking options (`--backup`, `--version`, `--source`,
`--installer`, `--repo`, `--install-dir`, …). The failure is fail-safe
(dies at parse time, pre-mutation) and already-published release assets
cannot be hot-fixed; the guard should accompany the next signed updater.
Not a blocker for the current recovery: every recovery path passes
complete values, verified by the 290-check contract suite re-run after
the round-2 patch.

### Report-hygiene erratum

Round-1's `UAEG_CURRENT_STATE.md` §2 probe timestamp ("~04:20 UTC,
uptime 2067.5 s") is internally inconsistent with round-2's direct
measurement (15:43 UTC, uptime 45 895.6 s ⇒ last service start ≈ 02:58
UTC). Corrected in `UAEG_CURRENT_STATE.md` §6; no operational conclusion
changes.
