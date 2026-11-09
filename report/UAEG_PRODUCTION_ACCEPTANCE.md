# Production Acceptance — Installer-Contract Audit (2026-11-09)

**Acceptance rule applied:** a criterion is VERIFIED only when proven
against the production system; anything proven only in the lab/sandbox or
not proven at all is marked accordingly. **This audit environment has no
VPS credentials** — production observations are external, read-only probes
(2026-11-09 ~04:20 UTC).

---

## 1. Acceptance matrix (mission §11)

| # | Check | Expected | Status | Evidence |
|---|---|---|---|---|
| 1 | UAEG systemd active | Active | **NOT VERIFIED** (needs on-box `systemctl status uaeg`) — indirectly: MCP healthy with 34-min uptime implies a running, supervised service | runbook Step 2a-0 |
| 2 | MCP port | 3902 listening | **VERIFIED (external)** — `https://host.zerotier.my.id:3902/healthz` → HTTP 200 | probe output §2 |
| 3 | Dashboard port | 3903 on 0.0.0.0 | **NOT MET (yet)** — TCP 3903 refused (fast RST) | probe output §2 |
| 4 | MCP health | Healthy | **VERIFIED** — `{"status":"ok","service":"UAEG","version":"0.4.1"}` | probe output §2 |
| 5 | MCP without key | Auth required | **VERIFIED** — `POST /mcp` without bearer → 401 | probe output §2 |
| 6 | Dashboard TLS | Valid and trusted | **NOT VERIFIABLE YET** (endpoint down). The certificate material that will serve it is verified: LE chain on 3902, `Verify return code: 0 (ok)`, SAN `host.zerotier.my.id`, valid to 2027-02-05 | probe output §2 |
| 7 | Dashboard without session | Login required | **VERIFIED IN LAB** (production pending Step 2): `/api/overview` → 401, browser → login page; locked by `test_e2e_installer_repair.py`, `test_e2e_dashboard_auth.py` | UAEG_TEST_RESULTS §3 |
| 8 | Correct password | Dashboard accessible | **VERIFIED IN LAB** incl. real-browser flow (screenshots in `report/evidence/browser_audit_20261109/`) | UAEG_TEST_RESULTS §3.2 |
| 9 | Wrong password | Rejected | **VERIFIED IN LAB** (HTTP 401 "Login failed."; browser shows the same) | UAEG_TEST_RESULTS §3/§3.2 |
| 10 | Protected API without session | Denied | **VERIFIED IN LAB** (401) | UAEG_TEST_RESULTS §3 |
| 11 | Password rotation | Old sessions revoked | **VERIFIED IN LAB** (generation bump kills sessions; server-side store) | `test_unit_regression_configset_pass.py` |
| 12 | Existing MCP keys | Preserved and functional | **VERIFIED IN LAB** across a full repair (byte-identical rows; pre-repair secret still authenticates on 3902). Production keys untouched by the incident (parse-time death, no mutation) | `test_e2e_installer_repair.py` |
| 13 | Database | Preserved | **VERIFIED IN LAB** (byte-level: keys/password identical, `quick_check` ok across repair) | `test_e2e_installer_repair.py` |
| 14 | Repair | Successful without data loss | **VERIFIED IN LAB** with the corrected command; **production execution pending (operator)** | UAEG_TEST_RESULTS §3.1 |
| 15 | Rollback | Verified | **VERIFIED IN LAB** (contract suite T-48 + installer trap: the pre-fix D10 run exercised a real rollback and restored state). Production backup rollback not drilled (no VPS access) | contract suite 290 PASS |
| 16 | External browser | Login + dashboard operational | **NOT MET (yet)** — 3903 refused until the operator completes Step 2/3 | §2 |

**Production acceptance: NOT VERIFIED** (dashboard plane). MCP plane:
verified operational. This matches the mission rule: no success declaration
while the dashboard remains unreachable.

## 2. External probe evidence (read-only, full TLS verification, no `-k`)

