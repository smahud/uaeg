# Patch Changelog — UAEG v0.3.1 → v0.3.2

**Tanggal:** 2026-11-07/08 · **Commit:** `5dfcb0a` (+ commit evidence) · **Baseline diaudit:** `4f5ac50` (main, pasca-v0.3.1)

---

## A. File yang dimodifikasi

| File | Perubahan |
|------|-----------|
| `upgrade.sh` | **Inti perbaikan P0.** `materialize_source()` ditulis ulang total: source rilis selalu di-*stage* di clone terisolasi (`mktemp -d /tmp/uaeg-upgrade-src.XXXXXX`, otomatis 0700) — `git checkout`/`fetch`/`reset`/`clean` di working tree operator **tidak pernah lagi dilakukan**. Fungsi baru: `resolve_repo_url()` (prioritas `--repo` > origin clone operator > default publik; URL yang dipakai selalu dicetak dan dicatat), `tag_exists_on_remote()` (klasifikasi kegagalan akurat: tag tidak ada vs `ls-remote` gagal), `handle_stage_failure()` (toleransi `best-effort` khusus dry-run). Verifikasi pasca-clone: HEAD staging == commit tag. `resolve_target_ref()` kini selalu `git ls-remote` ke remote (fallback tag lokal *dengan peringatan eksplisit* jika offline). `read_state()`: kunci konfigurasi yang absen dinormalkan ke default aplikasi (mirror `src/uaeg/config.py`) — perbandingan "konfigurasi efektif" (U-4) tidak lagi false-positive pada config lama/parsial. `--keep-backups` divalidasi numerik dan di-clamp ≥ 1 (retensi tak pernah menghapus satu-satunya recovery point). Dry-run ikut men-stage source (rencana akurat; degradasi anggun saat offline). `RECORD.json`: `contract_version=2`, `repo_url`. `--rollback` mencetak jalur pemulihan aplikasi yang jujur (tag `v<from_version>` immutable + data/config) saat `app/` sudah dipangkas. Exit code 4 (tree kotor) dihapus → reserved. |
| `tests/test_upgrade_contract.sh` | T-13/T-14 ditulis ulang untuk kontrak v2 (clone operator byte-for-byte tak tersentuh: HEAD, branch, status dibandingkan sebelum/sesudah; tree kotor tidak lagi menghalangi dan perubahan lokal dipertahankan). **Test baru:** T-17 (regresi insiden v0.3.1 — skenario operator persis: clone usang + aset terunduh untracked di dalamnya), T-18 (tag tidak ada → pesan akurat, pesan menyesatkan lama tidak boleh muncul), T-19 (standalone tanpa repo lokal → upgrade PENUH). |
| `docs/UPGRADE_CONTRACT.md` | Kontrak dinaikkan ke **v2**: U-12 dirumuskan ulang (staging terisolasi; tree operator tak pernah dibaca/diubah; exit 4 reserved), catatan kejujuran pemulihan `app/` pasca-sukses, `--keep-backups` min 1, changelog kontrak v2. |
| `docs/INSTALL.md` | Bagian "Upgrading" diperbarui: tidak perlu `git pull` lagi; dua alur (unduh-standalone / dari dalam checkout) ekuivalen; penjelasan staging terisolasi. |
| `pyproject.toml`, `src/uaeg/__init__.py`, `install.sh`, `README.md` | Versi 0.3.1 → **0.3.2** (pin installer `UAEG_RELEASE_VERSION` ikut — diverifikasi oleh `release.yml` agar one-line install tidak salah versi). |
| `report/evidence/upgrade_contract_suite_v0.3.2.txt`, `report/evidence/live_smoke_v0.3.2.txt` | Bukti eksekusi baru. |
| `report/ROOT_CAUSE_ANALYSIS.md`, `PATCH_CHANGELOG.md`, `TEST_EVIDENCE.md`, `UPGRADE_ROLLBACK_EVIDENCE.md`, `SECURITY_REVIEW.md`, `RELEASE_VERIFICATION.md`, `FULL_AUDIT.md`, `PRODUCTION_READINESS.md` | Laporan audit P0 ini. |

## B. Perbaikan non-insiden yang menyertai (root-cause, kecil, teruji)

1. **U-4 false-positive pada config parsial** — sebelum v0.3.2, config yang
   tidak memuat kunci (mis. ditulis tangan / era lama) membuat
   `None != False` pada `allow_public` → verifikasi gagal → **rollback
   palsu**. Kini absen = default aplikasi. (Ditemukan saat verifikasi
   perbaikan; direproduksi sebelum diperbaiki.)
2. **Retensi backup bisa menghapus semua recovery point** —
   `--keep-backups 0` menghapus seluruh backup termasuk yang baru
   terverifikasi. Kini di-clamp minimum 1 + validasi numerik.
3. **Pesan fetch menyesatkan di mode git lama** — script mencetak URL
   `--repo` default padahal fetch ke `origin` clone. Kini URL yang
   benar-benar dipakai selalu dicetak/dicatat.

## C. Hal yang SENGAJA tidak diubah (scope discipline)

- Arsitektur dua-listener (MCP 3902 / dashboard 3903), SQLite + WAL,
  auth model bearer-key, dashboard password + session, PTY/jobs — semua
  diaudit dan **lulus** (lihat `FULL_AUDIT.md`); tidak ada refactor.
- Perilaku produk "agent terautentikasi = eksekusi penuh di host"
  (by design, PRD FR-010/FR-013) — tidak diubah; batas autentikasinya
  diverifikasi ketat.
- `install.sh` mode upgrade (backup aplikasi + rollback internal) — lulus
  audit; hanya pin versi yang berubah.
- Workflow `release.yml`/`ci.yml` — lulus audit; tidak diubah.

## D. Metode verifikasi perubahan

| Lapis | Perintah | Hasil |
|-------|----------|-------|
| Reproduksi insiden (pra-perbaikan) | `scripts/repro_incident.sh` (isolated) | Gagal persis seperti operator (exit 1 + pesan salah) ✅ terkonfirmasi |
| Reproduksi perbaikan | `scripts/repro_fix.sh` (isolated, aset v0.3.2) | `UPGRADE BERHASIL v0.3.0 -> v0.3.2`, tree operator utuh ✅ |
| Kontrak upgrade | `bash tests/test_upgrade_contract.sh` | **95 PASS / 0 FAIL** ✅ |
| Unit Python | `pytest tests/test_unit_*.py tests/test_dashboard_auth.py` | **81 passed** ✅ |
| E2E MCP/TLS | `pytest tests/test_e2e_smoke.py` | **9 passed** ✅ |
| E2E dashboard/hardening | `pytest tests/test_e2e_dashboard_auth.py tests/test_e2e_hardening.py` | **23 passed** ✅ |
| Live smoke 13 tool MCP | `scripts/live_smoke.sh` (server nyata) | Semua tool + batas auth OK ✅ |
| Sintaks shell | `bash -n` semua `.sh` | OK ✅ |

## E. Kompabilitas

- Upgrade dari instalasi ≥ v0.2.0 tetap didukung (schema kompatibel;
  T-05/T-08/T-13 mensimulasikan lintas versi).
- `RECORD.json` lama (contract_version 1) tetap terbaca; `--rollback`
  tetap berfungsi atas backup lama (struktur direktori tidak berubah).
- Exit code 0/1/2/3/5 tidak berubah; 4 kini reserved (didokumentasikan).
- Kunci agent, hash password dashboard, DB, konfigurasi, sertifikat TLS:
  tidak tersentuh oleh jalur upgrade (dibuktikan T-05/T-13/T-17 + live
  smoke).
