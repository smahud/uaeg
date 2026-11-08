# The Permanent Upgrade Bootstrap (contract v3)

How UAEG's "download once, upgrade forever" mechanism works, the binding
bootstrap↔updater interface, and the release-signing trust model with its key
rotation policy. The behavioral contract itself is
[UPGRADE_CONTRACT.md](./UPGRADE_CONTRACT.md) (clauses U-13, U-14).

## Why

Before v0.3.3, `upgrade.sh` was a ~50 KB monolithic upgrader that had to be
re-downloaded for **every** release (the documented flow literally curled it
from `releases/latest` each time). That model:

- makes operators download code they cannot verify beyond a checksum that is
  fetched from the same place as the script (no publisher identity), and
- couples "how I invoke an upgrade" to "how the upgrade is implemented", so
  every updater improvement forces a new download.

v0.3.3 splits those concerns permanently.

## Architecture

```
Operator (downloads ONCE)          GitHub Releases (per version)          VPS
─────────────────────────         ────────────────────────────────         ───
upgrade.sh  = BOOTSTRAP           uaeg-manifest.json          ┐
 (~18 KB, stable,                 uaeg-manifest.json.sig      ┘ signed
  embeds trusted Ed25519          uaeg-updater-<version>.sh     the updater
  public key(s))                  install.sh / upgrade.sh       (assets)
        │                                 │
        │  1. resolve target release      │
        │     (latest Release, or         │
        │      --version X.Y.Z)           │
        │  2. download manifest + sig ────┘
        │  3. VERIFY Ed25519 signature against the embedded key
        │  4. download updater; VERIFY sha256 + size vs the SIGNED manifest
        │  5. exec the verified updater (interface below), forward flags
        │  6. propagate exit code; clean temp; assert own file is unchanged
        ▼
updater = the full safe-upgrade machinery (U-1..U-12):
  detect → backup → isolated staging clone → install → verify
  → auto-rollback; then cache itself for offline --rollback and
  refresh /usr/local/sbin/uaeg-upgrade (best effort)
```

Roles:

- **Bootstrap (`upgrade.sh`)** — small, stable, operator-owned. Decides *what*
  to install (release channel, never `main` by default), proves *who*
  published it and *that the bytes are intact*, then delegates. It must not
  grow per-release logic; expected to stay byte-stable for years.
