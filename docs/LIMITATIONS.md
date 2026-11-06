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
3. **The dashboard has no login.** Anyone who can reach port 3903 can see keys metadata,
   sessions, logs, and server details, and can generate/revoke keys. Network placement
   (loopback/LAN bind, optional CIDR allowlist) is the only boundary (FR-015).

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
7. **Revocation does not kill running processes.** Revoking a key blocks all new requests
   immediately, but jobs and PTY sessions the key already started keep running until they
   finish, fail, or are cancelled. Check `uaeg jobs list` / `uaeg sessions list` after a
   revocation.
8. **Jobs can end up `interrupted`.** After a reboot (or a SIGKILL of the service), jobs
   that were running are marked `interrupted` rather than falsely reported as running
   (FR-009). Their captured output remains readable, but the process is gone.

## Product scope

9. **Single operator, not multi-tenant.** One trusted person manages keys and the server.
   There is no user identity, no per-tenant isolation, and no billing ([PRD](./PRD.md)
   section 3).
10. **No RBAC and no approval workflows.** Keys are either `all` or advisory `restricted`;
    there are no roles, no per-tool permissions, and no human confirmation steps. This is
    excluded from the MVP by design (PRD section 13), not a gap to be filled soon.
11. **The audit log stores command text.** Commands run by agents are recorded verbatim;
    any secret embedded in a command line will appear in the log. Environment variable
    *values* are not logged ([SECURITY.md](./SECURITY.md#audit-log-guarantees)).
12. **Self-signed certificates are rejected by most hosted clients.** Self-signed mode is
    for local testing only. Real deployments need a domain plus certbot, or trusted manual
    certificates (FR-017, [CONFIGURATION.md](./CONFIGURATION.md#tls-modes)).

## Explicitly out of scope (PRD section 16)

The MVP intentionally does not implement: a cloud-hosted dashboard, AI model routing or
inference, a built-in chat agent, multi-tenant SaaS, Kubernetes, distributed execution
clusters, multi-VPS orchestration, mandatory Docker sandboxing, complex RBAC, agent approval
workflows, billing or subscriptions, Cloudflare dependency, SSH-based execution transport,
or automated integration with unsupported hosted platforms. The authoritative list is
[PRD.md](./PRD.md) section 16.
