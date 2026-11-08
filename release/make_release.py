#!/usr/bin/env python3
"""Build and sign the UAEG release manifest (contract v3, docs/BOOTSTRAP.md).

Run from the repository root BEFORE tagging a release:

    python3 release/make_release.py --key /path/to/uaeg-release-1.key

Produces under release/manifests/v<version>/:
  - uaeg-manifest.json      release manifest (asset inventory + updater coords)
  - uaeg-manifest.json.sig  Ed25519 signature over the EXACT manifest bytes
  - install.sh.sha256, upgrade.sh.sha256, uaeg-updater-<v>.sh.sha256
    (sha256sum -c compatible sidecars, for human verification)

The private signing key is NEVER committed; only the public key lives in
release/keys/. CI verifies the signature with the public key before any
release is published (release.yml).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UPDATER_INTERFACE = 1


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        sys.exit("cannot read version from pyproject.toml")
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--key",
        default=os.environ.get("UAEG_RELEASE_SIGNING_KEY", ""),
        help="path to the Ed25519 private key (never committed)",
    )
    args = ap.parse_args()

    version = read_version()
    updater_name = f"uaeg-updater-{version}.sh"
    files = {
        "install.sh": REPO / "install.sh",
        "upgrade.sh": REPO / "upgrade.sh",
        "updater.sh": REPO / "updater.sh",
    }
    for name, path in files.items():
        if not path.is_file():
            sys.exit(f"missing {name} ({path})")

    manifest = {
        "product": "uaeg",
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updater": {
            # The updater ships under a versioned asset name; the bytes are
            # exactly the repo's updater.sh.
            "path": updater_name,
            "sha256": sha256_file(files["updater.sh"]),
            "size": files["updater.sh"].stat().st_size,
            "interface": UPDATER_INTERFACE,
        },
        "assets": {
            "install.sh": {
                "sha256": sha256_file(files["install.sh"]),
                "size": files["install.sh"].stat().st_size,
            },
            "upgrade.sh": {
                "sha256": sha256_file(files["upgrade.sh"]),
                "size": files["upgrade.sh"].stat().st_size,
            },
        },
    }

    out_dir = REPO / "release" / "manifests" / f"v{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "uaeg-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # sha256sum -c sidecars (referencing the ASSET names they ship under)
    (out_dir / "install.sh.sha256").write_text(
        f"{manifest['assets']['install.sh']['sha256']}  install.sh\n", encoding="utf-8"
    )
    (out_dir / "upgrade.sh.sha256").write_text(
        f"{manifest['assets']['upgrade.sh']['sha256']}  upgrade.sh\n", encoding="utf-8"
    )
    (out_dir / f"{updater_name}.sha256").write_text(
        f"{manifest['updater']['sha256']}  {updater_name}\n", encoding="utf-8"
    )

    if not args.key:
        print(f"manifest written: {manifest_path}")
        print("NO SIGNATURE: pass --key (or UAEG_RELEASE_SIGNING_KEY) to sign.")
        return 1

    sig_path = out_dir / "uaeg-manifest.json.sig"
    proc = subprocess.run(
        [
            "openssl", "pkeyutl", "-sign",
            "-inkey", args.key,
            "-rawin", "-in", str(manifest_path),
            "-out", str(sig_path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"openssl signing failed: {proc.stderr.strip()}")

    # Immediate self-verification with the committed public key.
    pub = REPO / "release" / "keys" / "uaeg-release-1.pub.pem"
    proc = subprocess.run(
        [
            "openssl", "pkeyutl", "-verify", "-pubin",
            "-inkey", str(pub),
            "-rawin", "-in", str(manifest_path),
            "-sigfile", str(sig_path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"self-verification against the committed public key FAILED: {proc.stderr.strip()}")

    print(f"manifest written : {manifest_path}")
    print(f"signature written: {sig_path} (verified against {pub})")
    print(f"updater asset    : {updater_name}")
    print("commit these files, then tag v" + version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
