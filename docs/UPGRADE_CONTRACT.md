# Kontrak Upgrade Aman UAEG (Safe-Upgrade Contract)

> **Status: BINDING.** Dokumen ini adalah kontrak resmi proses upgrade UAEG.
> Setiap klausul (U-1 .. U-12) di bawah ini **dijamin oleh `upgrade.sh`** dan
> **ditegakkan oleh test suite otomatis** `tests/test_upgrade_contract.sh`
> (dijalankan di CI pada setiap push/PR). Perubahan pada `upgrade.sh` yang
> melanggar salah satu klausul wajib dianggap bug regresi dan harus gagal di CI.
>
> Versi kontrak: **2** — berlaku sejak UAEG v0.3.2. (Versi 1 berlaku sejak
> v0.3.1; lihat “Perubahan kontrak” di bawah.)

## Ringkasan satu kalimat

> Upgrade UAEG = **satu perintah** (`sudo bash upgrade.sh`), **otomatis
> terdeteksi**, **dicadangkan**, **diverifikasi**, dan **bisa dibatalkan** —
> tanpa langkah manual, tanpa menghafal flag, tanpa risiko kehilangan data.

## Perintah

```bash
# di VPS yang sudah terinstal — dari dalam klon repo operator ATAUPUN dari
# direktori mana pun (clone operator Anda TIDAK pernah disentuh):
sudo bash upgrade.sh

# tanpa klon repo (unduh skrip saja, verifikasi checksum dulu — lihat INSTALL.md):
sudo bash upgrade.sh

# mode lain:
sudo bash upgrade.sh --check        # status instalasi (read-only)
sudo bash upgrade.sh --dry-run      # tampilkan rencana tanpa eksekusi
sudo bash upgrade.sh --rollback     # pulihkan backup upgrade terakhir
sudo bash upgrade.sh --version X.Y.Z --allow-downgrade   # turun versi (eksplisit)
```

Kode keluar: `0` sukses · `1` error/ditolak · `2` gagal → sudah di-rollback ·
`3` instalasi tidak ditemukan · `4` reserved (tidak dipakai sejak kontrak v2) ·
`5` downgrade ditolak.

---

## Klausul

### U-1 — Satu perintah
`sudo bash upgrade.sh` cukup untuk menyelesaikan seluruh upgrade: deteksi,
backup, pengambilan source, upgrade in-place, restart terkontrol, verifikasi,
dan pelaporan. Operator tidak wajib melakukan langkah manual lain sebelum atau
sesudahnya (verifikasi tambahan tetap disarankan, tapi tidak wajib).

*Ditegakkan oleh:* T-01, T-05, T-13.

### U-2 — Deteksi instalasi otomatis
Script mendeteksi sendiri lokasi instalasi tanpa parameter: direktori install
(dari unit systemd `ExecStart=`, atau symlink `/usr/local/bin/uaeg`, atau
marker `.uaeg-installed`), data dir (`Environment=UAEG_DATA_DIR`), file
konfigurasi (`Environment=UAEG_CONFIG`), nama service, dan versi terpasang.
Instalasi dengan path khusus tetap didukung lewat flag `--install-dir`,
`--data-dir`, `--config-dir`. Jika tidak ada instalasi → script **menolak**
(exit 3) dan mengarahkan ke `install.sh`; upgrade.sh tidak pernah
meng-instal dari nol.

*Ditegakkan oleh:* T-03, T-10, T-13.

### U-3 — Zero data loss (backup pra-upgrade)
Sebelum satu byte pun diubah, script membuat backup lengkap di
`/var/backups/uaeg/upgrade-<timestamp>/`:

- **database** `uaeg.db` melalui *SQLite online backup API* — snapshot
  transaksional yang konsisten meskipun service masih berjalan (WAL ikut
  ter-consolidate; file `-wal`/`-shm` tidak disalin mentah);
- **seluruh data dir** (job output, artefak session, sertifikat TLS, hash
  password dashboard);
- **konfigurasi** `/etc/uaeg`;
- **aplikasi** (`/opt/uaeg` termasuk venv) untuk rollback penuh.

Tabel `keys` (verifier agent MCP) **tidak pernah ditulis** oleh proses upgrade.
Ruang disk diperiksa sebelum backup; jika tidak cukup → upgrade ditolak
sebelum mengubah apa pun.

*Ditegakkan oleh:* T-05, T-08, T-09.

### U-4 — Verifikasi pasca-upgrade (bukti, bukan asumsi)
Setelah upgrade, script **membuktikan** (dan mencatat di `RECORD.json`):

