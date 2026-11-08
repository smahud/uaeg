# Permanent Upgrade Design — v0.3.3

**Requirement (mission Phase D):** the operator downloads `upgrade.sh` ONCE;
all subsequent upgrades run the same local file (`bash upgrade.sh`), forever
— including releases that change the upgrade implementation itself. No
unverified remote script may be executed. The default release source is the
release channel, never a mutable branch.

**Status:** IMPLEMENTED, TESTED (155/155 contract assertions incl. the
multi-release compatibility test), DOCUMENTED. Full living documentation:
[docs/BOOTSTRAP.md](../docs/BOOTSTRAP.md) (architecture + binding interface
v1 + key rotation policy) and [docs/UPGRADE_CONTRACT.md](../docs/UPGRADE_CONTRACT.md)
(clauses U-13/U-14). This file records the engineering decisions.

---

## 1. Chosen architecture

```
stable local bootstrap  →  verified versioned updater  →  safe application upgrade
   (upgrade.sh)                  (uaeg-updater-<v>.sh)         (U-1..U-12 machinery)
```

- **Bootstrap (`upgrade.sh`, ~18 KB):** operator-owned, downloaded once.
  Resolves the target release (GitHub Releases "latest", or `--version`),
  downloads `uaeg-manifest.json` + `.sig`, verifies the **Ed25519 signature
  against the embedded public key**, validates the manifest structurally
  (safe updater filename, version match, supported interface), downloads the
  updater, verifies **SHA-256 + size against the signed manifest**, executes
  it with the documented env interface and forwarded flags, propagates the
  exit code, cleans its temp dir, and re-asserts that its own file was not
  modified.
- **Updater (`uaeg-updater-<v>.sh`, from the repo's `updater.sh`):** carries
  the full safe-upgrade machinery (detect → backup → isolated staging clone →
  install → verify zero data loss → auto-rollback). Free to change every
  release. On success it caches a verified copy of itself + manifest
  (`/var/lib/uaeg/upgrade/`, 0700, atomic) for offline `--rollback`/`--check`,
  and best-effort refreshes the system convenience copy
  `/usr/local/sbin/uaeg-upgrade`. It must never touch the operator's
  bootstrap file.

## 2. Why Ed25519-over-manifest (trust mechanism trade study)

| Option | Verdict |
|---|---|
| GPG/cosign signatures | New runtime dependency (gpgv/cosign) on the VPS or CI; heavier key management; rejected (dependency footprint rule). |
| Checksum sidecars only | Explicitly insufficient: a checksum fetched next to the script proves transport integrity, NOT publisher identity (mission requirement). |
| **Ed25519 via OpenSSL** (`pkeyutl -rawin`) | **Chosen:** zero new runtime dependencies (curl, openssl, sha256sum, python3 are already required by install.sh); 32-byte keys; verify-only code in the bootstrap is ~10 lines; CI can verify with the committed public key; rotation is a keyring edit. |
| Signed git tags | Requires git on the operator box at upgrade time and trust anchored to the git transport rather than the release channel; the updater fetch path already covers git needs. Not used for bootstrap trust. |

Trust anchor: the public key is embedded in the bootstrap the operator
downloads over TLS from github.com (publisher = the GitHub account + release
channel), and re-anchored by every subsequent signature check. The private
key never enters the repository; releases are signed at preparation time
(`release/make_release.py --key <private key>`) and CI re-verifies with the
committed public key BEFORE any release is published
(`release/verify_release.py` in `.github/workflows/release.yml`) — a
compromised or mistaken commit cannot publish unverified assets.

## 3. Interface contract (v1) — summarized

The bootstrap executes the verified updater with:

```
UAEG_BOOTSTRAP=1 UAEG_BOOTSTRAP_FILE=<operator bootstrap path>
UAEG_TARGET_VERSION=X.Y.Z UAEG_RELEASE_TAG=vX.Y.Z
UAEG_MANIFEST=<verified manifest path> UAEG_UPDATER=<this updater>
bash <updater> [forwarded v0.3.2-compatible flags]
```

Binding rules: target comes from the verified env (no network re-resolution);
exit codes propagate verbatim (0/1/2/3/5); the operator's bootstrap file is
never modified (self-checked after every run); success SHOULD self-cache for
offline rollback; interface versions gate compatibility (bootstrap refuses
newer-interface updaters with explicit refresh instructions). Full text:
docs/BOOTSTRAP.md.

## 4. Migration from v0.3.2 installs

One-time transition (no Git working-tree contact, no deletion of untracked
files, no config/secrets changes): download the new bootstrap anywhere
(`curl .../releases/latest/download/upgrade.sh` + checksum verify), run
`sudo bash upgrade.sh`. The bootstrap fetches the v0.3.3 updater via the
signed manifest and the updater performs the upgrade under the existing
contract (staging clone; operator trees untouched). From then on the same
file is reused indefinitely. The installer additionally installs
`/usr/local/sbin/uaeg-upgrade` on fresh installs/upgrades. The old v0.3.2
monolithic `upgrade.sh` in an operator clone is simply an untracked file —
it neither blocks nor is needed by the new flow (regression-tested by
T-17/T-23).

## 5. Failure & edge-case handling (all test-enforced)

| Situation | Behavior |
|---|---|
| Manifest missing / network down | Clear error; nothing executed; exit 1. |
| Manifest tampered (any byte) | Ed25519 verification fails → refuse; exit 1. |
| Manifest signed by an untrusted key | Refused with explicit "not signed by a trusted UAEG release key"; exit 1. |
| Updater bytes tampered | SHA-256/size mismatch vs signed manifest → refuse; exit 1. |
| Updater interface newer than bootstrap | Refused + one-time refresh instructions (documented exceptional procedure). |
| Release tree broken (updater installs, app fails verification) | Auto-rollback; exit 2; retry with the same bootstrap works. |
| Offline `--rollback` | Uses the cached verified updater; no network needed. |
| Silent downgrade | Refused by the updater (exit 5) unless `--allow-downgrade`. |
| `--check` / `--dry-run` | Read-only (no backup, no mutation) — verified. |
| Operator clone dirty / untracked upgrade.sh / stale tags | Never read or mutated (v0.3.2 contract, retained). |

## 6. Key rotation policy (exceptional by design)

Dual-signing transition window → new-key-only releases → affected bootstraps
fail loudly with refresh instructions → operator performs a ONE-TIME
`curl` refresh (the only routine event, together with an interface bump, that
requires re-downloading the bootstrap; documented in BOOTSTRAP.md and
TROUBLESHOOTING.md). Current key: `uaeg-release-1` (Ed25519; fingerprint via
`bash upgrade.sh --bootstrap-info`).

## 7. Implementation artifacts

| File | Role |
|---|---|
| `upgrade.sh` | The permanent bootstrap (release asset `upgrade.sh`). |
| `updater.sh` | The versioned updater (release asset `uaeg-updater-<v>.sh`). |
| `release/keys/uaeg-release-1.pub.pem` | Committed verification key. |
| `release/make_release.py` | Maintainer tool: builds + signs manifests + sidecars. |
| `release/verify_release.py` | Independent verifier (CI + operators). |
| `release/manifests/v0.3.3/…` | The signed v0.3.3 manifest + signature + sidecars. |
| `.github/workflows/release.yml` | Verifies then publishes the 8-asset release. |
| `tests/test_upgrade_contract.sh` T-20..T-31 | Enforcement (see UPGRADE_COMPATIBILITY_TESTS.md). |
