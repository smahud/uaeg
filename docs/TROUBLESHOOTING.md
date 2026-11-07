# Troubleshooting

Symptom-driven diagnosis for UAEG: quick-reference table, detailed sections for each
symptom, a `uaeg doctor` walkthrough, and the documented recovery procedure for when an
agent breaks the server itself (FR-020).

## First-response commands

Run these before anything else; they resolve most situations:

```bash
sudo systemctl status uaeg --no-pager
sudo journalctl -u uaeg -n 100 --no-pager
uaeg status
uaeg doctor
```

## Symptom quick reference

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Service will not start | Config validation error, bad TLS paths, port clash | [Section 1](#1-service-will-not-start) |
| Service starts, then immediately exits ("listener failed") | The other listener could not bind (port in use) — the service refuses to run half-alive | [Section 1](#1-service-will-not-start) |
| Startup fails with an "invalid CIDR" / allowlist error | Invalid entry in `network.allowed_dashboard_cidrs` | [Section 1](#1-service-will-not-start) |
| Database corrupt or unreadable | `serve` exits with code 2; `doctor` reports INTEGRITY CHECK FAILED | [Section 12](#12-database-corrupt-or-unreadable) |
| `/healthz` unreachable | Service down, bind/firewall, TLS mismatch in probe | [Section 2](#2-healthz-unreachable) |
| Agent gets `401 Unauthorized` | Revoked key, typo/truncated key, missing header | [Section 3](#3-agent-gets-401-unauthorized) |
| Agent gets TLS/certificate errors | Self-signed cert, cert does not match host, expired cert, clock skew | [Section 4](#4-agent-gets-tls-errors) |
| `tools/list` is empty / client sees no tools | Client is not sending the auth header (or is hitting the wrong URL) | [Section 5](#5-toolslist-is-empty-or-the-client-claims-no-tools) |
| PTY session not found after restart | Sessions are in-memory and die with the service | [Section 6](#6-pty-session-not-found-after-a-restart) |
| Job status is `interrupted` | VPS rebooted, or service was SIGKILLed before exit was recorded | [Section 7](#7-job-says-interrupted) |
| Dashboard unreachable | Bind address, LAN routing, CIDR allowlist, tunnel missing | [Section 8](#8-dashboard-unreachable) |
| Port already in use | Another process holds 3902/3903 | [Section 9](#9-port-already-in-use) |
| Disk filling up | Large job output files under `/var/lib/uaeg/jobs/` | [Section 10](#10-disk-space-from-job-output-files) |
| UAEG itself is gone/broken | Agent used root to stop or damage it | [Section 11](#11-the-agent-stopped-or-damaged-uaeg) |

## Detailed sections

### 1. Service will not start

**Diagnose:**

```bash
sudo journalctl -u uaeg -n 50 --no-pager
```

Startup failures are almost always config validation (see
[CONFIGURATION.md](./CONFIGURATION.md#validation-rules)): `mcp.port` equal to
`dashboard.port`, `tls.mode: "manual"` with missing/unreadable `tls.cert`/`tls.key` files,
`tls.mode: "certbot"` with `public_host` set to `localhost`, a wildcard `dashboard.bind`
without `dashboard.allow_public: true`, or an invalid entry in
`network.allowed_dashboard_cidrs` (since 0.2.0 a fail-closed startup error, not a silent
ignore — fix or remove the invalid entries, or set the key to `null`). Malformed JSON in
`/etc/uaeg/config.json` is also rejected at startup with the parse error.

A service that **starts and immediately exits** with a `listener failed` log line means one
of the two listeners (MCP or dashboard) could not bind — typically the port is already in
use. The service refuses to run half-alive and exits non-zero (systemd `Restart=on-failure`
retries); free the port per [Section 9](#9-port-already-in-use) and the service comes back.
A startup exit with code 2 and a clean, traceback-free error points at the database instead
(see [Section 12](#12-database-corrupt-or-unreadable)).

**Fix:** correct the setting (`uaeg config set ...` or edit the file), then
`sudo systemctl restart uaeg`. Verify with `uaeg doctor`.

### 2. `/healthz` unreachable

**Diagnose:** `sudo systemctl is-active uaeg` first — if the service is down, go to
[Section 1](#1-service-will-not-start). If it is active, probe locally on the VPS:

```bash
curl -k https://localhost:3902/healthz     # -k because the cert may not match "localhost"
ss -ltnp | grep 3902
```

**Causes and fixes:**

- **Bind address:** if `mcp.bind` is not `0.0.0.0`, the listener only answers on that
  address — probe that address, not `localhost`.
- **Firewall:** from outside, check the provider firewall and `ufw`
  (`sudo ufw status`); only 3902/tcp should be open. Open it: `sudo ufw allow 3902/tcp`.
- **TLS in the probe:** the endpoint is HTTPS. Plain `curl http://...:3902` will fail; use
  `https://` (with `-k` for self-signed) or `uaeg status`, which handles this for you.

### 3. Agent gets `401 Unauthorized`

**Diagnose:** auth is checked per request, so a `401` means the bearer secret did not match
an active key.

- **Revoked key:** `uaeg keys list` — is the key's status `active`? Revoked keys fail
  instantly.
- **Typo or truncation:** secrets are 48 characters starting with `uaeg_`. Check the client
  config for truncation, surrounding quotes, or a duplicated `Bearer Bearer` prefix. The
  header must be exactly `Authorization: Bearer uaeg_...`.
- **Prefix instead of secret:** the value shown by `uaeg keys list` is a prefix for
  identification, never the secret. If you lost the secret, `uaeg keys rotate <KEY_ID>`
  and update the client.
- **Client not sending the header at all:** see [Section 5](#5-toolslist-is-empty-or-the-client-claims-no-tools).
- **Clock (rare):** UAEG keys do not expire, but a badly skewed VPS clock breaks TLS before
  auth is even reached and corrupts audit timestamps. Check `timedatectl` and NTP sync
  (`timedatectl timesync-status`) if requests fail in confusing ways.

**Fix:** match the key in `uaeg keys list`, confirm `LAST USED` advances when the client
tries, rotate or regenerate as needed.

### 4. Agent gets TLS errors

**Diagnose:** identify which failure the client reports: untrusted issuer, hostname
mismatch, or expired certificate. `uaeg doctor` prints subject, SAN, and expiry locally.

- **Self-signed certificate:** most hosted clients refuse it. Fix: reinstall or reconfigure
  with a domain plus certbot (`tls.mode: "certbot"`), or provide trusted manual
  certificates ([INSTALL.md](./INSTALL.md)).
- **Public IP without a matching certificate:** a domain certificate does not validate
  against an IP address. Obtain a trusted certificate that covers the IP, or point a domain
  at the VPS and use certbot.
- **Hostname mismatch:** the URL host must match the certificate's name. If you reach the
  server by IP, the cert must cover that IP; if by domain, the domain must be in the SAN.
- **Expired certificate:** certbot standalone needs port 80 free during renewal; if renewal
  failed, reissue. Confirm with `uaeg doctor` (expiry) afterwards.
- **Clock skew:** if the VPS clock is wrong, a valid certificate can appear expired or not
  yet valid. Check `timedatectl`.

### 5. `tools/list` is empty or the client claims no tools

**Diagnose:** the most common cause is a client that cannot send custom headers — without
`Authorization`, every request is rejected with `401`, and some clients report this as "no
tools" rather than as an auth failure. Verify the client is configured for:

1. a **remote/Streamable HTTP** MCP server (not stdio) at `https://HOST:3902/mcp`,
2. a custom header `Authorization: Bearer <UAEG_AGENT_KEY>`.

**Fix:** reconfigure the client. If the platform offers no way to add external MCP servers
with custom headers, it is unsupported — record it as such (FR-006, [AT-19](./ACCEPTANCE.md)).
While iterating, `uaeg keys list` shows whether `LAST USED` moves: if it never does, your
requests are not reaching auth at all (wrong URL/port).

### 6. PTY session not found after a restart

**Diagnose:** expected behavior. PTY session state is in memory; a UAEG restart closes all
terminals. Session records persist, live sessions do not
([ARCHITECTURE.md](./ARCHITECTURE.md#pty-sessions), [LIMITATIONS.md](./LIMITATIONS.md)).

**Fix:** open a new session with `shell_open`. For work that must survive restarts, use
background jobs (`background=true`), which reattach automatically. Always check
`shell_sessions()` after a client reconnect instead of assuming a session ID is still valid.

### 7. Job says `interrupted`

**Diagnose:** `interrupted` means UAEG cannot account for the process — the VPS rebooted,
or the service was killed with SIGKILL before the exit code was written. It is deliberately
not reported as `running` (FR-009).

**Fix:** nothing to fix in UAEG — the process is gone. Re-run the job if the work matters;
any output captured before the interruption is still available via
`uaeg jobs output <JOB_ID>` or `job_output`. If you see `interrupted` without a known
reboot, check `last reboot` and `sudo journalctl -u uaeg` for SIGKILL/OOM events.

### 8. Dashboard unreachable

**Diagnose:**

```bash
uaeg status                          # which address is the dashboard bound to?
ss -ltnp | grep 3903                 # confirm the listener and its address
```

- **Bound to loopback (default):** `http://127.0.0.1:3903` works only on the VPS itself.
  From your workstation use a tunnel: `ssh -L 3903:127.0.0.1:3903 root@gw`, then open
  `http://localhost:3903`. Or rebind to the LAN (next bullet).
- **Bound to a LAN IP but you are not on that network:** the dashboard is not routed to the
  public internet, by design. Connect from the LAN/VPN, or rebind.
- **CIDR allowlist:** if `network.allowed_dashboard_cidrs` is set, requests from outside
  those networks are rejected. Add your source network
  (`uaeg config set network.allowed_dashboard_cidrs '["192.168.1.0/24"]'`) and restart.
- **Port 3903 not reachable on the LAN:** check routing and any host firewall between you
  and the VPS on the private interface. Never "fix" this by opening 3903 to the public
  internet — the dashboard has no login (FR-015).

### 9. Port already in use

**Diagnose:**

```bash
ss -ltnp | grep -E '3902|3903'
```

**Fix:** free the port (stop the conflicting service), or move UAEG:

```bash
uaeg config set mcp.port 3952
sudo systemctl restart uaeg
sudo ufw allow 3952/tcp     # and update the agent URL
```

Note `mcp.port` and `dashboard.port` must differ; startup validation enforces this.

### 10. Disk space from job output files

**Diagnose:**

```bash
du -sh /var/lib/uaeg/jobs/* | sort -h | tail
```

Background jobs write full stdout/stderr to files under `/var/lib/uaeg/jobs/<job_id>/`;
chatty or runaway jobs can grow them without bound.

**Fix:** cancel runaway jobs (`uaeg jobs cancel <JOB_ID>` or the dashboard Sessions tab) —
both terminate the job's process group (SIGTERM, then SIGKILL).
For completed jobs whose output you no longer need, remove the job directory — job metadata
remains in the database, but `job_output` will no longer serve output for it. Going
forward, redirect verbose output to compressed files inside the command itself
(`./configure > build.log 2>&1`) and read them selectively.

### 11. The agent stopped or damaged UAEG

This is the documented root-risk scenario (FR-020): an authenticated agent can run
`systemctl stop uaeg`, kill the process, uninstall files, rewrite firewall rules, or damage
the OS. `Restart=on-failure` covers crashes, but not a clean stop, a removed unit, or a
broken system.

See [Recovery procedure (FR-020)](#recovery-procedure-fr-020) below. Before restarting
UAEG, revoke the key the agent used (`uaeg keys revoke <KEY_ID>`) if the agent is acting
against your intent.

### 12. Database corrupt or unreadable

**Diagnose:** `uaeg serve` exits with code 2 and a clean error (no traceback) when
`/var/lib/uaeg/uaeg.db` cannot be opened or is corrupt; `uaeg doctor` runs
`PRAGMA quick_check` and reports `database ... INTEGRITY CHECK FAILED`. A database written
by a NEWER UAEG schema version is refused with an "upgrade UAEG first" error.

**Fix (recovery):**

```bash
sudo systemctl stop uaeg
mv /var/lib/uaeg/uaeg.db /var/lib/uaeg/uaeg.db.corrupt-$(date +%Y%m%d)
sudo systemctl start uaeg        # a fresh database is created
```

Keys must be re-issued (`uaeg generate`) — the old key verifiers lived in the moved file.
Before discarding it, the old database can be inspected read-only with `sqlite3`
(job history, session records, the audit log).

## `uaeg doctor` walkthrough

`uaeg doctor` runs every check below and prints one line per check plus a summary. The
**execution user check passes when the service runs as root** — that is the supported
deployment (the service runs as root by design); a non-root run fails the check with an
explanation.

| Check | Verifies | If it fails |
|-------|----------|-------------|
| `config` | Config file exists, parses, and passes validation | The parse/validation error is printed; fix per [Section 1](#1-service-will-not-start) |
| `data dir` | `/var/lib/uaeg` exists | Check the path and `UAEG_DATA_DIR`; confirm it matches the unit's environment |
| `database` | SQLite opens, tables present, and `PRAGMA quick_check` (integrity check) passes | Corruption is reported as `INTEGRITY CHECK FAILED`; see [Section 12](#12-database-corrupt-or-unreadable) |
| `systemd service` | The unit at `/etc/systemd/system/uaeg.service` (generated by the installer) is active | `sudo systemctl status uaeg`; see [Section 1](#1-service-will-not-start) |
| `MCP listener` | The MCP port is listening on the configured address | See [Section 9](#9-port-already-in-use); confirm bind addresses with `ss -ltnp` |
| `dashboard listener` | The dashboard port is listening on the configured address | See [Section 8](#8-dashboard-unreachable) and [Section 9](#9-port-already-in-use) |
| `health endpoint` | `/healthz` answers on the MCP listener | See [Section 2](#2-healthz-unreachable) |
| `TLS certificate` | Certificate subject, SAN, and expiry; warns when expiry is within 30 days | See [Section 4](#4-agent-gets-tls-errors); renew or replace the certificate |
| `execution user` | The service runs as root | Passes on a standard install (by design). A non-root service fails with an explanation; see [SECURITY.md](./SECURITY.md) |
| `exec shell` | The configured `exec.shell` exists on disk | Point `exec.shell` at an installed shell (e.g. `/bin/bash`) and restart |

## Recovery procedure (FR-020)

When UAEG is stopped, the network path is broken, or the OS is damaged, normal access
(SSH, dashboard, MCP) may all be gone. Recovery uses out-of-band access.

**Prerequisites (arrange these before you need them):**

- Provider console access (VNC/serial) that does not depend on SSH or the VPS firewall.
- A recent snapshot, with a restore procedure you have tested at least once.

**Procedure:**

1. **Get a console** from the provider. Do not waste time on SSH if the agent may have
   broken it or the firewall.
2. **Assess before changing anything:** `systemctl status uaeg`, `journalctl -u uaeg -n 50`,
   `df -h`, `ip a`. Look for what the agent did: stopped service, removed files, changed
   firewall rules (`iptables -L` / `nft list ruleset`).
3. **If only UAEG is down:** `sudo systemctl start uaeg`, then `uaeg doctor`. If the unit or
   files were deleted, reinstall from the repo — state in `/etc/uaeg` and `/var/lib/uaeg`
   survives a reinstall (the 0.2.0 installer preserves the existing configuration; see
   [INSTALL.md](./INSTALL.md#upgrading)).
4. **If the firewall locks you out:** reset rules from the provider console, or use the
   provider's firewall (outside the OS) to re-admit your management traffic.
5. **If the OS is damaged:** restore the snapshot. This is why destructive experiments
   require snapshots as a prerequisite, not an afterthought.
6. **Before reconnecting the agent:** revoke the key it used
   (`uaeg keys revoke <KEY_ID>`), review the audit log (dashboard Logs tab or the
   `exec_logs` table) to see exactly what it ran, then issue a fresh key only when you
   understand what happened.
7. **Record the incident.** Update `restricted_paths` (advisory), snapshots, or your agent
   instructions as appropriate — but remember that none of these are hard containment
   ([LIMITATIONS.md](./LIMITATIONS.md)).
