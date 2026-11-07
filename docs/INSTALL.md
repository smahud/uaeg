# Installation

How to install, verify, upgrade, and remove UAEG on a Debian VPS, using either the git
checkout installer or the integrity-verified one-line installer.

## Prerequisites

- Debian 13 recommended (Debian 12 supported), with Python 3.11+ installed or installable
  (Debian 13 ships Python 3.13).
- Root access. The installer runs as root and the service runs as root by design
  (see [SECURITY.md](./SECURITY.md)).
- A VPS dedicated to this purpose. Authenticated agents get full root (FR-010).
- A public domain with an A/AAAA record pointing at the VPS (recommended — enables Let's
  Encrypt), or a public IP with an operator-provided certificate.
- Inbound TCP open on the MCP port you choose (default 3902). Port 80 must be free and
  reachable during certificate issuance if you use `--domain` (certbot standalone).
- Network egress for `apt` and `pip`.

## Option A — git installation (FR-001)

```bash
git clone https://github.com/smahud/uaeg.git
cd uaeg
sudo ./install.sh --domain gw.example.com
```

On a supported OS (Debian 12/13) the installer runs unattended when you pass flags — the
only prompt is the confirmation on an unsupported OS, which `--yes` auto-accepts.

### Installer flags

| Flag | Default | Description |
|------|---------|-------------|
| `--port N` | `3902` | Public MCP HTTPS port |
| `--dashboard-port N` | `3903` | Dashboard HTTP(S) port |
| `--dashboard-bind {ip\|lan\|loopback\|public}` | `loopback` | Dashboard bind address. `lan` prefers a private (RFC1918) address (10/8, 172.16/12, 192.168/16) and **refuses** to auto-bind a public address when no private one exists — it tells you to use loopback, an explicit private IP, or add a private interface; a literal IP address also works. `public` binds `0.0.0.0` with `dashboard.tls=true` (HTTPS) — it **requires** `sudo uaeg pass` to have been run first and the installer aborts otherwise |
| `--domain DOMAIN` | — | Public DNS name. Provisions a Let's Encrypt certificate via certbot standalone (port 80 must be free) |
| `--public-ip IP` | — | Use a public IP as the public host (pair with `--cert`/`--key`) |
| `--cert FILE` `--key FILE` | — | Manual TLS: use operator-provided certificate files |
| `--self-signed` | default fallback | Generate a self-signed certificate (local testing only) |
| `--data-dir DIR` | `/var/lib/uaeg` | Data directory (database, job files, generated TLS material) |
| `--config-dir DIR` | `/etc/uaeg` | Configuration directory |
| `--install-dir DIR` | `/opt/uaeg` | Install directory (plain copy of the source plus virtualenv) |
| `--no-service` | — | Skip systemd unit installation (containers, testing) |
| `--open-firewall` | — | Open the MCP port with `ufw` when `ufw` is active (otherwise the exact command is only printed) |
| `--version X.Y.Z` | the release this script belongs to | Standalone mode: install this release tag (or set the `UAEG_VERSION` environment variable) |
| `--yes` | — | Non-interactive; auto-accept the unsupported-OS confirmation |

### Example installs

Production with a domain and certbot, dashboard reachable from the LAN:

```bash
sudo ./install.sh --domain gw.example.com --dashboard-bind lan
```

Public IP only, with an operator-provided certificate (e.g. a certificate that covers the
IP address):

```bash
sudo ./install.sh --public-ip 203.0.113.10 \
     --cert /root/gw.crt --key /root/gw.key
```

LAN-only test box with a self-signed certificate (hosted clients will usually reject
self-signed certificates — see [LIMITATIONS.md](./LIMITATIONS.md); use `curl -k` for local
testing):

```bash
sudo ./install.sh --self-signed --dashboard-bind lan
```

Dashboard reachable from the **internet** over HTTPS with password login (v0.3.0) — set
the dashboard password FIRST, then re-run the installer (it aborts if the password is
missing):

