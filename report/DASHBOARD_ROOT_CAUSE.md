# Dashboard Root Cause — Production Incident (host.zerotier.my.id:3903)

**Date:** 2026-11-08 · **Severity:** P0 (mission trigger) · **Status:** ROOT CAUSE
ESTABLISHED WITH EVIDENCE; production fix = operator runbook (deployment access
not available from the audit environment — see PRODUCTION_DEPLOYMENT.md).

---

## 1. Reported symptom

> "The dashboard at https://host.zerotier.my.id:3903 does not display a usable
> interface."

## 2. Evidence timeline (all measurements from this audit, UTC)

| Time (UTC) | Measurement | Result |
|---|---|---|
| 01:44:23 | `curl https://host.zerotier.my.id:3903/` | **Connection refused** (RST in 147 ms — not a timeout) |
| 01:44:23 | `curl https://host.zerotier.my.id:3902/mcp` | **Connection refused** (RST in 270 ms) |
| 01:44:2x | TCP scan 125.163.142.132 | 22/80/443 **OPEN**; 3902/3903 **refused** |
| 01:44:2x | Egress control | github.com 200, example.com 200 (sandbox network healthy) |
| 03:08:13 | TCP scan (repeat) | 22/80/443 OPEN; **3902 OPEN**; 3903 still **refused** |
| 03:08:38 | `GET https://host.zerotier.my.id:3902/healthz` | `{"status":"ok","service":"UAEG","version":"0.3.2","uptime_seconds":7625.7}` |
| 03:08:38 | `GET /mcp` (no key) | **401 Unauthorized** (correct fail-closed auth) |
| 03:08:38 | TLS certificate (3902) | Let's Encrypt, CN/SAN `host.zerotier.my.id`, valid to 2027-02-05 |

Raw captures: `report/evidence/production_network_probe_v033.txt`,
`report/evidence/production_mcp_probe_v033.txt`.

## 3. What the evidence proves

1. **The host is up and healthy** (SSH/HTTP/HTTPS open; the sandbox's network
   path is verified good by control probes). The failure is not DNS, not
   routing, not the audit environment.
2. **The UAEG service process is alive and healthy on the MCP side**: v0.3.2,
   `/healthz` OK, TLS 1.3 with a valid, browser-trusted Let's Encrypt
   certificate, and bearer authentication correctly returns 401 before any
   MCP handling. The process has been up since ≈ 01:01 UTC (uptime 7625 s at
   03:08:38, continuous — no crash loop).
3. **Both UAEG ports were refused at 01:44 while the service was already
   running** (uptime proves the process predated that probe). A kernel-level
   "nothing listening on that address" RST cannot explain a refused
   connection to a live `0.0.0.0:3902` listener — so at 01:44 a **firewall
   REJECT** (fast RST, not DROP-timeout) covered both ports. Between 01:44
   and 03:08 the **3902 rule was opened** (operator activity — consistent
   with the v0.3.2 deployment runbook's `ufw allow 3902/tcp` step). 3903
   remains blocked-or-unbound.
4. **The dashboard port 3903 is still refused with MCP working.** UAEG runs
   both listeners in one supervised process (a dead dashboard listener stops
   the whole service — server.py fail-together supervision, verified by
   `test_amain_returns_nonzero_when_listener_crashes`). Since the process is
   alive and serving MCP, the dashboard listener exists — it simply is **not
   reachable on the public interface**.

## 4. Root cause (layered)

**Direct cause — the dashboard listener is not reachable from the public
internet**, because one or both of:

- **RC-A (primary): the dashboard is bound to `127.0.0.1` (loopback)** —
  this is the **installer default** (`dashboard.bind = "127.0.0.1"` in
  config.py; `--dashboard-bind loopback` default in install.sh). A loopback
  bind is unreachable from any external address by design; external SYNs to
  the public IP get a kernel RST — exactly the observed "connection refused".
  For public access UAEG requires an explicit, fail-closed opt-in:
  `dashboard.bind=0.0.0.0` + `dashboard.allow_public=true` +
  `dashboard.tls=true` + a password set via `sudo uaeg pass`
  (Config.validate + serve-time guard; also enforced by the installer and
  the upgrader).
- **RC-B (contributing): firewall** — at minimum a firewall REJECT covered
  both ports at 01:44 (proven by the refused-to-open transition on 3902).
  If RC-A is fixed but the firewall still rejects 3903, the symptom
  persists.

**Contributing factor (process):** the v0.3.2 deployment to MicroTix was
performed by the operator AFTER the audit trail marked it "PENDING operator"
(report/PRODUCTION_READINESS.md §3 of v0.3.2). The operator completed the MCP
path (service healthy, port opened) but the **public-dashboard steps were
never executed** — unsurprising, because the v0.3.2 runbook's default flow
does not include them (the installer defaults the dashboard to loopback and
the firewall section says "do NOT open 3903" for private binds).

**Explicitly ruled out (with evidence):**
- Frontend defect: the v0.3.2/v0.3.3 dashboard UI renders and functions
  correctly in a real browser (login → dashboard → all tabs → key creation →
  logout; zero console errors, favicon 200) —
  `report/evidence/lab_browser_verification_v033.txt` + screenshots. The UI
  never received the operator's request; the TCP layer failed first.
- Service down / crash loop: disproven by live `/healthz` + continuous
  uptime.
- TLS/certificate problem on the reachable path: disproven by the valid
  Let's Encrypt chain on 3902 (and an unreachable port never reaches TLS
  anyway).
