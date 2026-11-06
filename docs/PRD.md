# UAEG — PRODUCT REQUIREMENTS DOCUMENT

| | |
|---|---|
| **Project** | Universal Agent Execution Gateway |
| **Version** | 1.0 — MVP Baseline |
| **Status** | Approved Architecture Baseline / Ready for Implementation Planning |
| **Target** | Debian 13, Single VPS |
| **Principle** | Remote Agent, Native Execution, Full OS Control, Minimal Complexity |

---

## 1. Product Vision

UAEG enables compatible cloud-hosted AI agents to execute operating-system commands directly on a remote VPS using MCP over HTTPS.

The hosted AI agent remains on its original platform. UAEG provides access to the VPS filesystem, terminal, processes, package managers, services, and operating-system capabilities.

UAEG must not require SSH, a locally installed AI agent runtime, Cloudflare Tunnel, or an external control-plane service.

The MVP prioritizes direct execution, ease of installation, reliability, and minimal operational complexity.

## 2. Core Principles

1. Native execution: Commands execute on the actual target VPS, not in a simulated filesystem or cloud-agent sandbox.
2. Full root control: Authenticated agents receive unrestricted host OS privileges by default.
3. Direct connection: Hosted agents connect to a configurable public HTTPS endpoint.
4. Minimal configuration: Connection requires only a URL and an API key when supported by the hosted platform.
5. Server-owned administration: API keys, access rules, sessions, and server settings are managed on the VPS.
6. No unnecessary abstractions: Reuse native Linux functionality instead of rebuilding package managers, service managers, or terminal functionality.
7. Evidence-based implementation: Features must be tested against actual VPS execution.
8. No overengineering: Do not introduce distributed systems, complex orchestration, or additional infrastructure without demonstrated necessity.

## 3. Target Users

Single trusted operator managing one or more hosted AI agents connected to an experimental VPS.

The MVP is not a multi-tenant SaaS product.

## 4. Deployment Architecture

The MVP consists of:

- One UAEG server application.
- One MCP HTTPS listener.
- One LAN-only administration dashboard listener.
- One UAEG CLI.
- One SQLite database.
- One systemd service.
- One native execution subsystem.

The server and dashboard share the same backend and configuration.

Default ports:

- MCP: 3902
- Dashboard: 3903

Both ports must be configurable.

## 5. Installation Requirements

### FR-001 — Git Installation

Support:

```
git clone <REPOSITORY_URL>
cd uaeg
sudo ./install.sh --port 3902
```

The installer must:

- Verify supported operating system and prerequisites.
- Install required dependencies.
- Build or install the UAEG application.
- Register and start a systemd service.
- Configure selected ports and network bindings.
- Initialize persistent configuration and SQLite storage.
- Verify service health.
- Display the connection URL and next steps.

The installer must not overwrite unrelated system configurations without an explicit installation option.

### FR-002 — One-Line Installation

Provide an optional one-line installer from an official, versioned release.

The bootstrap must support integrity verification and avoid executing an unverified, mutable installation script as root.

### FR-003 — Configuration

Support configuration of:

- Public host or domain.
- MCP port.
- Dashboard bind address.
- Dashboard port.
- TLS certificate mode.
- Data directory.
- Execution defaults.

Configuration must persist across restarts and application upgrades.

## 6. MCP Connectivity

### FR-004 — Remote MCP

Implement a standards-compatible Streamable HTTP MCP endpoint:

`https://HOST:PORT/mcp`

Support MCP initialization, tool discovery, tool invocation, and the protocol lifecycle required by compatible hosted clients.

### FR-005 — Authentication

Accept:

`Authorization: Bearer <UAEG_AGENT_KEY>`

Requirements:

- Reject missing or invalid credentials.
- Use cryptographically secure random keys.
- Store only non-recoverable key verifiers.
- Support multiple independent agent keys.
- Support immediate key revocation.
- Use HTTPS with a certificate trusted by the client.

Bearer authentication is the MVP mechanism. OAuth compatibility is conditional on the requirements of specific hosted platforms.

### FR-006 — Platform Compatibility

Provide documented integration instructions for compatible remote MCP clients.

Do not claim compatibility with platforms that cannot configure or invoke external MCP tools.

