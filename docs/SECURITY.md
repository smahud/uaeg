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
| Transport confidentiality/integrity | Protected | TLS is mandatory on the public MCP listener; UAEG never silently falls back to plain HTTP (FR-017). The only bypass is the explicit `UAEG_DEV_INSECURE_HTTP=1` development escape hatch, which serves plain HTTP on the MCP listener with a loud warning — never for production |
| Key material at rest | Protected | Only SHA-256 verifiers are stored; the config file is mode 0600; plaintext secrets are never persisted or logged |
| Unauthorized API use | Protected while the key stays secret | 256-bit CSPRNG keys, bearer auth checked on every request, immediate revocation (FR-005, FR-012) |
| Anonymous public execution | Blocked | No valid bearer token, no MCP protocol handling — `401` before anything else |
| Dashboard exposure | Mitigated by password login + network placement | Loopback/LAN bind by default; since v0.3.0 `sudo uaeg pass` enables a native password login (scrypt hash, server-side sessions, CSRF + Origin checks, per-IP lockout). A wildcard bind requires `allow_public` + `dashboard.tls` + a password or the service refuses to start; optional CIDR allowlist (FR-015, FR-018) |
| Host OS integrity | **Not protected** | An authenticated agent has full root: it can read/modify/delete anything, stop UAEG, rewrite firewall rules, or destroy the OS (FR-020) |
| "Restricted" paths | **Not protected** | `restricted_paths` are advisory rules told to the agent; a root shell bypasses them trivially (FR-014) |
| Secrets inside commands | Redacted in logs (best effort) | Command text persisted in the audit log, job/session metadata, dashboard previews, and journal lines has high-confidence secret shapes masked with `***REDACTED***` (see [below](#secret-redaction-in-logs)); raw job output files are not altered |
| Processes started by a revoked key | Contained on revocation | Revoking terminates the key's live PTY sessions (SIGHUP then SIGKILL) and cancels its running background jobs (SIGTERM then SIGKILL, status `cancelled`); opt out with `--keep-processes` / `{"keep_processes": true}`. Rotation terminates nothing |

If any row in the "not protected" block is unacceptable for your use case, do not deploy
UAEG on that machine. This is a deliberate MVP trade-off, not an oversight
([PRD](./PRD.md) section 13; [LIMITATIONS.md](./LIMITATIONS.md)).

## Key handling

- **Generation:** secrets are 48-character `uaeg_...` strings carrying 256 bits of entropy
  from the operating system CSPRNG (FR-005).
- **Display:** the full secret is printed exactly once, at `generate`/`rotate` time. Store
  it immediately; it cannot be recovered.
- **Storage:** only a SHA-256 verifier is stored in the database. Listings show a short
  prefix (`uaeg_9mKQ…`) and the audit log identifies keys by key ID and name, so you can
  identify keys without ever having the secret.
- **Revocation:** immediate for new requests — the next call with a revoked key gets
  `401` — and it contains the key's running work: live PTY sessions are terminated (SIGHUP
  then SIGKILL) and running background jobs are cancelled (SIGTERM then SIGKILL, status
  `cancelled`). Opt out per revocation with the CLI `--keep-processes` flag or the
  dashboard JSON body `{"keep_processes": true}`; the dashboard revoke response reports
  the result (`containment`: `terminated_sessions`, `terminated_jobs`). Revoking one key
  never touches another key's sessions or jobs.
- **Hygiene:** one key per agent, named after the agent. Per-agent keys give per-agent audit
  trails and surgical revocation. On any suspicion of compromise: revoke first — since 0.2.0
  that also terminates the key's live sessions and jobs — then investigate.
- **Rotation is continuity, not containment:** `uaeg keys rotate` revokes the old key ID and
  creates a NEW key ID entry (the old row is kept for audit); the agent must be reconfigured
  with the new secret. Rotation deliberately terminates nothing — it is routine credential
  hygiene for a key that is still trusted, not the response to a compromise.

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
- **Firewall.** Only the MCP port (default 3902/tcp) is public by default. Open 3903 only
  when the dashboard is deliberately public (`--dashboard-bind public`: password + HTTPS).
- **Set a dashboard password.** Run `sudo uaeg pass` so the login is enforced even on a
  LAN bind; a 12+ character unique password is required.
- **CIDR allowlist.** If the dashboard is LAN-bound, also set
  `network.allowed_dashboard_cidrs` so a misconfigured bind cannot silently expose it.
  Since 0.2.0 an invalid CIDR entry is a clear startup error (fail closed) — it can no
  longer be silently ignored or silently disable the allowlist.
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

What the execution log (`exec_logs`, dashboard Logs tab) records: timestamp, key identity
(key ID and name), tool, status, exit code, duration, and the **command text**, plus
metadata and an output preview. Boundaries to understand:

- **Environment variable values are not logged.** Passing a secret via `env` keeps it out
  of the audit log.
- **Command text is redacted before storage** (see below): high-confidence secret shapes
  are masked, so `export API_KEY=sk-live-...` is stored as `export
  API_KEY=***REDACTED***`. Redaction is best-effort, not a guarantee — prefer `env` for
  secrets when the workflow allows it.

### Secret redaction in logs

Command text stored in the audit log (`exec_logs`), job metadata (jobs table), session
metadata (sessions table), session previews on the dashboard, and journal log lines is
passed through `security.redact_secrets()` before it is persisted or emitted. Only
high-confidence secret shapes are masked, so ordinary shell commands are not mangled:

- `Authorization: Bearer/Basic/Token` header and assignment values →
  `Authorization: Bearer ***REDACTED***`
- URLs with embedded credentials → `https://user:***REDACTED***@host`
- well-known token shapes → `***REDACTED***`: `uaeg_`, `ghp_`-style GitHub tokens, `sk-`
  keys, `AKIA` AWS access key IDs, `xox[baprs]-` Slack tokens, `AIza` Google API keys,
  JWTs
- PEM private key blocks → `<redacted private key>`
- `password=` / `passwd=` / `secret=` / `token=` / `api_key=` / `access_key=` /
  `client_secret=` / `aws_secret_access_key=` assignments → the name is kept, the value is
  masked with `***REDACTED***`

What is **not** redacted:

- **Raw job output files.** `/var/lib/uaeg/jobs/<job_id>/` files are never altered; a
  secret an agent prints to a background job's stdout/stderr is on disk verbatim.
- **The agent's own output.** The authenticated agent still receives its own full
  stdout/stderr via `job_output`/`shell_output`; only what UAEG writes into its own logs
  and metadata is masked.
- The root operator can inspect the raw files under `/var/lib/uaeg/jobs/<job_id>/`
  directly.
- Environment variable values passed to `shell_exec` were never logged (unchanged).

## Reporting

Suspect a security-relevant bug (e.g. auth bypass, key disclosure)? Open an issue at
https://github.com/smahud/uaeg/issues. Do not post secrets or verifier material in
public issues.
