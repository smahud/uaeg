# Production Recovery Runbook — v0.4.1 (MicroTix VPS, host.zerotier.my.id)

**Date:** 2026-11-09 · **Mission P0-1 (runtime-consistency fix) + P0-3
(dashboard 3903 restoration)** · **Executor: the VPS operator (root)** —
this audit environment has GitHub access only (no VPS credentials); every
command below was verified in the isolated lab against the real scripts,
and the release assets are public.

**Revision 2 (2026-11-09, installer-contract audit):** revision 1 shipped a
`--repair` invocation with flags that do not exist (`--tls-mode`,
`--cert-file`, `--key-file`) — the repair died with
`ERROR: unknown option: --tls-mode` **before any mutation** (safe failure,
nothing was changed; reproduced in `report/evidence/` of the audit). The
canonical TLS interface of `install.sh` is exactly:
`--domain DOMAIN` · `--cert FILE --key FILE` · `--self-signed`.
This revision corrects the invocation, the `uaeg config set` ordering
(each `set` validates the WHOLE config, so `dashboard.bind 0.0.0.0` must
come LAST), and replaces the `curl -sk` external check with real TLS
verification (`-k` disables validation and is not proof of trusted TLS).
All corrected commands were re-verified against the real parser and are
enforced by the regression suite (`tests/test_installer_cli_contract.py`).

**Current production state (external probes, round 2 — 2026-11-09 15:56:58 UTC;
evidence: `report/evidence/production_probe_round2_20261109.txt`):**

| Endpoint | State |
|---|---|
| `https://host.zerotier.my.id:3902/healthz` | OK — UAEG **v0.4.1**, uptime ≈ 13 h (continuously up since ≈ 03:00 UTC) |
| `https://host.zerotier.my.id:3902/mcp` | 401 without key (auth intact) |
| `https://host.zerotier.my.id:3903/login` | **connection refused — the P0 still to fix** |

**Status note (round 2, 15:57 UTC):** Step 1 below (safe upgrade to
v0.4.1) is **COMPLETE** in production — the healthz version plus the
continuous ~13 h uptime prove it, and prove that no restart or
configuration change has happened since. **Steps 2–4 remain to be executed
by the operator.** The round-2 independent re-audit (see
`report/UAEG_CURRENT_STATE.md` §6) re-verified the whole patch-set from
scratch (208 pytest + 290 upgrade-contract checks, 0 failures) and found
one additional small defect in `upgrade.sh` (fixed on `main`; does not
affect the commands in this runbook — all of which pass complete values).
The historical probe table from revision 2 (02:41 UTC, v0.4.0) is
superseded by the one above.

---

## Why this is safe now (what v0.4.1 changed)

The failed `--repair` on 2026-11-08 happened because the installer copied
an OLDER source over `/opt/uaeg` BEFORE validating the existing
configuration against it (root cause: `report/ROOT_CAUSE_ANALYSIS.md`,
Insiden 3; reproduction: `report/evidence/incident_repro_v041.txt`).
v0.4.1 refuses that entire class **before touching anything**:

- older source over newer installed app → **exit 5, nothing changed**;
- source whose schema rejects the existing config (e.g. no
  `dashboard.tls`) → **`CONFIG_INCOMPATIBLE`, nothing changed**;
- every config-touching step runs with a sanitized interpreter whose
  loaded `uaeg` origin is asserted.

## Step 1 — upgrade the installation to v0.4.1 (safe, verified channel)

On the VPS as root, from anywhere (e.g. `/root`):

```bash
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh.sha256
sha256sum -c install.sh.sha256          # MUST print: install.sh: OK
sudo bash install.sh                    # verified safe upgrade to v0.4.1
```

Notes:
- This is the **standalone** installer: it fetches the v0.4.1 source,
  verifies the Ed25519-signed manifest, and runs the safe-upgrade pipeline
  (backup → staged deploy → verify → rollback on failure). Your keys,
  database, dashboard password and configuration are preserved.
- Releases are now signed with `uaeg-release-4` (key rotation 3→4 —
  `docs/BOOTSTRAP.md`). A bootstrap/installer downloaded during v0.3.4–
  v0.4.0 will refuse v0.4.1 with an explicit rotation message; the fresh
  download above is the documented one-time refresh.
- If you previously ran `--repair` from an old clone: do NOT re-run that
  old installer. Use the fresh file above (it will refuse stale-source
  repairs with exit 5 by design — that refusal is the fix working).

Expected end state: `uaeg version` → `0.4.1`, `systemctl status uaeg`
active, and the installer prints a **verified dashboard listener** line.

## Step 2 — expose the dashboard on 0.0.0.0:3903 (fail-closed prerequisites)

The dashboard requires, in this order (nothing is exposed until all hold):

```bash
# 2a-0. Preflight (read-only — nothing is changed; abort if any check fails)
systemctl is-active uaeg                                  # must print: active
uaeg version                                               # must print: 0.4.1
sudo uaeg status                                           # listeners + health
# TLS material exists, is currently valid, covers the host, and the
# certificate matches the private key (the same files the healthy MCP
# listener on 3902 already serves):
LE=/etc/letsencrypt/live/host.zerotier.my.id
sudo openssl x509 -in "$LE/fullchain.pem" -noout -dates -ext subjectAltName
[ "$(sudo openssl x509 -in "$LE/fullchain.pem" -noout -modulus)" = \
  "$(sudo openssl rsa  -in "$LE/privkey.pem"    -noout -modulus)" ] \
  && echo "cert/key match" || echo "MISMATCH — STOP"
df -h /var/lib/uaeg                                        # room for backups

# 2a. Dashboard password (skip if already set — it is preserved either way;
#     do NOT reset it if you know it)
sudo uaeg pass                          # interactive, hidden input, twice

# 2b. Apply the public bind with the v0.4.1 installer (from the SAME
#     v0.4.1 file downloaded in step 1 — a current source is now enforced).
#     TLS flags are EXACTLY "--cert FILE --key FILE" (manual mode); there
#     is no --tls-mode / --cert-file / --key-file option (see revision 2
#     note above — verified against the parser and by regression tests).
sudo bash install.sh --repair \
  --dashboard-bind public \
  --cert /etc/letsencrypt/live/host.zerotier.my.id/fullchain.pem \
  --key  /etc/letsencrypt/live/host.zerotier.my.id/privkey.pem
```

