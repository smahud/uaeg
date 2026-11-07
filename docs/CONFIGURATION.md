# Configuration

Every UAEG setting: where configuration lives, the full JSON schema with defaults, TLS modes,
dashboard network access, port changes, and persistence semantics.

## Locations and precedence

The configuration file is `/etc/uaeg/config.json` (mode 0600, owned by root). Settings
resolve as: **config file > built-in default** — there are no per-setting environment
overrides. The only environment variables UAEG recognizes are:

| Variable | Effect |
|----------|--------|
| `UAEG_CONFIG` | Path to the config file (default `/etc/uaeg/config.json`) |
| `UAEG_DATA_DIR` | Override the data directory (default `/var/lib/uaeg`) |
| `UAEG_DEV_INSECURE_HTTP` | When set to `1`, serves the MCP endpoint without TLS (a loud log warning is emitted). **Development only — never in production.** |

If you relocate the data or config directory after installation, set the corresponding
variable for both your shell and the systemd unit, or the service and CLI will disagree
about where state lives.

## Path layout

| Path | Contents |
|------|----------|
| `/opt/uaeg` | Plain copy of the source plus `.venv` (editable install, no `.git`) |
| `/usr/local/bin/uaeg` | Symlink to `/opt/uaeg/.venv/bin/uaeg` |
| `/etc/uaeg/config.json` | Configuration (mode 0600) |
| `/var/lib/uaeg/uaeg.db` | SQLite database (WAL mode): keys, jobs, sessions, exec logs |
| `/var/lib/uaeg/jobs/<job_id>/` | Per-job files: `stdout`, `stderr`, `pid`, `exit` |
| `/var/lib/uaeg/tls/self-signed.{crt,key}` | Generated self-signed certificate |
| `/etc/systemd/system/uaeg.service` | systemd unit (`Restart=on-failure`) |

## Full schema

Defaults shown; the config file needs only the keys you want to change.

```json
{
  "public_host": "localhost",
  "mcp": { "bind": "0.0.0.0", "port": 3902 },
  "dashboard": { "bind": "127.0.0.1", "port": 3903, "allow_public": false },
  "tls": { "mode": "self-signed", "cert": null, "key": null },
  "exec": {
    "shell": "/bin/bash",
    "default_cwd": "~",
    "default_timeout_s": 120,
    "max_output_bytes": 1048576,
    "max_sessions": 16,
    "pty_buffer_bytes": 262144,
    "restricted_paths": []
  },
  "network": { "allowed_dashboard_cidrs": null }
}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `public_host` | string | `"localhost"` | Host name or IP agents use to reach the MCP endpoint; used in the printed connection URL and as the self-signed certificate identity |
| `mcp.bind` | string | `"0.0.0.0"` | Bind address of the public MCP listener |
| `mcp.port` | integer | `3902` | Public MCP HTTPS port |
| `dashboard.bind` | string | `"127.0.0.1"` | Dashboard bind address; a wildcard requires `dashboard.allow_public` |
| `dashboard.port` | integer | `3903` | Dashboard HTTP port |
| `dashboard.allow_public` | boolean | `false` | Explicit opt-in to bind the dashboard on all interfaces |
| `tls.mode` | string | `"self-signed"` | One of `certbot`, `manual`, `self-signed` |
| `tls.cert` | string \| null | `null` | Certificate file path (required for `manual`) |
| `tls.key` | string \| null | `null` | Private key file path (required for `manual`) |
| `exec.shell` | string | `"/bin/bash"` | Shell used for `shell_exec` and PTY sessions |
| `exec.default_cwd` | string | `"~"` | Default working directory for executions |
| `exec.default_timeout_s` | integer | `120` | Default sync `shell_exec` timeout in seconds |
| `exec.max_output_bytes` | integer | `1048576` | Per-stream capture cap (bytes) for synchronous `shell_exec` |
| `exec.max_sessions` | integer | `16` | Maximum concurrent PTY sessions |
| `exec.pty_buffer_bytes` | integer | `262144` | Per-session PTY output ring buffer (bytes) |
| `exec.restricted_paths` | array | `[]` | Advisory restricted directories, communicated via `server_info` — **not a security boundary** (FR-014) |
| `network.allowed_dashboard_cidrs` | array \| null | `null` | Optional CIDR allowlist for dashboard requests (e.g. `"192.168.1.0/24"`, `"10.0.0.0/8"`). Since 0.2.0 entries are validated at startup and an invalid entry refuses to start (fail closed); `null` disables the feature |

## Validation rules

The service validates configuration at startup and refuses to start with clear errors
([FR-020](./TROUBLESHOOTING.md#1-service-will-not-start)):

- `mcp.port` must not equal `dashboard.port`.
- `tls.mode: "manual"` requires `tls.cert` and `tls.key` files that exist on disk.
- `tls.mode: "certbot"` rejects `public_host` set to `localhost`. A public IP technically
  passes validation, but certbot needs a real DNS domain to issue a certificate — use a
  domain in practice.
- `dashboard.bind` set to a wildcard (`0.0.0.0`) is rejected unless
  `dashboard.allow_public: true` is set explicitly (FR-015).
- `network.allowed_dashboard_cidrs` entries must be valid CIDR networks; since 0.2.0 an
  invalid entry is a clear startup error instead of being silently ignored.

## TLS modes

| Mode | Use when | Notes |
|------|----------|-------|
| `certbot` | You have a public domain | Installer provisions a Let's Encrypt certificate via certbot standalone; port 80 must be free during issuance and renewal |
| `manual` | Public IP only, or an existing certificate | You provide trusted cert/key files (e.g. a certificate covering your IP address); UAEG serves them as given |
| `self-signed` | Local testing only | Zero-config; the generated pair lands in `/var/lib/uaeg/tls/`. **Most hosted clients reject self-signed certificates** — do not use for real agents |

Guidance (FR-017): the deployment must present a certificate trusted by the intended MCP
client. Prefer `--domain` + certbot. With only a public IP, obtain a trusted certificate
that covers the IP and use `manual`; a domain-issued certificate will not match an IP, and
UAEG never silently falls back to plain HTTP on the public listener (the only exception is
the `UAEG_DEV_INSECURE_HTTP` development escape hatch).

## Dashboard network access

The dashboard has **no login**; its network placement is the boundary (FR-015, FR-018):

1. **Bind address** (`dashboard.bind`) — default `127.0.0.1` (loopback only). The
   installer's `--dashboard-bind lan` prefers a private (RFC1918) address and refuses to
   auto-bind a public address when no private one exists; a literal IP also works. A
   wildcard bind is refused unless `dashboard.allow_public: true` is set explicitly.
2. **Optional CIDR allowlist** (`network.allowed_dashboard_cidrs`) — when set, requests
   from source addresses outside the listed networks are rejected even if they reach the
   listener. Invalid entries are rejected at startup (fail closed) — since 0.2.0 they are
   a startup error, not a silent ignore.

Use both when the dashboard must be reachable from a LAN segment:

```bash
uaeg config set dashboard.bind 192.168.1.20
uaeg config set network.allowed_dashboard_cidrs '["192.168.1.0/24"]'
sudo systemctl restart uaeg
```

## Changing ports safely

Both ports are configurable (FR-003). To move the MCP port from 3902 to 3950:

```bash
uaeg config set mcp.port 3950
sudo systemctl restart uaeg        # validation runs at startup
uaeg status                        # confirm the listener moved
sudo ufw allow 3950/tcp            # update the firewall
sudo ufw delete allow 3902/tcp     # remove the old rule if no longer needed
```

Then update the agent's server URL to `https://HOST:3950/mcp` — existing MCP sessions on the
old port are gone the moment the service restarts. The same procedure applies to
`dashboard.port`; never open the dashboard port publicly.

