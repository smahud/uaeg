# UAEG — Deployment & Pemulihan yang Telah Dibuktikan (Live, 2026-11-10)

| | |
|---|---|
| **Isi** | Langkah update, rollback, dan reconnect yang benar-benar dieksekusi pada VPS produksi, beserta runbook pemulihan |
| **Jalur yang dipakai** | `updater.sh --source` (mekanisme resmi teruji kontrak U-1/T-05) + transient systemd scope |
| **Hasil akhir** | Live: **v0.4.1 → v0.4.1+audit1** (commit `8369e6e`); key, config, password dashboard, data, dan TLS tidak berubah; rollback otomatis terbukti pada satu kegagalan nyata |
| **Terkait** | [UAEG_LIVE_TEST_EVIDENCE.md](UAEG_LIVE_TEST_EVIDENCE.md) · [UAEG_LIVE_FINDINGS_AND_FIXES.md](UAEG_LIVE_FINDINGS_AND_FIXES.md) |

---

## 1. Pra-kondisi keamanan (dilakukan sebelum menyentuh service)

1. **Identitas sumber terpaku hash**: branch `audit/live-2026-11-10-deploy`
   dipush ke GitHub; di VPS di-clone dan `git rev-parse HEAD` diverifikasi
   sama dengan `8369e6e8ce7c19d7d6a15c8a31360eb7b658279f` SEBELUM updater
   dijalankan; penanda versi `0.4.1+audit1` dicek di `pyproject.toml`.
2. **Before-state direkam**: versi healthz, MainPID, SHA-256
   `config.json`/`uaeg.db`/`dashboard-pass.json`, daftar key, hitungan tabel DB,
   jumlah backup, ruang disk (114 G bebas), akses keluar ke GitHub (200).
3. **Probe kemampuan**: `systemd-run --scope true` OK; verifikasi bahwa
   perintah UAEG berjalan di cgroup `uaeg.service` (premis perlindungan F4).
4. **Jalur pemulihan tersedia sebelum mulai**: 5 arsip backup lama +
   updater otomatis membuat backup baru (DB via SQLite online-backup API —
   konsisten WAL; config; app) sebelum mutasi apa pun; auto-rollback pada
   kegagalan verifikasi; `install.sh --rollback` manual dari cache updater.

## 2. Eksekusi update live (yang benar-benar dijalankan)

```
# 1) di VPS (via shell_exec UAEG):
git clone --depth 1 -b audit/live-2026-11-10-deploy https://github.com/smahud/uaeg.git /tmp/uaeg-patch-src
git -C /tmp/uaeg-patch-src rev-parse HEAD          # == 8369e6e... (diverifikasi)

# 2) updater resmi, dibungkus transient scope AGAR BERTAHAN dari service stop
#    (v0.4.1 yang terpasang belum punya guard F4 — pembungkus manual adalah
#    mekanisme yang sama yang diotomatisasi patch):
shell_exec(background=true, command=
  "systemd-run --scope --quiet --unit=uaeg-livepatch \
     bash /tmp/uaeg-patch-src/updater.sh --source /tmp/uaeg-patch-src --yes")
```

Alur yang dijalankan updater (terekam di `install.log` + journal):
`read_state` → backup `/var/backups/uaeg/upgrade-<ts>/` → `systemctl stop uaeg`
→ deploy app + venv (pinned) → **config dipertahankan apa adanya** → unit file
→ `systemctl start uaeg` → verifikasi (healthz, versi, jumlah key identik,
integritas DB, schema) → `RECORD.json` → cache updater.

## 3. Dua eksekusi, dua hasil — keduanya bernilai bukti

### 3.1 Percobaan 1 (04:13 WIB) — GAGAL terkontrol → auto-rollback SEMPURNA

Penanda versi pertama (`0.4.1.zaudit1`) ternyata **tidak valid PEP 440**; pip
menolak pembuatan metadata (`project.version must be pep440`). Apa yang
terjadi:

- updater (dalam scope `uaeg-livepatch`) **selamat dari `systemctl stop`** —
  bukti langsung mekanisme F4 bekerja pada kode yang belum berpatch sekalipun
  (dibungkus manual);
- `WARNING: installation failed - rolling back to the previous working state`;
  `restored previous application files`; service restart;
- `RECORD.json`: `result="rolled-back"`, `rollback_verified=true`;
- healthz kembali `v0.4.1` ±20 detik setelah kegagalan; **key yang sama
  langsung bisa reconnect** (seluruh skrip polling agent berjalan tanpa
  interupsi); data/config tidak tersentuh (hash identik).

