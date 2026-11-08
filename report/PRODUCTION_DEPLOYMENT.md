# Production Deployment — MicroTix VPS

**Date:** 2026-11-08 · **Status: PENDING (OPERATOR ACTION REQUIRED).**

> **SUPERSEDED RUNBOOK (v0.4.0):** this document predates the unified
> installer. The current, authoritative production runbook — including the
> v0.3.3 → v0.4.0 upgrade via the single `install.sh` file and the
> public-dashboard enablement steps — is
> **[report/DASHBOARD_PRODUCTION_CHECK.md](./DASHBOARD_PRODUCTION_CHECK.md)**.
> The commands below that download `upgrade.sh` are kept for history only.

The audit environment has no SSH credentials or ZeroTier membership for the
MicroTix VPS (mission clause: "If production access ... is unavailable,
complete all repository-side work and explicitly report the blocked
production steps"). All repository-side work is complete; the release is
published (see RELEASE_VERIFICATION.md). This document is the exact,
copy-pasteable operator runbook, in dependency order.

---

## 0. Current production state (measured 2026-11-08, evidence in
`evidence/production_*_v033.txt`)

- UAEG **v0.3.2** healthy on the MCP side: `https://host.zerotier.my.id:3902`
  live (healthz OK, uptime 2 h+, Let's Encrypt cert valid to 2027-02-05,
  unauthenticated /mcp → 401).
- **Dashboard `https://host.zerotier.my.id:3903` is NOT publicly reachable**
  (connection refused) — root cause: loopback-bound dashboard (installer
  default) and/or firewall. See DASHBOARD_ROOT_CAUSE.md.
- Ports 22/80/443 open. A firewall change during the audit window opened
  3902 (operator activity observed).

## 1. Pre-deployment safety (record before touching anything)

```bash
sudo uaeg version                       # expect: 0.3.2
sudo uaeg status && sudo uaeg doctor    # service + DB + TLS health
sudo uaeg keys list                     # RECORD key ids/status (no secrets leave the box)
sudo uaeg config show > ~/uaeg-config-$(date +%F).json   # config fingerprint
sudo systemctl status uaeg
df -h /var/backups /var/lib/uaeg        # capacity
ls /var/backups/uaeg/                   # existing backup inventory
```

Backups are created automatically by the upgrade itself (consistent SQLite
snapshot + config + app under `/var/backups/uaeg/upgrade-<ts>/`), with
automatic rollback on any failed verification — no manual snapshot is
strictly required; take one anyway if the provider console makes it cheap.

## 2. One-time transition to the permanent bootstrap + upgrade to v0.3.3

```bash
cd ~   # anywhere; NOT inside the old clone is fine too
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
sha256sum -c upgrade.sh.sha256
sudo bash upgrade.sh
```

What this does (v0.3.3, contract v3): resolves the latest release →
downloads the **signed manifest** → verifies the Ed25519 signature →
downloads `uaeg-updater-0.3.3.sh` → verifies SHA-256+size against the signed
manifest → runs the safe-upgrade (detect → backup → isolated staging →
install → verify zero data loss: keys identical, DB integrity, config
preserved, dashboard password intact, /healthz OK → automatic rollback on
failure). **From now on, every future upgrade is just `sudo bash
upgrade.sh` — the same file, never re-downloaded.**

(If you prefer to preview first: `sudo bash upgrade.sh --check` and
`sudo bash upgrade.sh --dry-run` are read-only.)

Post-upgrade verification:

```bash
uaeg version                            # expect: uaeg 0.3.3
sudo uaeg doctor
sudo uaeg keys list                     # identical key set (zero data loss)
systemctl status uaeg
curl -sk https://127.0.0.1:3902/healthz
ls /var/lib/uaeg/upgrade/               # cached updater (offline rollback capability)
ls -l /usr/local/sbin/uaeg-upgrade      # system bootstrap convenience copy
```

Rollback if ever needed: `sudo bash upgrade.sh --rollback` (works offline).

## 3. Enable the public dashboard (the actual incident fix)

```bash
# 3a. Dashboard password (fail-closed prerequisite; skip if already set)
sudo uaeg pass                          # interactive, hidden, entered twice

# 3b. Public bind with TLS (reuses the existing Let's Encrypt certificate)
sudo uaeg config set dashboard.bind 0.0.0.0
sudo uaeg config set dashboard.allow_public true
sudo uaeg config set dashboard.tls true

# 3c. Apply and open the firewall
sudo systemctl restart uaeg
ufw allow 3903/tcp                      # adapt to your firewall if not ufw

# 3d. Verify
sudo uaeg status && sudo uaeg doctor
```

Then from any external browser: `https://host.zerotier.my.id:3903` →
login page → authenticate → dashboard (Keys / Permissions / Sessions / Logs
/ Server) → Sign out. Expected behavior matrix: the lab evidence
`report/evidence/lab_browser_verification_v033.txt` (0 console errors,
favicon 200, full flow).

If 3903 still refuses after 3b/3c: check `sudo ss -tlnp | grep 3903`
(expected `0.0.0.0:3903`), `ufw status`, and `journalctl -u uaeg -n 50`
(the service fails closed with a clear message if the password/TLS
prerequisites are missing).

## 4. MCP agent continuity

No agent action is required: keys, verifiers, sessions metadata and
configuration survive the upgrade (proven by U-4 evidence on every contract
test). Existing hosted-agent connectors keep working after the service
restart; detached background jobs keep running by design.

## 5. Blocked steps (explicit)

| Step | Status | Blocker |
|---|---|---|
| SSH diagnosis/execution on the VPS | BLOCKED | No SSH credentials/ZeroTier membership provided to the audit environment |
| Browser verification against production | BLOCKED (depends on §3) | Same |
| Post-deploy production evidence capture | BLOCKED (depends on §3) | Same |

Everything else in the mission (audit, fixes, tests, release publication
with verified assets, runbooks) is COMPLETE.