```bash
sudo uaeg pass
sudo ./install.sh --domain gw.example.com --dashboard-bind public
# the dashboard is then at https://gw.example.com:3903 (open it: ufw allow 3903/tcp)
```

## Option B — one-line verified install (FR-002)

The release artifact is a versioned `install.sh` with a SHA-256 checksum. Verify before
running — never execute an unverified, mutable script as root:

```bash
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh.sha256
sha256sum -c install.sh.sha256
sudo bash install.sh --domain gw.example.com
```

Line by line: download the script, download its checksum, verify integrity
(`install.sh: OK`), then run it. The verified script accepts the same flags as above; when
it detects it is running standalone (no repository next to it) it downloads the matching
versioned release archive from GitHub and installs from that. Pin a specific version with
`--version X.Y.Z` (or the `UAEG_VERSION` environment variable); the default is the release
this `install.sh` belongs to. The integrity chain is: the script is checksum-verified by
you, and the source archive travels over HTTPS from github.com.

## What the installer does

1. Checks the OS (Debian 12/13) and Python 3.11+.
2. Installs `python3`, `python3-venv`, `openssl`, and `curl` via `apt`.
3. Prepares the install directory (default `/opt/uaeg`): a plain copy of the source (no
   `.git`, made with `tar`) plus a `.venv`, with the package installed via
   `pip install -e .`.
4. Symlinks `/opt/uaeg/.venv/bin/uaeg` to `/usr/local/bin/uaeg`.
5. Writes the configuration to `/etc/uaeg/config.json` (mode 0600) from the chosen flags
   (upgrades preserve the existing configuration — see below).
6. Provisions TLS: certbot standalone for `--domain` (installing a renewal deploy hook at
   `/etc/letsencrypt/renewal-hooks/deploy/uaeg.sh` that restarts UAEG after each successful
   renewal), copies the files given with `--cert`/`--key`, or generates a self-signed pair
   under `/var/lib/uaeg/tls/`.
7. Installs the systemd unit `/etc/systemd/system/uaeg.service` (`Restart=on-failure`),
   generated by the installer with parameterized paths — the checked-in
   `systemd/uaeg.service` is a reference copy with default paths — enables and starts it.
8. Polls `/healthz` (on the effective port from the written configuration) until the
   service answers.
9. Prints the connection URL and next steps, and — if `ufw` is detected — either opens the
   MCP port itself (`--open-firewall`) or prints the exact `ufw allow` command to use.

The installer does not touch unrelated system configuration beyond the items above.

## Post-install verification

```bash
sudo systemctl status uaeg --no-pager   # should be active (enabled)
uaeg status                             # service, health, listeners, counts
uaeg doctor                             # full diagnostics
uaeg generate --name "first-agent"      # create your first key (secret shown once)
```

Then connect an agent: [USAGE.md](./USAGE.md).

## Firewall notes

Open the MCP port to the public internet. Keep the dashboard port closed unless it is
deliberately public:

```bash
sudo ufw allow 3902/tcp    # MCP, public
# 3903 (dashboard): keep it closed while it is LAN/loopback-bound; open it only for a
# public dashboard (--dashboard-bind public: password login + HTTPS enforced)
```

If you used `--domain`, port 80 must be free and reachable for certbot's standalone
challenge. Certbot standalone also needs port 80 when it renews, so either keep it open or
re-open it before renewal is due; after each successful renewal the deploy hook
(`/etc/letsencrypt/renewal-hooks/deploy/uaeg.sh`, written by the installer) restarts UAEG so
the new certificate is loaded.

## Upgrading

Since v0.3.1 upgrading is **one command**, governed by the
[safe-upgrade contract](./UPGRADE_CONTRACT.md) (contract v2 since v0.3.2):
automatic installation detection, a consistent pre-upgrade backup (database,
config, keys metadata, TLS material, the app itself), in-place upgrade with
the existing configuration preserved, post-upgrade zero-data-loss
verification, and automatic rollback to the pre-upgrade state if anything
fails.

