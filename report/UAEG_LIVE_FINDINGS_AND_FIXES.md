# UAEG — Temuan & Perbaikan Live Audit 2026-11-10

| | |
|---|---|
| **Cakupan** | Temuan audit terhadap kode `main` @ `478907c` (= v0.4.1 + 2 fix installer) dan kondisi live; akar masalah; patch; pengujian; status |
| **Patch set** | Branch `audit/live-2026-11-10` — commit `f1d326f` (F1–F7) + `16c2475` (F8 harness); branch deploy `audit/live-2026-11-10-deploy` @ `8369e6e` (+ penanda versi `0.4.1+audit1`) |
| **Pengujian** | Suite penuh **226 lulus** (208 baseline + 18 regresi baru); kontrak upgrade **290/290 lulus** (dari checkout main maupun branch audit); CI PR #1 (matriks Python 3.11/3.12/3.13 + e2e + installer repair + smoke + kontrak) — lihat status akhir di [UAEG_LIVE_RELEASE_DECISION.md](UAEG_LIVE_RELEASE_DECISION.md) |
| **Status live** | **DITERAPKAN** pada VPS 2026-11-10 04:17 WIB (v0.4.1+audit1); seluruh verifikasi pascapatch lulus |

Klasifikasi bukti mengikuti master prompt: **[analisis kode]** = pembacaan
sumber; **[tes laboratorium]** = suite otomatis di lingkungan terisolasi;
**[verifikasi live]** = dieksekusi pada VPS produksi melalui UAEG.

---

## F1 — Lapisan tool MCP gagal-terbuka (fail-open) saat identitas key tak teresolusi

- **Klasifikasi**: Keamanan akses — **Tinggi** (edge case, dampak operator-level)
- **Bukti**: [analisis kode] + [tes laboratorium] (regresi baru)
- **Komponen**: `src/uaeg/mcp_tools.py`, `src/uaeg/executor/jobs.py::_gate`,
  `src/uaeg/executor/pty.py::get`, `src/uaeg/auth.py::request_key`

**Kondisi.** Manajer job/sesi memperlakukan `owner_key_id=None` sebagai
**level operator** (dashboard/CLI melihat semua) — itu memang desain yang
didokumentasikan. Namun lapisan tool MCP meneruskan hasil `request_key(ctx)`
**apa adanya**: bila resolusi gagal (race pencabutan key antara autentikasi
middleware dan eksekusi tool, kegagalan konteks request pada perubahan internal
SDK, atau error SQLite sesaat yang ditelan `except Exception` di `request_key`),
maka `owner_key_id=None` mengalir ke manager → caller agent mendapat **tampilan
level operator atas job/sesi milik semua key** (`shell_sessions` tanpa filter,
`job_status/output/wait/cancel` lintas key, `job_list` tanpa filter), dan
`shell_exec(background=true)` melahirkan job tanpa pemilik (visible-to-all).

**Perbaikan.** Helper `_require_key(ctx)` di `mcp_tools.py`: bila identitas tak
teresolusi, panggilan **ditolak fail-closed** dengan pesan yang dapat
di-retry, dan penolakan diaudit (`status="denied"`). Diterapkan pada 12 tool
bermuatan (kecuali `server_info` yang informatif). Semantik operator-level
`None` pada manager **tetap** (dipakai dashboard/CLI) — batasnya kini dijaga di
lapisan tool.

**Regresi.** `tests/test_unit_regression_v042.py`: (a) 12 tool menolak
`ctx` tak teresolusi + baris audit `denied` tercatat + tidak ada baris
eksekusi; (b) spawn background tanpa identitas tidak membuat baris job;
(c) semantik operator `None` pada manager tetap ada (dokumentasi desain);
(d) sanity live pasca-patch: pemanggilan sah via HTTP tetap berhasil
(`f1_live_ok`).

**Status**: FIXED — kode + lab + live sanity.

## F2 — `uaeg doctor` menyamarkan bind publik MCP sebagai loopback

- **Klasifikasi**: Kejelasan/security-posture — **Sedang**
- **Bukti**: [verifikasi live] (pra-patch: doctor mencetak `MCP listener:
  127.0.0.1:3902` padahal socket asli `0.0.0.0:3902`) + [tes laboratorium]
- **Komponen**: `src/uaeg/cli.py` (`cmd_doctor`, `cmd_status`)