## Persistence semantics

- The config file is the source of truth for settings and survives restarts and upgrades
  (FR-003). It is read at service start; `uaeg config set` writes the file immediately, but
  changes take effect only after `systemctl restart uaeg`. Since 0.2.0 the installer
  preserves the existing configuration on upgrade and applies only explicitly passed
  flags — versions before 0.2.0 reset the config on upgrade.
- Keys, jobs, session records, and audit logs live in SQLite, not in the config; they are
  unaffected by config edits.
- Installer upgrades and `uninstall.sh` without `--purge` leave `/etc/uaeg` and
  `/var/lib/uaeg` intact, so configuration and state survive reinstalls (FR-019).

## Managing configuration with the CLI

```bash
uaeg config show                                  # full effective config
uaeg config get tls.mode                          # -> self-signed
uaeg config set exec.default_timeout_s 300        # integers are parsed
uaeg config set dashboard.bind 192.168.1.20       # strings as-is
uaeg config set network.allowed_dashboard_cidrs '["10.0.0.0/8"]'
sudo systemctl restart uaeg
```

`get`/`set` take dotted key paths. Edit the file directly only if you also keep it valid
JSON — startup validation will reject malformed files.

## Examples

Different ports and a longer default timeout:

```json
{
  "public_host": "gw.example.com",
  "mcp": { "port": 443 },
  "dashboard": { "port": 3903 },
  "exec": { "default_timeout_s": 300 }
}
```

Manual TLS with operator-provided certificates:

```json
{
  "public_host": "203.0.113.10",
  "tls": { "mode": "manual", "cert": "/etc/uaeg/gw.crt", "key": "/etc/uaeg/gw.key" }
}
```

LAN dashboard with CIDR allowlist and advisory restricted paths:

```json
{
  "dashboard": { "bind": "192.168.1.20" },
  "network": { "allowed_dashboard_cidrs": ["192.168.1.0/24"] },
  "exec": { "restricted_paths": ["/etc/uaeg", "/var/lib/uaeg"] }
}
```

Remember: `restricted_paths` is advisory — the agent is told to leave these alone, but a
root shell is not stopped by them ([LIMITATIONS.md](./LIMITATIONS.md)).
