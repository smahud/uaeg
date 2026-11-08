# Kontrak Upgrade Aman UAEG (Safe-Upgrade Contract)

> **Status: BINDING.** Dokumen ini adalah kontrak resmi proses upgrade UAEG.
> Setiap klausul (U-1 .. U-16) di bawah ini **dijamin oleh `install.sh`
> (installer/entrypoint terpadu sejak v0.4.0), `upgrade.sh` (bootstrap
> standalone) + `updater.sh` (versioned updater)** dan **ditegakkan oleh test
> suite otomatis** `tests/test_upgrade_contract.sh` (dijalankan di CI pada
> setiap push/PR). Perubahan yang melanggar salah satu klausul wajib dianggap
> bug regresi dan harus gagal di CI.
>
> Versi kontrak: **4** — berlaku sejak UAEG v0.4.0. (Versi 3 sejak v0.3.3;
> versi 2 sejak v0.3.2; versi 1 sejak v0.3.1; lihat “Perubahan kontrak”.)

## Ringkasan satu kalimat

> Upgrade UAEG = **satu perintah** (`sudo bash install.sh`), **file yang sama
> selamanya** (unduh sekali), **otomatis terdeteksi** (install baru bila belum
> ada, upgrade aman bila sudah ada), **ditandatangani + diverifikasi**,
> **dicadangkan**, dan **bisa dibatalkan** — tanpa langkah manual, tanpa
> menghafal flag, tanpa risiko kehilangan data.

## Arsitektur permanen (kontrak v3)

```
operator (unduh SEKALI)            GitHub Releases (per versi)
──────────────────────────         ─────────────────────────────
bash upgrade.sh  (BOOTSTRAP)  ───►  uaeg-manifest.json
  · resolusi rilis target           uaeg-manifest.json.sig  (Ed25519)
  · verifikasi tanda tangan   ◄──┘  uaeg-updater-<v>.sh
  · verifikasi sha256+size          (installer & bootstrap juga sebagai aset)
  · eksekusi updater terverifikasi
        │
        ▼
  updater (mesin safe-upgrade U-1..U-12 — berubah bebas per rilis)
  · staging clone terisolasi → backup → install → verifikasi → rollback
  · cache salinan dirinya di /var/lib/uaeg/upgrade/ (rollback offline)
  · system bootstrap copy: /usr/local/sbin/uaeg-upgrade (best effort)
```

`upgrade.sh` (bootstrap) milik operator dan **tidak pernah ditimpa** oleh
upgrade mana pun; implementasi upgrade terperinci hidup di aset rilis
`uaeg-updater-<version>.sh` yang diverifikasi tanda tangannya sebelum
dieksekusi. Detail lengkap: [docs/BOOTSTRAP.md](./BOOTSTRAP.md).

## Perintah (kontrak v4 — install.sh adalah entrypoint terpadu)

```bash
# PERTAMA (sekali saja — satu file untuk semuanya):
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/install.sh.sha256
sha256sum -c install.sh.sha256

sudo bash install.sh --domain gw.example.com     # instalasi baru (flag lengkap:
                                                 # lihat docs/INSTALL.md)

# SELANJUTNYA — file lokal yang sama, untuk semua rilis mendatang:
sudo bash install.sh                            # safe upgrade ke rilis stabil terbaru

# perintah lain:
sudo bash install.sh --check                    # status instalasi + pembaruan tersedia
sudo bash install.sh --dry-run                   # tampilkan rencana tanpa eksekusi
sudo bash install.sh --rollback                  # pulihkan backup upgrade terakhir
                                                 # (bekerja OFFLINE dari updater ter-cache)
sudo bash install.sh --start|--stop|--restart|--status   # kelola service systemd
sudo bash install.sh --repair                    # pasang ulang in-place; data persisten
                                                 # (kunci, DB, password, TLS) dipertahankan
sudo bash install.sh --version                   # cetak versi installer
sudo bash install.sh --version X.Y.Z             # pin rilis target (install baru / upgrade)
                                                 # downgrade eksplisit tetap memakai
                                                 # --allow-downgrade via upgrade.sh
```

