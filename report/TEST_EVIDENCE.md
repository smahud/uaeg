# Test Evidence — UAEG v0.3.2 (audit P0)

**Tanggal:** 2026-11-07/08 · **Commit diaudit:** `4f5ac50` → patch `5dfcb0a` · **Environment:** sandbox Debian-compatible, Python 3.12.16, git 2.47.3, tanpa root/systemd (patch test-only standar CI untuk root-check/apt — identik spirit `.github/workflows/ci.yml`)

> Prinsip: **bukti, bukan klaim.** Setiap baris di bawah berasal dari
> eksekusi nyata yang direproduksi selama audit ini; salinan log mentah
> di `report/evidence/`.

---

## 1. Kontrak upgrade — `tests/test_upgrade_contract.sh` (kontrak v2)

Perintah: `bash tests/test_upgrade_contract.sh`
Hasil: **95 PASS / 0 FAIL, exit 0** — log lengkap:
`report/evidence/upgrade_contract_suite_v0.3.2.txt`

Matriks (19 grup):

| Test | Skenario | Hasil |
|------|----------|-------|
| T-01 | Fresh install terisolasi (installer nyata + venv + pip) | PASS |
| T-02 | 2 agent key + perubahan config operator | PASS |
| T-03 | `--check` deteksi instalasi/versi/path (U-2, U-10) | PASS |
| T-04 | `--dry-run` rencana tanpa perubahan (U-11) | PASS |
| T-GUARD | Dashboard publik tanpa password → ditolak (U-8) | PASS |
| T-13 | Upgrade ke tag terbaru via staging; **clone operator HEAD/branch/status identik** (U-12 v2) | PASS |
| T-14 | Tree kotor → upgrade tetap jalan, edit lokal dipertahankan (U-12 v2) | PASS |
| T-05 | Upgrade `--source`: zero data loss + bukti RECORD/state/log (U-1/3/4/8) | PASS |
| T-06 | Idempoten (re-run) (U-7) | PASS |
| T-07 | Bukti smoke /healthz tercatat (U-4) | PASS |
| T-08 | Release rusak → rollback OTOMATIS exit 2, app lama hidup lagi (U-5) | PASS |
| T-09 | Rollback manual memulihkan data pra-upgrade (U-6) | PASS |
| T-10 | Tanpa instalasi → exit 3 (U-2) | PASS |
| T-11 | Rahasia tidak pernah bocor ke log/RECORD/state (U-9) | PASS |
| T-12 | Retensi `--keep-backups 1` (U-7) | PASS |
| T-16 | Downgrade diam-diam → exit 5 (U-12) | PASS |
| T-15 | Standalone dry-run menemukan target via ls-remote (U-2/11) | PASS |
| **T-17** | **REGRESI INSIDEN v0.3.1**: clone usang @v0.3.0 + aset terunduh untracked di dalamnya → upgrade **BERHASIL**, tree operator byte-for-byte utuh | PASS |
| **T-18** | Tag tidak ada → pesan "tidak ditemukan di remote"; pesan menyesatkan lama tidak muncul | PASS |
| **T-19** | Standalone tanpa repo lokal → upgrade PENUH dari remote | PASS |

## 2. Unit tests Python

Perintah: `python -m pytest tests/test_unit_core.py tests/test_unit_executor.py
tests/test_unit_containment.py tests/test_unit_security_redaction.py
tests/test_unit_server_hardening.py tests/test_dashboard_auth.py -q`

Hasil: **81 passed** (0 failed) — mencakup auth bearer/revokasi, containment
advisory, redaksi secret di log, hardening server, dashauth.

## 3. E2E — MCP asli + TLS asli

Perintah: `python -m pytest tests/test_e2e_smoke.py -q`
Hasil: **9 passed** — siklus MCP Streamable HTTP penuh (initialize → session
→ tools/call) di atas HTTPS self-signed nyata; semua 13 tool tercakup
(server_info, shell_exec, shell_open, shell_input, shell_output,
shell_resize, shell_close, shell_sessions, job_status, job_output, job_wait,
job_cancel, job_list).

