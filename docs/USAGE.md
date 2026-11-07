# Usage

Day-to-day operation of UAEG: creating keys, connecting a hosted agent, every CLI subcommand,
all 13 MCP tools with worked examples, background-job lifecycle, PTY output cursors, and the
dashboard.

## First steps after install

```bash
uaeg status                 # service active, health OK, listeners on 3902/3903
uaeg doctor                 # full diagnostics; resolve any ERROR before continuing
uaeg generate --name "my-agent"
```

`generate` prints the key secret exactly once — copy it into your password manager or the
hosted platform immediately. Then connect the agent (below) and have it call `server_info`
first: the tool reports the execution context (host, user, working directory, timeouts) and
the advisory usage rules configured on the server.

## Generating your first key

```bash
uaeg generate --name "my-agent"
```

```text
Agent key created: my-agent  (k-1a2b3c4d5e6f)

  Secret (shown ONCE, store it now):
    uaeg_Qm7zK4pLxV2nR8tWcYb3HfDjS6gN1aEu5oPiT0hZqwe

  MCP endpoint : https://gw.example.com:3902/mcp
  Header       : Authorization: Bearer <secret>
  Permissions  : all
```

(Output is illustrative.) Key IDs are `k-` followed by 12 hex characters.

Facts worth knowing:

- Keys are 48-character secrets beginning with `uaeg_`, generated from 256 bits of CSPRNG
  entropy. The server stores only a SHA-256 verifier — the secret itself cannot be recovered
  (FR-005, FR-012).
- `--permissions restricted` marks the key as restricted; the advisory usage rules from
  `exec.restricted_paths` are then communicated to the agent in `server_info`. This is
  advisory only, not a security boundary (FR-014, see [LIMITATIONS.md](./LIMITATIONS.md)).
- Give every agent its own key. The audit log identifies keys by key ID and key name (the
  prefix appears in `uaeg keys list` only), so per-agent keys give you per-agent
  traceability and per-agent revocation.
- Lost a secret? Rotate the key (`uaeg keys rotate <KEY_ID>`) or revoke and generate a new
  one.

## CLI reference

All commands run on the VPS. `uaeg` is installed as `/usr/local/bin/uaeg`.

### `uaeg serve`

Runs the server in the foreground. This is what the systemd unit executes; you rarely need
it manually except for debugging outside systemd.

### `uaeg init`

Writes a default config file (to `/etc/uaeg/config.json`, or `$UAEG_CONFIG` if set) without
starting anything. Intended for advanced/manual setups such as containers.

### `uaeg pass`

Sets or changes the **dashboard login password** — the credential that protects the web
dashboard (it is separate from the MCP agent keys and never affects them).

```bash
sudo uaeg pass
```

```
UAEG Dashboard Password Setup

Enter new password:
Confirm new password:

Password updated successfully.
  - it takes effect immediately (no service restart needed)
  - all existing dashboard sessions have been revoked
  - the MCP agent keys are NOT affected (separate credential)
```

Behavior:

- Input is hidden while typing; the password is entered **twice** and must match
  (mismatches and rule violations allow retry; Ctrl+C cancels and keeps the old password).
- Minimum length 12 characters; empty passwords are rejected.
- Stored as a salted **scrypt** hash (never plaintext, never logged); the file is written
  atomically (`/var/lib/uaeg/dashboard-pass.json`, mode 0600).
- No old password is required — the command runs with local administrator (root)
  privilege on the VPS.
- Once a password exists, the dashboard login is enforced on every interface; without one
  the dashboard runs in legacy unauthenticated mode on a private bind only.

### `uaeg generate`

```bash
uaeg generate
uaeg generate --name "Claude"
uaeg generate --name "ci-agent" --permissions restricted
```

Creates a key and prints the full secret exactly once (see above).

### `uaeg keys list`

```bash
uaeg keys list
```

```text
KEY ID           NAME              PREFIX            STATUS   MODE        CREATED (UTC)        LAST USED
k-1a2b3c4d5e6f   my-agent          uaeg_Qm7z...      active   all         2025-06-01 09:20:00   2025-06-01 12:44:00
k-0f3e9d8c7b6a   test              uaeg_Vz3p...      revoked  all         2025-06-01 09:31:00   2025-06-01 10:02:00
```

(Output is illustrative.) MODE is the permission mode (`all`/`restricted`). The prefix is
for identification only — it never authenticates; the audit log identifies keys by key
ID and name.