```
DNS:      host.zerotier.my.id -> 125.163.142.132
MCP:      curl https://host.zerotier.my.id:3902/healthz -> HTTP 200
          {"status":"ok","service":"UAEG","version":"0.4.1","uptime_seconds":2067.5}
MCP auth: POST /mcp (no bearer) -> HTTP 401
TLS:      subject=CN=host.zerotier.my.id
          issuer=C=US, O=Let's Encrypt, CN=YE2
          notBefore=Nov  7 17:20:22 2026 GMT
          notAfter=Feb  5 17:20:21 2027 GMT
          SAN=DNS:host.zerotier.my.id
          openssl s_client -verify_return_error -> "Verify return code: 0 (ok)"
DASH:     https://host.zerotier.my.id:3903/login -> connection refused (RST)
```

## 3. Exact operator actions still required (on the VPS, as root)

Follow `report/PRODUCTION_RECOVERY_V041.md` (revision 2). Condensed:

1. **Preflight** (runbook Step 2a-0) — read-only; abort on any failure.
2. **Dashboard password** — only if not already set (it is preserved; do
   NOT reset a known password): `sudo uaeg pass`.
3. **Expose the dashboard** — either:
   - **guaranteed on the installed v0.4.1 (no installer involved):**
     ```bash
     sudo uaeg config set dashboard.allow_public true
     sudo uaeg config set dashboard.tls true
     sudo uaeg config set dashboard.bind 0.0.0.0      # LAST (order matters)
     sudo systemctl restart uaeg
     ```
   - or the installer path from a **current `main` checkout** (D10 fixed
     there; the released v0.4.1 installer file still carries D10):
     ```bash
     sudo bash install.sh --repair \
       --dashboard-bind public \
       --cert /etc/letsencrypt/live/host.zerotier.my.id/fullchain.pem \
       --key  /etc/letsencrypt/live/host.zerotier.my.id/privkey.pem
     ```
     (`/root/uaeg` staleness is auto-refused with exit 5 if older — that
     refusal is the guard working; use a fresh clone.)
4. **Firewall** — `ufw status | grep -q 3903/tcp || ufw allow 3903/tcp`
   (runbook Step 3).
5. **Verify** (runbook Step 4, full TLS verification, no `-k`):
   `curl -fsS https://host.zerotier.my.id:3903/login` → 200;
   `curl -s https://host.zerotier.my.id:3903/api/overview` → 401;
   then the browser flow: login page → password → dashboard → logout.
   On-box: `sudo uaeg status && sudo uaeg doctor`.
6. **Rollback** (only if anything looks wrong):
   `sudo bash install.sh --rollback` (or restore the config backup the
   installer printed).

## 4. Remaining risks

| Risk | Assessment |
|---|---|
| Released v0.4.1 installer still carries D10 (public-bind) | Mitigated: runbook's D10 caveat + guaranteed manual path; permanent fix on `main`; next release cuts it out |
| Production config's `allow_public` state unknown from here | Irrelevant for the manual path (it sets it explicitly, order-correct) |
| Provider-level firewall (beyond ufw) may also block 3903 | Runbook Step 3 note covers non-ufw firewalls; fast RST suggests a local reject rule or no listener — after Step 2/3, re-probe externally |
| No v0.4.2 signed release yet | Signing key is maintainer-held; operator can run from `main` checkout (version-equal preflight accepts it) or the manual path |
| Session idle timeout 30 min / absolute 12 h | Existing designed behavior, unchanged |

## 5. Final success condition (mission §17)

> The operator can open `https://host.zerotier.my.id:3903`, receive the
> HTTPS login page, authenticate with the existing dashboard password,
> reach the functional dashboard, and continue using existing MCP agent
> keys on port 3902 — without loss of persistent data.

**Status: NOT YET MET — one operator action away.** Everything upstream of
that action is verified: the corrected procedure is safe (preflight +
rollback), the exact command shape is regression-tested end-to-end with
zero data loss, the TLS material is valid and trusted, and the MCP plane
(3902) is already healthy on v0.4.1. Dashboard 3903 remains refused until
the operator executes §3 steps 2–5.

## 6. Delivery record

- Baseline commit: `30d047d04729a3d703f00c8bcd3fc288ff7bb4f8`
- Patch-set commit: **see `git log` on `main` (recorded in the delivery
  summary; a SHA cannot be embedded in the commit it identifies)**
- CI: status checked after the push and recorded in the delivery summary.
