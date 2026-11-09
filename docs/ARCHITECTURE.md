# Architecture

How UAEG is put together: the process model, request flows, the three execution subsystems,
the data model, and the reasoning behind the security-relevant design decisions.

## Design goals

From the [PRD](./PRD.md) principles: native execution on a real host, full root for
authenticated agents, direct HTTPS connection, minimal configuration, server-owned
administration, reuse of native Linux facilities, and no overengineering. Everything below
follows from those constraints — one service, one database, three runtime dependencies.

## Process model

There is exactly **one process**: the systemd service `uaeg` (`/etc/systemd/system/uaeg.service`,
`Restart=on-failure`), running as **root by design** because agents execute with full host
privileges (FR-010, FR-013). That single asyncio process runs two listeners:

- **MCP listener** — public HTTPS (default `0.0.0.0:3902`), endpoint `/mcp`, built on the
  official MCP SDK (`mcp==1.27.0`) using the Streamable HTTP transport with JSON responses
  and stateful sessions. TLS is mandatory (certbot / manual / self-signed). Every request
  passes bearer authentication.
- **Dashboard listener** — private HTTP (default `127.0.0.1:3903`), a plain Starlette app
  with no login. Reachability is controlled by bind address plus an optional CIDR allowlist;
  a wildcard bind is refused unless explicitly opted in (FR-015, FR-018).

Both listeners share one runtime: the configuration, the SQLite store, the job manager, the
PTY session manager, and the audit log. Sharing the runtime is what makes the dashboard a
live view of exactly what the MCP side is doing — and why there is deliberately no public
unauthenticated administrative route anywhere.

The listeners are supervised: if either fails to start or dies for any reason (a bind
failure, an unexpected exception), the whole process stops with a non-zero exit code and a
clear log line — a regular failure in one listener no longer leaves a half-running service.
systemd (`Restart=on-failure`) restarts it. SIGTERM/SIGINT stop both listeners cleanly
(detached background jobs keep running); an unreadable/corrupt database or invalid TLS
material is a clean startup error (exit code 2, no traceback).

## Request flows

### MCP tool call

```text
Hosted agent
    |  HTTPS POST https://HOST:3902/mcp
    |  Authorization: Bearer uaeg_...
    v
TLS termination (uvicorn)
    |
    v
Auth middleware: SHA-256(secret) -> keys table lookup
    |  unknown, malformed, or revoked key  -> 401 (checked per request)
    v
MCP session manager (official SDK, stateful Streamable HTTP sessions)
    |  initialize / tools/list / tools/call ...
    v
Tool handler -> executor (sync runner | job manager | PTY manager)
    |  spawns processes as root; captures output
    v
Audit log row in SQLite (key identity, tool, status, exit, duration, command)
    |
    v
JSON response -> agent
```

### Dashboard request

```text
Operator browser (LAN or loopback)
    |  HTTP http://BIND:3903/...
    v
Dashboard listener (bind-restricted; optional CIDR allowlist; no login)
    |
    v
Starlette routes -> shared runtime
    |  same SQLite store, job manager, PTY manager as the MCP side
    v
HTML/JSON responses (keys, sessions, logs, server info)
```

## Execution subsystems

### Synchronous runner

`shell_exec` (without `background`) spawns the configured shell with the command, optionally
merged environment, working directory, and timeout, then captures stdout and stderr
concurrently. Each stream is capped at `exec.max_output_bytes` (default 1 MiB): beyond the
cap, bytes are counted (`stdout_bytes`/`stderr_bytes`) but discarded, and
`stdout_truncated`/`stderr_truncated` report the loss. On timeout the process is killed and
`timed_out` is set. This path holds the HTTP request open, which is why long or verbose work
belongs in background jobs.

### Background jobs and the jobwrap shim

Background jobs must outlive both the HTTP request and the client connection (FR-009), and
survive service restarts with honest state. The mechanism:

1. The service allocates a `job_id` and creates `/var/lib/uaeg/jobs/<job_id>/`.
2. It launches a tiny shim (shipped inside the package as `uaeg._jobwrap`) in a **detached
   process group** (`setsid`), so the job is not part of UAEG's process tree. When the
   service itself runs under systemd (standard install), the shim is additionally placed in
   its own **transient systemd scope** (`systemd-run --scope --unit=uaeg-job-<id>`):
   `setsid` alone does NOT escape the unit's cgroup, and the shipped unit uses the systemd
   default `KillMode=control-group`, which kills *every* process in the cgroup on
   `systemctl stop/restart uaeg`. The scope is a separate cgroup, so the job survives the
   service stop/restart as documented. A one-shot capability probe at first use detects
   whether scopes can be created; without systemd, or when the probe fails, the legacy
   direct spawn is used and a warning states that jobs will not survive service restarts
   on that host (live audit 2026-11-10).
3. The shim runs the command with stdout/stderr redirected to the job's `stdout` and
   `stderr` files, writes its own pid to `pid`, and writes the final exit code to `exit`
   when the command finishes.
4. UAEG watcher tasks follow the running pids and tail the output files so `job_status` /
   `job_output` answer from durable files.
