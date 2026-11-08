# Limitations

An honest list of what UAEG does not do, cannot protect against, or deliberately excludes.
Read it before trusting the gateway with anything important.

## Host and privilege

1. **Full root by design.** An authenticated agent can do anything root can do on the VPS,
   including modifying the OS, stopping UAEG itself, and destroying the installation
   (FR-010, FR-020). There is no sandbox. If you need containment, run UAEG inside a VM you
   are prepared to lose — and see [SECURITY.md](./SECURITY.md) for recovery prerequisites.
2. **Advisory restrictions are not a boundary.** `exec.restricted_paths` and the
   `restricted` permission mode are usage rules communicated to the agent via
   `server_info`. A root shell bypasses application-level path checks trivially; UAEG does
   not claim these directories are protected (FR-014).
3. **The dashboard defaults to legacy no-login mode only on private binds.** Since v0.3.0
   the dashboard has a native password login (`sudo uaeg pass`): when a password exists,
   every page and API route requires a session. When NO password was ever configured,
   the dashboard keeps the v0.2.0 behavior - anyone who can reach port 3903 sees keys
   metadata, sessions, logs, and can generate/revoke keys; network placement
   (loopback/LAN bind, optional CIDR allowlist) is then the only boundary (FR-015).
   A public (`0.0.0.0`) bind REQUIRES a password + TLS and is refused at startup
   otherwise; since v0.3.4, deleting the password file at runtime also fails CLOSED
   (authentication stays enforced) instead of silently reverting to legacy mode.

## Runtime behavior

4. **PTY sessions are lost on server restart; jobs are not.** Session state (terminal,
   buffer, cursors) is in memory. When UAEG restarts, terminals die with it. Background
   jobs keep running and reattach on startup ([ARCHITECTURE.md](./ARCHITECTURE.md)).
5. **Synchronous output is capped at 1 MiB per stream.** `shell_exec` captures at most
   `exec.max_output_bytes` (default 1048576 bytes) of stdout and stderr each; the excess is
   dropped and reported via `stdout_truncated`/`stderr_truncated`. Use `background=true`
   and `job_output` for verbose commands.
6. **The PTY buffer is capped.** Each session keeps only the most recent
   `exec.pty_buffer_bytes` (default 256 KiB) of output. An agent that stops polling loses
   older bytes (`truncated: true`). Redirect chatty programs to files and read those.
   For ~60 seconds after a session's process exits, its final output and exit code stay
   readable (`alive: false`); after that the session id is expired (v0.3.4).
7. **Revocation containment is best-effort process-group signaling.** Since 0.2.0, revoking
   a key also terminates its live sessions (SIGHUP then SIGKILL) and cancels its running
   background jobs (SIGTERM then SIGKILL), with a `--keep-processes` opt-out. The signals
   target the recorded session/job process groups — a process that escaped into its own
   process group (e.g. a double-forking daemon started inside a job) is not caught.
   Rotation terminates nothing ([SECURITY.md](./SECURITY.md)).
8. **Jobs can end up `interrupted`.** After a reboot (or a SIGKILL of the service), jobs
   that were running are marked `interrupted` rather than falsely reported as running
   (FR-009). Their captured output remains readable, but the process is gone.

## Product scope

9. **Single operator, not multi-tenant.** One trusted person manages keys and the server.
   There is no user identity and no billing ([PRD](./PRD.md)
   section 3, section 16). Since v0.3.4 sessions and background jobs ARE key-scoped on
   the agent side: an MCP key can only read, drive, close or cancel the sessions/jobs it
   created itself, and `shell_sessions`/`job_list` return only the caller's entries. The
   dashboard and CLI remain operator-level and see everything. Every key still executes
   with full root privileges - scoping bounds blast radius and audit attribution between
   agents, it is not a security sandbox.
10. **No RBAC and no approval workflows.** Keys are either `all` or advisory `restricted`;
    there are no roles, no per-tool permissions, and no human confirmation steps. This is
    excluded from the MVP by design (PRD section 13), not a gap to be filled soon.
11. **The audit log stores command text (redacted, best effort).** Commands run by agents
    are recorded with high-confidence secret shapes masked (`***REDACTED***`) since 0.2.0,
    but redaction is pattern-based, not a guarantee — a secret in a shape the redactor does
    not recognize still lands in the log, and raw job output files under
    `/var/lib/uaeg/jobs/` are never altered. Environment variable *values* are not logged
    ([SECURITY.md](./SECURITY.md#secret-redaction-in-logs)).
12. **Self-signed certificates are rejected by most hosted clients.** Self-signed mode is
    for local testing only. Real deployments need a domain plus certbot, or trusted manual
    certificates (FR-017, [CONFIGURATION.md](./CONFIGURATION.md#tls-modes)).
13. **Background-job output files are not size-capped.** Since v0.3.3 the audit log
    (`exec_logs`) is trimmed at startup to the newest 20,000 rows, but the stdout/stderr
    files a background job writes to `/var/lib/uaeg/jobs/<id>/` have no quota: a
    command like `yes > /dev/stdout` in background mode grows the file until disk runs
    out (synchronous `shell_exec` output IS capped at 1 MiB/stream). Operators can
    monitor the jobs view and cancel such jobs; a future release may add a configurable
    per-job cap.

## Explicitly out of scope (PRD section 16)

The MVP intentionally does not implement: a cloud-hosted dashboard, AI model routing or
inference, a built-in chat agent, multi-tenant SaaS, Kubernetes, distributed execution
clusters, multi-VPS orchestration, mandatory Docker sandboxing, complex RBAC, agent approval
workflows, billing or subscriptions, Cloudflare dependency, SSH-based execution transport,
or automated integration with unsupported hosted platforms. The authoritative list is
[PRD.md](./PRD.md) section 16.
