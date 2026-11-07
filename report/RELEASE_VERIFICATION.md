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

## 3. Verifikasi independen pasca-publikasi (dilakukan setelah terbit, hasil di-commit ke main)

Prosedur yang dieksekusi terhadap **aset yang benar-benar terbit**
(bukan artefak lokal):

1. `releases/latest` me-resolve ke `v0.3.2`;
2. mengunduh keempat aset dari URL publik
   `releases/latest/download/...`;
3. `sha256sum -c` kedua sidecar → OK;
4. provenance: konten `upgrade.sh` terbit == `git show v0.3.2:upgrade.sh`;
   versi pyproject di source tag == `0.3.2` == pin installer;
5. fresh-install smoke memakai **install.sh terbit** (lingkungan
   terisolasi, tanpa systemd);
6. upgrade smoke memakai **upgrade.sh terbit**: instalasi v0.3.1 →
   upgrade → verifikasi versi/keys/DB/config;
7. **pementasan ulang skenario insiden operator** dengan aset terbit
   (clone usang + unduhan untracked di dalamnya) → harus sukses;
8. verifikasi rollback (`--rollback`) pasca-upgrade.

> Hasil eksekusi butir 1–8 dicatat pada commit tindak-lanjut di `main`
> (bukan di dalam tag — tag immutable) dan dirangkum di
> `PRODUCTION_READINESS.md` serta laporan akhir audit.

## 4. Kebijakan tag & rilis yang diikuti

- Tag **tidak pernah** ditimpa/ditulis ulang; rilis historis (v0.2.0,
  v0.3.0 tanpa Release) dibiarkan apa adanya — upgrade by-tag tetap
  berfungsi dan `release.yml` menjamin aset untuk semua tag baru.
- `releases/latest` diarahkan oleh publikasi rilis stabil baru (bukan
  prerelease) — mekanisme GitHub standar, tanpa manipulasi metadata.
