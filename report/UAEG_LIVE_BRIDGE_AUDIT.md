# UAEG — Live Bridge Audit (Audit Jembatan Live)

| | |
|---|---|
| **Judul** | Audit koneksi live agent → UAEG → VPS, inventaris kondisi nyata, dan perbandingan versi |
| **Tanggal eksekusi** | 2026-11-10, 03:14 – 04:28 WIB (UTC+7) |
| **Auditor** | Agent pelaksana (terhubung via MCP Streamable HTTP + Bearer key resmi) |
| **Target live** | `https://host.zerotier.my.id:3902/mcp` (MCP) · `https://host.zerotier.my.id:3903` (dashboard) |
| **Baseline instruksi** | healthz 200 / v0.4.1; /mcp tanpa bearer → 401; /login 200; /api/overview anon → 401 (pemeriksaan 2026-11-10 ±02:20 WIB) |
| **Status akhir live** | **UAEG v0.4.1+audit1** (patch set audit 2026-11-10) — sehat, key lama tetap berfungsi |
| **Dokumen terkait** | [UAEG_LIVE_FINDINGS_AND_FIXES.md](UAEG_LIVE_FINDINGS_AND_FIXES.md) · [UAEG_LIVE_TEST_EVIDENCE.md](UAEG_LIVE_TEST_EVIDENCE.md) · [UAEG_DEPLOYMENT_AND_RECOVERY.md](UAEG_DEPLOYMENT_AND_RECOVERY.md) · [UAEG_LIVE_RELEASE_DECISION.md](UAEG_LIVE_RELEASE_DECISION.md) |

---

## 1. Ringkasan eksekutif

1. **Koneksi live terbukti nyata.** Agent berhasil melakukan handshake MCP
   (initialize, protokol dinegosiasi `2025-11-25`), menemukan 13 tool, memanggil
   `server_info`, lalu mengeksekusi perintah di VPS melalui `shell_exec` —
   hostname VPS `microtix`, user efektif `root` (UID 0), Debian 13. Output dan
   exit code terpropagasi akurat. Hostname/UID ini **berbeda** dari lingkungan
   cloud agent, membuktikan eksekusi benar-benar terjadi di VPS.
2. **Seluruh kapabilitas bridge lulus uji live**: perintah sinkron (marker,
   quoting, unicode, env, cwd, exit code, timeout, pembatasan output), file
   round-trip di direktori uji khusus, background job (status/wait/output/
   cancel/list + persistensi lintas koneksi), PTY (open/input/output/resize/
   close + grace window), dan reconnect dengan key yang sama.
3. **Identitas instalasi terbukti dengan hash**, bukan sekadar string versi:
   seluruh 21 file `src/uaeg/*.py` di VPS identik (SHA-256) dengan tag `v0.4.1`
   sekaligus `main` @ `478907c`; `install.sh` dan `upgrade.sh` cocok dengan
   **v0.4.1 tetapi bukan main** — main memuat dua perbaikan installer pasca-rilis
   yang belum dirilis/deploy.
4. **Ditemukan 7 temuan (F1–F7) + 1 temuan harness CI (F8)**; semuanya
   diperbaiki pada branch `audit/live-2026-11-10` (+18 tes regresi; suite penuh
   226 lulus; kontrak upgrade 290/290 lulus).
5. **Patch diterapkan pada VPS live melalui jalur resmi** `updater.sh --source`
   (dibungkus transient systemd scope) → versi live kini **0.4.1+audit1**
   (commit `8369e6e`), dengan bukti pascapatch lengkap — termasuk bukti live
   bahwa job background kini **bertahan dari `systemctl restart uaeg`** dan
   bahwa rollback otomatis updater bekerja pada kegagalan nyata.
6. **Rilis resmi v0.4.2 BLOCKED**: kunci penandatanganan rilis (private key)
   tidak tersedia bagi agent; kandidat rilis disiapkan lewat PR #1.

## 2. Baseline dan verifikasi ulang

Pemeriksaan tanpa kredensial oleh agent pada 2026-11-10 ±03:10 WIB (TLS
divalidasi penuh, tanpa `curl -k`):

| Pemeriksaan | Hasil instruksi (±02:20) | Hasil agent (±03:10) |
|---|---|---|
| `GET :3902/healthz` | HTTP 200, status ok, version 0.4.1 | HTTP 200, `{"status":"ok","service":"UAEG","version":"0.4.1",...}` |
| `POST :3902/mcp` tanpa bearer | HTTP 401 | HTTP 401, JSON-RPC error `-32001` "missing bearer token" + `WWW-Authenticate` |
| `GET :3903/login` | HTTP 200 | HTTP 200 |
| `GET :3903/api/overview` tanpa sesi | HTTP 401 | HTTP 401 `{"error":"authentication required"}` |