- DNS/network problem: disproven by the A record + open control ports +
  healthy egress probes.

## 5. Expected vs actual behavior

| | Expected (operator intent) | Actual |
|---|---|---|
| `https://host.zerotier.my.id:3903` | Login page → authenticated dashboard | TCP RST before any HTTP/TLS — browser connection-error page |
| `https://host.zerotier.my.id:3902/mcp` | MCP endpoint (bearer auth) | Working (401 without key, healthy) |

## 6. Fix (operator runbook — requires VPS access; not executable from this
audit environment)

On the VPS as root:

```bash
# 0. Confirm current state (expected: bind 127.0.0.1, no public access)
sudo uaeg config get dashboard.bind          # -> 127.0.0.1  (the root cause)
sudo uaeg doctor

# 1. Dashboard password (fail-closed prerequisite; skip if already set)
sudo uaeg pass                               # interactive, hidden input, twice

# 2. Enable the public dashboard (fail-closed: password + TLS + opt-in)
sudo uaeg config set dashboard.bind 0.0.0.0
sudo uaeg config set dashboard.allow_public true
sudo uaeg config set dashboard.tls true      # reuses the existing LE certificate

# 3. Apply + open the firewall
sudo systemctl restart uaeg
ufw allow 3903/tcp                           # or the equivalent for your firewall

# 4. Verify from a browser: https://host.zerotier.my.id:3903 -> login page
sudo uaeg status && sudo uaeg doctor
```

(Equivalent one-shot alternative: `sudo bash install.sh --dashboard-bind
public` from the release tree — same prerequisites, config preserved.)

The v0.3.3 upgrade itself (recommended BEFORE or right after these steps) is
one command with the new permanent bootstrap — see
PRODUCTION_DEPLOYMENT.md §"Operator runbook".

## 7. Repository-side hardening included in v0.3.3 (defense in depth)

The code could not have prevented a deployment-configuration gap, but v0.3.3
removes every avoidable aggravator found in the audit:

- `uaeg doctor`/`status` and the dashboard Server tab make the bind address
  and auth mode plainly visible for triage.
- Dashboard fixes so a recovering service presents usable error states
  instead of ambiguity: per-endpoint connection truth (F-1), CSRF
  self-healing (F-2), double-submit protection that previously could destroy
  a one-time key secret (F-3), legacy-mode `whoami` 500 fixed (F-5),
  `/api/sessions` race 500 fixed (F-6), favicon route (console clean).
- INSTALL.md/README now document the public-dashboard flow and the
  one-time bootstrap download explicitly (the v0.3.2 docs' "do NOT open
  3903" guidance is scoped to private binds, with the public flow one
  section away).

## 8. Follow-up verification (operator)

After the runbook: `https://host.zerotier.my.id:3903` must render the login
page; correct password → dashboard; wrong password → "Login failed.";
logout → login page. Evidence template: the lab browser-verification output
in `report/evidence/lab_browser_verification_v033.txt` is the exact expected
behavior matrix.
