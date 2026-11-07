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

State lives outside the install directory, so upgrades do not touch keys, jobs, or logs
(FR-019). `/opt/uaeg` is a plain copy of the source (no `.git`), so upgrade by re-running
the installer from a fresh checkout — it detects the existing installation and refreshes
it in place:

```bash
cd "$(mktemp -d)"
git clone https://github.com/smahud/uaeg.git .
sudo ./install.sh                            # existing configuration is preserved
uaeg version        # confirm the new version
uaeg doctor         # confirm health
```

Since 0.2.0 the upgrade path preserves configuration and protects itself (FR-003):

- **Configuration is preserved.** Only values explicitly passed as flags on this
  invocation (`--port`, `--dashboard-port`, `--dashboard-bind`, `--domain`, `--public-ip`,
  `--cert`/`--key`, `--self-signed`) are applied; everything set via `uaeg config set` or
  the dashboard survives. A timestamped backup `config.json.bak-<ts>` is still written
  next to the config.
- **Automatic rollback.** The current application directory is backed up to
  `/opt/uaeg.upgrade-bak-<ts>` before anything is replaced. If any step fails — including
  the post-install health check — the installer restores the previous application files
  and configuration and restarts the previous service; on success the rollback backup is
  removed.
- **Effective health check.** The health check uses the effective port/public host from
  the (possibly preserved) configuration, not the installer defaults, so customized
  installs verify correctly.
- **Changing the host or TLS mode on an upgrade** requires passing the TLS flags
  (`--domain`, or `--cert`/`--key`, or `--self-signed`); without TLS flags the existing
  TLS configuration is kept.

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