Aturan dispatch (U-16): invokasi **bare** (tanpa flag, atau hanya `--yes` dan/atau
`--version X.Y.Z`) pada instalasi yang sudah ada **selalu** memilih jalur safe
upgrade terverifikasi; flag konfigurasi apa pun (`--port`, `--dashboard-bind`,
`--domain`, ...) memilih jalur in-place (rekonfigurasi, perilaku v0.3.x);
`--repair` adalah bentuk eksplisit dari jalur in-place.

`upgrade.sh` tetap tersedia sebagai aset rilis (bootstrap standalone, kontrak
v3) untuk operator yang sudah mengunduhnya; perilaku dan antarmukanya tidak
berubah. Dokumentasi publik sekarang mengarah ke `install.sh`.

Kode keluar: `0` sukses · `1` error/ditolak (termasuk verifikasi gagal) ·
`2` gagal → sudah di-rollback · `3` instalasi tidak ditemukan (mode
`--check`/`--rollback`) · `4` reserved · `5` downgrade ditolak.

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

### U-13 — Bootstrap permanen (satu unduhan selamanya)
Operator mengunduh `upgrade.sh` (bootstrap) **sekali**; semua upgrade
berikutnya memakai **file lokal yang sama** (`sudo bash upgrade.sh`).
Bootstrap tidak pernah ditimpa oleh proses upgrade (SHA-256 file operator
tidak berubah — diperiksa ulang oleh bootstrap setelah setiap run), dan
tidak bergantung pada implementasi updater rilis tertentu: bootstrap yang
sama harus berhasil men-upgrade lintas rilis yang **mengubah implementasi
updater-nya** melalui antarmuka terdokumentasi
([BOOTSTRAP.md](./BOOTSTRAP.md), interface v1). Sumber target default
adalah **GitHub Releases** (kanal rilis) — **bukan** branch `main` yang
mutable (`--main` tetap tersedia sebagai opt-in eksplisit).

*Ditegakkan oleh:* T-20, T-21, T-22, T-23 (uji kompatibilitas multi-rilis:
bootstrap SHA identik lintas rilis A → B(updater berubah) → gagal-C → retry-D),
T-30.

### U-14 — Autentisitas & integritas artifact terverifikasi
Sebelum mengeksekusi apa pun, bootstrap memverifikasi:

1. **Autentisitas publisher** — manifest rilis (`uaeg-manifest.json`) harus
   bertanda tangan Ed25519 yang valid atas kunci publik yang tertanam di
   bootstrap (`release/keys/`, kebijakan rotasi: BOOTSTRAP.md).
2. **Integritas artifact** — SHA-256 + ukuran updater harus cocok dengan
   manifest yang sudah ditandatangani; manifest harus well-formed (path
   updater aman — tanpa `/`/`..`, versi sesuai target, interface didukung).

Manifest yang di-tamper, updater yang di-tamper, tanda tangan kunci asing,
interface terlalu baru, dan manifest yang hilang **semua ditolak sebelum
sesuatu dieksekusi** (exit 1, instalasi tidak tersentuh). Checksum
`.sha256` tetap disediakan untuk verifikasi manusia, tetapi **checksum
tidak dianggap bukti identitas publisher** — hanya tanda tangan yang
 menjadi bukti. Rilis dipublikasikan CI yang memverifikasi ulang manifest
dan tanda tangan sebelum aset diunggah (`.github/workflows/release.yml`).

*Ditegakkan oleh:* T-24 (manifest tampered), T-25 (updater tampered),
T-26 (kunci asing), T-27 (interface baru), T-28 (manifest hilang),
dan `release/verify_release.py` di CI.

### U-15 — Konfirmasi interaktif dibaca dari keyboard operator

Pertanyaan konfirmasi y/N (upgrade, rollback, dan peringatan OS pada
installer) **wajib dibaca dari keyboard operator, bukan dari stdin skrip**.
stdin bisa berupa hal lain ketika prompt tampil: skrip itu sendiri
(`curl ... | bash`), `/dev/null`/stdin terputus (sudo/CI), atau pipe otomasi.
Membaca stdin pada bentuk-bentuk itu membuat jawaban "y" yang diketik
operator tidak pernah sampai ke skrip — persis insiden v0.3.4
(2026-11-08): operator menjawab `y` dan updater salah melapor
`cancelled by the operator` (atau mati diam-diam karena `set -e` + EOF,
tanpa pesan sama sekali).

Aturan resolusi input (`confirm_ask()` di `updater.sh`/`install.sh`):