1. versi terpasang == versi target;
2. service systemd aktif dan `/healthz` menjawab (mode tanpa systemd:
   *smoke test* — server dinyalakan sesaat lalu dimatikan);
3. **agent keys identik** — himpunan `(key_id, status)` sebelum == sesudah;
4. integritas database `PRAGMA quick_check` = `ok`;
5. jumlah baris `jobs`/`sessions`/`exec_logs` tidak berkurang, `keys` sama
   persis;
6. **konfigurasi efektif tidak berubah** (port, bind, TLS, public_host);
7. password dashboard tetap terkonfigurasi (jika sebelumnya ada) dan halaman
   dashboard menjawab 200/302.

State sebelum/sesudah disimpan sebagai `before-state.json` /
`after-state.json` di direktori backup — bukti audit yang bisa dibandingkan
ulang kapan pun. Gagal salah satu check = upgrade dinyatakan gagal (→ U-5).

*Ditegakkan oleh:* T-05, T-07, T-08.

### U-5 — Rollback otomatis
Kegagalan pada **tahap mana pun** setelah backup dibuat — installer error,
health check gagal, verifikasi U-4 gagal, bahkan interrupt (Ctrl+C) — membuat
script otomatis memulihkan state pra-upgrade: aplikasi, data, dan konfigurasi
dikembalikan, service dinyalakan lagi, lalu dipverifikasi ulang. Exit code `2`
berarti "gagal dan sudah kembali ke kondisi semula". Backup tetap disimpan
sebagai bukti.

*Ditegakkan oleh:* T-08.

### U-6 — Rollback manual
`sudo bash upgrade.sh --rollback` memulihkan data + konfigurasi (dan aplikasi,
jika masih tersimpan) dari backup upgrade mana pun dalam window retensi.
Script memperingatkan bahwa perubahan yang dibuat *setelah* backup tersebut
(key/job baru) akan hilang, dan meminta konfirmasi.

*Ditegakkan oleh:* T-09.

### U-7 — Idempoten
Menjalankan `upgrade.sh` berulang kali aman: tidak ada efek ganda, tidak ada
backup menimpa, tidak ada migrasi ganda. Upgrade ke versi yang sama dianggap
*re-install/repair* yang sah. Retensi backup menjaga `--keep-backups`
(default 5) direktori terbaru.

*Ditegakkan oleh:* T-06, T-12.

### U-8 — Tidak mengganggu (non-disruptive)
- Upgrade **tidak pernah mengubah** konfigurasi: tidak ada flag bind/TLS/port
  yang dikirim ke installer — nilai lama dipertahankan apa adanya (FR-003).
- **Agent key MCP tidak tersentuh** — koneksi agent MicroTix tetap valid;
  satu-satunya dampak adalah restart terkontrol service UAEG (downtime
  ± 15–60 detik), dan agent HTTP reconnect otomatis pada request berikutnya.
- Layanan lain di VPS tidak disentuh. Job background yang terlepas (detached)
  tetap berjalan sesuai desain.
- Jika dashboard terikat publik tetapi password belum diset → upgrade
  **menolak jalan** (fail-safe), bukan membiarkan dashboard tanpa auth.

*Ditegakkan oleh:* T-05 (config & keys identik), T-GUARD (refusal publik).

### U-9 — Rahasia tetap rahasia
Script tidak pernah membuat, mencetak, atau menyimpan password; `uaeg pass`
tetap interaktif-only dan tidak pernah dipanggil oleh upgrade. Log, `RECORD.json`,
dan state JSON **tidak memuat** verifier agent key maupun hash password —
hanya `key_id`/`name`/`prefix`/`status` (identifier publik). Backup hanya
tinggal di VPS dengan permission `0700`/`0600` dan **tidak pernah**
di-commit ke repository.

*Ditegakkan oleh:* T-11.

### U-10 — Observable (setiap run tercatat)
Setiap eksekusi menghasilkan `RECORD.json` (mode, from/to version, ref+SHA
git, hasil, waktu) dan `install.log` (output installer). `--check`
menampilkan status instalasi, riwayat upgrade terakhir, jumlah backup, dan
target yang tersedia. Output selalu diakhiri status jelas: sukses / gagal +
apa yang sudah dilakukan + langkah berikutnya.

*Ditegakkan oleh:* T-03, T-05.

### U-11 — Tanpa tebak-tebakan (dry-run & check)
`--dry-run` menampilkan rencana lengkap (dari → ke, apa yang dibackup,
downtime) **tanpa mengubah apa pun**; `--check` read-only. Operator bisa
selalu tahu apa yang akan terjadi sebelum terjadi.

*Ditegakkan oleh:* T-04.

