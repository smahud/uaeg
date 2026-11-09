# Production Recovery Runbook — v0.4.1 (MicroTix VPS, host.zerotier.my.id)

**Date:** 2026-11-09 · **Mission P0-1 (runtime-consistency fix) + P0-3
(dashboard 3903 restoration)** · **Executor: the VPS operator (root)** —
this audit environment has GitHub access only (no VPS credentials); every
command below was verified in the isolated lab against the real scripts,
and the release assets are public.

**Current production state (external probes, 2026-11-09 02:41 UTC):**

| Endpoint | State |
|---|---|
| `https://host.zerotier.my.id:3902/healthz` | OK — UAEG **v0.4.0**, uptime ≈ 6.3 h |
| `https://host.zerotier.my.id:3902/mcp` | 401 without key (auth intact) |
| `https://host.zerotier.my.id:3903/login` | **connection refused — the P0 to fix** |

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
# 2a. Dashboard password (skip if already set — it is preserved either way;
#     do NOT reset it if you know it)
sudo uaeg pass                          # interactive, hidden input, twice

# 2b. Apply the public bind with the v0.4.1 installer (from the SAME
#     v0.4.1 file downloaded in step 1 — a current source is now enforced)
sudo bash install.sh --repair \
  --dashboard-bind public \
  --tls-mode manual \
  --cert-file /etc/letsencrypt/live/host.zerotier.my.id/fullchain.pem \
  --key-file  /etc/letsencrypt/live/host.zerotier.my.id/privkey.pem
```

Notes:
- `--dashboard-bind public` sets `dashboard.bind=0.0.0.0`,
  `allow_public=true`, `dashboard.tls=true` and reuses your existing
  Let's Encrypt material (the same certificate the healthy MCP listener on
  3902 already serves — valid to 2027-02-05).
- The installer **refuses** to expose the dashboard without a password
  (fail-closed) and now **positively verifies** the dashboard listener
  answers on its effective bind before declaring success.
- If your certificate lives elsewhere, adjust the two paths; `sudo uaeg
  doctor` lists what it found.
- Equivalent manual alternative (same effect, no installer):
  ```bash
  sudo uaeg config set dashboard.bind 0.0.0.0
  sudo uaeg config set dashboard.allow_public true
  sudo uaeg config set dashboard.tls true
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
# 4d. MCP untouched
curl -sk https://host.zerotier.my.id:3902/healthz                 # {"status":"ok",...,"version":"0.4.1"}
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
- Do not use `--tls-mode self-signed` for the public dashboard —
  browsers will reject it; the existing Let's Encrypt material is reused.
