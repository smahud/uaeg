# UAEG — Universal Agent Execution Gateway

UAEG is an open-source gateway that lets a compatible cloud-hosted AI agent execute real shell
commands, run background jobs, and drive interactive terminals on a remote Linux VPS over
MCP (Model Context Protocol) and HTTPS — with no SSH, no tunnels, no local agent runtime, and
no external control plane. The agent stays on its hosting platform; UAEG gives it the full
native capabilities of the server, up to and including root.

[![CI](https://github.com/smahud/uaeg/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/smahud/uaeg/actions/workflows/ci.yml)

**Current release: v0.4.0** (tag `v0.4.0`). The repository carries its own audit trail
— release decisions, security findings, and test evidence — under [report/](report/).

> **SECURITY WARNING — READ THIS BEFORE INSTALLING**
>
> - **An authenticated agent receives full, unrestricted root access to the VPS.** There is
>   no sandbox, no permission gate, and no confirmation step between a tool call and
>   execution.
> - UAEG is intended for a **dedicated, disposable, experimental VPS** operated by a single
>   trusted person. Do not install it on a machine that holds anything you care about.
> - Optional "restricted paths" are **advisory rules communicated to the agent — they are not
>   a security boundary.** A root shell can bypass them trivially (FR-014).
> - The dashboard has a **native password login** (since v0.3.0). Set the password with
>   `sudo uaeg pass`; until then it runs in legacy unauthenticated mode and MUST stay on a
>   private bind. A public (`0.0.0.0`) dashboard bind requires a password AND HTTPS, or the
>   service refuses to start (fail closed).
> - An agent can stop UAEG itself, rewrite firewall rules, or destroy the operating system.
>   A provider console and snapshots are the recovery path (FR-020).
>
> Read [docs/SECURITY.md](docs/SECURITY.md) and [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
> before deploying.

## Features

- Native execution on the real host — not a simulated filesystem or cloud sandbox (FR-007).
- Full root control for authenticated agents, by design (FR-010, FR-013).
- Remote MCP over Streamable HTTPS with bearer-key authentication; multiple independent
  keys with immediate revocation — revoking a key also terminates that key's live
  sessions and background jobs (FR-004, FR-005, FR-011, FR-012).
- Background jobs that survive client disconnects and service restarts with honest
  state reporting across reboots (interrupted, never falsely running) (FR-009).
- Interactive persistent terminals (real PTYs) with input, resize, and reconnection (FR-008).
- LAN/loopback dashboard for key management, live sessions, audit logs, and server status
  (FR-015, FR-016); command text and output previews are secret-redacted in the audit trail.
- Dashboard authentication (v0.3.0): `uaeg pass` sets a scrypt-hashed login password; all
  pages and API routes then require a revocable server-side session (HttpOnly cookie,
  CSRF token, per-IP lockout). Public exposure over HTTPS with `0.0.0.0` is supported
  safely once a password exists.
- One systemd service, one SQLite database, three runtime dependencies (FR-019). Fail-closed
  startup: a broken listener, config, certificate or database stops the whole service with a
  clear error instead of running half-alive.
- Guided installer with an integrity-verified one-line option (FR-001, FR-002); since v0.4.0
  `install.sh` is the single public entrypoint — the same downloaded file installs UAEG, performs
  every future safe upgrade (signature-verified release manifest, auto-backup, zero-data-loss
  verification, automatic rollback), and manages the service
  (`--check`, `--dry-run`, `--rollback`, `--repair`, `--start/--stop/--restart/--status`) under
  the [upgrade contract](docs/UPGRADE_CONTRACT.md).

## Architecture

One process, two listeners. The MCP listener is public and bearer-authenticated; the
dashboard listener is private by default and password-protected once `uaeg pass` has been
run.

```text
  Hosted AI agent                     Operator (browser)
  (any remote MCP client)             (LAN/loopback, or public with a password)
        |                                         |
        | HTTPS   https://HOST:3902/mcp           | HTTP(S) http(s)://HOST:3903
        | Authorization: Bearer <UAEG_AGENT_KEY>  | (login page -> session cookie)
        v                                         v
 +---------------------------------------------------------------+
 |                    UAEG service (systemd)                     |
 |                                                               |
 |  +--------------------------+   +---------------------------+ |
 |  | MCP listener (TLS)       |   | Dashboard listener        | |
 |  | bearer auth per request  |   | password login + sessions | |
 |  | stateful MCP sessions    |   | optional CIDR allowlist   | |
 |  +------------+-------------+   +-------------+-------------+ |
 |               |                               |               |
 |      shared runtime: keys, jobs, PTY sessions, audit log      |
 |      SQLite database (/var/lib/uaeg/uaeg.db, WAL mode)       |
 +------------------------------+--------------------------------+
                                |
                                |  native root execution
                                v
                        Debian VPS host OS
       (shell, files, packages, systemd, Docker, networking)
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart

```bash
git clone https://github.com/smahud/uaeg.git
cd uaeg
sudo ./install.sh --domain gw.example.com   # see docs/INSTALL.md for all options
sudo uaeg generate --name "my-agent"         # prints the full secret exactly once
```

Configure the hosted agent with:

- Server URL: `https://gw.example.com:3902/mcp`
- Header: `Authorization: Bearer <UAEG_AGENT_KEY>`

Verify from the VPS:

```bash
sudo uaeg status
sudo uaeg doctor
```

Upgrading is one command with one file you download **once**: since v0.4.0 the release's
`install.sh` asset is a unified installer + verified upgrade bootstrap — it embeds the trusted
release keyring, fetches and Ed25519-verifies each release's signed manifest, then runs the
versioned safe-upgrade updater (auto-detect, backup, zero-data-loss verification, automatic
rollback), and also manages the service. Download it one time, then reuse the same local file
for every future release, even ones that change the upgrade implementation itself
([upgrade contract](docs/UPGRADE_CONTRACT.md) · [bootstrap design](docs/BOOTSTRAP.md)):

```bash
# one-time setup (verify the download, then):
sudo bash install.sh --domain gw.example.com   # fresh install
# every future upgrade AND service command: the same file
sudo bash install.sh                           # verified safe upgrade to the latest release
sudo bash install.sh --check                   # status + available update
sudo bash install.sh --rollback                # restore the latest upgrade backup
```

> **v0.4.0 note (release-signing key rotation + unified installer):** releases are now
> signed with `uaeg-release-3`, and `install.sh` is the single public install/upgrade file.
> A bootstrap downloaded during v0.3.4/v0.3.5 will refuse v0.4.0+ with an explicit
> "signature invalid / key rotation" error — that is the documented fail-closed behavior;
> re-download `install.sh` once ([docs/BOOTSTRAP.md](docs/BOOTSTRAP.md)).
> A bootstrap downloaded fresh (v0.4.0+) trusts all historical keys and needs no refresh.

The first tool an agent should call is `server_info`, which reports the execution context and
the advisory usage rules. Full walkthrough: [docs/USAGE.md](docs/USAGE.md).

## Requirements

- Debian 13 recommended (Debian 12 supported). Python 3.11 or newer; Debian 13 ships 3.13.
- Root access to a VPS dedicated to this purpose.
- A public domain pointed at the VPS (recommended — enables Let's Encrypt via certbot), or a
  public IP with an operator-provided trusted certificate.
- Inbound TCP on your chosen MCP port (default 3902) reachable through any firewall.

## MCP tools

| Tool | Purpose |
|------|---------|
| `server_info` | Execution context and advisory usage rules; the agent should call this first |
| `shell_exec` | Run a bash command synchronously, or launch it as a background job |
| `shell_open` | Open an interactive PTY session (persistent terminal) |
| `shell_input` | Send keystrokes (including control characters) to a PTY session |
| `shell_output` | Read new PTY output since a sequence cursor |
| `shell_resize` | Resize a PTY session's terminal dimensions |
| `shell_close` | Terminate a PTY session |
| `shell_sessions` | List live PTY sessions (used for reconnection) |
| `job_status` | Query a background job's state and exit code |
| `job_output` | Read a background job's captured output with offset/limit |
| `job_wait` | Wait (bounded) for a background job to finish |
| `job_cancel` | Cancel a running background job |
| `job_list` | List background jobs |

Parameter-level reference with worked examples: [docs/USAGE.md](docs/USAGE.md).

## CLI overview

| Command | Purpose |
|---------|---------|
| `uaeg serve` | Run the server in the foreground (used by systemd) |
| `uaeg init` | Write a default config file (advanced/manual setups) |
| `uaeg pass` | Set or change the dashboard login password (interactive, hidden input, stored as a scrypt hash) |
| `uaeg generate` | Create an agent key; the secret is printed exactly once |
| `uaeg keys list` | List keys: ID, name, prefix, status, permissions, created, last used |
| `uaeg keys revoke <KEY_ID>` | Revoke a key (immediate) and terminate its live sessions/jobs; `--keep-processes` opts out |
| `uaeg keys rotate <KEY_ID>` | Issue a replacement secret for a key |
| `uaeg jobs list` / `status` / `output` / `cancel` | Inspect and control background jobs (cancel = SIGTERM/SIGKILL the process group) |
| `uaeg sessions list` / `kill` | Inspect recorded sessions and terminate live ones (SIGHUP/SIGKILL) |
| `uaeg status` | Service state, health check, port listeners, counts |
| `uaeg doctor` | Full diagnostics: config, data, DB (incl. integrity check), service, ports, TLS (incl. SAN), exec user |
| `uaeg config show` / `get` / `set` | Read and change configuration |
| `uaeg version` | Print the version |

## Platform compatibility

UAEG works with any hosted platform that lets you add an **external MCP server over
Streamable HTTP** and set a **custom `Authorization` header** on its requests. The platform
must be able to reach a URL with an explicit port (`https://host:3902/mcp`) and trust the
server certificate.

Platforms that cannot add external MCP servers — stdio-only clients, locked integration
catalogs, clients that cannot set custom headers — are **unsupported**, and UAEG does not
claim compatibility with them (FR-006).

A platform's built-in terminal or filesystem tools are **not redirected to the VPS**; they
keep running on the platform. The agent must explicitly call UAEG tools for VPS operations.

## Documentation

| Document | Contents |
|----------|----------|
| [docs/INSTALL.md](docs/INSTALL.md) | Prerequisites, installer flags and examples, verified one-line install, upgrade, uninstall, firewall notes |
| [docs/UPGRADE_CONTRACT.md](docs/UPGRADE_CONTRACT.md) | Binding safe-upgrade contract (clauses U-1..U-12): one command, auto-backup, verify, auto-rollback — enforced by the CI upgrade-contract suite (in Bahasa Indonesia) |
| [docs/USAGE.md](docs/USAGE.md) | CLI reference, connecting a hosted agent, all 13 MCP tools with worked examples, job lifecycle, PTY cursors, dashboard guide |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Full config schema, TLS modes, env overrides, path layout, persistence |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Process model, request flows, execution subsystems, data model, design decisions |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, key handling, TLS guidance, hardening, recovery prerequisites |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptom-to-fix tables, `uaeg doctor` walkthrough, documented recovery procedure |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Honest list of known limitations and out-of-scope items |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | AT-01..AT-19 acceptance runbook for a real VPS |
| [docs/PRD.md](docs/PRD.md) | Product requirements (authoritative spec) |

## Security and limitations

UAEG is deliberately a power tool with a thin safety story: authenticated agents get root
and soft restrictions are advisory. The dashboard is password-protected once `uaeg pass`
has been run; without a password it must stay on a private bind (legacy mode). Before
deploying, read [docs/SECURITY.md](docs/SECURITY.md) (threat model and hardening) and
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) (known limitations, out of scope).

## License

MIT — see [LICENSE](LICENSE). UAEG is experimental software (v0.4.0); treat it accordingly.
