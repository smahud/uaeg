# Evidence — Sesi 3 (verifikasi ulang, 2026-11-10 ±05:44–06:30 WIB)

Semua probe dijalankan dari sandbox auditor (host `c-6af228f3...`, user `z`)
menuju `https://host.zerotier.my.id:3902/mcp` (MCP) dan `:3903` (dashboard).
Key agent dibaca dari file chmod 600 dan tidak pernah dicetak/di-log.

## E1 — Konektivitas & TLS (tanpa kredensial)

| Probe | Hasil |
|---|---|
| `GET /healthz` (3902) | 200 — `{"status":"ok","service":"UAEG","version":"0.4.1+audit1","uptime_seconds":4569.7}` |
| `POST/GET /mcp` tanpa bearer | 401 |
| `GET /login` (3903) | 200 |
| `GET /api/overview` tanpa sesi | 401 |
| TLS 3902 & 3903 | CN=host.zerotier.my.id, issuer Let's Encrypt (YE2), valid 2026-11-07 → 2027-02-05, SAN cocok |

## E2 — MCP handshake + tool discovery (key aktif baru)

- `initialize` → 200, `serverInfo UAEG 0.4.1+audit1`, protocol `2024-11-05`
  (klien negosiasi turun; sesi-2 sebelumnya `2025-11-25` — server mendukung keduanya).
- `tools/list` → **13 tools** sesuai kontrak: shell_exec, shell_open/input/output/resize/close/sessions,
  job_status/output/wait/cancel/list, server_info.

## E3 — Bukti eksekusi di VPS (bukan sandbox auditor)

`shell_exec` compound command → `uid=0(root)`, `hostname=microtix`,
kernel `6.12.115+deb13.1-amd64` (Debian 13 trixie), `/home/z` TIDAK ada di target
(`NO_HOME_Z`), exit_code=0, stdout nyata. Sandbox auditor: user `z`, host `c-6af228f3...`,
kernel 5.10.134 — jelas dua mesin berbeda.

## E4 — server_info (dipanggil PERTAMA sebelum shell_exec)

`ok=true, version=0.4.1+audit1, hostname=microtix, exec_user=root,
key=k-c9f935ef39a2 (cloud-agent, mode=all)`.

## E5 — Kontinuitas & pencabutan kunci (live)

| Uji | Hasil |
|---|---|
| Key baru (k-c9f935ef39a2, dibuat 2026-11-10 05:37 WIB) | initialize OK (berulang, tiap sesi) — autentikasi pasca-upgrade+pasca-rotasi ✓ |
| Key lama sesi-2 (k-837c0f289a4a) | **401 "Unauthorized: invalid or revoked bearer token"** — revocation ditegakkan per-request ✓ |
| `uaeg keys list` | 1 aktif (k-c9f935ef39a2), 2 revoked (k-837c0f289a4a, k-89e573b7ab0e) |

## E6 — Snapshot live (semua read-only)

- Service `active` sejak 04:27:52 WIB (restart AT-11 proof), MainPID 30777.
- Listener: `0.0.0.0:3902` (MCP) + `0.0.0.0:3903` (dashboard) — bind publik disengaja
  (akses remote); doctor mengonfirmasi password+TLS enforced untuk dashboard.
- Backup upgrade: `upgrade-20261110-041721` (deploy sukses), `upgrade-20261110-041342`
  (deploy-1 gagal → auto-rollback), `upgrade-20261110-014541`, `upgrade-20261109-095832`.
- Journal ERROR sejak deploy: hanya 2 baris SSE-writer dari proses LAMA
  (04:17:10 & 04:17:20, pid 30205 pra-restart) — nol error pada proses patched.
- healthz saat verifikasi akhir: `ok, 0.4.1+audit1, uptime 5608s`.

## E7 — Forensik deploy-1 yang gagal (bukti auto-rollback jujur)

`/var/backups/uaeg/upgrade-20261110-041342/RECORD.json`:
- `to_version: "0.4.1.zaudit1"` → pip: `project.version must be pep440` → metadata-generation-failed.
- `result: "rolled-back"`, `reason: "upgrade failed; pre-upgrade state restored automatically"`,
  `rollback_verified: true`, `service_active_before: true`.
- install.log berakhir: `restored previous application files`.
Kesimpulan: kegagalan dilaporkan jujur (bukan sukses palsu), state pra-upgrade dipulihkan,
lalu deploy-2 (`8369e6e`, versi PEP 440 `0.4.1+audit1`) sukses.

## E8 — Verifikasi independen repository (sandbox auditor)

- `git ls-remote`: main=`705c792`, `audit/live-2026-11-10`=`121a7ca` (PR #1, MERGED),
  `audit/live-2026-11-10-deploy`=`8369e6e` (= sha di RECORD.json VPS),
  `release/v0.4.2-prep`=`7bdf0a7` (PR #2, CI 5/5), tag terbaru `v0.4.1`.
- `origin/main` (705c792) MEMUAT semua fix: F1 (`_require_key`=13), F4 (`selfupdate_guard`),
  F5 (`_in_systemd_service`), tests v042 PRESENT, 5 laporan live-bridge PRESENT.
- Diff kode deploy-branch vs main: hanya string versi (0.4.1+audit1 vs 0.4.1) + pin installer.
- **Peringatan metodologis**: branch `main` LOKAL pada clone lama tertinggal di `478907c`
  (pra-merge) — sempat mengesankan fix "hilang dari main"; verifikasi terhadap
  `origin/main` membantahnya. Selalu verifikasi terhadap remote ref.
- pytest (checkout `origin/main`, Python 3.12): **197 unit/regresi LULUS**,
  **18/18 v042 regression LULUS**, **21 e2e (smoke+hardening) LULUS**.
- `tests/test_upgrade_contract.sh`: **290 PASS / 0 FAIL**.
- `uaeg doctor` & `uaeg status` (analisis kode, batas fungsi ketat): TIDAK ada panggilan
  tulis (murni read-only); `cfg.save()` hanya ada di `cmd_config set`.

## E9 — Error install.sh yang dilaporkan operator (input sesi ini)

```
ERROR: target v0.4.1 is OLDER than the installed v0.4.1+audit1 (downgrade).
If this is intentional: sudo bash upgrade.sh --version 0.4.1 --allow-downgrade
```
Guard urutan versi bekerja sesuai desain (exit pra-mutasi; tidak ada byte berubah di /opt/uaeg).
Lihat laporan utama §2 untuk opsi penanganan.

## E10 — Suite penuh pada tree v1.0.0 (sebelum push, sesudah bump)

Checkout pada commit bump v1.0.0 (`9c26340` + laporan `27ea3be`), Python 3.12:

| Suite | Hasil |
|---|---|
| pytest LENGKAP (unit + regresi + e2e termasuk installer-repair yang mengunduh arsip tag riil) | **226/226 LULUS** (`pytest_v100_full.log`) |
| `tests/test_upgrade_contract.sh` | **290 PASS / 0 FAIL** (`contract_v100.log`) |
| `bash -n` install.sh / upgrade.sh / updater.sh | lulus |
