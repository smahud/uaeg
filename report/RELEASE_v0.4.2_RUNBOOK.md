# Release runbook — v0.4.2 (live audit 2026-11-10 patch set, F1–F8)

| | |
|---|---|
| **Status** | PREPARED by the 2026-11-10 live-audit agent. Everything that does **not** require the private signing key is done. The remaining steps are the operator's, in order. |
| **Base** | `main` @ `705c792` (merge of PR #1 — audit patch set F1–F8, CI 5/5 green on PR head `121a7ca`) |
| **Signing key** | `uaeg-release-4` (still valid — rotation **not** required; see `report/UAEG_LIVE_RELEASE_DECISION.md` §2) |
| **Production VPS** | `host.zerotier.my.id` running `0.4.1+audit1` (target_git_sha `8369e6e`, deployed 2026-11-10 04:17 WIB, all post-patch verifications green) |

## 0. What is already prepared (this branch)

- Version bumped to `0.4.2` in every location the release process expects:
  `pyproject.toml`, `src/uaeg/__init__.py`, `install.sh` (`UAEG_RELEASE_VERSION`).
- `README.md`: current-release line, a v0.4.2 note (F1–F8 summary), license line.
- `tests/test_e2e_installer_repair.py` **pins its standalone-download phases** to
  `PINNED_SOURCE_RELEASE` (the newest *published* release) and asserts the served
  `/healthz` version against that pin — so the suite needs no per-release edit and
  does not depend on the tree's current version. Without the pin the suite would
  404 on the release-prep commit (version bumped, tag not pushed yet); the v0.4.1
  release commit only passed CI because the tag was pushed in the same second and
  `release.yml` won the race.
- Deliberately **not** done (requires the private key, held only by the operator):
  `release/manifests/v0.4.2/*` and tag `v0.4.2`. The audit agent does not possess
  `uaeg-release-4` (or any release private key) and will neither fabricate a
  substitute key nor bypass signature verification. Until signed, the official
  v0.4.2 release stays **BLOCKED by design** — the live VPS already runs the full
  patch content as `0.4.1+audit1`, so this is a packaging step, not a security gap.

## 1. Merge this prep PR

Merge only when ready to sign and tag in the same sitting (README already
announces v0.4.2). Verify CI is green on the PR head (same 5 jobs as PR #1).

## 2. Generate and sign the manifest

From a checkout of `main` at the merged state:

```bash
python3 release/make_release.py --key /path/to/uaeg-release-4.key
```

Expected output:

```
manifest written : release/manifests/v0.4.2/uaeg-manifest.json
signature written: release/manifests/v0.4.2/uaeg-manifest.json.sig (verified against uaeg-release-4.pub.pem)
updater asset    : uaeg-updater-0.4.2.sh
commit these files, then tag v0.4.2
```

(The script self-verifies the signature against the committed public keys before
printing success; hashes cover `install.sh`, `upgrade.sh`, `updater.sh` exactly as
they will ship.)

## 3. Commit the release artifacts

```bash
git add release/manifests/v0.4.2/
git commit -m "release: v0.4.2 — manifest signed with uaeg-release-4"
git push origin main
```

## 4. Tag and push (annotated, like v0.4.1)

```bash
git tag -a v0.4.2 -m "UAEG v0.4.2 — live audit 2026-11-10 patch set (F1–F8)"
git push origin v0.4.2
```

`release.yml` then re-verifies the manifest signature against the committed public
key and every asset hash before publishing the 8 documented assets
(`install.sh`, `upgrade.sh`, `uaeg-updater-0.4.2.sh`, manifest `+.sig`, three
`.sha256` sidecars). A release cannot publish unverified content.

## 5. Post-publication verification (public URLs)

```bash
curl -fsSL -o /tmp/install-v042.sh https://github.com/smahud/uaeg/releases/download/v0.4.2/install.sh
sha256sum /tmp/install-v042.sh        # must equal release/manifests/v0.4.2/install.sh.sha256
curl -fsSL https://github.com/smahud/uaeg/releases/download/v0.4.2/uaeg-manifest.json
bash /tmp/install-v042.sh --check     # read-only status probe on the VPS
```

## 6. Upgrade the production VPS (signed path)

From an operator shell (or via UAEG itself — post-F4 the self-update guard
re-execs into a transient systemd scope, and post-F5 jobs survive the restart):

```bash
sudo bash /tmp/install-v042.sh
```

The VPS runs `0.4.1+audit1`; `sort -V` orders `0.4.1+audit1 < 0.4.2`, so the
signed upgrade applies cleanly **with the same agent key** (no key rotation
involved). Data, config, TLS certificate and dashboard password are preserved;
`install.sh` keeps a pre-upgrade backup and `sudo bash install.sh --rollback`
restores it (7 archives currently in `/var/backups/uaeg/`).

## 7. Verify after the upgrade

- `https://host.zerotier.my.id:3902/healthz` → `"version": "0.4.2"`
- Agent: `server_info` (first tool of every session) + a trivial `shell_exec`
  (`id -un; hostname`) with the existing key
- `sudo systemctl status uaeg` and `uaeg doctor` (bind now reported truthfully — F2)
- Dashboard `https://host.zerotier.my.id:3903/login` answers 200; anonymous API
  access stays 401

## Rollback

`sudo bash install.sh --rollback` (see `docs/TROUBLESHOOTING.md` and
`report/UAEG_DEPLOYMENT_AND_RECOVERY.md` for the full recovery matrix).