Notes:
- `--dashboard-bind public` sets `dashboard.bind=0.0.0.0`,
  `allow_public=true`, `dashboard.tls=true` and reuses your existing
  Let's Encrypt material (the same certificate the healthy MCP listener on
  3902 already serves — valid to 2027-02-05).
- **D10 caveat (audit 2026-11-09):** the RELEASED v0.4.1 installer
  contains a defect where `--dashboard-bind public` never actually sets
  `allow_public`/`tls` (the flag was assigned inside a command-
  substitution subshell and lost, since v0.3.0), so the config write is
  refused unless the existing config ALREADY has
  `dashboard.allow_public=true`. The defect is fixed on the `main`
  branch (regression-tested by `tests/test_e2e_installer_repair.py`)
  and will ship in the next release. Until then, either run step 2b
  from a current `main` checkout (`git clone
  https://github.com/smahud/uaeg.git && cd uaeg`), or use the manual
  alternative below — it works on the installed v0.4.1 as-is and needs
  no installer at all.
- The installer **refuses** to expose the dashboard without a password
  (fail-closed) and now **positively verifies** the dashboard listener
  answers on its effective bind before declaring success.
- If your certificate lives elsewhere, adjust the two paths; `sudo uaeg
  doctor` lists what it found.
- Since the audit that follows this runbook, the installer additionally
  validates the TLS flags BEFORE any mutation: `--cert` without `--key`
  (or vice versa), a missing certificate file, or mixing TLS sources
  (`--domain` + `--cert`, `--self-signed` + `--cert`) each fail immediately
  with an explicit error instead of dying half-way through.
- Equivalent manual alternative (same effect, no installer). **ORDER
  MATTERS**: every `uaeg config set` re-validates the WHOLE configuration,
  so the wildcard bind must be set LAST — setting `dashboard.bind 0.0.0.0`
  first is rejected ("Refusing to bind ... without an explicit opt-in")
  and silently leaves the dashboard on 127.0.0.1 (verified defect in
  revision 1 of this runbook):
  ```bash
  sudo uaeg config set dashboard.allow_public true
  sudo uaeg config set dashboard.tls true
  sudo uaeg config set dashboard.bind 0.0.0.0
  sudo systemctl restart uaeg
  ```

## Step 3 — open the firewall for 3903/tcp

```bash
ufw status | grep -q 3903/tcp || ufw allow 3903/tcp
```

(At 01:44 UTC on 2026-11-08 both 3902/3903 were rejected by a firewall
rule; 3902 was opened later. If you use something other than ufw, allow
3903/tcp in it — the installer prints the exact hint if it detects ufw
active.)

## Step 4 — verify (the acceptance matrix)

From any external machine:

```bash
# 4a. Login page over HTTPS
curl -sI https://host.zerotier.my.id:3903/login | head -1        # HTTP/2 200
# 4b. TLS chain (browser-trusted, correct SAN)
curl -sv https://host.zerotier.my.id:3903/login 2>&1 | grep -E 'subject:|issuer:|SSL cert'
# 4c. Unauthenticated API access is refused
curl -s https://host.zerotier.my.id:3903/api/overview | head -1  # 401/403, NOT data
# 4d. MCP untouched — REAL TLS verification. Do NOT use -k here: a -k
#     response is not proof of trusted TLS (revision 1 of this runbook
#     used -k — corrected). The Let's Encrypt chain must validate as-is:
curl -fsS https://host.zerotier.my.id:3902/healthz
#     expected: {"status":"ok",...,"version":"0.4.1"}
```

Then in a real browser: `https://host.zerotier.my.id:3903` → login page →
correct password → dashboard (Server/Sessions/Logs tabs) → wrong password
→ "Login failed." → logout → login page. The exact expected behavior
matrix is `report/evidence/lab_browser_verification_v040.txt`.

On the VPS: `sudo uaeg status && sudo uaeg doctor`.

## Rollback (if anything looks wrong)

```bash
sudo bash install.sh --rollback        # restores the latest upgrade backup
```

The v0.4.1 upgrade took a consistent backup (app + config + data) before
deploying; rollback is one command and does not touch your agent keys.

## What NOT to do (each was verified as a failure path)

- Do not re-run an installer file that lives inside the old stale clone
  (v0.4.1 will refuse it with exit 5 — recover by using the fresh file).
- Do not delete `dashboard.tls` (or any field) from
  `/etc/uaeg/config.json` to "make the error go away" — that disables
  HTTPS on the public dashboard; the schema-incompatible source is the
  thing that was wrong, not the configuration.
- Do not bind the dashboard publicly without the password step — the
  installer refuses it by design (fail-closed).
- Do not use `--self-signed` for the public dashboard —
  browsers will reject it; the existing Let's Encrypt material is reused.
  (There is no `--tls-mode` flag in any version of the installer — TLS mode
  is selected by the flag you pass: `--domain`, `--cert/--key`, or
  `--self-signed`.)