**Akar masalah.** Doctor mem-probe listener wildcard melalui `127.0.0.1`
(wajar untuk uji koneksi) tetapi **menampilkan alamat probe** sebagai bind —
operator bisa percaya endpoint eksekusi-root hanya loopback, padahal terbuka ke
internet (dan memang harus terbuka agar agent hosted dapat terhubung). Kasus
serupa di `cmd_status` (hanya menampilkan port, tanpa bind).

**Perbaikan.** Doctor menampilkan **bind terkonfigurasi** (`0.0.0.0:3902`),
menandai probe (`probed via 127.0.0.1`), dan menambahkan peringatan eksplisit
"MCP listener is bound to ALL interfaces (public)…" — konsisten dengan
peringatan dashboard publik yang sudah ada. `cmd_status` kini menampilkan
`bind:port`.

**Regresi.** Dua tes doctor (wildcard & privat) dengan mock; **verifikasi
live pascapatch**: `uaeg doctor` mencetak
`[✓] MCP listener: 0.0.0.0:3902 (probed via 127.0.0.1)` + baris peringatan
"ALL interfaces", semua cek lulus.

**Status**: FIXED — kode + lab + live.

## F3 — Ringkasan installer melaporkan mode TLS yang salah pada upgrade preserve-config

- **Klasifikasi**: Pelaporan — **Rendah**
- **Bukti**: [verifikasi live] (`install.log` upgrade 2026-11-10 01:45:
  `TLS mode : self-signed` padahal config efektif `certbot` dan listener
  memakai sertifikat Let's Encrypt aktif) + [analisis kode]
- **Komponen**: `install.sh` (blok Summary; variabel `$TLS_MODE`)

**Akar masalah.** `$TLS_MODE` mencerminkan **permintaan** CLI instalasi ini
(default `self-signed`); pada jalur upgrade yang mempertahankan konfigurasi
("keeping the existing public_host and TLS configuration"), nilai variabel
tidak pernah diperbarui, sehingga ringkasan akhir menyesatkan.

**Perbaikan.** Blok read-back `EFFECTIVE_PORTS` (yang sudah membaca config
untuk verifikasi) diperluas membaca `tls.mode` efektif; Summary mencetak
`$EFFECTIVE_TLS_MODE` (fallback `$TLS_MODE` bila read-back kosong).

**Verifikasi live.** `install.log` upgrade sukses 04:17 kini mencetak
`TLS mode : certbot`. (Tes otomatis installer repair e2e tetap hijau.)

**Status**: FIXED — kode + live.

## F4 — Pembaruan-diri dari dalam UAEG akan terbunuh oleh `systemctl stop` miliknya sendiri

- **Klasifikasi**: Keandalan update — **Tinggi**
- **Bukti**: [analisis kode] + [verifikasi live] ( premis terkonfirmasi:
  perintah `shell_exec` berjalan di cgroup `0::/system.slice/uaeg.service`;
  unit memakai `KillMode=control-group`) + [tes laboratorium]
- **Komponen**: `updater.sh`, `install.sh`

**Akar masalah.** `start_new_session`/setsid **tidak** mengeluarkan proses dari
cgroup systemd. Job/popen yang dilahirkan server berada di cgroup
`uaeg.service`; `systemctl stop uaeg` meng-SIGTERM/SIGKILL seluruh cgroup —
termasuk updater yang sedang berjalan. Tanpa perlindungan: best case
TERM-trap memicu rollback dan upgrade tidak pernah berhasil; worst case
SIGKILL di tengah restore meninggalkan instalasi setengah dan service mati
(operator harus lewat SSH). Tidak ada deteksi/peringatan apa pun di v0.4.1.

**Perbaikan.** `selfupdate_guard()` pada `updater.sh` (mode mutasi:
upgrade/rollback) dan `install.sh` (mode mutasi: install/upgrade/repair/
rollback/start/stop/restart; mode baca `status/check/dry-run` lolos): bila
berjalan di dalam cgroup `/${SERVICE_NAME}.service` → **re-exec ke transient
systemd scope** `systemd-run --scope --unit=uaeg-selfupdate-$$` (cgroup
terpisah yang selamat dari stop service; argumen & env diwariskan; hanya bila
skrip berjalan dari file nyata) atau **menolak dengan pesan yang dapat
ditindaklanjuti** (jalankan dari shell operator). Setelah patch F5 aktif, job
yang dilahirkan UAEG sudah berada di scope per-job sehingga guard otomatis
lolos (belt-and-suspenders).

**Regresi.** Fungsi guard diekstraksi **verbatim** dari skrip rilis via `sed`
lalu diuji dalam harness: (a) di-dalam-cgroup + systemd-run tersedia → re-exec
terjadi (fake systemd-run merekam `--scope --unit=uaeg-selfupdate-*` + argumen,
exit code terpropagasi); (b) tanpa systemd-run → refuse rc=9 dengan pesan;
(c) mode baca → lolos; (d) di luar cgroup service → no-op; (e) varian
install.sh (MODE_CMD) sama; (f) `bash -n` keempat skrip.
**Verifikasi live (mekanisme inti)**: deployment audit dijalankan dengan
pembungkus manual `systemd-run --scope --unit=uaeg-livepatch` pada v0.4.1 yang
belum berpatch — updater **selamat dari service stop** dan menyelesaikan
alurnya penuh (backup → stop → install → start → verify, serta rollback pada
percobaan gagal).

**Status**: FIXED — kode + lab + live (mekanisme scope terbukti; guard teruji
lab verbatim).

## F5 — Job background mati bersama service di bawah systemd (pelanggaran kontrak FR-009/AT-11)

- **Klasifikasi**: Keandalan kontrak — **Tinggi**
- **Bukti**: [analisis kode] (`_jobwrap.py` tanpa escape cgroup; log server
  mengklaim "background jobs keep running detached"; README/ARCHITECTURE
  menjanjikan job "survive service restarts") + [verifikasi live]
- **Komponen**: `src/uaeg/executor/jobs.py` (spawn), `docs/ARCHITECTURE.md`

**Akar masalah.** Shim job adalah anak langsung server (setsid hanya memisahkan
sesi/proses-group, bukan cgroup). Dengan `KillMode=control-group`,
`systemctl stop/restart uaeg` membunuh semua shim+perintahnya. Klaim
"job bertahan restart" hanya benar saat menjalankan tanpa systemd. `reattach()`
pada kenyataannya hanya berguna untuk skenario non-systemd.

**Perbaikan.** Saat server berjalan di dalam cgroup service systemd:
- probe satu-kali kemampuan (`systemd-run --scope true`, ~puluhan ms, hasil
  di-cache); gagal → fallback jalur lama + **peringatan keras di journal**
  ("jobs will NOT survive service restarts on this host");
- setiap job dilahirkan dalam **transient scope per-job**
  (`--unit=uaeg-job-<id>`). `systemd-run --scope` mengeksekusi perintah pada
  proses yang sama, sehingga pid shim, semantik killpg/cancel/reap, dan
  berkas pid/exit tidak berubah.
- `docs/ARCHITECTURE.md` diperbarui menjelaskan mekanisme + fallback.

**Regresi/lab.** (a) deteksi cgroup (service vs scope vs legacy vs file hilang);
(b) probe dinonaktifkan saat di luar service; (c) probe gagal → fallback +
job tetap jalan; (d) **jalur fungsional penuh dengan fake systemd-run yang
mengeksekusi shim sungguhan**: probe OK → spawn via `--unit=uaeg-job-<id>` →
job selesai `exit=0` + output jujur.

**Verifikasi live pascapatch.**
1. Job uji berjalan di cgroup `0::/system.slice/uaeg-job-<id>.scope`
   (bukan `uaeg.service`);
2. journal mencatat `"background jobs run in transient systemd scopes"`;
3. **AT-11 live**: job `sleep 25; echo AT11_JOB_SURVIVED` dilaunch →
   `systemctl restart uaeg` (service pulih ±2 s) → reconnect dengan key yang
   sama → job **selesai exit=0** dengan marker; baris DB `completed`,
   output terbaca penuh.

**Status**: FIXED — kode + lab + **live (bukti restart nyata)**.

## F6 — Traceback ERROR di journal pada setiap disconnect klien MCP

- **Klasifikasi**: Higiene log — **Rendah**
- **Bukti**: [verifikasi live] — 9 entri `mcp.server.streamable_http: Error in
  standalone SSE writer` (+ traceback `anyio.ClosedResourceError`) di journal
  hanya dalam jendela probe audit 03:00–04:13; [analisis kode] — normal
  teardown transport, bukan error UAEG.
- **Komponen**: `src/uaeg/logging_util.py`

**Perbaikan.** `logging.Filter` sempit pada logger `mcp.server.streamable_http`:
menjatuhkan **hanya** rekaman "Error in standalone SSE writer" level < CRITICAL
yang traceback-nya `ClosedResourceError`/`CancelledError`; error lain tetap
tercatat. Filter dipasang idempoten oleh `setup_logging()`.

**Verifikasi live pascapatch**: setelah 2 siklus koneksi–putus klien, entri
baru "standalone SSE writer" = **0** (basis pembanding pra-patch: 9 dalam
jendela setara).

**Status**: FIXED — kode + lab + live.

## F7 — Warning pydantic-settings "lifespan" pada setiap konstruksi FastMCP

- **Klasifikasi**: Higiene log — **Rendah**
- **Bukti**: [verifikasi live] (journal setiap startup pra-patch) +
  [analisis kode] (sumber: forward-ref `lifespan` pada model settings SDK MCP,
  terpicu saat `build_mcp_asgi_app`).
- **Perbaikan**: penekanan warning yang sempit berdasarkan pesan
  (`Field 'lifespan' has an incomplete definition.*`), idempoten.
- **Verifikasi live**: startup 0.4.1+audit1 bersih dari warning tersebut.
- **Status**: FIXED — kode + lab + live.

## F8 — Harness kontrak upgrade hanya bisa dijalankan dari checkout `main`

- **Klasifikasi**: Infrastruktur CI — **Sedang**
- **Bukti**: [tes laboratorium] — suite 290/290 dari checkout main, tetapi
  222/290 (68 gagal berantai) dari checkout branch lain; CI PR #1 (PR pertama
  repo) memperlihatkan kegagalan yang sama persis (`error: src refspec main
  does not match any` pada T-13/T-17/T-21).