Since v0.3.2 the release source is always fetched into an **isolated staging
directory** — your git checkout (if any) is never read for content or
modified, and an untracked `upgrade.sh` downloaded into it cannot block the
upgrade (this was the v0.3.1 incident). Both flows below are equivalent:

From any directory on the VPS — download the script, verify its checksum,
run it (it clones the target release by itself):

```bash
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
sha256sum -c upgrade.sh.sha256
sudo bash upgrade.sh
```

Or from the operator's git checkout (no `git pull` needed — the checkout is
left untouched):

```bash
cd /path/to/uaeg-checkout
sudo bash upgrade.sh
```

Useful modes (all safe to run at any time):

| Command | What it does |
|---------|--------------|
| `sudo bash upgrade.sh --check` | Read-only status: install, version, service, keys, backups, available target |
| `sudo bash upgrade.sh --dry-run` | Show the full plan (from → to, backup, downtime) without changing anything |
| `sudo bash upgrade.sh --rollback` | Restore data + config (and app, if retained) from the latest backup |
| `sudo bash upgrade.sh --version X.Y.Z` | Upgrade to a specific release tag |
| `sudo bash upgrade.sh --source DIR` | Upgrade from a local source tree (offline) |
| `sudo bash upgrade.sh --allow-downgrade` | Explicitly permit a downgrade |

What the upgrade guarantees (details and enforcement in
[UPGRADE_CONTRACT.md](./UPGRADE_CONTRACT.md)):

- **Zero data loss.** Agent keys, jobs, sessions, audit logs and the
  dashboard password are never touched; the database is backed up through
  SQLite's online backup API (a transactionally consistent snapshot) before
  anything changes, and after the upgrade the script *proves* the keys are
  identical, the DB passes `quick_check`, row counts did not decrease and
  the effective configuration is unchanged — evidence is written to
  `RECORD.json` + `before/after-state.json` in the backup directory
  (`/var/backups/uaeg/upgrade-<ts>/`).
- **Automatic rollback.** Any failure (installer error, failed health check,
  failed verification, Ctrl+C) restores the pre-upgrade app, data and
  configuration, restarts the service and exits with code `2`.
- **Non-disruptive.** No bind/TLS/port flags are ever applied during an
  upgrade; the existing configuration wins (FR-003). MCP agent keys and
  connections stay valid; the only impact is one controlled service restart
  (±15–60 s downtime).
- **No secrets in logs.** The script never creates, prints or stores
  passwords; `uaeg pass` stays interactive-only. Verifier hashes never
  appear in logs or records; backups stay on the VPS with `0700`
  permissions and are never committed to the repository.

Exit codes: `0` ok · `1` refused/failed · `2` failed and rolled back ·
`3` no installation found · `4` dirty git tree · `5` downgrade refused.

The old manual procedure (re-run `install.sh` from a fresh checkout) still
works — `upgrade.sh` uses it internally in upgrade mode — but it performs
none of the backup/verification steps above, so prefer `upgrade.sh`.

## Uninstall

```bash
sudo ./uninstall.sh            # stops/disables the service, removes the unit and /opt/uaeg
```

`uninstall.sh` keeps `/etc/uaeg` (configuration) and `/var/lib/uaeg` (database, keys
verifiers, job output) so a reinstall restores your previous state — keys and data are
untouched, and the installer preserves the existing configuration (see
[Upgrading](#upgrading)). To remove everything:

```bash
sudo ./uninstall.sh --purge    # also deletes /etc/uaeg and /var/lib/uaeg
```

## Next steps

- [USAGE.md](./USAGE.md) — keys, connecting an agent, tool walkthroughs
- [CONFIGURATION.md](./CONFIGURATION.md) — schema, TLS modes, changing ports
- [ACCEPTANCE.md](./ACCEPTANCE.md) — the AT-01..AT-19 acceptance runbook
