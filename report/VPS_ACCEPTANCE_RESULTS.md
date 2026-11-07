# UAEG — VPS Acceptance Results (AT-01..AT-19)

| | |
|---|---|
| **Runbook** | docs/ACCEPTANCE.md (AT-01..AT-19, PRD section 15) |
| **Release under test** | v0.2.0 (release commit; see [RELEASE_DECISION.md](RELEASE_DECISION.md)) |
| **Execution environment** | Debian GNU/Linux 13 (trixie) container — real userspace of the target OS, but: **non-root (no sudo), no systemd daemon, single network interface, no public IP/DNS, no hosted MCP platform, no sshd** |
| **Execution date** | 2026-11-07/08 (UTC) |
| **Method** | `scripts/acceptance_run.py` (audit harness): performs a real `install.sh` installation into an isolated tree, then drives the AT procedures through the installed CLI, a real TLS MCP client, the dashboard HTTP API and direct DB inspection. Non-root/systemd steps disabled exactly as in the repository's own CI installer-smoke job. |

**Honesty rule applied**: tests that require a real dedicated VPS, root, systemd, a hosted
MCP client or a second LAN host are reported **NOT EXECUTED** (or **ADAPTED** with the
executed equivalent and the missing leg named) — never as passed. Per PRD section 15 the
MVP is accepted only after these tests pass **on an actual target VPS**; that final gate
remains open (see [RELEASE_DECISION.md](RELEASE_DECISION.md)).

## Results

| ID | Result | Notes |
|----|--------|-------|
| AT-01 Install from the repository | **ADAPTED** | Installer completed end-to-end in an isolated tree (`--no-service`): deps, venv, pinned install, self-signed TLS, config write, URL+next-steps output, `uaeg version` → 0.2.0. **Systemd registration leg NOT EXECUTED** (container has no systemd/root; unit content and installer wiring code-verified). |
| AT-02 Custom MCP port | **PASS** | `uaeg config set mcp.port` → restart → listener moved (verified via connection + healthz 200 on new port); conflicting `dashboard.port` rejected with a clear config error. |
| AT-03 Generate an agent key | **PASS** | 48-char `uaeg_…` secret printed exactly once; re-running `generate` never re-displays it; `keys list` shows the key active with prefix. |
| AT-04 Hosted MCP client over HTTPS + bearer | **NOT EXECUTED** | No hosted platform exists in the sandbox. Protocol-level equivalent executed with a real TLS client: initialize (server `UAEG` v0.2.0), 13/13 tools listed, `server_info` OK. The hosted-platform leg (custom header, non-standard port, trust chain) must be run by the operator. |
| AT-05 `whoami` root context | **ADAPTED** | `shell_exec("id -un")` → `z` == `server_info().exec_user`, exit 0 — execution-user consistency verified. **Root-context leg NOT EXECUTED** (sandbox is non-root by design); on a standard install the service runs as root (unit `User=root`, doctor explains). |
| AT-06 Package install via apt | **NOT EXECUTED** | Requires root. Execution subsystem is a full root shell on a standard install (code-verified); operator must run on the VPS. |
| AT-07 Create and modify files | **PASS** | create → append → read (host-side cross-check `v1\nv2\n`) → delete, all via the agent. |
| AT-08 Build and automated test | **PASS** | `gcc` build + binary run (`at08 5` in stdout) and `python -m unittest` (OK, exit 0) via the agent, cwd-scoped per the runbook. |
| AT-09 Create and manage a systemd service | **NOT EXECUTED** | Requires root + systemd daemon. Unit-file generation by the installer and `systemctl` interactions are code-verified only. |
| AT-10 Interactive PTY exchange | **PASS** | open → `uptime` output captured with advancing seq cursor → resize 200×50 (`stty size` → `50 200`) → session listed → close. |
| AT-11 Background job across disconnect | **PASS** | `sleep 3 && echo at11-done` backgrounded; client fully disconnected; fresh client session queried status (`running` during the window) → `completed` exit 0 → output retrieved. |
| AT-12 Revoked key rejected | **PASS** | Pre-revoke request non-401; after `uaeg keys revoke`: every request 401; AT-03 key still works; key shows revoked. **v0.2.0 containment verified**: the temp key's live session was terminated and its running job cancelled by the revocation (`terminated 1 session(s), 1 background job(s)`). |
| AT-13 Second key independent access | **PASS** | Second key authenticates, executes `whoami`, independent of the first. |
| AT-14 Dashboard from approved address | **ADAPTED** | Served from loopback (the approved source in this environment): 5 tabs render, overview 200, keys listed, secret modal API verified in the e2e dashboard tests. **True LAN-address leg NOT EXECUTED** (single-interface container). |
| AT-15 Dashboard denied from unapproved source | **ADAPTED** | With `allowed_dashboard_cidrs=["10.99.0.0/16"]`, a request from 127.0.0.1 → **HTTP 403** (CIDR control enforced); `ss` shows the dashboard bound to 127.0.0.1 only (never 0.0.0.0). **Public-interface / second-host leg NOT EXECUTED.** Since v0.2.0 an invalid CIDR entry is a startup error (fail closed), so with an allowlist configured the only outcomes are 403/refused/timeout. |
| AT-16 Restart persistence | **PASS** | `exec.default_timeout_s=333` survived restart; keys identical before/after; job history preserved; agent re-authenticated with the existing key and executed post-restart. |
| AT-17 Audit log metadata | **PASS** | Row for `echo at17-marker`: identity `at03`, tool `shell_exec`, status `ok`, exit 0; env value `SECRET=at17secret` absent from all log fields; command text stored redacted (high-confidence secret shapes masked) while remaining identifiable. |
| AT-18 No SSH required | **PASS** | All agent operations above ran over MCP/HTTPS; no sshd process or listener exists in this environment at all; UAEG's codebase invokes no ssh anywhere. |
| AT-19 Unsupported platforms honestly reported | **PASS** | README/USAGE declare stdio-only, no-custom-header and locked-catalog platforms unsupported; no blanket compatibility claims found in any doc. |

**Totals: 12 PASS · 4 ADAPTED · 3 NOT EXECUTED · 0 FAIL.**

## Outstanding legs requiring an authorized Debian 13 VPS (the "real runbook" gate)

1. **AT-01/AT-09**: full systemd lifecycle — install with service registration, `systemctl is-active`, agent-created `at09.service` start/stop/cleanup.
2. **AT-04**: a hosted MCP platform (e.g. a configured remote client) connecting with URL + bearer header + custom port + trusted cert; `LAST USED` advancing.
3. **AT-05**: `whoami` → `root` on the standard root install.
4. **AT-06**: `apt-get update && apt-get install -y jq` via the agent.
5. **AT-14/AT-15**: dashboard from an operator LAN workstation; denial from outside the approved network (hotspot or CIDR-outside LAN address).
6. Certbot issuance + renewal drill (port 80 + DNS): certificate issuance at install, renewal hook restarting UAEG (code-verified, not drilled).

Operator authorization prerequisites per the audit mission: none of the above were
attempted destructively; the sandbox run used only isolated directories and loopback ports.
The VPS legs are non-destructive except AT-06/AT-09 package/service fixtures, which the
runbook cleans up.
