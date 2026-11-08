#!/usr/bin/env python3
"""Verify a UAEG release: manifest signature, asset integrity, consistency.

Used by CI (release.yml) before publishing and by anyone who wants to check
a checkout/extracted release:

    python3 release/verify_release.py            # verify this repo checkout
    python3 release/verify_release.py --dir DIR  # verify an extracted copy

Checks:
  1. release/manifests/v<version>/uaeg-manifest.json matches the version in
     pyproject.toml (or install.sh's pin).
  2. The manifest's Ed25519 signature verifies against ANY committed public
     key in release/keys/ (key rotation: releases <= v0.3.3 are signed by
     uaeg-release-1, later releases by uaeg-release-2).
  3. Every asset listed in the manifest (install.sh, upgrade.sh, updater.sh)
     exists in the tree and matches the recorded sha256 + size.
  4. The manifest's updater interface is within the bootstrap's supported
     interface (reads BOOTSTRAP_INTERFACE from upgrade.sh).

Exit 0 = release is consistent and authentically signed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=".", help="repository/release root")
    args = ap.parse_args()
    root = Path(args.dir).resolve()

    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        fail(f"no pyproject.toml in {root}")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    if not m:
        fail("cannot read the version from pyproject.toml")
    version = m.group(1)

    manifest_path = root / "release" / "manifests" / f"v{version}" / "uaeg-manifest.json"
    sig_path = manifest_path.with_suffix(".json.sig")
    pub_keys = sorted((root / "release" / "keys").glob("*.pub.pem"))
    if not pub_keys:
        fail(f"no public keys in {root / 'release' / 'keys'}")
    for p in (manifest_path, sig_path):
        if not p.is_file():
            fail(f"missing {p.relative_to(root)}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        fail(f"manifest is not valid JSON: {exc}")

    if manifest.get("product") != "uaeg":
        fail("manifest product is not 'uaeg'")
    if manifest.get("version") != version:
        fail(f"manifest version {manifest.get('version')!r} != pyproject version {version!r}")

    # --- signature (authenticity; any trusted release key) ---
    verified_with = None
    for pub_key in pub_keys:
        proc = subprocess.run(
            [
                "openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub_key),
                "-rawin", "-in", str(manifest_path), "-sigfile", str(sig_path),
            ],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            verified_with = pub_key.name
            break
    if verified_with is None:
        fail(
            "manifest signature does NOT verify against any committed public "
            f"key in release/keys/ ({[k.name for k in pub_keys]})"
        )
    print(f"ok    manifest signature verified ({verified_with})")

    # --- assets (integrity) ---
    updater_rel = (manifest.get("updater") or {}).get("path", "")
    if not updater_rel or "/" in updater_rel:
        fail("manifest updater.path missing or unsafe")
    checks = [
        ("install.sh", root / "install.sh", (manifest.get("assets") or {}).get("install.sh")),
        ("upgrade.sh", root / "upgrade.sh", (manifest.get("assets") or {}).get("upgrade.sh")),
        ("updater.sh", root / "updater.sh", manifest.get("updater")),
    ]
    for name, path, meta in checks:
        if not path.is_file():
            fail(f"missing {name} ({path})")
        if not isinstance(meta, dict):
            fail(f"manifest has no integrity metadata for {name}")
        got_sha, got_size = sha256_file(path), path.stat().st_size
        if got_sha != meta.get("sha256"):
            fail(f"{name} sha256 mismatch: manifest {meta.get('sha256')} != actual {got_sha}")
        if got_size != meta.get("size"):
            fail(f"{name} size mismatch: manifest {meta.get('size')} != actual {got_size}")
        print(f"ok    {name} matches the signed manifest ({got_sha[:16]}…)")

    # --- bootstrap/updater interface compatibility ---
    bootstrap = (root / "upgrade.sh").read_text(encoding="utf-8")
    bm = re.search(r'^BOOTSTRAP_INTERFACE="(\d+)"', bootstrap, re.M)
    um = re.search(r'^UPDATER_INTERFACE="(\d+)"', (root / "updater.sh").read_text(encoding="utf-8"), re.M)
    if not bm or not um:
        fail("cannot read BOOTSTRAP_INTERFACE/UPDATER_INTERFACE from the scripts")
    b_iface, u_iface = int(bm.group(1)), int(um.group(1))
    if manifest["updater"].get("interface") != u_iface:
        fail("manifest updater.interface != updater.sh's UPDATER_INTERFACE")
    if u_iface > b_iface:
        fail(f"updater interface v{u_iface} exceeds the bootstrap's supported v{b_iface}")
    print(f"ok    bootstrap/updater interface compatible (bootstrap v{b_iface} >= updater v{u_iface})")

    print(f"\nRELEASE v{version} VERIFIED: signature + assets + interface consistency")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