- **Akar masalah**: suite membangun remote staging dengan `git clone $REPO_DIR`
  lalu `git push origin main`; dari checkout non-main, clone default ke branch
  itu dan `main` lokal tidak ada.
- **Perbaikan**: `git checkout -q -B main` setelah setiap clone staging
  (T-13, T-17, helper `make_release_repo`, `make_release_repo_uni`) — suite
  kini branch-agnostic.
- **Verifikasi**: 290/290 dari branch audit; CI PR #1 dijalankan ulang hijau.
- **Status**: FIXED — kode + lab + CI.

## Temuan pengamatan (tanpa perubahan kode)

1. **Parameter tool asing diam-diam diabaikan** (mis. `timeout_s` alih-alih
   `timeout_seconds`, `input` alih-alih `data`) — perilaku pydantic/FastMCP
   (extra ignore). Konsekuensi nyata teramati saat probe awal agent
   (A7/A2 salah kaprah, terdeteksi & dikoreksi). Skema tool adalah kontrak
   yang sah; mengubah ke `extra=forbid` berisiko memecah klien yang ada —
   didokumentasikan di laporan ini sebagai perilaku yang diketahui.
2. **Status job pembungkus updater** ("interrupted") untuk dua job deployment
   — shim job tersebut dilahirkan oleh v0.4.1 pra-patch (di cgroup service)
   sementara updaternya sendiri berada di scope dan sukses; setelah F5, job
   semacam ini akan tetap `running`→`completed` dengan benar. Kosmetik,
   by-design, terdokumentasi.