### `uaeg keys revoke <KEY_ID>`

```bash
uaeg keys revoke k-0f3e9d8c7b6a
```

Revocation is immediate and checked on every request: the next MCP call with that key
fails with `401`. Revocation also contains the key's running work: its live PTY sessions
are terminated (SIGHUP then SIGKILL) and its running background jobs are cancelled
(SIGTERM then SIGKILL, recorded as `cancelled`); the command prints a containment
summary (`terminated N session(s), N background job(s)`). Pass `--keep-processes` to
leave the key's processes running. Sessions and jobs owned by other keys are unaffected.
The dashboard's revoke button does the same (opt out with a `keep_processes` JSON body).

### `uaeg keys rotate <KEY_ID>`

```bash
uaeg keys rotate k-1a2b3c4d5e6f --name "my-agent-v2"
```

Revokes the old key ID and creates a new key entry — new key ID, new secret, old row kept
for audit — and prints the new secret exactly once; the old secret stops working
immediately, so reconfigure the agent with the new one. Rotation terminates nothing: it is
routine credential hygiene, not containment (revoke for that). Use `--name` to update the
display name at the same time.

### `uaeg jobs list`

```bash
uaeg jobs list --limit 20
```

```text
JOB ID                                STATUS      EXIT   STARTED (UTC)           COMMAND
job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5  running     -      2025-06-01 12:40:00     cd /srv/app && make test
job-a93f0c1d2e3f4a5b6c7d8e9f0a1b2c3d  completed   0      2025-06-01 11:02:00     apt-get update && apt-get install -y jq
```

(Output is illustrative.) Job IDs are `job-` followed by 32 hex characters.

### `uaeg jobs status <JOB_ID>`

```bash
uaeg jobs status job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5
```

Shows status, exit code, start/finish times, and the command.

### `uaeg jobs output <JOB_ID>`

```bash
uaeg jobs output job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5                          # stdout from the beginning
uaeg jobs output job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5 --stream stderr          # stderr stream
uaeg jobs output job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5 --offset 4096 --limit 2000
```

Reads the job's captured output files; `--offset`/`--limit` paginate by bytes (a negative
`--offset` is clamped to 0).

### `uaeg jobs cancel <JOB_ID>`

Terminates a running background job: SIGTERM to its process group, then SIGKILL if it
does not exit in the grace period, and records the `cancelled` status. It is idempotent —
cancelling a job that already finished reports its real final status (and exit code)
without changing it.

### `uaeg sessions list` / `uaeg sessions kill`

```bash
uaeg sessions list
uaeg sessions kill 5f2a9c31d4b8e7a2c6f0d3e1b4a7982d
```

`sessions list` shows the recorded session history (ID, status, owning key, created,
command) — open, closed, and interrupted sessions alike. Live sessions are listed by the
`shell_sessions` MCP tool. `sessions kill` terminates a live session (SIGHUP then SIGKILL
to its process group) and marks it closed; it works whether the server is running or not —
a running server notices the dying process and releases the PTY.

### `uaeg status`

```bash
uaeg status
```

```text
UAEG v0.3.0
  service      : active
  MCP port     : 3902 (listening)
  dashboard    : 127.0.0.1:3903 (listening)
  health       : ok (v0.3.0, uptime 512s)
  keys         : 1 active, 1 revoked
  jobs/sessions: 1 running jobs, 1 open sessions
  MCP URL      : https://localhost:3902/mcp
```

(Output is illustrative.)

### `uaeg doctor`

Full diagnostics: config validity, data directory, database (including an integrity check),
systemd service state, MCP and dashboard listeners, health endpoint, TLS certificate
(subject, SAN, expiry — with a warning when it expires within 30 days), execution user
(root is a **passing** check on a standard install — the service runs as root by design; a
non-root service fails with an explanation), and the configured exec shell. See the
walkthrough in [TROUBLESHOOTING.md](./TROUBLESHOOTING.md).

### `uaeg config show` / `get` / `set`

```bash
uaeg config show
uaeg config get exec.default_timeout_s        # -> 120
uaeg config set exec.default_timeout_s 300
sudo systemctl restart uaeg                   # changes take effect on restart
```

See [CONFIGURATION.md](./CONFIGURATION.md) for the full schema.

### `uaeg version`

Prints the installed version, e.g. `uaeg 0.2.0`.

## Connecting a hosted agent

Give the platform two things:

