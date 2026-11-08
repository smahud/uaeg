# Release Verification — v0.3.3

**Date:** 2026-11-08 · **Tag:** `v0.3.3` · **Commit:** `fff55c3`
("release: v0.3.3 — dashboard hardening, audit fixes, permanent upgrade
bootstrap (contract v3)") · **Release:** published 2026-11-08T03:21:50Z by
the verified release workflow (run conclusion: **success**).

---

## 1. Publication path (the established project workflow)

1. All changes committed to `main` and pushed (`9da477e..fff55c3`).
2. Annotated tag `v0.3.3` created on `fff55c3` and pushed.
3. `.github/workflows/release.yml` ran on the tag and, BEFORE publishing:
   - verified the tagged tree ships `install.sh`, `upgrade.sh`,
     `updater.sh` and the public key;
   - verified the installer pins the tag's version (0.3.3);
   - **verified the release manifest signature and every asset hash**
     (`release/verify_release.py`) — signature OK, all three assets matched,
     interface compatible;
   - staged the versioned updater asset + sidecars and re-verified them
     (`sha256sum -c` clean);
   - created the GitHub Release with 8 assets and smoke-checked that every
     documented URL resolves to a published asset.
4. Workflow conclusion: **success** (run `42442393496`).

## 2. Published asset inventory (verified via API)

| Asset | Size | Sidecar/proof |
|---|---|---|
| `install.sh` | 24 490 B | `.sha256` (OK) — pins 0.3.3 |
| `upgrade.sh` (permanent bootstrap) | 17 686 B | `.sha256` (OK) |
| `uaeg-updater-0.3.3.sh` | 52 268 B | `.sha256` (OK) + signed-manifest hash |
| `uaeg-manifest.json` | 548 B | — |
| `uaeg-manifest.json.sig` | 64 B | Ed25519 over the exact manifest bytes |

## 3. Independent post-publication verification (operator's-eye view)

Performed from a clean directory using only the DOCUMENTED public URLs, no
authentication (evidence: `report/evidence/release_assets_v033.txt`):

- All 8 assets downloaded from
  `https://github.com/smahud/uaeg/releases/latest/download/...` ✓
- `sha256sum -c` OK for install.sh, upgrade.sh, uaeg-updater-0.3.3.sh ✓
- **Ed25519 manifest signature verified** against the committed public key
  (`release/keys/uaeg-release-1.pub.pem`, fingerprint `d6ced90e372b478e`) ✓
- Updater bytes match the signed manifest (SHA-256 + size) ✓; manifest
  version 0.3.3, updater interface 1 ✓
- The published bootstrap executes and self-describes
  (`--bootstrap-info`: interface v1, trusted key uaeg-release-1) ✓

## 4. Local pre-publication verification (same checks, on the tree)

`release/verify_release.py` PASS at commit time — manifest signature, three
asset hashes, interface consistency (evidence:
`report/evidence/manifest_verification_v033.txt`).

## 5. CI

- CI run for `fff55c3` (main): matrix 3.11/3.12/3.13 + upgrade-contract +
  installer-smoke jobs — status recorded in FINAL_ACCEPTANCE.md at closing
  time (the v0.3.2-era runs on main are all green; the release-blocking
  gates for the tag are the release workflow's own verification steps,
  which passed).
- The release workflow's verification is the publication gate: a tag whose
  manifest signature or asset hashes do not verify CANNOT publish.

## 6. Release notes content

The release body documents the one-time bootstrap transition
(download + checksum + run), the asset semantics (bootstrap vs updater vs
signed manifest), and points to the audit trail under `report/` on the tag.
