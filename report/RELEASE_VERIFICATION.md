# Release Verification — UAEG v0.3.2

**Tanggal:** 2026-11-07/08 · **Tag rilis:** `v0.3.2` · **Baseline:** `4f5ac50` → patch `5dfcb0a`

---

## 1. Verifikasi pra-tag (dieksekusi sebelum tagging)

| Check | Hasil |
|-------|-------|
| Seluruh suite lolos (kontrak 95/0, unit 81, e2e 9+23) | ✅ `TEST_EVIDENCE.md` |
| Tidak ada defek P0/P1 terbuka | ✅ `FULL_AUDIT.md` §4 |
| Versi seragam `0.3.2`: pyproject, `__version__`, pin `UAEG_RELEASE_VERSION` install.sh, README | ✅ (juga dijaga otomatis oleh `release.yml`) |
| Diff final direview (8 file kode/test/docs + laporan; tanpa perubahan di luar scope) | ✅ `PATCH_CHANGELOG.md` §A |
| Backward compat: upgrade ≥ v0.2.0, RECORD lama terbaca, exit code stabil (4 reserved terdokumentasi) | ✅ `PATCH_CHANGELOG.md` §E |
| Jalur upgrade dari versi terpasang saat ini (0.3.0/0.3.1 → 0.3.2) — termasuk **skenario insiden persis** (aset terunduh di dalam clone usang) | ✅ T-17 + `repro_fix.sh` |
| Semantik versi: patch release (0.3.1 → 0.3.2) — perbaikan cacat tanpa API/behavior break | ✅ |
| Tag `v0.3.2` menunjuk commit rilis di `main` (bukan detached/orphan) | ✅ |

## 2. Gerbang publikasi otomatis (`.github/workflows/release.yml`)

Dijalankan oleh GitHub Actions saat tag `v*` di-push:

1. checkout commit ter-tag;
2. memastikan `install.sh` & `upgrade.sh` ada di tree tag;
3. membuat sidecar `sha256sum` (kompatibel `sha256sum -c`);
4. **memverifikasi pin versi installer == versi tag** (mencegah one-line
   install salah versi);
5. `gh release create --verify-tag` dengan 4 aset;
6. smoke-check keempat URL aset terdokumentasi.

Publikasi gagal → workflow gagal secara **terlihat** (bukan senyap).
Retry aman: `--verify-tag` + aset di-upload ulang idempoten oleh workflow.

## 3. Verifikasi independen pasca-publikasi — HASIL (dieksekusi 2026-11-07/08)

Seluruh butir §3 di bawah dieksekusi terhadap **aset yang benar-benar
terbit** dan lulus (log lengkap:
`report/evidence/published_asset_e2e_v0.3.2.txt`):

| # | Check | Hasil |
|---|-------|-------|
| 1 | `releases/latest` → `v0.3.2` | ✅ (terbit 2026-11-07T23:34:33Z, 4 aset lengkap) |
| 2 | Unduh 4 aset dari URL publik `releases/latest/download/...` | ✅ |
| 3 | `sha256sum -c` kedua sidecar | ✅ `upgrade.sh: OK`, `install.sh: OK` |
| 4 | Provenance: aset terbit == `git show v0.3.2:...` (byte-identical); pin installer `0.3.2` == pyproject tag `0.3.2` | ✅ |
| 5 | Fresh-install smoke dengan **install.sh terbit** (standalone bootstrap mengambil source v0.3.2 dari GitHub, venv+pip nyata, serve, `/healthz` OK v0.3.2, key dibuat, unauth MCP → 401) | ✅ |
| 6 | Upgrade smoke dengan **upgrade.sh terbit**: instalasi nyata v0.3.0 (dari source tag v0.3.0) + 2 agent key → upgrade → `UPGRADE BERHASIL: v0.3.0 -> v0.3.2`, **key fingerprint identik**, DB utuh, config dipertahankan, smoke `/healthz` OK, `RECORD.json` `contract_version=2`, `repo_url=https://github.com/smahud/uaeg.git`, `result=success` | ✅ |
| 7 | **Pementasan ulang skenario insiden operator** dengan aset terbit: clone usang @v0.3.0 (origin = GitHub nyata) + `upgrade.sh`/`.sha256` terunduh **di dalamnya** (untracked, bertabrakan dengan file yang dilacak tag target) + `sha256sum -c` OK + `bash upgrade.sh` → **rc=0**; HEAD/branch/status clone operator **identik byte-for-byte**; file terunduh tetap ada | ✅ |
| 8 | Rollback pasca-upgrade: data+config dipulihkan dari backup; app tetap v0.3.2 sesuai kontrak (app/ dipangkas setelah sukses terverifikasi) dan `--rollback` **mencetak jalur pemulihan app yang jujur** (`install.sh` dari tag `v0.3.0` immutable); re-upgrade setelahnya → rc=0, v0.3.2, keys identik | ✅ |

CI pada commit pasca-rilis (main `f0980cf`, memuat v0.3.2 + perbaikan
harness CI): **semua job sukses** (Test 3.11/3.12/3.13, Safe-upgrade
contract, Install script smoke) — menghapus kegagalan CI yang menghantui
main sejak era v0.3.1 (akar: checkout shallow actions/checkout —
`shallow update not allowed`; sekaligus kelas masalah "shallow clone"
dari daftar investigasi insiden).

## 4. Kebijakan tag & rilis yang diikuti

- Tag **tidak pernah** ditimpa/ditulis ulang; rilis historis (v0.2.0,
  v0.3.0 tanpa Release) dibiarkan apa adanya — upgrade by-tag tetap
  berfungsi dan `release.yml` menjamin aset untuk semua tag baru.
- `releases/latest` diarahkan oleh publikasi rilis stabil baru (bukan
  prerelease) — mekanisme GitHub standar, tanpa manipulasi metadata.
