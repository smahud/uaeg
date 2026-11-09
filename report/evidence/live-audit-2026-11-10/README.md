# Evidence — Live Audit 2026-11-10

Bukti mentah (tersaring) untuk laporan `UAEG_LIVE_*` dan
`UAEG_DEPLOYMENT_AND_RECOVERY.md` (root `report/`).

**Redaksi**: setiap berkas telah disaring — tidak berisi key penuh (termasuk
prefix 12 karakter tampilan CLI ikut diredaksi), Authorization header, cookie,
token, maupun password. Marker uji (`UAEG_AUDIT_20261110_*`) tidak memuat
rahasia.

| Berkas | Isi | Fase |
|---|---|---|
| `01_connect.jsonl` | Handshake MCP, tool discovery (13), `server_info`, bukti identitas VPS (`id`, `hostname`, `uname`, OS), uji exit code 7 + pemisahan stdout/stderr | Koneksi live |
| `02_capabilities.jsonl.gz` | Battery kapabilitas lengkap: marker, quoting, unicode, env, cwd, exit 127, timeout, truncation 2,7 MB→1 MiB, file round-trip `/tmp`, siklus background job (status/wait/output/cancel/list), PTY (open/input/output/resize/sessions/close), 5 permintaan paralel, reconnect + orphan job (JSONL dikompres: mengandung payload truncation 1 MiB) | Kapabilitas |
| `02b_retest.jsonl` | Uji ulang dengan nama parameter benar (`timeout_seconds`, `data`): timeout terbukti memotong (3002 ms, `timed_out=true`), PTY marker, resize (stty 35 100), `job_wait` terbatas, param `env`, perilaku parameter asing diabaikan | Kapabilitas |
| `03_inventory.jsonl` | OS/kernel/sumber daya, unit systemd (ExecStart/KillMode/Restart), listener 3902/3903 (wildcard), layout `/opt/uaeg`, ketiadaan `.git`, venv + versi paket, CLI help, data dir, proses, zombie=0, backup, journal | Inventaris |
| `03b_inventory2.jsonl` | `uaeg status`/`doctor` pra-patch, daftar key (prefix diredaksi), lokasi DB, jejak upgrade 01:45 WIB, journal stop/start, isi `report/` lama, hash 21 file sumber + file root (identitas v0.4.1) | Inventaris |
| `03c_inventory3.jsonl` | Hash lengkap `src/*.py` + manifest rilis v0.3.3–v0.4.1, unit file (env diredaksi nilai sensitifnya), config efektif (diredaksi), Let's Encrypt + timer, nft (kosong), cron, WAL/journal mode, tabel DB | Inventaris |
| `04_history.jsonl` | `last-manifest.json`, sejarah versi journal, jendela restart 00:52 (bersih), jejak traceback SSE pra-patch, struktur & log backup upgrade, `install.log` upgrade 01:45 (berisi "TLS mode : self-signed" — bukti F3) | Histori |
| `05_deploy.jsonl` | Precheck (probe scope OK, cgroup cmd = uaeg.service, hash before-state, hitungan DB, noise SSE=9), peluncuran updater terbungkus scope, percobaan-1 gagal (PEP440) → **RECORD rolled-back**, diagnosis lengkap, percobaan-2 → **RECORD success** | Deployment |
| `06_postpatch.jsonl` | Battery pascapatch: re-auth key sama, identitas root@microtix, **F5 live** (cgroup job = `uaeg-job-*.scope`), **AT-11** (job `AT11_JOB_SURVIVED` melewati `systemctl restart`), **F2** (doctor `0.0.0.0:3902 probed via 127.0.0.1` + peringatan), **F6** (0 entri SSE baru), **F3** (install.log `TLS mode : certbot`), preservasi hash config/password, doctor all-checks-passed, PTY pascapatch, cleanup | Verifikasi pascapatch |
| `07_followup.jsonl` | Sesi lanjutan (eksekusi rekomendasi): `server_info` + `shell_exec` key lama — `0.4.1+audit1`, `root@microtix`, service aktif sejak 04:27:52 WIB; healthz ok; TLS Let's Encrypt s.d. Feb 2027 | Tindak lanjut |
| `postpatch_verification_battery.txt` | Transkrip battery pascapatch (deployE) | Verifikasi pascapatch |
| `upgrade_contract_baseline_main_478907c.log` | Suite kontrak pada baseline `main` @ `478907c`: **290 PASS / 0 FAIL** | Lab |
| `upgrade_contract_patchset_from_main_checkout.log` | Suite kontrak pada patch set (checkout main): **290/290** | Lab |
| `upgrade_contract_patchset_from_audit_branch.log` | Suite kontrak pada patch set (checkout branch audit, pasca-F8): **290/290** — bukti harness branch-agnostic | Lab |
| `deploy_job_id.txt` | job_id peluncuran updater (percobaan-2) | Deployment |

Suite lab lain yang relevan (tidak disalin ke sini, dijalankan di sandbox
agent dan di CI): `pytest tests/` → **226 lulus** (termasuk
`tests/test_unit_regression_v042.py`, 18 kasus baru); CI PR #1 → **5/5 job
sukses** (Test 3.11/3.12/3.13, Install smoke, Upgrade contract) —
https://github.com/smahud/uaeg/actions/runs/42702551013.

Tindak lanjut 2026-11-10 (sesi lanjutan, lihat addendum §6 pada
`UAEG_LIVE_RELEASE_DECISION.md`): PR #1 di-merge ke `main` sebagai `705c792`
(CI pada merge commit 5/5 sukses); PR #2 (`release/v0.4.2-prep`) menyiapkan
rilis v0.4.2 — bump versi, pin unduhan e2e (`PINNED_SOURCE_RELEASE`,
memperbaiki race CI-latent yang ditemukan saat tindak lanjut), runbook
penandatanganan operator; pytest pada branch PR #2 → **226/226 lulus**; CI PR #2
(head `9fbe42e`) → **5/5 job SUKSES**. Rilis
resmi v0.4.2 tetap menunggu penandatanganan operator (kunci privat
`uaeg-release-4`).

Laporan historis lain di `report/` (mis. `FULL_AUDIT.md`, `PATCH_CHANGELOG.md`)
menggambarkan audit-audit **sebelumnya**; dokumen berprefix `UAEG_LIVE_`
tanggal 2026-11-10 adalah yang terkini untuk kondisi live.
