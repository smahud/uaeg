# Acceptance

The AT-01..AT-19 acceptance runbook from the [PRD](./PRD.md) (section 15), written as an
executable procedure: exact commands, expected results, and pass criteria for each test on a
real VPS.

## How to use this runbook

- Run on a fresh Debian 13 VPS dedicated to testing (agents get root — see
  [SECURITY.md](./SECURITY.md)).
- Prerequisites: `gw.example.com` (a real DNS record) pointing at the VPS, root access, an
  operator workstation on the LAN (AT-14), and access to a compatible hosted agent platform
  (from AT-04 on).
- Record the environment before starting:

  ```bash
  cat /etc/debian_version; python3 --version; uaeg version; git -C /opt/uaeg rev-parse HEAD
  ```

- Fill in the [results table](#results) as you go. A test passes only when **all** of its
  pass criteria hold.

## Sandbox smoke coverage

The repository's automated test suite (`pytest`, run by GitHub Actions CI on every push)
covers the protocol and execution flows — MCP over Streamable HTTP, bearer authentication
including revocation, `shell_exec`, background jobs, and PTY session logic — against a
sandboxed service instance (sandbox-tested on Python 3.12). CI is a smoke gate for every
change; **this VPS runbook is the final acceptance gate** (PRD section 15: the MVP is
accepted only after these tests pass on an actual target VPS).

## Test summary

| ID | Test |
|----|------|
| AT-01 | Install UAEG from GitHub |
| AT-02 | Configure a custom MCP port |
| AT-03 | Generate an agent key |
| AT-04 | Connect a hosted MCP client over HTTPS with bearer auth |
| AT-05 | Execute `whoami` and verify the root execution context |
| AT-06 | Install a software package via the native package manager |
| AT-07 | Create and modify files on the VPS |
| AT-08 | Execute a build and an automated test |
| AT-09 | Create and manage a systemd service |
| AT-10 | Open an interactive PTY and exchange input/output |
| AT-11 | Background job survives client disconnect and reconnection |
| AT-12 | Revoked key is rejected |
| AT-13 | Second key grants independent access |
| AT-14 | Dashboard reachable from an approved LAN address |
| AT-15 | Dashboard denied from an unapproved network source |
| AT-16 | Configuration and keys persist across restart |
| AT-17 | Execution logs contain accurate job and session metadata |
| AT-18 | No SSH connection required for agent execution |
| AT-19 | Unsupported platforms are reported as unsupported |

---

## AT-01 — Install UAEG from GitHub (FR-001)

**Commands:**

```bash
git clone https://github.com/smahud/uaeg.git
cd uaeg
sudo ./install.sh --domain gw.example.com
```

**Expected:** the installer completes without errors, polls `/healthz` successfully, prints
the connection URL (`https://gw.example.com:3902/mcp`) and next steps, and — if `ufw` is
active — prints the `ufw allow` command for port 3902.

**Pass criteria:**

```bash
systemctl is-active uaeg          # -> active
uaeg status                       # health OK, listeners on 3902/3903
uaeg doctor                       # no ERROR lines (the root warning is expected)
```

**Notes:** record OS version and Python version with the result.

## AT-02 — Configure a custom MCP port (FR-003)

**Commands:**

```bash
uaeg config set mcp.port 3950
sudo systemctl restart uaeg
uaeg status
ss -ltnp | grep 3950
```

**Expected:** the MCP listener moves to port 3950; the dashboard stays on 3903;
the agent URL becomes `https://gw.example.com:3950/mcp`.

**Pass criteria:** `uaeg status` reports the new port; `ss` shows UAEG listening on 3950;
and startup validation is confirmed — setting `dashboard.port` to the same value and
restarting is rejected with a clear configuration error.

**Notes:** open the new port in the firewall (`sudo ufw allow 3950/tcp`) for the listener
check from outside. Afterwards either keep 3950 (and use it in AT-04 on) or revert to 3902.

## AT-03 — Generate an agent key (FR-011)

**Commands:**

```bash
uaeg generate --name "at03"
```

**Expected:** the Key ID and the full 48-character `uaeg_...` secret are printed exactly
once.

**Pass criteria:** `uaeg keys list` shows the key with name `at03`, status `active`,
permissions `all`, and the matching prefix; running `generate` again never re-displays the
old secret.

**Notes:** store the secret for AT-04; this is your only chance.

## AT-04 — Connect a hosted MCP client (FR-004, FR-005)

**Commands (on the platform):** configure a remote MCP server with:

- URL: `https://gw.example.com:3902/mcp` (adjust the port if AT-02 kept 3950)
- Header: `Authorization: Bearer <secret from AT-03>`

Then let the client initialize and list tools.

**Expected:** the client initializes a session; `tools/list` returns the 13 UAEG tools;
calling `server_info()` returns the execution context and usage rules.

**Pass criteria:** all 13 tools are listed; `server_info()` succeeds; `uaeg keys list`
shows `LAST USED` advancing for the AT-03 key.

**Notes:** if the platform cannot express URL + custom header, it is unsupported — stop and
record that (it feeds AT-19); do not force it.

## AT-05 — Execute `whoami` (FR-007)

**Commands (agent):**

```text
shell_exec(command="whoami")
```

**Expected:**

```json
{ "ok": true, "exit_code": 0, "stdout": "root\n", "duration_ms": 12, "...": "..." }
```

**Pass criteria:** `stdout` is exactly `root\n` and `exit_code` is 0.

**Notes:** AT-05 expects `root` on a standard install — the service runs as root by design.
Any other user means the deployment differs from the supported one; investigate before
continuing. This test is both the core feature and the core risk
([SECURITY.md](./SECURITY.md)).

## AT-06 — Install a software package (FR-010)

**Commands (agent):**

```text
shell_exec(command="apt-get update && apt-get install -y jq", timeout_seconds=300)
shell_exec(command="jq --version")
```

**Expected:** first call exits 0 after apt completes; second call prints the jq version.

**Pass criteria:** both calls return `exit_code: 0` and `jq --version` output appears in
`stdout`.

## AT-07 — Create and modify files

**Commands (agent):**

```text
shell_exec(command="printf 'v1\n' > /root/at07.txt && cat /root/at07.txt")
shell_exec(command="printf 'v2\n' >> /root/at07.txt && cat /root/at07.txt")
```

**Expected:** `v1` then `v1\nv2` in `stdout`.

**Pass criteria:** outputs match; cross-check on the VPS (`cat /root/at07.txt`) shows
`v1` and `v2` on separate lines.

## AT-08 — Execute a build and an automated test

**Commands (agent):** send a `shell_exec` whose command is equivalent to:

```bash
apt-get install -y build-essential
mkdir -p /root/at08 && cd /root/at08
cat > math.c <<'EOF'
#include <stdio.h>
int add(int a, int b) { return a + b; }
int main(void) { printf("at08 %d\n", add(2, 3)); return 0; }
EOF
gcc -o math math.c && ./math
```

**Expected:** `stdout` contains `at08 5`, `exit_code` 0.

**Pass criteria:** compilation and execution succeed via the agent with no operator
intervention. Optionally repeat with a Python `unittest` file and `python3 -m unittest -v`
to cover a scripted test runner.

**Notes:** the first call may need `timeout_seconds=300` for the toolchain install.

## AT-09 — Create and manage a systemd service (FR-010)

**Commands (agent):** send a `shell_exec` whose command is equivalent to:

```bash
cat > /etc/systemd/system/at09.service <<'EOF'
[Unit]
Description=UAEG AT-09 test service

[Service]
ExecStart=/bin/sleep 600

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl start at09.service && systemctl is-active at09.service
```

Then clean up:

```bash
systemctl stop at09.service && systemctl disable at09.service
rm /etc/systemd/system/at09.service && systemctl daemon-reload
```

**Expected:** `is-active` prints `active`; cleanup runs cleanly.

**Pass criteria:** the agent-created unit starts and reports active; after cleanup,
`systemctl status at09.service` reports the unit no longer exists.

## AT-10 — Interactive PTY exchange (FR-008)

**Commands (agent):**

```text
shell_open(cols=120, rows=30)
# -> { "ok": true, "session_id": "<session-id-hex>", "pid": ..., ... }

shell_input(session_id="<session-id-hex>", data="uptime", add_newline=true)

shell_output(session_id="<session-id-hex>", since_seq=0)
# -> { "data": "...uptime output...", "seq": N, "alive": true, ... }

shell_resize(session_id="<session-id-hex>", cols=200, rows=50)

shell_input(session_id="<session-id-hex>", data="\x03")      # Ctrl-C, e.g. to interrupt a command

shell_close(session_id="<session-id-hex>")
```

**Expected:** the session opens; input produces output with an advancing `seq` cursor;
resize succeeds; Ctrl-C behaves as an interrupt.

**Pass criteria:** `shell_sessions()` lists the session before `shell_close` and not after;
output of `uptime` is captured; `shell_resize` returns ok and subsequent output reflects the
new geometry (e.g. in a full-screen program).

**Notes:** to exercise resize visibly, run a full-screen tool (e.g. `htop`) and resize it.

## AT-11 — Background job across disconnect (FR-009)

**Commands (agent):**

1. Start the job and note the `job_id`:

   ```text
   shell_exec(command="sleep 120 && echo at11-done", background=true)
   ```

2. **Fully close the client** (end the conversation / disconnect the MCP session) within
   the 120-second window.

3. After at least 30 seconds, reconnect the client (a fresh MCP session is fine).

4. Query the job:

   ```text
   job_status(job_id="job-...")
   job_wait(job_id="job-...", timeout_seconds=180)
   job_output(job_id="job-...", stream="stdout", offset=0, limit=65536)
   ```

**Expected:** `job_status` shows `running` (with a pid) when queried during the window, and
`completed` with `exit_code: 0` after the sleep finishes; `job_output` returns `at11-done\n`.

**Pass criteria:** the job kept running across the client disconnect; after reconnecting,
status and output are retrievable; cross-check `uaeg jobs list` on the VPS shows the same
job and final state.

**Notes:** the 120-second sleep leaves time to disconnect; adjust if your reconnect takes
longer.

## AT-12 — Revoked key is rejected (FR-005, FR-012)

**Commands:**

```bash
uaeg generate --name "at12-temp"          # copy the secret
KEY="uaeg_..."                            # the printed secret
KEY_ID="k-..."                           # the printed Key ID

# before revocation: any request with the key must NOT be 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -d '{}' https://gw.example.com:3902/mcp

uaeg keys revoke "$KEY_ID"

# after revocation: every request is 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -d '{}' https://gw.example.com:3902/mcp
```

**Expected:** the first probe returns a non-401 status (the empty body is a protocol
error, not an auth error); the second probe returns `401`.

**Pass criteria:** every request with the revoked key returns `401` immediately; the AT-03
key still works (revoke is per key); `uaeg keys list` shows `at12-temp` as revoked.

**Notes:** auth is checked before MCP body processing, so the empty `{}` body is fine for
the 401 check.

## AT-13 — Second key, independent access (FR-005)

**Commands:**

```bash
uaeg generate --name "at13-second"
```

Configure a second client (or reconfigure the existing one) with the new secret and run:

```text
shell_exec(command="whoami")
```

**Expected:** `root` in `stdout`, exit code 0.

**Pass criteria:** the second key authenticates independently; `uaeg keys list` shows each
key's `LAST USED` advancing separately; revoking the AT-12 temp key earlier did not affect
either working key.

**Notes:** proves multiple simultaneous keys and key independence.

## AT-14 — Dashboard from an approved LAN address (FR-015)

**Preparation:** the dashboard must be bound to the LAN — install with
`--dashboard-bind lan`, or rebind:

```bash
uaeg config set dashboard.bind 192.168.1.20        # your VPS LAN IP
uaeg config set network.allowed_dashboard_cidrs '["192.168.1.0/24"]'   # optional
sudo systemctl restart uaeg
```

**Commands:** from the operator workstation on that LAN, open `http://192.168.1.20:3903`.

**Expected:** the dashboard loads with the five tabs (Keys, Permissions, Sessions, Logs,
Server).

**Pass criteria:** all tabs render; Keys lists the keys created above; generating a key in
the dashboard shows the one-time secret modal; Server shows health, version, and TLS
details.

**Notes:** if the dashboard is loopback-bound, this test requires rebinding (above) or an
SSH tunnel — but the tunnel variant does not exercise LAN reachability, so rebind for the
real test.

## AT-15 — Dashboard denied from an unapproved source (FR-015)

**Commands:** from a host **outside** the approved network (e.g. a mobile hotspot):

```bash
curl -m 10 -sv http://<PUBLIC_IP>:3903/ -o /dev/null
```

**Expected:** the connection times out or is refused (dashboard not bound to the public
interface), or returns HTTP 403 (CIDR allowlist rejection).

**Pass criteria:** no dashboard content is served — a timeout, connection refused, or 403
all pass; **any `200` with HTML is a failure**. Confirm the bind on the VPS:

```bash
ss -ltnp | grep 3903     # must show the LAN IP or 127.0.0.1, not 0.0.0.0
```

**Notes:** also verify no provider-level firewall rule opens 3903 to the internet. If a
CIDR allowlist is configured, additionally test from a LAN address outside the list if
practical.

## AT-16 — Restart: persisted configuration and keys (FR-003, FR-019)

**Commands:**

```bash
uaeg config set exec.default_timeout_s 333
sudo systemctl restart uaeg
uaeg config get exec.default_timeout_s       # -> 333
uaeg keys list                               # all keys still present
uaeg jobs list                               # job history preserved
```

Then reconnect the agent with the existing AT-03 key and run `shell_exec("echo post-restart")`.

**Expected:** the changed value survives the restart; keys and job history are intact; the
agent authenticates without any re-registration.

**Pass criteria:** all four checks hold; the agent command succeeds after the restart.

**Notes:** revert `exec.default_timeout_s` to your preferred value afterwards.

## AT-17 — Execution logs contain accurate metadata (FR-016, FR-020)

**Commands (agent):** run a recognizable command:

```text
shell_exec(command="echo at17-marker")
```

Then open the dashboard **Logs** tab (and optionally the database on the VPS):

```bash
python3 - <<'EOF'
import sqlite3
con = sqlite3.connect("file:/var/lib/uaeg/uaeg.db?mode=ro", uri=True)
for row in con.execute("SELECT * FROM exec_logs ORDER BY rowid DESC LIMIT 5"):
    print(row)
EOF
```

**Expected:** the Logs tab shows a row for the command with time, the key identity used,
tool `shell_exec`, status, exit code 0, duration, and command text `echo at17-marker`; the
metadata and output preview expand correctly.

**Pass criteria:** the entry matches the actual execution (tool, exit code, command text,
key identity); timestamps are sane.

**Notes:** environment variable values must not appear in the log — optionally verify with
a call that passes `env={"SECRET": "at17"}` and confirm the value `at17` is absent from the
entry.

## AT-18 — No SSH connection required (PRD section 1)

**Procedure:**

1. Record the start time on the VPS:

   ```bash
   date -u && date +%s    # note T0
   ```

2. Perform a batch of agent executions from the hosted platform (AT-05 through AT-11
   already provide them; otherwise run several `shell_exec` calls now). Do not open any new
   SSH session yourself during this window.

3. Check sshd's journal for the window:

   ```bash
   journalctl -u ssh --since "@<T0>" --no-pager | grep -Ei 'accepted|session opened|pam_unix'
   ```

**Expected:** no new SSH authentication or session-opened entries during the window.

**Pass criteria:** all agent operations completed while `journalctl -u ssh` shows zero new
sessions attributable to them. The strongest variant: disable or remove sshd entirely
(`systemctl disable --now ssh`) and confirm agent execution still works — proving no SSH
involvement at all.

**Notes:** operator SSH logins made *before* T0 do not count; the check is about new
sessions during the agent activity window. Re-enable sshd afterwards if you need it for
administration.

## AT-19 — Unsupported platforms reported as unsupported (FR-006)

**Compatibility checklist** — a platform is compatible only if it supports **all** of:

1. Adding an external (remote) MCP server.
2. Streamable HTTP transport with JSON responses.
3. Custom request headers (`Authorization: Bearer ...`).
4. URLs with an explicit port (`https://host:3902/mcp`).
5. TLS with your server certificate chain.

A platform is **incompatible** if any of the following hold: stdio-only MCP support; no way
to set custom headers; a locked integration catalog with no external MCP option; a proxy
that strips `Authorization`; inability to reach non-standard ports.

**Procedure:**

1. Pick a candidate platform that fails at least one requirement.
2. Attempt to configure it as an MCP client for UAEG.
3. Compare the outcome with UAEG's documentation: the compatibility notes in
   [README.md](../README.md#platform-compatibility) and
   [USAGE.md](./USAGE.md#connecting-a-hosted-agent) must describe such platforms as
   unsupported and must not claim compatibility with them.

**Expected:** the platform cannot connect (by its own limitation), and UAEG's documentation
reports this class of platform as unsupported.

**Pass criteria:** the candidate is evaluated against the checklist and the result
(compatible / unsupported, with the failing requirement) is recorded; no UAEG documentation
or tooling claims compatibility for an incompatible platform.

**Notes:** the acceptance criterion is honest reporting, not making incompatible platforms
work (FR-006).

---

## Results

| ID | Result (pass/fail) | Date | Notes |
|----|--------------------|------|-------|
| AT-01 | | | |
| AT-02 | | | |
| AT-03 | | | |
| AT-04 | | | |
| AT-05 | | | |
| AT-06 | | | |
| AT-07 | | | |
| AT-08 | | | |
| AT-09 | | | |
| AT-10 | | | |
| AT-11 | | | |
| AT-12 | | | |
| AT-13 | | | |
| AT-14 | | | |
| AT-15 | | | |
| AT-16 | | | |
| AT-17 | | | |
| AT-18 | | | |
| AT-19 | | | |

The MVP is accepted when every row passes on the target VPS (PRD section 15 and
Definition of Done, section 17).
