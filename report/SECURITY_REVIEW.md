# Security Review — v0.3.3

**Date:** 2026-11-08 · **Scope:** full v0.3.2 audit + v0.3.3 patch review ·
**Verdict:** no unresolved release-blocking security defects; two
security-contract degradations (B-2, B-3) and one P1 reliability defect with
security impact (B-1) FIXED; the product's deliberate full-root execution
model remains as documented and is restated below without softening.

---

## 1. The deliberate model (unchanged, restated honestly)

UAEG gives an authenticated agent **full, unrestricted root** on the host
(PRD FR-010/FR-013). There is no sandbox and no confirmation step. Advisory
"restricted paths" are agent guidance, **not a security boundary** — any
root shell bypasses them trivially (FR-014). This is accepted risk
(documented in README/SECURITY/LIMITATIONS) for a dedicated,
operator-controlled VPS. The security story is therefore about **the gates
in front of root**: keys, authentication, revocation, exposure, TLS, and —
new in v0.3.3 — **upgrade-chain authenticity**.

## 2. Gates in front of root — verified state

| Gate | State (v0.3.3) |
|---|---|
| Agent keys | 256-bit secrets, shown once, stored only as SHA-256 verifiers; prefix-only identification in UI/CLI. Verified live in production (401 without key). |
| MCP bearer auth | Hash-then-lookup per request, BEFORE any MCP session handling; revocation immediate (existing session id → 401 after revoke — e2e-tested). |
| Dashboard login | scrypt (N=32768) password hashing, generation-tokened server-side sessions (password change revokes all sessions without restart), HttpOnly+SameSite cookies, `Secure` on HTTPS, per-IP sliding-window lockout (5 fails/15 min → 15 min lockout), CSRF token on every state change + Origin validation, capped request bodies. |
| Public exposure | Fail-closed: wildcard dashboard bind requires explicit opt-in + TLS + existing password at config load AND serve time; installer and upgrader enforce the same guard (T-GUARD). |
| TLS | Mandatory on the MCP listener (only dev-flag bypass, loud warning); production verified on a valid Let's Encrypt cert (SAN match, exp 2027-02). |
| Audit trail | Command text + previews secret-redacted at the single `add_log` choke point; env values never logged; secrets never appear in upgrade logs/records (T-11). |
| SQLite | WAL + busy_timeout; parameterized SQL everywhere; schema-version downgrade guard; atomic config writes. |
| **Upgrade chain (new)** | Ed25519-signed manifests; bootstrap refuses untrusted/unsigned/tampered updaters before execution; CI re-verifies before publishing; release assets hashed in the signed manifest. |

## 3. v0.3.3 fixes with direct security impact

- **B-1 (P1):** service-wide freeze via one `shell_input` into a suspended
  session (blocking write on the event loop). Fixed with a non-blocking
  master + bounded backlog; regression-tested. Security relevance: a single
  agent tool call could take down the operator's entire control plane
  (dashboard included) with no in-band recovery.
- **B-2:** revocation containment blindness beyond the newest 1000 session
  rows — a revoked (possibly compromised) agent's old open root PTY survived
  CLI revocation. Fixed (`open_sessions()`), regression-tested with 1100
  noise rows.
- **B-3:** `job_cancel` reported `cancelled` while TERM-ignoring processes
  kept running — containment and the audit state lied. Fixed with
  group-liveness escalation; regression-tested with `trap '' TERM`.
- **B-4:** pid recycling after reboots could aim containment signals at an
  **unrelated root process group**. Fixed with boot-id stamping.
- **B-9:** audit-log redaction now covers `mysql -ppassword123`-style short
  flags (best-effort contract, documented limits unchanged).
- **B-12:** rotation crash-window no longer leaves zero valid keys.
- **B-11:** bounded audit-log growth (startup retention 20k rows) — plus
  the dangling-write-lock fix found by the new tests themselves.
- Dashboard hardening (F-1..F-10, favicon): error-state truthfulness during
  incidents, CSRF self-healing, double-submit protection for one-time
  secrets, no-stale-cache after logout.

## 4. Threat-model notes on the new upgrade chain

- **Compromised GitHub account/release channel:** signatures bind to the
  committed Ed25519 key; a release published without a valid signature by
  `uaeg-release-1` is refused by every deployed bootstrap. (If the account
  itself is compromised, the attacker could also replace the bootstrap for
  NEW downloads — same trust level as any GitHub-distributed software; the
  embedded key at least protects EXISTING bootstraps.)
- **Private signing key compromise:** rotate per BOOTSTRAP.md (dual-sign
  window → new key → one-time operator refresh, loudly). The key lives only
  with the maintainer; it is never in the repo, CI variables, or logs.
- **MITM on the VPS:** TLS to GitHub + signature verification; channel
  overrides (`UAEG_RELEASE_API_BASE`) exist for mirrors/tests and never
  bypass verification — an attacker able to set a root user's environment
  already owns the machine (env control ≡ shell control).
- **Rollback path offline:** the cached updater is a root-owned 0700 file
  written only from verified bytes; tampering with it requires root, at
  which point UAEG is not the boundary.

## 5. Remaining accepted risks (explicit, none release-blocking)

1. **Root-by-design execution** (FR-010/FR-013) — the product itself.
2. **Background-job output files uncapped** (documented LIMITATIONS.md #13;
   sync exec is capped).
3. **Best-effort redaction** — pattern-based; documented.
4. **scrypt cost on very weak hardware** — ~80 ms/attempt by design;
   brute-force additionally blunted by per-IP lockout.
5. **Single-host single-operator** — no multi-tenant isolation claims.
6. **Production gap (operator action):** the MicroTix VPS still runs v0.3.2
   with the dashboard not publicly reachable (loopback/firewall) until the
   runbook in PRODUCTION_DEPLOYMENT.md is executed. v0.3.2 on that host
   carries the B-1 freeze defect; upgrading is recommended promptly.

## 6. Production exposure observation (live, 2026-11-08)

MCP is publicly reachable and correctly returns 401 before protocol
handling; the dashboard is not publicly reachable (see
DASHBOARD_ROOT_CAUSE.md). From an exposure standpoint this is the SAFE
direction (private-by-default), but it is not the operator's intended
state; the runbook enables it with the fail-closed prerequisites.