## 4. E2E — dashboard auth + hardening

Perintah: `python -m pytest tests/test_e2e_dashboard_auth.py tests/test_e2e_hardening.py -q`
Hasil: **23 passed** — halaman login sebelum dashboard, login benar/salah,
logout, rotasi password mencabut sesi tanpa restart, bind wildcard ditolak
tanpa password, HTTPS + login saat password diset, cookie/CSRF, rate limit.

## 5. Live smoke produksi-style (server hidup, 13 tool, batas auth)

Perintah: `bash scripts/live_smoke.sh` (isolated dirs, kunci disposabel)
Log: `report/evidence/live_smoke_v0.3.2.txt`

```
1) healthz            : {"status":"ok","service":"UAEG","version":"0.3.2",...}
2) initialize         : serverInfo ok, session 99d39df24c...
3) server_info        : version=0.3.2
4) shell_exec         : LIVE-SMOKE-OK-42
5) shell_open→input→output: PTY-OK-9 ; resize ok ; close=True
   shell_sessions     : count=0 (benar — sesi baru saja ditutup)
6) job: submit → status=running → list=1 → cancel → status=cancelled
   → status after=cancelled (PROSES MATI TIDAK PERNAH DILAPORKAN RUNNING)
   → job_output ok → job_wait ok
7) unauth MCP         : HTTP 401
8) bad key MCP        : HTTP 401
9) no-session tools   : Missing session ID (session enforcement jalan)
10) dash unauth API   : HTTP 200 pada bind loopback tanpa password
                        (info-page by design; bind publik tanpa password
                        ditolak config/serve — lihat SECURITY_REVIEW.md §3)
11) version           : uaeg 0.3.2
```

## 6. Reproduksi insiden (bukti akar masalah)

`scripts/repro_incident.sh` (aset v0.3.1 byte-identical, user-namespace
root, remote lokal; skenario persis transkrip operator):
hasil **exit 1** dengan urutan pesan identik transkrip (checkout collision
+ "tag tidak ditemukan setelah fetch"). Detail: `ROOT_CAUSE_ANALYSIS.md`.

`scripts/repro_fix.sh` (aset v0.3.2, skenario sama):
hasil **exit 0**, `UPGRADE BERHASIL: v0.3.0 -> v0.3.2`, tree operator
identik (HEAD/branch/status), kunci agent utuh, semua check
zero-data-loss lolos, RECORD `contract_version=2`.

## 7. Static

- `bash -n` semua skrip shell (`upgrade.sh`, `install.sh`, `uninstall.sh`,
  `tests/test_upgrade_contract.sh`): OK.
- `python -m compileall src/uaeg`: OK.
- Pemindaian referensi usang (`tidak ditemukan setelah fetch`,
  `untracked-files=no`, `contract_version=1` di `upgrade.sh`): bersih.
- Konsistensi versi: `pyproject.toml` == `__init__.__version__` ==
  `install.sh` pin `UAEG_RELEASE_VERSION` == README == `0.3.2`
  (dijaga tambahan oleh `release.yml` step "Verify the installer pins
  this tag's version").

## 8. Yang TIDAK diuji di lingkungan ini (dan di mana diuji)

- systemd nyata / boot VPS / ufw — di luar sandbox; jalur ini diaudit via
  kode (unit `uaeg.service`, `install.sh`) dan bukti penerimaan VPS
  sebelumnya (`report/VPS_ACCEPTANCE_RESULTS.md`); deployment live
  MicroTix ditandai **PENDING** (lihat `PRODUCTION_READINESS.md`).
- Let's Encrypt (butuh domain + port 80 publik) — tidak berubah di patch
  ini; risiko diwarisi dari v0.3.1.
