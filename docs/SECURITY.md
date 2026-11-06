# Security

UAEG's security model: what is protected, what is not, how keys and TLS work, how to harden
a deployment, and the recovery prerequisites you must have in place **before** an agent does
something destructive.

## The model in one paragraph

UAEG authenticates the agent, protects the transport, and restricts the dashboard by network
placement. It does **not** constrain what an authenticated agent can do: by design, an
authenticated key is equivalent to a root shell on the VPS (FR-005, FR-010, FR-013). Every
security decision downstream of that fact is about protecting the *key* and the *network
path*, never about containing the *agent*.

## Threat model

| Concern | Status | Notes |
|---------|--------|-------|
| Transport confidentiality/integrity | Protected | TLS is mandatory on the public MCP listener; UAEG never silently falls back to plain HTTP (FR-017) |
| Key material at rest | Protected | Only SHA-256 verifiers are stored; the config file is mode 0600; plaintext secrets are never persisted or logged |
| Unauthorized API use | Protected while the key stays secret | 256-bit CSPRNG keys, bearer auth checked on every request, immediate revocation (FR-005, FR-012) |
| Anonymous public execution | Blocked | No valid bearer token, no MCP protocol handling — `401` before anything else |
| Dashboard exposure | Mitigated by network placement | Loopback/LAN bind by default, wildcard bind refused without explicit opt-in, optional CIDR allowlist; there is **no login** (FR-015, FR-018) |
| Host OS integrity | **Not protected** | An authenticated agent has full root: it can read/modify/delete anything, stop UAEG, rewrite firewall rules, or destroy the OS (FR-020) |
| "Restricted" paths | **Not protected** | `restricted_paths` are advisory rules told to the agent; a root shell bypasses them trivially (FR-014) |
| Secrets inside commands | **Not protected** | The audit log records command text; any secret an agent embeds in a command will be logged (see below) |
| Processes started by a revoked key | **Not retroactively stopped** | Revocation blocks new requests; running jobs/PTYs continue until they finish or are cancelled |

If any row in the "not protected" block is unacceptable for your use case, do not deploy
UAEG on that machine. This is a deliberate MVP trade-off, not an oversight
([PRD](./PRD.md) section 13; [LIMITATIONS.md](./LIMITATIONS.md)).

## Key handling

- **Generation:** secrets are 48-character `uaeg_...` strings carrying 256 bits of entropy
  from the operating system CSPRNG (FR-005).
- **Display:** the full secret is printed exactly once, at `generate`/`rotate` time. Store
  it immediately; it cannot be recovered.
- **Storage:** only a SHA-256 verifier is stored in the database. Listings and logs show a
  short prefix (`uaeg_9mKQ…`) so you can identify keys without ever having the secret.
- **Revocation:** a status flip enforced per request — the next call with a revoked key
  gets `401`. Rotate (`uaeg keys rotate`) to replace a possibly-leaked secret while keeping
  the key entry.
- **Hygiene:** one key per agent, named after the agent. Per-agent keys give per-agent audit
  trails and surgical revocation. On any suspicion of compromise: revoke first, investigate
  second, then cancel anything the key left running (`uaeg jobs list`, `uaeg sessions list`).
- **Rotation of trust:** revoking a key does not kill processes it already started. Check
  for running jobs and live sessions after a revocation.

## TLS guidance

- Prefer a real domain plus certbot (Let's Encrypt) — hosted clients trust it with zero
  configuration ([INSTALL.md](./INSTALL.md)).
- Public IP only: use `manual` mode with a trusted certificate that covers the IP address.
- Self-signed mode is for local testing with `curl -k`; most hosted agents will refuse it
  ([LIMITATIONS.md](./LIMITATIONS.md)).
- Watch certificate expiry — `uaeg doctor` reports subject, SAN, and expiry. Certbot
  standalone renewals need port 80 free, like issuance.

## Operational hardening

- **Dedicated VPS.** Never install UAEG on a machine with data or access you cannot afford
  to lose. Assume the agent will eventually run `rm -rf` or equivalent.
- **Snapshots before destructive experiments.** A provider snapshot/restore you have
  actually tested is an operational prerequisite (FR-020).
- **Firewall.** Only the MCP port (default 3902/tcp) is public. Never open 3903.
- **CIDR allowlist.** If the dashboard is LAN-bound, also set
  `network.allowed_dashboard_cidrs` so a misconfigured bind cannot silently expose it.
- **Separate keys per agent** and revoke anything unused or suspicious.
- **Keep TLS valid** and renewal working.
- **Review the audit log** (dashboard Logs tab) for commands you did not expect.

## The documented limitation (FR-020)

Full root execution means an agent can, intentionally or not:

- stop, mask, or uninstall UAEG itself (`systemctl stop uaeg`, `rm -rf /opt/uaeg`);
- change firewall rules and lock you out of the network path;
- damage or destroy the operating system.

**Recovery prerequisites** — have these working *before* you need them:

1. Out-of-band access to the provider console (VNC/serial shell) that does not depend on
   SSH, UAEG, or the VPS firewall.
2. A recent snapshot, plus a restore procedure you have rehearsed at least once.

The recovery procedure itself is documented step by step in
[TROUBLESHOOTING.md](./TROUBLESHOOTING.md#recovery-procedure-fr-020).

## Audit log guarantees

What the execution log (`exec_logs`, dashboard Logs tab) records: timestamp, key identity,
tool, status, exit code, duration, and the **command text**, plus metadata and an output
preview. Two boundaries to understand:

- **Environment variable values are not logged.** Passing a secret via `env` keeps it out
  of the audit log.
- **Command text is logged verbatim.** If the agent embeds a secret in a command line
  (`export API_KEY=sk-live-...`), that text — and therefore the secret — is in the log.
  Prefer `env` for secrets when the workflow allows it.

## Reporting

Suspect a security-relevant bug (e.g. auth bypass, key disclosure)? Open an issue at
https://github.com/smahud/uaeg/issues. Do not post secrets or verifier material in
public issues.