5. **On service restart** the job manager reattaches: a live pid keeps the job `running`;
   an `exit` file transitions it to `completed`/`failed`. A job whose pid is gone with no
   `exit` file — the situation after a VPS reboot or a SIGKILL of the service — is marked
   `interrupted`, never falsely reported as `running`.

Because the durable state is plain files plus a database row, jobs are inspectable even
when UAEG itself is down (`uaeg jobs output`, or just `cat` the files).

### PTY sessions

`shell_open` allocates a real PTY pair and runs the configured shell in it. Keystrokes from
`shell_input` go to the master fd; output is read into a **bounded ring buffer** per session
(`exec.pty_buffer_bytes`, default 256 KiB) with monotonically increasing sequence numbers.
`shell_output(since_seq=...)` returns bytes past the cursor plus the next cursor; if a
reader falls behind by more than the buffer, the oldest bytes are dropped and `truncated`
is reported. `shell_resize` updates the kernel window size and raises SIGWINCH so
full-screen programs redraw. `max_sessions` bounds concurrent sessions.

Session *state* (fds, buffers, cursors) is in memory and dies with the process — a server
restart terminates all PTY sessions. Session *records* (id, command, key, timestamps) are
persisted for the audit trail. Jobs and sessions intentionally behave differently here:
jobs survive restarts, sessions do not.

## Data model

SQLite at `/var/lib/uaeg/uaeg.db`, opened in WAL mode for concurrent reader access from both
listeners. Five tables plus per-job files on disk:

| Table | Contents |
|-------|----------|
| `meta` | Service metadata, including the schema version (a database from a newer UAEG is refused at startup) |
| `keys` | Key ID, name, prefix, SHA-256 verifier (never the secret), status, permission mode, created, last used |
| `jobs` | Job ID, command (secret-redacted), owning key, status, pid, started/finished timestamps, exit code |
| `sessions` | PTY session records: session ID, command (secret-redacted), owning key, opened/closed, exit code |
| `exec_logs` | Audit log: timestamp, key identity, tool, status, exit code, duration, command text (secret-redacted), metadata, output preview (redacted) |

Job output itself lives on the filesystem at `/var/lib/uaeg/jobs/<job_id>/`
(`stdout`, `stderr`, `pid`, `exit`) so large outputs never bloat the database and remain
readable even while the service is stopped.

## Security-relevant design decisions

- **Root by design.** The service runs as root because agents must get full host privilege.
  There is no privilege dropping, because dropping it would contradict the product (FR-010).
  The corresponding risk — an agent can kill UAEG or damage the OS — is documented, not
  mitigated in code (FR-020, [SECURITY.md](./SECURITY.md)).
- **Verifier-only key storage.** Only SHA-256 hashes of secrets are stored; the CLI and
  dashboard show prefixes for identification. Revocation is a status flip checked on every
  request, so it is immediate.
- **Auth before routing.** Bearer verification runs ahead of the MCP session manager, so no
  MCP protocol handling occurs for unauthenticated requests, and `tools/list` cannot leak
  tool schemas to strangers.
- **TLS mandatory on the public listener.** The only bypass is the explicit
  `UAEG_DEV_INSECURE_HTTP=1` development flag, which serves plain HTTP on the MCP listener
  with a loud warning (never in production).
- **Network placement instead of login for the dashboard.** No application login exists, so
  the design refuses wildcard binds by default and supports a CIDR allowlist; the dashboard
  and MCP listeners never share a public unauthenticated route (FR-018).
- **Bounded memory.** Sync capture caps, the PTY ring buffer, and `max_sessions` exist so a
  runaway command cannot exhaust the service.
- **Env values are not logged; command text is redacted.** The audit log records command
  text and metadata — masked with `security.redact_secrets()`, so high-confidence secret
  shapes become `***REDACTED***` — but not environment variable values, reducing (not
  eliminating — see [SECURITY.md](./SECURITY.md#secret-redaction-in-logs)) secret leakage
  through logs.

## Why state lives outside the install directory

`/opt/uaeg` is a plain copy of the source (no `.git`). Keeping configuration in `/etc/uaeg`
and state in `/var/lib/uaeg` (FR-019) means:

- Installer upgrades and editable reinstalls cannot clobber operator state — since 0.2.0
  the installer preserves the existing configuration on upgrade (before 0.2.0 the config
  was reset), while keys and data always live outside the install directory.
- `uninstall.sh` without `--purge` can remove the code cleanly while leaving keys, config,
  and job history for a later reinstall.
- It matches normal Linux conventions (`/etc` config, `/var` state), so backup and monitoring
  tooling behave as operators expect.

## Dependencies

Runtime dependencies are exactly three, pinned in `pyproject.toml`: `mcp==1.27.0`,
`starlette==0.50.0`, `uvicorn==0.44.0`. Storage is stdlib `sqlite3` (WAL). Python 3.11+
(Debian 13 ships 3.13). There is no message broker, no container requirement, and no
external control plane — the PRD's "no unnecessary infrastructure" rule is enforced by the
dependency list.