### U-12 — Kompatibilitas & anti-downgrade diam-diam
Upgrade didukung dari instalasi ≥ v0.2.0 (schema database kompatibel
mundur; check `schema_version` menolak DB yang lebih baru dari aplikasi).
Downgrade **ditolak** kecuali eksplisit `--allow-downgrade` (mencegah
"upgrade" yang tanpa sadar memasang versi lebih lama).

**Kontrak v2:** source yang terpasang SELALU diambil dari *staging clone*
terisolasi (`mktemp`, mode 0700) pada tag/commit immutable yang dicatat di
`RECORD.json` (`target_ref` + `target_git_sha` + `repo_url`), lalu staging
dihapus. Clone git operator **tidak pernah dibaca isinya atau diubah** —
tidak ada fetch-into-clone, checkout, reset, atau clean. Akibatnya: tree
kotor, file untracked (termasuk `upgrade.sh` yang terunduh ke dalam
klon), detached HEAD, atau clone shallow **tidak dapat menghalangi
upgrade** dan tidak pernah dirusak. Kegagalan selalu dilaporkan dengan
kategori yang akurat (tag tidak ada vs clone/jaringan gagal) — kegagalan
checkout tidak boleh pernah dilaporkan sebagai "tag tidak ditemukan"
(insiden v0.3.1; lihat `report/ROOT_CAUSE_ANALYSIS.md`). Exit code `4`
(versi 1: "git tree kotor") kini reserved.

*Ditegakkan oleh:* T-13, T-14, T-17, T-18, T-19.

---

## Struktur direktori backup

```
/var/backups/uaeg/
└── upgrade-20260101-120000/
    ├── RECORD.json          # catatan mesin: versi, hasil, verifikasi
    ├── before-state.json    # state pra-upgrade  (bukti audit)
    ├── after-state.json     # state pasca-upgrade (bukti audit)
    ├── install.log          # output lengkap installer
    ├── smoke-serve.log      # log smoke test (mode tanpa systemd)
    ├── app/                 # aplikasi pra-upgrade (dihapus setelah sukses terverifikasi)
    ├── config/              # salinan /etc/uaeg
    └── data/                # snapshot DB konsisten + seluruh data dir
```

Setelah upgrade **sukses terverifikasi**, `app/` dihapus dari backup
(menghemat disk; data + config tetap tersedia untuk `--rollback`).
Pemulihan APLIKASI dari backup yang `app/`-nya sudah dihapus dilakukan
dengan: `install.sh` dari tag `v<from_version>` yang immutable (versi
tersebut tercatat di `RECORD.json` backup) + `--rollback` untuk data &
config; `--rollback` mencetak petunjuk pemulihan ini saat `app/` tidak
tersimpan. Retensi default 5 backup terbaru (`--keep-backups N`, minimum
efektif 1 — retensi tidak pernah menghapus satu-satunya recovery point).

## Batasan yang disadari (bukan pelanggaran kontrak)

- Downtime tetap ada (± 15–60 detik) karena aplikasi diganti in-place dan
  service harus direstart; ini desain arsitektur single-host UAEG.
- PTY session yang terbuka saat restart ditandai `interrupted` oleh aplikasi
  (perilaku v0.2.0+, bukan akibat upgrade tooling).
- `--skip-smoke` melemahkan U-4 poin 2 (hanya versi+data yang diverifikasi) —
  hanya untuk lingkungan khusus yang tidak bisa menjalankan server sesaat.

## Perubahan kontrak

Kontrak ini berversi (`contract_version` di `RECORD.json`). Perubahan klausul
harus: (1) menaikkan versi kontrak, (2) memperbarui test suite di commit yang
sama, (3) dicatat di `report/`.

### v2 (UAEG v0.3.2)

- **U-12 dirumuskan ulang** setelah insiden upgrade v0.3.1 di VPS MicroTix
  (`git checkout <tag>` di dalam klon operator bertabrakan dengan
  `upgrade.sh` untracked yang terunduh, dan kegagalan itu dikelirukan sebagai
  "tag tidak ditemukan"). Source rilis kini selalu di-*stage* di direktori
  terisolasi; klon operator tidak pernah disentuh; exit 4 menjadi reserved.
- **U-4 diperketat**: perbandingan "konfigurasi efektif" kini menormalkan
  kunci yang absen ke nilai bawaan aplikasi (config lama tanpa kunci baru
  tidak lagi memicu rollback palsu).
- `--keep-backups` divalidasi (angka) dan di-clamp minimum 1.
- `RECORD.json` kini mencatat `repo_url` yang benar-benar dipakai.

### v1 (UAEG v0.3.1)
