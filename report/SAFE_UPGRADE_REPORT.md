# Laporan — Safe-Upgrade Installer & Kontrak Upgrade (UAEG v0.3.1)

**Tanggal:** 2026-11-07/08 · **Status: GO** · **Commit:** `upgrade.sh` + `docs/UPGRADE_CONTRACT.md` + `tests/test_upgrade_contract.sh` (tag `v0.3.1`)

---

## 1. Ringkasan eksekutif

Keluhan operator: *"Proses upgrade terlalu ribet"* — prosedur v0.3.0 meminta backup
manual, pencatatan identitas manual, `git clone`/`checkout` manual, menjalankan
installer dengan flag yang tepat, lalu verifikasi manual 5+ perintah.

v0.3.1 mengganti semuanya dengan **satu perintah**:

```bash
cd /opt/uaeg-src && sudo git pull && sudo bash upgrade.sh
```

`upgrade.sh` (Bash murni di root repo) mendeteksi instalasi secara otomatis,
membuat backup konsisten, melakukan upgrade tanpa menyentuh konfigurasi,
**memverifikasi zero-data-loss secara terbukti**, dan me-rollback otomatis bila
gagal. Jaminannya dibakukan sebagai **kontrak yang mengikat**
(`docs/UPGRADE_CONTRACT.md`, klausul U-1..U-12) dan **ditegakkan CI**
(`tests/test_upgrade_contract.sh`, 73 assertion, semua lulus).

## 2. Apa yang dibangun

| Artefak | Isi |
|---|---|
| `upgrade.sh` | Installer upgrade aman: mode `--check`, `--dry-run`, `--rollback`, `--version`, `--source`, `--main`, `--keep-backups`, `--allow-downgrade`; exit code 0/1/2/3/4/5 |
| `docs/UPGRADE_CONTRACT.md` | Kontrak 12 klausul (lihat §3) — binding, berversi, dipetakan ke test |
| `tests/test_upgrade_contract.sh` | 17 kasus / 73 assertion; menjalankan skrip **asli** di direktori terisolasi (tanpa root/systemd, patch test-only identik pola CI installer job) |
| `.github/workflows/ci.yml` | Job baru `upgrade-contract` — suite wajib lulus di setiap push/PR |
| `docs/INSTALL.md`, `README.md` | Dokumentasi satu-perintah + tabel mode |
| `report/VPS_DEPLOYMENT_RUNBOOK.md` | Bagian baru "Upgrade selanjutnya: SATU PERINTAH" |
| Bump versi | `pyproject.toml`, `src/uaeg/__init__.py`, `install.sh` → `0.3.1` |

## 3. Kontrak upgrade (ringkas)

- **U-1** satu perintah, tanpa langkah manual wajib
- **U-2** deteksi instalasi otomatis (unit systemd → symlink → marker); tanpa instalasi → menolak (exit 3)
- **U-3** backup pra-upgrade konsisten: DB via SQLite online backup API + seluruh data dir + config + aplikasi; cek ruang disk dulu
- **U-4** verifikasi pasca-upgrade: versi, service/healthz (atau smoke test), **keys identik (key_id+status)**, `quick_check`, jumlah baris tidak berkurang, config efektif tidak berubah, password dashboard utuh — bukti di `RECORD.json` + `before/after-state.json`
- **U-5** rollback OTOMATIS pada kegagalan tahap mana pun (termasuk Ctrl+C) → exit 2
- **U-6** rollback manual `--rollback` kapan pun dalam window retensi
- **U-7** idempoten + retensi backup (`--keep-backups`, default 5)
- **U-8** non-disruptive: config tidak pernah diubah, keys MCP tak tersentuh, hanya restart terkontrol; dashboard publik tanpa password → ditolak
- **U-9** tanpa kebocoran rahasia: tidak ada password dibuat/dicetak, verifier/hash tidak pernah masuk log/RECORD, backup 0700 hanya di VPS
- **U-10** observable: `RECORD.json` + `install.log` per run; `--check` menampilkan status & riwayat
- **U-11** `--dry-run` rencana tanpa perubahan
- **U-12** anti-downgrade diam-diam (exit 5) + anti git-tree kotor (exit 4)

## 4. Hasil pengujian

### 4.1 Suite kontrak (`tests/test_upgrade_contract.sh`)

**73 PASS / 0 FAIL** — bukti lengkap: `report/evidence/upgrade_contract_suite_v0.3.1.txt`

