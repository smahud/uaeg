# Upgrade & Rollback Evidence — UAEG v0.3.2

**Tanggal:** 2026-11-07/08 · **Kontrak:** v2 (docs/UPGRADE_CONTRACT.md) · **Suite:** `tests/test_upgrade_contract.sh` — 95 PASS / 0 FAIL

---

## 1. Siklus lengkap yang dibuktikan (per eksekusi nyata)

| # | Siklus | Bukti |
|---|--------|-------|
| 1 | Fresh install → konfigurasi → generate key → serve → /healthz | T-01, T-02, live smoke |
| 2 | Upgrade existing (lintas versi, remote & `--source`) dengan zero data loss | T-13 (0.3.x→7.7.7), T-05 (→9.9.9), T-17 (0.3.0→10.0.0 **skenario insiden**), T-19 (standalone) |
| 3 | Re-upgrade / re-install versi sama (idempoten) | T-06, T-14, T-19 |
| 4 | Upgrade gagal → **rollback otomatis** → aplikasi lama hidup kembali | T-08 (exit 2; "rollback terverifikasi: aplikasi kembali ke v9.9.9"; smoke /healthz app lama OK) |
| 5 | Rollback manual (`--rollback`) memulihkan data pra-upgrade | T-09 |
| 6 | Downgrade eksplisit saja | T-16 (exit 5) |
| 7 | Backup retensi | T-12 (keep=1), clamp ≥1 |
| 8 | Rahasia tak bocor | T-11 |

## 2. Kontrak zero-data-loss — apa yang diverifikasi setiap upgrade (U-4)

Setiap upgrade yang sukses membuktikan dan mencatat (`before-state.json` /
`after-state.json` / `RECORD.json` di direktori backup):

1. versi terpasang == versi target (dari venv nyata);
2. service aktif / smoke `/healthz` menjawab (server benar-benar dinyalakan);
3. **himpunan `(key_id, status)` agent identik** — verifikasi dan status
   (termasuk revoked) tidak berubah;
4. `PRAGMA quick_check` = ok;
5. jumlah baris `keys` identik persis; `jobs`/`sessions`/`exec_logs` tidak
   berkurang;
6. konfigurasi efektif identik (port, bind, TLS, allow_public,
   public_host) — kunci absen dinormalkan ke default aplikasi (v0.3.2);
7. password dashboard tetap terkonfigurasi (jika sebelumnya ada) dan
   halaman dashboard menjawab 200/302.

Bukti contoh (T-17, skenario insiden — dari 0.3.0 ke 10.0.0):

```
semua check zero-data-loss lolos
UPGRADE BERHASIL: v0.3.0 -> 10.0.0
RECORD: contract_version=2, from_version=0.3.0, to_version=10.0.0,
        target_ref=v10.0.0, repo_url=<remote>, result=success,
        verification=all-checks-passed
```

## 3. Rollback — kejujuran pemulihan aplikasi (diperbaiki di v0.3.2)

- Backup pra-upgrade memuat: snapshot DB konsisten (SQLite online backup
  API), seluruh data dir (TLS, hash password, artefak), konfigurasi, dan
  aplikasi+venv.
- **Setelah sukses terverifikasi** `app/` dipangkas dari backup (hemat
  disk) — data+config tetap utuh untuk `--rollback`.
- Sejak v0.3.2, `--rollback` **mencetak jalur pemulihan aplikasi yang
  jujur** ketika `app/` tidak tersimpan:
  `install.sh` dari tag `v<from_version>` yang immutable (tercatat di
  RECORD.json backup) + `--rollback` untuk data & config. Dokumen kontrak
  menyatakan hal ini eksplisit (tidak mengklaim rollback aplikasi penuh
  dari backup yang app/-nya sudah dihapus).
- Retensi tidak pernah menghapus satu-satunya recovery point
  (`--keep-backups` di-clamp ≥ 1; pruning hanya setelah verifikasi sukses).
- Peringatan yang benar tetap berlaku: rollback mengganti data dengan
  kondisi **pra-upgrade** — tulisan (key/job) SETELAH backup akan hilang;
  operator dikonfirmasi dulu (T-09 membuktikan key ke-3 yang dibuat
  setelah backup hilang saat rollback, sesuai peringatan).

## 4. Bukti kegagalan terkontrol (fault injection)

| Skenario injeksi | Hasil |
|------------------|-------|
| Source rusak (syntax error di server.py) | installer gagal → rollback otomatis → exit 2 → app lama hidup (T-08) |
| Tag tidak ada (`--version 0.0.42`) | exit 1, pesan akurat "tidak ditemukan di remote", **tanpa** backup tercipta, versi tidak berubah (T-18) |
| Aset terunduh di dalam clone operator | **sukses** (insiden v0.3.1 tidak terulang; tree utuh) (T-17) |
| Tree kotor | sukses, edit lokal dipertahankan (T-14) |
| Tag lokal usang (clone tua, remote maju) | resolusi tetap benar via ls-remote (T-13, T-17) |
| Dashboard publik tanpa password | upgrade ditolak sebelum backup (T-GUARD) |
| Tanpa instalasi | exit 3 (T-10) |
| Downgrade diam-diam | exit 5 (T-16) |
| Ruang disk kurang | ditolak sebelum backup (guard di `run_upgrade`; perhitungan `app+data × 1.2 + 50 MiB`) |

## 5. Catatan batas (jujur)

- Rollback = kembali ke titik backup; write pasca-backup hilang sesuai
  peringatan — ini perilaku terdokumentasi, bukan cacat.
- PTY yang terbuka saat restart ditandai `interrupted` (perilaku v0.2.0+,
  terdokumentasi di kontrak §Batasan).
- Downtime terkontrol ±15–60 detik (restart service) — by design.