1. stdin adalah terminal → baca stdin (run interaktif normal).
2. ada terminal pengendali → baca `/dev/tty` (bentuk `curl|bash`, stdin
   terputus, sudo) — prompt tetap TAMPIL di terminal sungguhan.
3. tidak ada terminal sama sekali → terima satu baris terpipah (otomasi:
   `printf 'y\n' | ...`), atau laporkan kegagalan dengan jujur.

Pelaporan penolakan harus menyatakan alasan yang BENAR: jawaban `n`/baris
kosong → "cancelled by the operator" / "aborted"; tidak ada jawaban sama
sekali (EOF/tanpa terminal) → pesan eksplisit "no confirmation was
received" + arahan ke `--yes` untuk run unattended. `y`/`yes` (huruf besar
atau kecil, dengan atau tanpa spasi/CR) diterima; baris kosong berarti
tidak (default `[y/N]`).

*Ditegakkan oleh:* T-32 (stdin terputus + y di terminal), T-33 (bentuk
curl|bash), T-34 (jawaban n), T-35 (tanpa terminal + tanpa jawaban),
T-36 (otomasi terpipah), T-37 (konfirmasi rollback), T-38 (probe helper
byte-exact dari kedua skrip).

### U-16 — Entrypoint terpadu (kontrak v4): install.sh meng-instal DAN meng-upgrade

`install.sh` adalah satu-satunya file publik yang perlu diunduh operator
(sejak v0.4.0). Perilaku wajib:

1. **Dispatch tanpa argumen**: belum terpasang → instalasi baru; sudah
   terpasang + invokasi bare → safe upgrade ke rilis stabil terbaru **melalui
   rantai verifikasi yang sama dengan bootstrap** (manifest bertanda tangan
   Ed25519 diverifikasi terhadap keyring ter-embed di dalam install.sh,
   kemudian sha256+size updater diverifikasi terhadap manifest, baru
   updater dieksekusi). Interface bootstrap↔updater (v1) tidak berubah.
2. **Perintah layanan**: `--start` / `--stop` / `--restart` mengelola unit
   systemd `uaeg`; `--status` menampilkan status service. Tanpa systemd/unit
   → penolakan jelas (rc 1), tidak pernah mengubah apa pun.
3. **`--check`** read-only (status instalasi + target tersedia; exit 3 bila
   belum terpasang), **`--dry-run`** menampilkan rencana tanpa mengubah apa pun
   (rencana install baru bila belum terpasang), **`--rollback [ID]`**
   memulihkan backup upgrade (preferensi updater ter-cache; offline),
   **`--repair`** menjalankan ulang installer in-place dengan data persisten
   dan konfigurasi dipertahankan.
4. **Ambiguitas `--version` diselesaikan**: tanpa argumen → mencetak versi
   file; dengan `X.Y.Z` → pin rilis target (tag instalasi baru ATAU target
   safe upgrade).
5. **Anti-loop**: dipanggil oleh updater (lingkungan `UAEG_BOOTSTRAP=1`) atau
   dengan flag penargetan (`--install-dir` dst.) → SELALU jalur installer
   in-place, tidak pernah dispatch ulang ke safe upgrade.
6. Semua flag first-install v0.3.x tetap berfungsi dan semantiknya tidak
   berubah; `upgrade.sh` tetap berfungsi sebagai bootstrap standalone.

*Ditegakkan oleh:* T-39 (help/version), T-40 (bare → upgrade terverifikasi;
RECORD bootstrap_file = install.sh operator), T-41 (reuse multi-rilis +
updater berubah + SHA file identik), T-42 (pin --version), T-43/T-44/T-45
(manifest tampered / kunci asing / updater tampered → ditolak), T-46
(interface baru → tolak + instruksi refresh install.sh), T-47 (--check /
--dry-run), T-48 (rilis rusak → rollback otomatis rc 2), T-49 (--rollback
updater ter-cache), T-50 (perintah service tanpa systemd → rc 1), T-51
(--repair mempertahankan data + menerapkan flag), T-52 (baseline v0.3.5 →
upgrade terverifikasi, kunci identik).

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