**Ini bukti live pertama di repo ini bahwa jalur auto-rollback bekerja pada
kegagalan upgrade nyata di produksi** — bukan hanya di lab.

### 3.2 Percobaan 2 (04:17 WIB) — BERHASIL

Versi diperbaiki menjadi `0.4.1+audit1` (PEP 440 valid; urutan `sort -V`
dipilih agar `0.4.1 < 0.4.1+audit1 < 0.4.2` — upgrade resmi berikutnya tetap
lancar). Update sehat:

- `RECORD.json`: `result="success"`, `from_version=0.4.1`,
  `to_version=0.4.1+audit1`, `target_git_sha=8369e6e`;
- healthz: `{"status":"ok","version":"0.4.1+audit1"}`;
- ringkasan installer kini benar: **`TLS mode : certbot`** (bukti F3);
- rollback backup app dihapus otomatis setelah verifikasi (`upgrade verified -
  removing rollback backup`), arsip backup data+config tetap utuh di
  `/var/backups/uaeg/upgrade-20261110-041724/`.

## 4. Verifikasi pascapatch (ringkasan; detail di TEST_EVIDENCE)

| Aspek | Hasil |
|---|---|
| Re-auth + `server_info` (key SAMA) | `0.4.1+audit1`, `exec_user=root`, `k-837c0f289a4a` |
| `shell_exec` identitas | `0 / root / microtix` |
| F5 live | job di `uaeg-job-<id>.scope`; journal "transient systemd scopes"; **AT-11**: job selesai `exit=0` melewati `systemctl restart` |
| F6 live | 0 entri baru "standalone SSE writer" setelah 2 disconnect (basis pra-patch: 9) |
| F2 live | doctor: `MCP listener: 0.0.0.0:3902 (probed via 127.0.0.1)` + peringatan ALL-interfaces; "all checks passed" |
| F1 live | pemanggilan sah tetap berhasil; regresi lab memastikan penolakan bila identitas tak teresolusi |
| Data | key 1 aktif + 1 revoked (identik); `config.json` & `dashboard-pass.json` SHA-256 **identik** pra/pasca; DB utuh (jobs bertambah dari aktivitas audit saja) |
| TLS | sertifikat sama (Let's Encrypt, exp 2027-02-05) |
| Dashboard | `/login` 200; API anon 401 |
| Zombie | 0 |
| Cleanup | `/tmp/uaeg-patch-src` dihapus; sesi PTY uji ditutup |

## 5. Runbook pemulihan (terbukti atau tersedia)

| Skenario | Prosedur | Status bukti |
|---|---|---|
| Upgrade gagal di tengah | updater auto-rollback (backup pre-upgrade → restore app/config → restart → verifikasi) | **TERBUKTI LIVE** (§3.1) |
| Rollback manual setelah upgrade "sukses" tapi tidak diinginkan | `sudo bash install.sh --rollback [ID]` (offline, dari cache updater + `/var/backups/uaeg/upgrade-*`) | Tersedia (kontrak T-09/T-37 lab 290/290; tidak diperlukan pada live) |
| Service tidak mau naik pasca-update | `systemctl status uaeg` + `journalctl -u uaeg`; config terjaga (updater tak pernah menimpa konfigurasi); restore data via arsip backup | Runbook dokumen repo; tidak terjadi |
| Kehilangan akses agent sama sekali | SSH/console operator (di luar jalur UAEG); `sudo bash install.sh --rollback` | Jalur darurat tetap ada (agent tidak pernah menutup SSH) |
| Key dikompromikan | dashboard/CLI revoke → containment otomatis menghentikan job+PTY milik key (lab e2e) | Lab |
| Versi penanda audit ingin dinaik-turunkan | `sort -V`: `0.4.1 < 0.4.1+audit1 < 0.4.2` — rilis resmi berikutnya (0.4.2/0.4.3) otomatis dianggap lebih baru | Diverifikasi empiris |

## 6. Downtime yang terjadi

| Peristiwa | Durasi service down |
|---|---|
| Percobaan 1 (gagal + rollback) | ±15–20 detik (04:13:45–04:14:05 sekitar) |
| Percobaan 2 (sukses) | ±5–8 detik (04:17:2x) |
| AT-11 restart | ±2 detik (04:27:5x) |

Seluruh jendela itu, dashboard `/login` kembali 200 dan MCP menerima key lama
segera setelah service naik.