A hosted platform's built-in terminal or filesystem is not automatically redirected by UAEG. Agents must invoke UAEG tools for VPS operations.

## 7. Native Execution

### FR-007 — Shell Execution

Provide "shell_exec" supporting:

- Command execution with root privileges.
- Configurable working directory.
- Environment variables.
- Exit code.
- Standard output and error.
- Execution timeout.
- Asynchronous job execution.
- Output pagination or chunk retrieval.

Commands execute on the VPS host.

### FR-008 — Persistent Terminal

Provide:

- "shell_open"
- "shell_input"
- "shell_output"
- "shell_close"

Requirements:

- Native PTY support.
- Interactive input and output.
- Session identifiers.
- Terminal resizing where supported.
- Process status reporting.
- Reconnection to an active session.
- Explicit session termination.

### FR-009 — Background Jobs

Long-running operations must not depend on the lifetime of an HTTP request.

Each background job must have:

- Unique job ID.
- Process identifier where applicable.
- Status.
- Start and completion timestamps.
- Exit code when finished.
- Retrievable output.
- Cancellation capability.

The implementation must distinguish between a persistent running process and persisted job metadata. After a VPS reboot, previously running jobs may be marked interrupted rather than incorrectly reported as running.

### FR-010 — OS Capabilities

The default execution environment must permit authenticated agents to:

- Install and remove OS packages.
- Install language runtimes and dependencies.
- Create, modify, and delete files.
- Manage Git repositories.
- Execute build and test commands.
- Manage Docker and containers.
- Manage systemd services.
- Modify OS and network configuration.
- Start, stop, and inspect processes.
- Install additional development software.

Root permissions remain subject to hypervisor and operating-system constraints.

## 8. API Key Management

### FR-011 — Key Generation

Support:

```
uaeg generate
uaeg generate --name "Claude"
```

Generate a unique key with default permission "all".

Display the complete secret only during creation.

### FR-012 — Key Administration

Support:

```
uaeg keys list
uaeg keys revoke <KEY_ID>
uaeg keys rotate <KEY_ID>
uaeg status
uaeg doctor
```

Key metadata must include:

- Key ID.
- Display name.
- Key prefix.
- Creation time.
- Last-used time, when available.
- Status.
- Permission mode.

The server must not store recoverable plaintext API keys.

## 9. Permission Management

### FR-013 — Default Permissions

Default permission mode: "all".

All authenticated agent keys may execute native root commands unless their configuration is changed.

### FR-014 — Optional Soft Restrictions

Allow the operator to configure advisory restricted directories.

Soft restrictions may prevent direct UAEG file operations and may be communicated to the agent as usage rules.

Soft restrictions are not a security boundary.

A root shell can bypass application-level path restrictions. The product must not claim that soft-restricted directories are protected from an agent with unrestricted root execution.

Enforced filesystem isolation is outside MVP scope.

## 10. Local Dashboard

### FR-015 — Dashboard Access

Provide an HTTP dashboard accessible through a configured private LAN interface and custom port.

No application login is required in the MVP.

The dashboard must not listen on all interfaces by default.

The installer must configure or verify network-level restrictions so the dashboard is reachable only from the operator-approved private network or source addresses.

If the server has no suitable private interface, default to loopback and require explicit network configuration.

### FR-016 — Dashboard Features

Provide five sections:

**Keys**

- Generate and revoke keys.
- View key metadata.
- Set permission mode.

**Permissions**

- View default access mode.
- Manage optional advisory restricted paths.

**Sessions**

- View active and historical terminal sessions.
- View job status.
- Terminate sessions and jobs.

**Logs**

- View execution timestamps.
- View agent/key identity.
- View command metadata, status, and exit code.
- Inspect available execution output.

**Server**

- View endpoint configuration.
- View service health.
- View runtime version.
- Inspect network and TLS status.

Avoid complex visualizations and analytics in MVP.

## 11. TLS and Network

### FR-017 — Direct HTTPS

Support direct connection using a public domain or public IP address and a configurable port.

The deployment must present a TLS certificate trusted by the intended MCP client.

For domain-based installation, support automated certificate provisioning when validation prerequisites are satisfied.

For public-IP installation, support an appropriate trusted IP-address certificate or operator-provided certificate.

Do not silently fall back to insecure HTTP for the public MCP endpoint.

### FR-018 — Network Separation