- **Updater (`uaeg-updater-<v>.sh`, built from the repo's `updater.sh`)** —
  release-coupled. Contains all the safe-upgrade machinery and may change
  freely from release to release.

## Bootstrap ↔ updater interface (v1) — BINDING

The bootstrap executes:

```bash
UAEG_BOOTSTRAP=1 \
UAEG_BOOTSTRAP_FILE=<path to the operator's bootstrap> \
UAEG_TARGET_VERSION=<X.Y.Z> \
UAEG_RELEASE_TAG=<vX.Y.Z> \
UAEG_MANIFEST=<path to the verified manifest> \
UAEG_UPDATER=<path to the updater being executed> \
bash <updater> [forwarded operator args]
```

Rules (a future updater MUST honor these to stay compatible):

1. The target release comes from `UAEG_RELEASE_TAG` /
   `UAEG_TARGET_VERSION` (already signature-verified by the bootstrap) —
   the updater MUST NOT re-resolve it from the network when
   `UAEG_BOOTSTRAP=1`.
2. Forwarded flags keep their v0.3.2 meanings (`--check`, `--dry-run`,
   `--rollback`, `--repo`, `--installer`, `--yes`, `--allow-downgrade`, …).
   `--source DIR` and `--main` are honored as in v0.3.2.
3. Exit codes are propagated verbatim: 0 ok · 1 error · 2 rolled back ·
   3 no installation · 5 downgrade refused.
4. The updater MUST NOT modify the operator's bootstrap file
   (`UAEG_BOOTSTRAP_FILE`); the bootstrap re-checks its own SHA-256 after
   every run and warns loudly on any change.
5. On success the updater SHOULD cache a copy of itself + the manifest under
   the upgrade cache (default `/var/lib/uaeg/upgrade/`, overridable with
   `UAEG_UPGRADE_CACHE_DIR`) so `--rollback`/`--check` work offline. The
   cache is written atomically with 0700 permissions.
6. The updater MAY refresh the *system* bootstrap convenience copy at
   `/usr/local/sbin/uaeg-upgrade` from the release tree. It MUST NOT touch
   anything under the operator's own directories.
7. Interface versioning: the manifest declares `updater.interface`. A
   bootstrap refuses to run an updater whose interface is NEWER than it
   supports (with explicit refresh instructions); older interfaces remain
   supported.

Breaking changes to this interface require bumping the interface version and
are, by design, one of the only two events that force operators to refresh
the bootstrap (see below).

## Release signing (trust model)

- Each release ships `uaeg-manifest.json` — the asset inventory: updater
  filename, SHA-256, size, interface version, plus hashes for `install.sh`
  and `upgrade.sh` — and `uaeg-manifest.json.sig`, an **Ed25519 signature
  over the exact manifest bytes** (OpenSSL `pkeyutl -rawin`; no new runtime
  dependencies — curl, openssl, sha256sum, python3 are already required).
- The bootstrap embeds the trusted public key(s) (keyring between marked
  lines in `upgrade.sh`; committed in `release/keys/`). A manifest signed by
  any other key is rejected.
- Integrity of the updater is checked against the **signed** manifest, so a
  checksum alone is never treated as proof of publisher identity (U-14).
- Release publishing: `release/make_release.py --key <private key>` builds +
  signs the manifest (the private key NEVER enters the repository);
  `.github/workflows/release.yml` re-verifies the signature and every asset
  hash with `release/verify_release.py` BEFORE creating the GitHub Release.
  Anyone can audit a checkout with `python3 release/verify_release.py`.

## Key rotation policy

Rotation is an **exceptional, operator-visible event**, never silent:

1. New releases may be dual-signed during a transition window (the keyring
   accepts the old and the new key).
2. After the window, releases are signed with the new key only. Bootstraps
   that only carry the old key will refuse those releases with an explicit
   "signature invalid / key rotation" message.
3. Recovery is a ONE-TIME bootstrap refresh (the only routine situation that
   requires it, together with an interface bump):

   ```bash
   curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
   curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
   sha256sum -c upgrade.sh.sha256   # verify over TLS from github.com
   ```

   This is documented in the bootstrap's own failure messages and in
   TROUBLESHOOTING.md; it is NOT part of normal upgrade flow.

Current keys: `uaeg-release-1` (historical: releases <= v0.3.3) and
`uaeg-release-2` (v0.3.4+). Fingerprints (sha256 of the PEM, first 16 hex):
see `bash upgrade.sh --bootstrap-info`.

**Rotation 1 -> 2 at v0.3.4** (bootstrap keyring v2): the publisher's
`uaeg-release-1` private key is no longer available, so v0.3.4 and later
manifests are signed with `uaeg-release-2`. The v0.3.4 bootstrap trusts both
keys (historical releases keep verifying for pins/downgrades). Operators
holding a bootstrap downloaded during v0.3.3 will see the explicit
"signature invalid / key rotation" refusal when targeting v0.3.4+ and must
perform the ONE-TIME refresh above. Operators who download the bootstrap
fresh (the normal path for the MicroTix recovery, whose v0.3.1-era upgrade
never completed) get the v0.3.4 keyring directly and need no refresh.

## Migration from v0.3.2 installs

One-time transition (documented in INSTALL.md and the release notes):

```bash
cd ~  # anywhere; the file no longer needs to live inside a clone
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
sha256sum -c upgrade.sh.sha256
sudo bash upgrade.sh        # upgrades to v0.3.3 via the signed manifest
```

From then on: `sudo bash upgrade.sh`, forever. The old v0.3.2 monolithic
`upgrade.sh` (if present in `~/uaeg`) keeps working for one last run only if
it is the v0.3.2 asset — operators should simply replace it with the
bootstrap. Nothing on the operator's machine is modified by the bootstrap
itself; the updater follows the v0.2 contract (staging clone; operator git
trees never touched). `install.sh` additionally installs the convenience copy
`/usr/local/sbin/uaeg-upgrade` on fresh installs and upgrades.

## Test coverage

`tests/test_upgrade_contract.sh` (T-20..T-31) runs the full chain against a
local release server with a test-only keyring substitution:

| Test | Proves |
|------|--------|
| T-20 | bootstrap info + keyring fingerprint |
| T-21 | signature-verified upgrade #1 (release A) |
| T-22 | operator bootstrap SHA unchanged; updater cached; system copy installed |
| T-23 | **multi-release compatibility**: same bootstrap upgrades release B whose updater implementation CHANGED; bootstrap SHA still unchanged; operator clone untouched |
| T-24 | tampered manifest → rejected |
| T-25 | tampered updater bytes → rejected |
| T-26 | manifest signed by a foreign key → rejected |
| T-27 | updater interface too new → refused with refresh instructions |
| T-28 | missing manifest → clear error, nothing executed |
| T-29 | `--check` / `--dry-run` through the release channel |
| T-30 | broken release → auto-rollback via bootstrap; retry with the same bootstrap succeeds |
| T-31 | offline `--rollback` from the cached updater |

The signature/verification chain in those tests is real (OpenSSL Ed25519);
only the release channel and the key are simulated locally.