- **Server URL:** `https://HOST:3902/mcp` (Streamable HTTP transport)
- **Header:** `Authorization: Bearer <UAEG_AGENT_KEY>`

Most clients that support remote MCP servers accept something shaped like this (field names
vary; what matters is the URL, the transport, and the custom header):

```json
{
  "mcpServers": {
    "uaeg": {
      "type": "streamable-http",
      "url": "https://gw.example.com:3902/mcp",
      "headers": { "Authorization": "Bearer uaeg_Qm7z..." }
    }
  }
}
```

```yaml
mcp_servers:
  uaeg:
    transport: streamable_http
    url: https://gw.example.com:3902/mcp
    headers:
      Authorization: "Bearer uaeg_Qm7z..."
```

### What a platform must support to be compatible

1. Adding an **external (remote) MCP server** — not only local stdio servers.
2. The **Streamable HTTP** transport with JSON responses.
3. **Custom request headers**, specifically `Authorization: Bearer ...`.
4. Reaching a URL with an **explicit port** (`https://host:3902/mcp`).
5. Trusting your server certificate (see [CONFIGURATION.md](./CONFIGURATION.md) for TLS
   modes; most hosted clients reject self-signed certificates).

Platforms that cannot do these things are unsupported, and UAEG does not claim compatibility
with them (FR-006). Report them as incompatible rather than attempting workarounds — that
expectation is itself acceptance test [AT-19](./ACCEPTANCE.md).

### Important: the platform's terminal is not the VPS

A hosted platform's built-in terminal or filesystem tools continue to run **on the
platform**, not on your VPS. UAEG does not redirect them. For VPS operations the agent must
call the UAEG tools (`shell_exec`, `shell_open`, `job_*`). `server_info` exists so the agent
can confirm where it is executing before it acts.

## MCP tool reference

All tools return JSON objects. `ok` is `true` when the call itself succeeded.

### `server_info()`

No parameters. Returns the execution context (host, user, default working directory, shell,
timeout and output caps) and the server's advisory usage rules. **Agents should call this
first** — it tells them they are on the VPS as root and which paths the operator asks them
to leave alone.

### `shell_exec(command, cwd?, env?, timeout_seconds?, background?)`

Runs `command` with the configured shell (default `/bin/bash`) as root.

Parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `command` | required | The bash command line |
| `cwd` | `exec.default_cwd` (`~`) | Working directory |
| `env` | inherited | Object of extra environment variables merged into the child environment |
| `timeout_seconds` | `exec.default_timeout_s` (120) | Sync-mode timeout; on expiry the process is killed |
| `background` | `false` | If `true`, launch as a background job and return immediately |

Synchronous result:

```json
{
  "ok": true, "exit_code": 0, "stdout": "root\n", "stderr": "",
  "duration_ms": 12, "timed_out": false,
  "stdout_bytes": 5, "stderr_bytes": 0,
  "stdout_truncated": false, "stderr_truncated": false
}
```

Each stream is captured up to `exec.max_output_bytes` (default 1 MiB); if a stream exceeds
the cap the remainder is dropped and the matching `*_truncated` flag is set (`*_bytes` still
reports the full size). `error` is present on spawn failures. For anything long-running or
verbose, use `background=true`:

```json
{ "ok": true, "background": true, "job_id": "job-b7c1a2f3...", "pid": 18342, "status": "running" }
```

### `shell_open(command?, cwd?, env?, cols=120, rows=30)`

Opens a real PTY running the configured shell (or `command` inside it) and returns
`{ok, session_id, pid, ...}`. The session stays alive across tool calls and MCP
reconnects until the shell exits, you call `shell_close`, or the server restarts.

### `shell_input(session_id, data, add_newline=false)`

Writes `data` to the session's terminal input. Control characters work: `\x03` is Ctrl-C,
`\r` is Enter (or pass `add_newline=true` to append `\n` after the data).

### `shell_output(session_id, since_seq=0, limit_bytes=262144)`

Returns new output since the sequence cursor:

```json
{
  "data": "root@gw:~# uptime\n 12:44:01 up 3 days,  4:11,  1 user,  load average: 0.08, 0.02, 0.01\n",
  "seq": 287, "truncated": false, "alive": true, "pid": 18411, "exit_code": null
}
```