MCP listener: public HTTPS, bearer authentication required.

Dashboard listener: private LAN or loopback HTTP, network restricted, no application login.

The dashboard and MCP listener must not accidentally share a public unauthenticated administrative route.

## 12. Reliability

### FR-019 — Service Management

Run UAEG as a systemd-managed service with automatic restart after unexpected failure.

Persist configuration, keys, logs, and job metadata outside the application installation directory.

### FR-020 — Operational Recovery

Provide:

- "uaeg status"
- "uaeg doctor"
- Health endpoint.
- Service logs.
- Clear error messages.
- Documented recovery procedure.

Full root execution may permit an agent to terminate UAEG, change firewall rules, or damage the operating system. The product must document this limitation.

Recovery through provider console or VPS snapshot is an operational prerequisite for destructive experiments.

## 13. Security Baseline

The MVP intentionally permits full root execution for authenticated agents in a trusted experimental environment.

Minimum requirements:

- HTTPS for public MCP access.
- Strong, independently generated API keys.
- No anonymous public execution.
- Immediate key revocation.
- Private dashboard network binding.
- Basic execution audit records.
- Protection of key material in storage.
- No sensitive credentials in ordinary logs.

Do not implement complex RBAC, approval workflows, multi-user identity management, or mandatory execution sandboxing in the MVP.

## 14. Non-Functional Requirements

- Support Debian 13.
- Support concurrent authenticated MCP connections.
- Support multiple agent keys.
- Prevent session identifier collisions.
- Bound in-memory output buffers.
- Persist job metadata and available output.
- Handle disconnected clients without unintentionally terminating background jobs.
- Provide clear diagnostic errors.
- Support installation and removal without destroying unrelated VPS resources.
- Keep dependencies and operational components minimal.

## 15. Acceptance Tests

The MVP is accepted only after these tests pass on an actual target VPS.

- **AT-01**: Install UAEG from GitHub.
- **AT-02**: Configure a custom MCP port.
- **AT-03**: Generate an agent key.
- **AT-04**: Connect a compatible hosted MCP client using HTTPS and bearer authentication.
- **AT-05**: Execute "whoami" and verify the intended root execution context.
- **AT-06**: Install a software package using the native package manager.
- **AT-07**: Create and modify files on the VPS.
- **AT-08**: Execute a build and automated test.
- **AT-09**: Create and manage a systemd service.
- **AT-10**: Open an interactive PTY and exchange input/output.
- **AT-11**: Start a background job, disconnect the client, reconnect, and retrieve job status.
- **AT-12**: Revoke an API key and verify that subsequent requests are rejected.
- **AT-13**: Create a second key and verify independent access.
- **AT-14**: Access the dashboard from an approved LAN address.
- **AT-15**: Verify dashboard access is denied from an unapproved network source.
- **AT-16**: Restart UAEG and verify persisted configuration and keys.
- **AT-17**: Verify that execution logs contain accurate job and session metadata.
- **AT-18**: Confirm no SSH connection was required for agent-to-VPS execution.
- **AT-19**: Verify that an unsupported hosted platform is reported as unsupported rather than falsely declared compatible.

## 16. Explicitly Out of Scope

The MVP must not implement:

- Cloud-hosted UAEG dashboard.
- AI model routing.
- AI model inference.
- Built-in chat agent.
- Multi-tenant SaaS.
- Kubernetes.
- Distributed execution clusters.
- Multi-VPS orchestration.
- Mandatory Docker sandbox.
- Complex role-based access control.
- Agent approval workflows.
- Billing.
- Usage subscriptions.
- Cloudflare dependency.
- SSH-based execution transport.
- Automated integration with unsupported hosted platforms.

## 17. Definition of Done

UAEG MVP is complete when:

1. A compatible hosted AI agent connects directly through MCP and HTTPS.
2. The agent executes native root commands on the target VPS.
3. Interactive and background execution work reliably.
4. The operator manages API keys and sessions from the local dashboard.
5. Configuration persists after service restart.
6. The required acceptance tests pass.
7. Installation, usage, troubleshooting, and known limitations are documented.
8. No unnecessary infrastructure has been introduced.

**Final product objective**: Provide the simplest practical bridge between a compatible hosted AI agent and a fully controllable remote Linux operating system.