Sertifikat (kedua port): Let's Encrypt `CN=host.zerotier.my.id`,
SAN `DNS:host.zerotier.my.id`, berlaku 2026-11-07 s.d. 2027-02-05, issuer
`C=US,O=Let's Encrypt,CN=YE2`. Konsisten dengan mode `certbot` pada config.

## 3. Koneksi live (bukti inti)

- **Handshake & discovery**: initialize sukses; server `UAEG` / `0.4.1`
  (pembanding protokol `2025-11-25`); 13 tool ditemukan: `shell_exec`,
  `shell_open`, `shell_input`, `shell_output`, `shell_resize`, `shell_close`,
  `shell_sessions`, `job_status`, `job_output`, `job_wait`, `job_cancel`,
  `job_list`, `server_info`.
- **`server_info`** (dipanggil pertama sesuai aturan): `exec_user=root`,
  `hostname=microtix`, `platform=Linux-6.12.115+deb13.1-amd64...`, key
  `k-837c0f289a4a` ("cloud-agent", mode `all`), aturan advisory penggunaan
  dikembalikan (termasuk peringatan bahwa perintah berjalan nyata dengan
  privilege user service).
- **Identitas eksekusi** (`shell_exec`): `id -u` → `0`; `id -un` → `root`;
  `id -Gn` → `root`; `hostname` → `microtix` (FQDN `microtix.microtix.com`);
  `uname -sr` → `Linux 6.12.115+deb13.1-amd64`; `pwd` → `/root`;
  OS `Debian GNU/Linux 13 (trixie)`. Hostname sandbox agent
  (`c-6af228f3-...`) berbeda total — bukti eksekusi di VPS, bukan lokal.
- **Akurasi pelaporan**: exit code 7 + stdout `out_marker` + stderr
  `err_marker` terpisah dan benar; `timed_out=true` pada `timeout_seconds=3`
  vs `sleep 30` (durasi 3002 ms); perintah tak dikenal → exit 127; output besar
  (`seq 1 400000`) terpotong jujur (`stdout_bytes=2688895`, `stdout_truncated=true`,
  payload dibatasi 1 MiB/stream).

## 4. Inventaris kondisi nyata VPS (pra-patch)

| Aspek | Fakta terverifikasi |
|---|---|
| OS / kernel | Debian 13 (trixie), Linux 6.12.115+deb13.1-amd64, x86_64, systemd 257 |
| Sumber daya | 16 vCPU, RAM 1,9 GiB (423 MiB terpakai), disk `/` 122G (1,4G terpakai, 114G bebas) |
| Uptime host | 2 hari (boot 2026-11-08 01:00:34 WIB) |
| Service | `uaeg.service` loaded/active/running/enabled; `ExecStart=/opt/uaeg/.venv/bin/uaeg serve`; `User=root`; `Restart=on-failure`; `RestartSec=2`; `KillMode=control-group` (default); unit `/etc/systemd/system/uaeg.service` |
| Listener | `0.0.0.0:3902` (MCP, TLS) dan `0.0.0.0:3903` (dashboard, TLS) milik PID uaeg; SSH `0.0.0.0:22`; tidak ada aturan nftables aktif |
| Config | `/etc/uaeg/config.json` (0600, root) + 6 backup berekor waktu; `mcp.bind=0.0.0.0`; `dashboard.bind=0.0.0.0`, `allow_public=true`, `tls=true`; `tls.mode=certbot`; `allowed_dashboard_cidrs=null`; `restricted_paths=[]` |
| Data | `/var/lib/uaeg`: `uaeg.db` (SQLite WAL, 6 tabel: exec_logs, jobs, keys, meta, sessions, sqlite_sequence), `dashboard-pass.json` (0600), `jobs/` (per-job dir), `sessions/`, `tls/`, `upgrade/` (cache updater + manifest) |
| Key | 1 aktif (`k-837c0f289a4a` "cloud-agent", mode all) + 1 revoked (`k-89e573b7ab0e`); hanya prefix yang ditampilkan CLI, verifier SHA-256 saja di DB |
| Aplikasi | `/opt/uaeg` (editable install, `.venv` Python 3.13.5; mcp 1.27.0, pydantic 2.14.3, uvicorn 0.44.0, cryptography 50.0.2); **tanpa metadata git** — identitas dibuktikan via SHA-256 seluruh file (§5) |
| Backup | `/var/backups/uaeg/upgrade-*` (5 arsip pra-audit: 11-08 08:01, 11-08 17:40, 11-09 03:20, 11-09 09:58, 11-10 01:46) berisi `RECORD.json`, `before-state.json`, `after-state.json`, `install.log`, `config/`, `data/` (backup DB via SQLite online-backup API — konsisten WAL) |
| Jejak versi journal | v0.2.0, v0.3.2, v0.3.3 (3×), v0.4.0 (2×), v0.4.1 (3×) — host telah melewati banyak siklus upgrade |
| Restart 00:52 WIB 11-10 | Stop/start bersih (bukan crash); 34 s kemudian login dashboard sukses dari `172.16.100.1` (sesi ZeroTier operator) |
| Upgrade 01:45–01:46 WIB 11-10 | Re-install in-place v0.4.1 dari manifest v0.4.1 (dibuat 2026-11-09 02:45 UTC); backup `upgrade-20261110-014541`; `install.log` utuh |