Kontrak ini berversi (`contract_version` di `RECORD.json`; RECORD tetap
mencatat `3` untuk kompatibilitas pembaca lama — klausul v4 tidak mengubah
format RECORD). Perubahan klausul harus: (1) menaikkan versi kontrak,
(2) memperbarui test suite di commit yang sama, (3) dicatat di `report/`.

### v4 (UAEG v0.4.0)

- **U-16 ditambahkan**: `install.sh` menjadi entrypoint terpadu — install
  baru, safe upgrade terverifikasi (bootstrap ter-embed, keyring + verifikasi
  sama), perintah layanan (`--start/--stop/--restart/--status`), `--check`,
  `--dry-run`, `--rollback`, `--repair`, dan `--version` tanpa argumen
  (cetak versi) vs `--version X.Y.Z` (pin target). Operator mengunduh SATU
  file. Semua flag first-install v0.3.x tetap berfungsi; jalur safe upgrade
  dipilih oleh invokasi bare pada instalasi yang ada.
- **Rotasi kunci penandatanganan 2 → 3** (`uaeg-release-3`, bootstrap
  keyring v3): kunci privat `uaeg-release-2` tidak lagi tersedia bagi
  publisher. Keyring (install.sh dan upgrade.sh) mempercayai KETIGA kunci
  sehingga manifest rilis historis tetap terverifikasi. Bootstrap lama
  (keyring 1+2, termasuk operator v0.3.4/v0.3.5) akan MENOLAK manifest
  v0.4.0+ dengan pesan rotasi eksplisit — pemulihannya satu kali unduh
  ulang `install.sh` (prosedur istimewa, BOOTSTRAP.md). Operator v0.3.3
  (keyring hanya kunci 1) memang sudah memerlukan refresh sejak v0.3.4.
- **updater.sh**: penambahan env fallback yang kompatibel (`UAEG_REPO_URL`,
`UAEG_BACKUPS_DIR`) di samping flag yang sudah ada — flag > env > default;
perilaku default tidak berubah.
- `upgrade.sh` tetap dipublikasikan sebagai aset (bootstrap standalone
  kontrak v3) dengan perubahan HANYA keyring + identitas (BOOTSTRAP_VERSION
  3); antarmuka dan perilaku tidak berubah.

### v3.1 (UAEG v0.3.5)

- **U-15 ditambahkan** setelah insiden v0.3.4 (2026-11-08): konfirmasi y/N
  dibaca dari stdin skrip, sehingga jawaban "y" operator tidak pernah
  terbaca saat stdin bukan keyboard (terputus, `curl|bash`, sudo/CI) dan
  kegagalan itu dikelirukan sebagai "cancelled by the operator" — atau mati
  diam-diam tanpa pesan. Klarifikasi semantik (bukan perubahan antarmuka):
  semua flag, exit code, dan format RECORD tetap; `contract_version` di
  RECORD.json tetap `3`. Diregresi permanen oleh T-32..T-38.
- Bentuk `bash -s` / skrip-via-stdin (curl|bash) tidak lagi crash
  `BASH_SOURCE unbound` pada updater.

### v3 (UAEG v0.3.3)

- **Arsitektur permanen (U-13):** `upgrade.sh` menjadi *bootstrap* stabil
  milik operator (unduh sekali, dipakai selamanya); mesin safe-upgrade
  pindah ke aset rilis `uaeg-updater-<version>.sh` yang diperbarui bebas
  per rilis melalui antarmuka terdokumentasi (BOOTSTRAP.md interface v1).
- **Autentisitas (U-14):** manifest rilis ditandatangani Ed25519
  (kunci `uaeg-release-1`); bootstrap menolak manifest/updater yang tidak
  terverifikasi. Aset rilis kini berjumlah delapan (install/upgrade/updater
  + sidecar + manifest + signature).
- **Operasi offline:** updater menyimpan salinan terverifikasi di
  `/var/lib/uaeg/upgrade/` — `--rollback` (dan `--check`) tetap berfungsi
  tanpa jaringan setelah upgrade pertama yang sukses.
- **Salinan sistem:** `install.sh`/updater memasang bootstrap kenyamanan di
  `/usr/local/sbin/uaeg-upgrade` (best effort; file operator tidak pernah
  disentuh).
- Semua flag v0.3.2 tetap berfungsi (`--check`, `--dry-run`, `--version`,
  `--rollback`, `--source`, `--main`, `--repo`, `--installer`, dst.).

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
