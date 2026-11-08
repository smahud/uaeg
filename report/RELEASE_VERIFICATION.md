# Release Verification — v0.3.5

**Date:** 2026-11-08 · Tag `v0.3.5` (commit `e8d81f4`) · published by the
Release workflow (run 42492912159, success) after CI on the same commit was
fully green: Test matrix 3.11/3.12/3.13 + Install script smoke +
Safe-upgrade contract suite — all `success` (run 42492568195).

**Independent post-publication verification: 36 PASS / 0 FAIL, strictly from
the public URLs** (`report/evidence/published_asset_e2e_v035.txt`,
`scripts/verify_published_v035.sh`):

1. `releases/latest` -> `v0.3.5`; all 8 documented assets present.
2. `upgrade.sh.sha256` sidecar verifies; **published bootstrap is
   BYTE-STABLE vs the v0.3.4 asset** — operators keep the file they already
   downloaded (download-once promise, BOOTSTRAP.md).
3. Manifest Ed25519 signature verifies with the tagged tree's
   `uaeg-release-2` public key, NOT with the old key; manifest asset hashes
   match the downloaded bytes; manifest version 0.3.5; interface 1.
4. Published bootstrap reports keyring v2 (both release keys).
5. Fresh install via the published one-line installer (standalone mode,
   fetches the v0.3.5 tarball from GitHub): rc=0, version 0.3.5, healthz OK
   over TLS, healthz reports 0.3.5.
6. **INCIDENT CHECK (2026-11-08, live against the published bytes):**
   populated v0.3.3 installation (2 agent keys, operator config change),
   published bootstrap executed WITHOUT `--yes`, stdin DETACHED
   (`< /dev/null`), the operator answers `y` on a REAL pty — the exact
   transcript v0.3.4 refused. Result: **rc=0**, the prompt IS displayed,
   no false "cancelled by the operator", v0.3.3 -> v0.3.5, agent keys
   identical (zero data loss), operator config preserved,
   `UPGRADE SUCCESSFUL`.
7. `--yes` re-run (re-install/repair) rc=0; keys identical.
8. Fault injection (broken installer): automatic rollback (exit 2),
   rollback verified over /healthz, keys intact, app back at 0.3.5.
9. Operator directory file set untouched; the operator's bootstrap file
   byte-identical after upgrade + re-run + rollback.
10. Key rotation: a v0.3.3-era bootstrap (keyring v1) REFUSES v0.3.5
    fail-closed (rc=1, SIGNATURE INVALID, "nothing was executed or
    changed"), installation untouched.

---

# Release Verification — v0.3.3

---

# Release Verification — v0.3.4

**Tag:** v0.3.4 · **Published:** 2026-11-08 · **Verifier:** independent
round-2 audit (this session), from public URLs.

## Pre-publication gates (all executed before tagging)

1. `release/verify_release.py` — manifest signature verifies against
   `uaeg-release-2.pub.pem` (any-of-keys policy); install.sh / upgrade.sh /
   updater.sh sha256+size match the signed manifest; bootstrap/updater
   interfaces compatible (v1 ≥ v1). PASS.
2. Full test matrix on the tagged tree state: 118 unit/regression + 32 e2e
   + 155 contract assertions + 18/18 real-asset incident reproduction. PASS.
3. `bash -n` on all shipped shell scripts. PASS.
4. install.sh pins `UAEG_RELEASE_VERSION="0.3.4"` == tag version (release
   workflow gate). PASS.

## Post-publication verification (EXECUTED 2026-11-08 — 32/32 PASS)

Harness: `scripts`-side verifier run with EUID=0 via a root user namespace
(published script bytes unmodified; the installers' apt step skipped
in-tree exactly as GitHub CI does; the bootstrap's ONE GitHub API call
carried through its documented UAEG_RELEASE_API_BASE mirror because the
sandbox IP had exhausted the unauthenticated API rate limit — artifact
downloads and signatures all came from github.com). Raw log:
`report/evidence/published_asset_e2e_v034.txt`.

1. `releases/latest` resolves to **v0.3.4**; the Release workflow
   (run 4bda64a7) completed **success**; CI on the same commit: **success**
   (Python 3.11/3.12/3.13 + contract + installer jobs).
2. All **8 documented assets** present with the expected names.
3. `upgrade.sh.sha256` sidecar: **upgrade.sh: OK**.
4. Downloaded manifest's Ed25519 signature **verifies against the committed
   uaeg-release-2 public key** and correctly does NOT verify against the
   old uaeg-release-1 key (rotation in effect).
5. Manifest sha256+size match the independently downloaded install.sh /
   upgrade.sh / uaeg-updater-0.3.4.sh bytes; updater interface 1.
6. `--bootstrap-info`: bootstrap version **2**, trusted keys
   **uaeg-release-1,uaeg-release-2** (fingerprints d6ced90e372b478e,
   8d3fcff09c9aed7d).
7. **Fresh install** through the published one-line installer in standalone
   mode (it fetches the v0.3.4 source tarball from GitHub itself): rc=0,
   `uaeg version` = 0.3.4, `/healthz` OK over TLS with version 0.3.4.
8. **Upgrade smoke**: populated v0.3.3 installation (2 agent keys,
   operator config port 3984) upgraded to v0.3.4 via the published
   bootstrap from inside an operator directory carrying the downloaded
   untracked bootstrap — rc=0, UPGRADE SUCCESSFUL, staging clone of tag
   v0.3.4, agent keys byte-identical, operator config preserved,
   operator directory file set and bootstrap file byte-identical.
9. **Fault injection**: a deliberately broken v0.3.4 source tree upgrade
   fails → **automatic rollback (exit 2)** — application back at 0.3.4,
   keys intact, "rollback verified: the old application answers /healthz".
10. **Key-rotation semantics**: the historical v0.3.3-era bootstrap (which
    trusts only uaeg-release-1) targeting v0.3.4 **refuses fail-closed
    (exit 1)** with "RELEASE MANIFEST SIGNATURE INVALID … key rotation …
    NOTHING was executed or changed"; the installation remains at 0.3.4.

Additional cross-release evidence: the v0.3.4 dual-key bootstrap upgrades a
v0.3.0 installation to the key-1-signed historical v0.3.3 release
(19/19 assertions — `report/evidence/incident_repro_real_v033.txt`),
confirming rotation did not break historical-release targeting.


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
