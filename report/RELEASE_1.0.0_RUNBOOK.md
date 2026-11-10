# Release runbook — v1.0.0 (live audit 2026-11-10 patch set, F1–F8)

| | |
|---|---|
| **Status** | EXECUTED 2026-11-10 — keyring rotation 4→5 (`uaeg-release-5`, operator-authorized) executed by the agent; see `report/evidence/key_rotation_v100.txt` and the addendum in `report/UAEG_LIVE_RELEASE_DECISION.md`. |
| **Base** | `main` = merge of PR #2 (`release/v0.4.2-prep`, CI 5/5 green on head `9fbe42e`) + the v1.0.0 bump commit on top |
| **Version decision** | Operator-approved jump straight to **1.0.0** (skipping a separate v0.4.2 release) — rationale in `report/UAEG_LIVE_RELEASE_DECISION.md` §4 |
| **Signing key** | `uaeg-release-5` — rotation 4→5: the `uaeg-release-4` private key is no longer available to the publisher (same situation as every prior release); operator-authorized 2026-11-10, same operational shape as rotations 1→2 / 2→3 / 3→4 (`docs/BOOTSTRAP.md`) |
| **Production VPS** | `host.zerotier.my.id` running `0.4.1+audit1` (target_git_sha `8369e6e`, deployed 2026-11-10 04:17 WIB, all post-patch verifications green) |

## 0. What is already prepared (on `main`)

- PR #2 merged (version bump machinery, e2e standalone-download pin, the original
  v0.4.2 runbook — superseded by this file).
- Version bumped to `1.0.0` in every location the release process expects:
  `pyproject.toml`, `src/uaeg/__init__.py`, `install.sh` (`UAEG_RELEASE_VERSION`),
  and the README (current-release line, release note, license line).
- `tests/test_e2e_installer_repair.py` **pins its standalone-download phases** to
  `PINNED_SOURCE_RELEASE = "0.4.1"` (the newest *published* release) and asserts
  the served `/healthz` version against that pin — the suite needs no per-release
  edit. After v1.0.0 is published, optionally bump the pin to `1.0.0`.
- Full local suites re-run on the bumped tree: pytest unit/regression 197 + v042
  regression 18 + e2e smoke/hardening 21, and `tests/test_upgrade_contract.sh`
  **290 PASS / 0 FAIL**.
- Signing resolution (2026-11-10): the `uaeg-release-4` private key turned out
  to be no longer available to the publisher — the same situation as every
  prior release (rotations 1→2, 2→3, 3→4 in `docs/BOOTSTRAP.md`). With the
  operator's explicit authorization, the release uses **keyring rotation
  4→5** (`uaeg-release-5`, `BOOTSTRAP_VERSION` 5, keyrings trust all five
  keys): no substitute key is fabricated for key-4, no signature verification
  is bypassed, and the rotation is operator-visible exactly like its
  predecessors.

## 1. Confirm CI is green on `main`

The push of the bump commit triggers the same 5 CI jobs (pytest 3.11/3.12/3.13,
install-script smoke, safe-upgrade contract). All must be green before tagging.

## 2. Generate and sign the manifest

From a checkout of `main` at the bumped state:

```bash
python3 release/make_release.py --key /path/to/uaeg-release-5.key
```

Expected output:

```
manifest written : release/manifests/v1.0.0/uaeg-manifest.json
signature written: release/manifests/v1.0.0/uaeg-manifest.json.sig (verified against uaeg-release-5.pub.pem)
updater asset    : uaeg-updater-1.0.0.sh
commit these files, then tag v1.0.0
```

(The script self-verifies the signature against the committed public keys before
printing success; hashes cover `install.sh`, `upgrade.sh`, `updater.sh` exactly as
they will ship.)

## 3. Commit the release artifacts

```bash
git add release/manifests/v1.0.0/
git commit -m "release: v1.0.0 — manifest signed with uaeg-release-5"
git push origin main
```

## 4. Tag and push (annotated, like v0.4.1)

```bash
git tag -a v1.0.0 -m "UAEG v1.0.0 — live audit 2026-11-10 patch set (F1–F8), first stable milestone"
git push origin v1.0.0
```

`release.yml` then re-verifies the manifest signature against the committed public
key and every asset hash before publishing the 8 documented assets
(`install.sh`, `upgrade.sh`, `uaeg-updater-1.0.0.sh`, manifest `+.sig`, three
`.sha256` sidecars). A release cannot publish unverified content.

## 5. Post-publication verification (public URLs)

```bash
curl -fsSL -o /tmp/install-v100.sh https://github.com/smahud/uaeg/releases/download/v1.0.0/install.sh
sha256sum /tmp/install-v100.sh        # must equal release/manifests/v1.0.0/install.sh.sha256
curl -fsSL https://github.com/smahud/uaeg/releases/download/v1.0.0/uaeg-manifest.json
bash /tmp/install-v100.sh --check     # read-only status probe on the VPS
```

## 6. Upgrade the production VPS (signed path)

From an operator shell (or via UAEG itself — post-F4 the self-update guard
re-execs into a transient systemd scope, and post-F5 jobs survive the restart):

```bash
sudo bash /tmp/install-v100.sh
```

The VPS runs `0.4.1+audit1`; `sort -V` orders `0.4.1+audit1 < 1.0.0`, so the
signed upgrade applies cleanly **with the same agent key** (no key rotation
involved). Data, config, TLS certificate and dashboard password are preserved;
`install.sh` keeps a pre-upgrade backup and `sudo bash install.sh --rollback`
restores it. The stale `~/uaeg` checkout on the VPS should be refreshed or
deleted afterwards (it predates v0.4.2-era guards).

## 7. Verify after the upgrade

- `https://host.zerotier.my.id:3902/healthz` → `"version": "1.0.0"`
- Agent: `server_info` (first tool of every session) + a trivial `shell_exec`
  (`id -un; hostname`) with the existing key
- `sudo systemctl status uaeg` and `uaeg doctor` (bind now reported truthfully — F2)
- Dashboard `https://host.zerotier.my.id:3903/login` answers 200; anonymous API
  access stays 401

## Rollback

`sudo bash install.sh --rollback` (see `docs/TROUBLESHOOTING.md` and
`report/UAEG_DEPLOYMENT_AND_RECOVERY.md` for the full recovery matrix).
