# UAEG — Universal Agent Execution Gateway

UAEG is an open-source gateway that lets a compatible cloud-hosted AI agent execute real shell
commands, run background jobs, and drive interactive terminals on a remote Linux VPS over
MCP (Model Context Protocol) and HTTPS — with no SSH, no tunnels, no local agent runtime, and
no external control plane. The agent stays on its hosting platform; UAEG gives it the full
native capabilities of the server, up to and including root.

> **SECURITY WARNING — READ THIS BEFORE INSTALLING**
>
> - **An authenticated agent receives full, unrestricted root access to the VPS.** There is
>   no sandbox, no permission gate, and no confirmation step between a tool call and
>   execution.
> - UAEG is intended for a **dedicated, disposable, experimental VPS** operated by a single
>   trusted person. Do not install it on a machine that holds anything you care about.
> - Optional "restricted paths" are **advisory rules communicated to the agent — they are not
>   a security boundary.** A root shell can bypass them trivially (FR-014).
> - The dashboard has **no login**. Its network placement (LAN/loopback bind) is the only
>   thing protecting it.
> - An agent can stop UAEG itself, rewrite firewall rules, or destroy the operating system.
>   A provider console and snapshots are the recovery path (FR-020).
>
> Read [docs/SECURITY.md](docs/SECURITY.md) and [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
> before deploying.

## Features

- Native execution on the real host — not a simulated filesystem or cloud sandbox (FR-007).
- Full root control for authenticated agents, by design (FR-010, FR-013).
- Remote MCP over Streamable HTTPS with bearer-key authentication; multiple independent
  keys with immediate revocation (FR-004, FR-005, FR-011, FR-012).
- Background jobs that survive client disconnects and reboots with honest state reporting
  (FR-009).
- Interactive persistent terminals (real PTYs) with input, resize, and reconnection (FR-008).
- LAN/loopback dashboard for key management, live sessions, audit logs, and server status
  (FR-015, FR-016).
- One systemd service, one SQLite database, three runtime dependencies (FR-019).
- Guided installer with an integrity-verified one-line option (FR-001, FR-002).

## Architecture

One process, two listeners. The MCP listener is public and authenticated; the dashboard
listener is private and unauthenticated but network-restricted.

```text
  Hosted AI agent                     Operator (private LAN or loopback)
  (any remote MCP client)             (browser; the dashboard has no login)
        |                                         |
        | HTTPS   https://HOST:3902/mcp           | HTTP   http://HOST:3903
        | Authorization: Bearer <UAEG_AGENT_KEY>  |
        v                                         v
 +---------------------------------------------------------------+
 |                    UAEG service (systemd)                     |
 |                                                               |
 |  +--------------------------+   +---------------------------+ |
 |  | MCP listener (TLS)       |   | Dashboard listener        | |
 |  | bearer auth per request  |   | bind-restricted HTTP,     | |
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
| `uaeg generate` | Create an agent key; the secret is printed exactly once |
| `uaeg keys list` | List keys: ID, name, prefix, status, permissions, created, last used |
| `uaeg keys revoke <KEY_ID>` | Revoke a key (takes effect immediately) |
| `uaeg keys rotate <KEY_ID>` | Issue a replacement secret for a key |
| `uaeg jobs list` / `status` / `output` / `cancel` | Inspect and control background jobs |
| `uaeg sessions list` / `kill` | Inspect and terminate PTY sessions |
| `uaeg status` | Service state, health check, port listeners, counts |
| `uaeg doctor` | Full diagnostics: config, data, DB, service, ports, TLS, version |
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
| [docs/USAGE.md](docs/USAGE.md) | CLI reference, connecting a hosted agent, all 13 MCP tools with worked examples, job lifecycle, PTY cursors, dashboard guide |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Full config schema, TLS modes, env overrides, path layout, persistence |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Process model, request flows, execution subsystems, data model, design decisions |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, key handling, TLS guidance, hardening, recovery prerequisites |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptom-to-fix tables, `uaeg doctor` walkthrough, documented recovery procedure |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Honest list of known limitations and out-of-scope items |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | AT-01..AT-19 acceptance runbook for a real VPS |
| [docs/PRD.md](docs/PRD.md) | Product requirements (authoritative spec) |

## Security and limitations

UAEG is deliberately a power tool with a thin safety story: authenticated agents get root,
soft restrictions are advisory, and the dashboard trusts the network. Before deploying, read
[docs/SECURITY.md](docs/SECURITY.md) (threat model and hardening) and
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) (known limitations, out of scope).

## License

MIT — see [LICENSE](LICENSE). UAEG is experimental software (v0.1.0); treat it accordingly.