## 5. Identitas instalasi — bukti hash (pra-patch)

Tidak ada `.git` di `/opt/uaeg`, sehingga identitas dibuktikan dengan membandingkan
SHA-256 setiap file terpasang terhadap referensi git (sandbox agent meng-clone
repo publik `smahud/uaeg` @ `main` = `478907c`, tag `v0.4.1`):

- **21/21 file `src/uaeg/*.py`** — identik dengan **v0.4.1** (sekaligus `main`;
  tidak ada perubahan Python antara keduanya).
- `updater.sh`, `uninstall.sh`, `pyproject.toml`, `README.md` — identik dengan
  v0.4.1 (dan main).
- **`install.sh` dan `upgrade.sh` — identik dengan v0.4.1, TIDAK dengan main**
  (main memuat `b428482` "fix(install): enforce TLS CLI contract pre-mutation;
  repair --dashboard-bind public; correct recovery runbook" dan `2c3b66d`
  "fix(upgrade): actionable missing-value errors" — dua perbaikan pasca-rilis
  yang **belum dirilis dalam tag** dan **belum dideploy** di VPS).

Kesimpulan: VPS menjalankan **aset rilis v0.4.1 secara persis** (bukan build
acak, bukan main). `manifest` upgrade terakhir (`/var/lib/uaeg/upgrade/
last-manifest.json`) konsisten: version 0.4.1, `install.sh` sha256
`9c27b873…`, updater `ea8cbc70…` — cocok dengan file terpasang.

## 6. Perbedaan kode–rilis–live (peta situasi)

| Artefak | Identitas | Catatan |
|---|---|---|
| Tag publik terbaru | `v0.4.1` (rilis + manifest bertanda tangan, kunci release-4) | Aset terverifikasi installer (rantai: keyring embedded → manifest sig → sha256 updater) |
| `main` @ `478907c` | v0.4.1 + 2 commit perbaikan installer/upgrade (`b428482`, `2c3b66d`) + laporan | Belum dirilis; CI hijau pada commit ini (diverifikasi ulang oleh CI PR #1 untuk patch set audit) |
| VPS pra-audit | **persis v0.4.1** (hash semua file) | install.sh/upgrade.sh versi v0.4.1 — TIDAK memuat 2 perbaikan main |
| VPS pasca-audit | **0.4.1+audit1** = patch set audit (F1–F7) di atas main, commit `8369e6e` | Diterapkan 2026-11-10 04:17 WIB via `updater.sh --source` dalam transient scope; identitas dilacak via versi + SHA sumber pada `RECORD.json` |

## 7. Batas bukti (apa yang TIDAK diuji)

- **Login dashboard dengan password produksi TIDAK diuji agent** (password
  tidak diberikan dan tidak diminta). Bukti yang tersedia: halaman `/login`
  HTTP 200, API anonim 401, konfirmasi operator (login sukses dari
  `172.16.100.1` pada 2026-11-10 00:52:38 WIB, tercatat di journal), dan
  seluruh lapisan autentikasi dashboard (scrypt, session server-side,
  throttle, CSRF, cookie) diaudit kode + tercakup suite tes (216→226 lulus).
- **Revocation/rotasi key TIDAK dijalankan pada key produksi** (tidak perlu
  memutus jalan kontrol operator). Perilaku revoke/rotate/containment
  diverifikasi dari kode + suite tes eksisting (e2e hardening) + lab.
- **Reboot VPS, kegagalan disk, fault-injection fisik TIDAK dilakukan** pada
  host produksi; rekonsiliasi pasca-reboot diverifikasi dari kode
  (boot-id guard) dan suite kontrak, bukan dengan mereboot host live.
- **Uji beban/Daya tahan jangka panjang** di luar cakupan.
- Agent **tidak mengubah** password, key, config (kecuali apa yang secara
  eksplisit disebut di [UAEG_DEPLOYMENT_AND_RECOVERY.md](UAEG_DEPLOYMENT_AND_RECOVERY.md)),
  firewall, maupun layanan lain di VPS.

## 8. Penutup

Bridge UAEG berfungsi nyata end-to-end dan, setelah patch audit, memenuhi
kontrak FR-009/AT-11 secara live (job bertahan restart service). UAEG tetap
siap dipakai untuk pekerjaan proyek utama dengan key yang sama. Keputusan rilis
dan langkah lanjutan: [UAEG_LIVE_RELEASE_DECISION.md](UAEG_LIVE_RELEASE_DECISION.md).
