# Dashboard Production Check — host.zerotier.my.id (v0.4.0 mission)

**Date:** 2026-11-08 · **Severity:** P0 · **Status: ROOT CAUSE CONFIRMED
WITH FRESH EVIDENCE; production fix BLOCKED on VPS access** (no SSH/operator
credentials exist in the audit environment — reported truthfully, not
silently claimed).

---

## 1. Authoritative hostname (resolved)

- **`host.zerotier.my.id` → 125.163.142.132** — live A record; the running
  service and its Let's Encrypt certificate use exactly this name.
- `host.zeotier.my.id` — **no DNS record exists** (NXDOMAIN). The "zeotier"
  spelling seen in an earlier message is a typo and is NOT authoritative.
- All URLs in this report and in the runbook use `host.zerotier.my.id`.

## 2. Current production state (fresh external evidence, 2026-11-08T16:00Z)

Full capture: `report/evidence/production_probe_v040.txt`.

| Check | Result | Meaning |
|---|---|---|
| DNS `host.zerotier.my.id` | 125.163.142.132 | host reachable, not a DNS problem |
| TCP 3903 (dashboard) | **REFUSED** (RST, ~80 ms) | nothing is listening on the public interface — not a timeout, not a filter drop |
| TCP 3902 (MCP) | OPEN | the service process is alive and publicly bound |
| `GET https://…:3902/` | `{"service":"UAEG","version":"0.3.3",…}` | **production runs v0.3.3 — two releases behind v0.3.5** |
| `GET /healthz` (3902) | `{"status":"ok",…,"uptime_seconds":19156}` | healthy, no crash loop (restarted ≈ 10:40 UTC today) |
| `GET /mcp` without key | 401 | authentication correctly fail-closed |
| TLS cert (3902) | Let's Encrypt, CN/SAN `host.zerotier.my.id`, valid 2026-11-07 → 2027-02-05 | browser-trusted; the reported "ports opened" matches the MCP side |
| TLS 3903 | connection refused | no TLS layer is ever reached on 3903 |

**Interpretation.** Since UAEG serves both listeners from one supervised
process (fail-together design, regression-tested), a live healthy MCP
listener means the dashboard listener exists but is bound to a non-public
address — the previously established root cause **RC-A: `dashboard.bind =
127.0.0.1` (the installer default)** — and/or **RC-B: a firewall REJECT**.
The operator upgraded v0.3.2 → v0.3.3 (new since the last audit) but the
public-dashboard steps were still never executed. An open port at any
firewall layer cannot fix a loopback bind.

## 3. Why this cannot be closed from here

Fixing it requires editing `/etc/uaeg/config.json` (or running the
installer with `--dashboard-bind public`), restarting the service, and
adjusting the host firewall — all root operations **on the VPS**. This
environment has no SSH credentials for 125.163.142.132 (only a GitHub PAT
was provided). Per the mission's own rule, this requirement is marked
**BLOCKED** with the exact diagnostics and runbook below. Laboratory
evidence (report/evidence/lab_browser_verification_v040.txt +
browser_v040/ screenshots) proves the v0.4.0 dashboard UI/login/logout and
MCP endpoint behave correctly **when reachable** — it is explicitly NOT
claimed as a production fix.

## 4. Operator runbook (one file, ~5 minutes, no password reset)

Run on the VPS as root. Nothing below resets the dashboard password or
touches agent keys; HTTPS and password authentication are preserved.

```bash
# 0) One-time: fetch the unified installer (verified) — this replaces the
#    old upgrade.sh flow AND performs the v0.3.3 -> v0.4.0 safe upgrade:
cd ~
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh.sha256
sha256sum -c install.sh.sha256        # must print: install.sh: OK

# 1) Safe upgrade to v0.4.0 (auto-detect, backup, verify, auto-rollback):
sudo bash ~/install.sh
#    (keep this file — every future upgrade is: sudo bash ~/install.sh)

# 2) Triage the dashboard (expected today: bind 127.0.0.1):
sudo bash ~/install.sh --check       # shows: version, dashboard bind/port/tls, "dashboard pw"
sudo uaeg doctor

# 3) Dashboard password — ONLY if step 2 shows "dashboard pw : False":
sudo uaeg pass                       # interactive, hidden input
#    (if it already shows True, SKIP this — the existing password is kept)

# 4) Enable the public dashboard (fail-closed: password + TLS + opt-in):
sudo uaeg config set dashboard.bind 0.0.0.0
sudo uaeg config set dashboard.allow_public true
sudo uaeg config set dashboard.tls true        # reuses the existing LE certificate

# 5) Apply + open the firewall:
sudo bash ~/install.sh --restart
sudo ufw allow 3903/tcp                        # or the equivalent for your firewall

# 6) Verify from the VPS:
sudo bash ~/install.sh --status
sudo uaeg doctor
```

Equivalent one-shot alternative for step 4 (same prerequisites, config
otherwise preserved): `sudo bash ~/install.sh --repair --dashboard-bind
public`.

## 5. Required diagnostics (if the operator prefers to report back first)

If anything above fails or the acceptance checks (§6) do not pass, collect
and return:

```bash
sudo uaeg version; sudo uaeg status; sudo uaeg doctor
sudo cat /etc/uaeg/config.json                       # redact nothing — it holds no secrets
ss -ltnp | grep -E '3902|3903'                       # listener bind addresses + pids
sudo systemctl status uaeg --no-pager -l
sudo journalctl -u uaeg -n 50 --no-pager
sudo ufw status verbose        # or: sudo iptables -L -n | head -40
curl -sk https://127.0.0.1:3903/ -o /dev/null -w '%{http_code}\n'   # from the VPS itself
```

The single most diagnostic line is `ss -ltnp | grep 3903`: it shows whether
the dashboard is bound to `127.0.0.1:3903` (RC-A confirmed) or
`0.0.0.0:3903` (then RC-B firewall, since external SYNs are refused while
the listener is public).

## 6. Acceptance criteria (to be verified with real evidence)

| # | Criterion | How to verify |
|---|---|---|
| 1 | `https://host.zerotier.my.id:3903` loads the login page from the public internet | browser, or `curl -sI https://host.zerotier.my.id:3903/` → 200/302 |
| 2 | The EXISTING password authenticates | browser login (password is preserved by every upgrade path — contract U-4) |
| 3 | Dashboard renders and all tabs work | KEYS / PERMISSIONS / SESSIONS / LOGS / SERVER (lab reference: browser_v040 screenshots) |
| 4 | Logout returns to the login page | browser |
| 5 | MCP port 3902 still operational | `curl -s https://host.zerotier.my.id:3902/healthz` → ok |

This audit environment will re-verify items 1 and 5 externally the moment
the operator reports completion (they require no credentials — just say
"done").

## 7. History and repo-side hardening already in place

- Root cause and original evidence chain: `report/DASHBOARD_ROOT_CAUSE.md`
  (v0.3.3 audit; RC-A/RC-B as above — unchanged conclusion).
- v0.3.3+ `uaeg doctor`/`status` and the dashboard SERVER tab expose the
  bind address and auth mode for exactly this triage.
- v0.4.0 adds `install.sh --check` / `--status` so the operator can see the
  bind without reading JSON. The code-side dashboard behavior is fully
  verified in a real browser (see §3).