| Kasus | Skenario | Hasil |
|---|---|---|
| T-01 | Fresh install terisolasi | PASS |
| T-02 | 2 agent key + config operator diubah (port 3954) | PASS |
| T-03 | `--check` deteksi instalasi/versi/path | PASS |
| T-04 | `--dry-run` menampilkan rencana, nol perubahan | PASS |
| T-GUARD | Dashboard publik tanpa password → ditolak rc=1 | PASS |
| T-13 | Git-mode: fetch → tag terbaru (v7.7.7) → upgrade | PASS |
| T-14 | Git tree kotor → exit 4 | PASS |
| T-05 | Upgrade 7.7.7→9.9.9: keys identik, DB ok, config 3954 utuh, RECORD sukses, `app/` dibersihkan | PASS |
| T-06 | Idempoten (re-run aman) | PASS |
| T-07 | Bukti smoke test /healthz | PASS |
| T-08 | Release rusak → **rollback otomatis exit 2**, state pulih, RECORD `rolled-back` | PASS |
| T-09 | Rollback manual: key ke-3 hilang kembali ke 2 (restore data nyata) | PASS |
| T-10 | Tanpa instalasi → exit 3 | PASS |
| T-11 | Secret key & verifier tidak muncul di log/RECORD manapun | PASS |
| T-12 | Retensi `--keep-backups 1` | PASS |
| T-16 | Downgrade diam-diam → exit 5 | PASS |
| T-15 | Skrip standalone (tanpa repo) dry-run menemukan target via ls-remote | PASS |

### 4.2 Regresi suite yang sudah ada

`pytest tests/test_unit_*.py tests/test_dashboard_auth.py` → **81 lulus, rc=0**
dengan versi 0.3.1 (tidak ada regresi). Suite e2e tetap dijalankan CI
seperti sebelumnya.

### 4.3 Bug ditemukan & diperbaiki SELAMA pengujian (bukti ketatnya suite)

1. **`safe_rm` menolak semua path absolut** — pola case `"/"*` mencocokkan
   semua path absolut → upgrade sukses tapi exit 1 & backup `app/` tak
   terhapus. Fix: whitelist path absolut, tolak `""`, `/`, dan path relatif.
2. **`ls -1d glob | wc -l` + `pipefail` + `set -e`** — saat belum ada backup,
   `ls` exit 2 membunuh `--check`. Fix: `find -maxdepth 1` (selalu exit 0).
3. **`svc_stop()` mengembalikan 1 saat `--no-service`** — pola
   `[[ ]] && { …; }` mengembalikan status false → `set -e` membunuh
   **rollback di tengah jalan** (T-08/T-08 lama gagal diam-diam). Fix:
   semua mutator `svc_*` selalu `return 0` + rollback kini diverifikasi
   ulang (smoke serve pada mode tanpa systemd).
4. **Backup data menyalin file dua kali** — `find` rekursif mengeluarkan
   direktori **dan** isinya → `cp` "will not overwrite just-created" setelah
   restore pertama. Fix: salin hanya entri level-atas (`-maxdepth 1`).

Semua bug di atas tertangkap oleh suite kontrak sebelum rilis — inilah alasan
kontraknya harus otomatis.

## 5. Instruksi operator (VPS MicroTix)

**Upgrade berikutnya (setelah v0.3.1 ini terpasang):**

```bash
cd /opt/uaeg-src        # klon repo operator
sudo git pull
sudo bash upgrade.sh
```

**Pemasangan v0.3.1 ini sendiri** (dari v0.3.0, satu kali saja — karena
`upgrade.sh` belum ada di v0.3.0):

```bash
cd /opt/uaeg-src
sudo git fetch --tags
sudo git checkout v0.3.1        # atau: sudo git pull (main)
sudo bash upgrade.sh            # deteksi → backup → upgrade → verifikasi
```

Opsional sebelum eksekusi: `sudo bash upgrade.sh --check` (status) dan
`--dry-run` (rencana). Setelah selesai: `uaeg version` (harus 0.3.1),
`sudo uaeg keys list` (jumlah key sama), `sudo uaeg doctor`.

Rollback bila diperlukan: `sudo bash upgrade.sh --rollback`.

> Catatan downtime: satu restart terkontrol (±15–60 detik); koneksi agent MCP
> otomatis tersambung kembali. Password dashboard, agent key, job, dan
> konfigurasi tidak tersentuh.

## 6. Keputusan

**GO** — seluruh klausul kontrak terpenuhi dan teruji (73/73), regresi nol
(81/81), dokumen & CI terpasang. Risiko tersisa: downtime singkat yang sudah
disebutkan (desain single-host), dan `--skip-smoke` yang melemahkan U-4 bila
dipakai (tidak dipakai di jalur normal).