3. **Sesi PTY yang sudah ditutup tetap terdaftar ±60 s** (grace window baca
   output akhir; `max_sessions` hanya menghitung sesi hidup) — desain sah,
   dipahami, tidak diubah.
4. **Gap rilis main-vs-v0.4.1** (2 fix installer/upgrade) — diwarisi patch set
   ini (patch dibuat di atas main), rekomendasi rilis ada pada dokumen
   keputusan rilis.
5. **Host firewall tidak aktif** (tidak ada ruleset nftables; port 3902/3903
   terbuka ke internet) — sesuai desain produk (endpoint publik + TLS + auth);
   dicatat sebagai fakta posture, bukan temuan kodefek. Perlindungan utama:
   kredensial + TLS + revoke-per-request.

## Peta uji ulang penuh

| Suite | Hasil |
|---|---|
| `pytest tests/` (unit + e2e + installer repair + smoke) | **226 lulus** (sandbox, Python 3.12; CI: 3.11/3.12/3.13) |
| `tests/test_unit_regression_v042.py` (baru, F1–F6) | **18 lulus** |
| `tests/test_upgrade_contract.sh` (dari main dan dari branch audit) | **290/290 lulus** |
| `bash -n` semua skrip shell | OK |
| Verifikasi pascapatch live (deployE/F/G) | Semua asersi lulus (lihat [UAEG_LIVE_TEST_EVIDENCE.md](UAEG_LIVE_TEST_EVIDENCE.md)) |