`seq` is the cursor for the next call. `truncated: true` means output was dropped because
the reader fell more than `exec.pty_buffer_bytes` (default 256 KiB) behind. See
[PTY output and seq cursors](#pty-output-and-seq-cursors) below.

### `shell_resize(session_id, cols, rows)`

Updates the PTY window size and signals the process group (SIGWINCH) so full-screen
programs redraw correctly.

### `shell_close(session_id)`

Terminates the session and its processes. The shell exiting on its own also ends the
session.

### `shell_sessions()`

Lists live sessions with their IDs and commands. Use it after a client reconnect to find a
still-running terminal — sessions are not tied to the MCP connection.

### `job_status(job_id)`

Returns the job's status, exit code when finished, start/finish timestamps, and pid when
running.

### `job_output(job_id, stream="stdout", offset=0, limit=65536)`

Reads captured output of a background job. `stream` is `"stdout"` or `"stderr"`;
`offset`/`limit` paginate by bytes, so callers can tail a growing job by advancing the
offset.

### `job_wait(job_id, timeout_seconds=30)`

Blocks (bounded) until the job finishes or the timeout elapses, then returns the final or
current status. Do not use long waits for very slow jobs — poll `job_status` instead.

### `job_cancel(job_id)`

Terminates a running job; its status becomes `cancelled`.

### `job_list(limit=50)`

Lists jobs (newest first) with status, exit code, and command — useful to find a `job_id`
after losing it.

## Worked examples

### 1. Verify the execution context (AT-05)

```text
> server_info()
{ "ok": true, "user": "root", "host": "gw.example.com", "cwd": "/root", ... }

> shell_exec(command="whoami")
{ "ok": true, "exit_code": 0, "stdout": "root\n", "duration_ms": 12, ... }
```

`stdout` is `root` on a standard install — that is the point of the product, and its
biggest risk.

### 2. Install a package with apt (AT-06)

```text
> shell_exec(command="apt-get update && apt-get install -y jq", timeout_seconds=300)
{ "ok": true, "exit_code": 0, "stdout": "…", "stderr": "", "duration_ms": 41870, ... }

> shell_exec(command="jq --version")
{ "ok": true, "exit_code": 0, "stdout": "jq-1.7\n", ... }
```

### 3. Background build job with polling (AT-08, AT-11)

```text
> shell_exec(command="cd /srv/webapp && npm run build", background=true)
{ "ok": true, "background": true, "job_id": "job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5", "pid": 18342, "status": "running" }

> job_status(job_id="job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5")
{ "ok": true, "job_id": "job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5", "status": "running", "pid": 18342, "exit_code": null, ... }

> job_wait(job_id="job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5", timeout_seconds=120)
{ "ok": true, "status": "completed", "exit_code": 0, "duration_ms": 71340, ... }

> job_output(job_id="job-b7c1a2f3d4e5f6a7b8c9d0e1f2a3b4c5", stream="stdout", offset=0, limit=65536)
{ "ok": true, "data": "built in 71s\n", ... }
```

The job runs detached from the request: closing the client, the conversation, or the MCP
session does not stop it.

### 4. Interactive PTY session (AT-10)

```text
> shell_open(cols=120, rows=30)
{ "ok": true, "session_id": "5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", "pid": 18411, ... }

> shell_input(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", data="htop", add_newline=true)   # run a full-screen tool

> shell_output(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", since_seq=0)
{ "data": "  1  [|||||||||||  12.3%]  …", "seq": 1493, "truncated": false,
  "alive": true, "pid": 18411, "exit_code": null }

> shell_resize(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", cols=200, rows=50)          # htop redraws wider

> shell_input(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", data="q")                    # quit htop

> shell_input(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", data="\x03")                 # Ctrl-C a stray command

> shell_close(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d")
{ "ok": true }
```

If the client disconnects mid-session, reconnect and call `shell_sessions()` to find the
session, then continue polling `shell_output` with your last cursor.

### 5. Disconnect and reconnect to a job (AT-11)

1. Start a long job in the background:

   ```text
   > shell_exec(command="sleep 90 && echo done", background=true)
   { "ok": true, "background": true, "job_id": "job-c2d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5", "pid": 19001, "status": "running" }
   ```

2. Close the client entirely (end the conversation / disconnect the MCP session).

3. Reconnect later (a fresh MCP session is fine) and query the same `job_id`:

   ```text
   > job_status(job_id="job-c2d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5")
   { "ok": true, "status": "running", "pid": 19001, "exit_code": null, ... }
   > job_wait(job_id="job-c2d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5", timeout_seconds=120)
   { "ok": true, "status": "completed", "exit_code": 0, ... }
   > job_output(job_id="job-c2d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5", stream="stdout", offset=0, limit=65536)
   { "ok": true, "data": "done\n", ... }
   ```

The job never noticed the disconnect. Job IDs can also be found from the CLI
(`uaeg jobs list`) or the dashboard.

## Background job lifecycle

| Status | Meaning |
|--------|---------|
| `running` | The process is alive (pid known and monitored) |
| `completed` | Exited with code 0 |
| `failed` | Exited with a non-zero code |
| `cancelled` | Killed via `job_cancel` / `uaeg jobs cancel`, or by revoking the owning key |
| `interrupted` | UAEG can no longer account for the process — typically after a VPS reboot or the service being SIGKILLed before the exit code was recorded |

`interrupted` is deliberate honesty (FR-009): after a reboot every pre-reboot process is
gone, so the job manager marks unaccounted-for jobs `interrupted` instead of falsely
reporting `running`. Metadata and any output captured before the interruption remain
readable via `job_status`/`job_output`.

Note the asymmetry with terminals: **jobs survive server restarts; PTY sessions do not.**

## PTY output and seq cursors

Each PTY session keeps a bounded ring buffer (default 256 KiB) of recent output. Every byte
has a monotonically increasing sequence number. `shell_output` takes `since_seq` and returns
the bytes after that point plus the next cursor:

```text
> shell_output(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", since_seq=0)
{ "data": "root@gw:~# ", "seq": 12, ... }

> shell_input(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", data="uname -a", add_newline=true)

> shell_output(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", since_seq=12)
{ "data": "uname -a\nLinux gw 6.1.0-18-amd64 … x86_64 GNU/Linux\nroot@gw:~# ",
  "seq": 151, "truncated": false, ... }

> shell_output(session_id="5f2a9c31d4b8e7a2c6f0d3e1b4a7982d", since_seq=151)
{ "data": "", "seq": 151, ... }        # nothing new yet
```

Polling loop for an agent: remember the last `seq`, call `shell_output(since_seq=seq)`,
append `data`, save the new `seq`, repeat. If you fall behind by more than the buffer, the
oldest bytes are dropped and `truncated` is `true` in the next response — poll faster, or
redirect chatty output to a file and read it with `shell_exec`.

## Dashboard

The dashboard runs on its own listener (default `http://127.0.0.1:3903`, or
`http://<lan-ip>:3903` when bound to the LAN, or `https://<domain>:3903` when exposed
publicly). Since v0.3.0 it is protected by a **password login** once you run
`sudo uaeg pass` — every page and API route then requires a valid session, and a
Sign out button ends it. Without a password it stays in legacy unauthenticated mode and
must remain on a private bind
([FR-015](./CONFIGURATION.md#dashboard-authentication-and-network-access)). To reach a
loopback-bound dashboard remotely you can also use an SSH tunnel from your workstation:
`ssh -L 3903:127.0.0.1:3903 root@gw`.

To expose the dashboard directly on the internet (HTTPS + password, e.g. on a VPS):

```bash
sudo uaeg pass
sudo ./install.sh --dashboard-bind public --domain gw.example.com
# then open the port: ufw allow 3903/tcp
```

The login page appears before the dashboard; failed attempts are rate-limited per source
IP (temporary lockout), and rotating the password revokes all active sessions.

| Tab | What you can do |
|-----|-----------------|
| **Keys** | Generate, revoke, and rotate keys; revocation applies the same session/job containment as the CLI; the full secret appears once in a modal after generation; set a key's permission mode (`all`/`restricted`) |
| **Permissions** | View the default access mode and edit the advisory `restricted_paths` list (advisory only — not a security boundary) |
| **Sessions** | See live PTY sessions and background jobs; terminate sessions, cancel jobs, and view job output in a viewer |
| **Logs** | Audit log of executions: time, key identity, tool, status, exit code, duration, command, with expandable metadata and an output preview |
| **Server** | Version, uptime, config paths, TLS certificate details (subject, issuer, SAN, expiry), and object counts |

The dashboard reads and writes the same database the CLI uses, so actions taken there are
immediately visible to `uaeg` commands and vice versa.

## Related docs

- [CONFIGURATION.md](./CONFIGURATION.md) — every setting, TLS modes, changing ports
- [SECURITY.md](./SECURITY.md) — key handling, hardening, what is and is not protected
- [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) — symptom tables and recovery
- [ACCEPTANCE.md](./ACCEPTANCE.md) — AT-01..AT-19 acceptance runbook
