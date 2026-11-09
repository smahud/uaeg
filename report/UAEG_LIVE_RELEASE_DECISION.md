# UAEG — Keputusan Rilis Live Audit 2026-11-10

| | |
|---|---|
| **Isi** | Verdict terpisah untuk (1) kode/repository, (2) rilis, (3) VPS live |
| **Diputuskan** | 2026-11-10 ±04:35 WIB oleh agent pelaksana audit |
| **Dasar** | [UAEG_LIVE_BRIDGE_AUDIT.md](UAEG_LIVE_BRIDGE_AUDIT.md) · [UAEG_LIVE_FINDINGS_AND_FIXES.md](UAEG_LIVE_FINDINGS_AND_FIXES.md) · [UAEG_LIVE_TEST_EVIDENCE.md](UAEG_LIVE_TEST_EVIDENCE.md) · [UAEG_DEPLOYMENT_AND_RECOVERY.md](UAEG_DEPLOYMENT_AND_RECOVERY.md) |

---

## 1. Verdict KODE (repository)

### **GO** — patch set audit layak menjadi dasar rilis resmi berikutnya

- Temuan F1–F7 diperbaiki sampai akar masalah dengan regresi bermakna
  (+18 kasus); temuan F8 (harness CI) diperbaiki sehingga suite kontrak
  branch-agnostic.
- **Kualitas terverifikasi ulang secara independen**:
  - `pytest tests/` (unit + e2e TLS/MCP nyata + dashboard auth + installer
    repair + smoke): **226/226 lulus** (sandbox Python 3.12).
  - `tests/test_upgrade_contract.sh`: **290/290 lulus** — dari checkout `main`
    maupun dari branch audit.
  - **CI GitHub Actions (PR #1, branch `audit/live-2026-11-10` @ `16c2475`):
    5/5 job SUKSES** — Test Python 3.11 / 3.12 / 3.13, Install script smoke
    (no-service), dan Safe-upgrade contract tests.
  - `bash -n` seluruh skrip shell: lulus.
- Branch: `audit/live-2026-11-10` (patch murni, tetap versi `0.4.1`) —
  PR #1: https://github.com/smahud/uaeg/pull/1 (menuju `main`).
- Tidak ada perubahan model produk: akses root agent tetap (per PRD FR-010);
  perbaikan keamanan F1 justru menutup eskalasi tak sengaja ke level operator.

### Catatan untuk reviewer PR

1. F1: batas penolakan ada di lapisan TOOL (manager tetap operator-aware untuk
   dashboard/CLI) — lihat regresi `test_f1_documented_operator_level_none_still_exists`.
2. F5: probe satu-kali + fallback berperingat; semantik pid/killpg/reap tidak
   berubah karena `systemd-run --scope` mengeksekusi pada proses yang sama.
3. F4: guard menolak bentuk stdin/eval (curl|bash) dari dalam cgroup —
   pesan mengarahkan ke shell operator; bentuk file selalu di-re-exec.
4. Nomor versi `0.4.1+audit1` hanya ada di branch deploy
   (`audit/live-2026-11-10-deploy`), BUKAN di PR — commit rilis resmi
   (bump `0.4.2` + manifest) tetap tahap operator.

## 2. Verdict RILIS (v0.4.2 resmi)

### **BLOCKED** — menunggu kunci penandatanganan rilis (bukan menunggu kualitas kode)

Alasan dan prasyarat:

1. **Kunci penandatanganan rilis tidak tersedia bagi agent.** Rantai kepercayaan
   rilis (keyring embedded di `install.sh` → manifest `.sig` → sha256 updater)
   menuntut private key (`release/make_release.py --key`); hanya public key
   (`uaeg-release-1..4.pub.pem`) yang ada di repo. Agent **tidak membuat
   pengganti sembarangan dan tidak melewati verifikasi** — sesuai batasan
   master prompt.
2. Yang sudah disiapkan agent: branch + PR #1 hijau penuh (kandidat konten
   v0.4.2). Yang harus dilakukan operator:
   1. review + merge PR #1;
   2. commit rilis `v0.4.2` (bump `pyproject.toml`, `src/uaeg/__init__.py`,
      `install.sh UAEG_RELEASE_VERSION`, README);
   3. `python3 release/make_release.py --key <private-key>` (manifest+sig),
      push tag — workflow `release.yml` memverifikasi ulang signature & hash
      sebelum mempublikasikan;
   4. verifikasi pasca-publikasi dari URL publik (pola laporan lama).
3. **Tidak ada aset rilis historis yang diubah diam-diam**; manifest historis
   (v0.3.3–v0.4.1) utuh. `install.sh` tetap satu file publik untuk
   install/update/repair/status/rollback; kompatibilitas `upgrade.sh` lama
   dijaga (tidak dihapus, tidak berubah kontraknya).
4. Rotasi kunci signing **tidak diperlukan** untuk rilis ini (kunci release-4
   masih sah); manajemen kunci tetap tanggung jawab operator agar tidak hilang
   dan tidak memaksa rotasi tiap sesi.

## 3. Verdict VPS LIVE (host.zerotier.my.id)

### **PASS — patched & healthy** — UAEG v0.4.1+audit1 (commit `8369e6e`), siap untuk pekerjaan proyek utama

| Kriteria | Status |
|---|---|
| Patch audit diterapkan | ✅ 2026-11-10 04:17 WIB via `updater.sh --source` (scope-wrapped); `RECORD.json result=success` |
| Bukti pascapatch | ✅ server_info/shell_exec (key sama, root@microtix), job scope + AT-11 restart-survival, doctor akurat, noise journal 0, dashboard 200/401-anon, data/config/hash identik, TLS sama, zombie 0 |
| Key lama | ✅ tetap aktif dan dipakai seluruh verifikasi (tanpa rotasi/revoke) |
| Dashboard | ✅ login page tersedia; auth password tidak diuji agent (password tidak diminta) — konfirmasi operator tercatat |
| Jalur pemulihan | ✅ auto-rollback terbukti live pada kegagalan nyata; 7 arsip backup di `/var/backups/uaeg/`; runbook di dokumen deployment |
| Identitas instalasi | ✅ dilacak: versi `0.4.1+audit1` + `target_git_sha=8369e6e` pada RECORD + hash file; TIDAK disimpulkan dari string versi saja |
| Naik ke v0.4.2 resmi nanti | ✅ otomatis: `sort -V` `0.4.1+audit1 < 0.4.2` — `sudo bash install.sh` (jalur bertanda tangan) akan menerimanya |

**Catatan posture (bukan blocker):** MCP & dashboard terikat `0.0.0.0`
(demanded oleh arsitektur agent-hosted), `allowed_dashboard_cidrs=null`,
tidak ada host firewall. Perlindungan: TLS + bearer per-request + revoke
instan + password dashboard (scrypt, throttle) + redaksi log. Operator dapat
menambah CIDR allowlist dashboard kapan saja via `uaeg config set`
(fail-closed) bila ingin mempersempit.

## 4. Rekapitulasi acceptance (detail di TEST_EVIDENCE §3)

| Kelompok | Verdict |
|---|---|
| Koneksi agent → UAEG | PASS |
| Eksekusi VPS nyata | PASS |
| File dan direktori | PASS |
| Background dan PTY | PASS |
| Reconnect | PASS |
| Lifecycle systemd | PASS (reboot fisik produksi tidak dilakukan — kode+lab) |
| Kelayakan keamanan | PASS (dalam cakupan; revocation key produksi lab-only) |
| Update dan recovery | PASS (live: sukses + auto-rollback nyata; lab: 290/290) |
| Dashboard live | PASS (batas bukti password) |
| Release | v0.4.1 PASS; **v0.4.2 BLOCKED (signing key)** |
| Patch pada VPS live | **PASS — diterapkan & terverifikasi** |

## 5. Langkah berikutnya yang direkomendasikan

1. **Operator**: review & merge PR #1 → cut v0.4.2 dengan kunci signing →
   `sudo bash install.sh` di VPS (upgrade bertanda tangan; versi naik mulus
   dari `0.4.1+audit1`).
2. **Operator (opsional, cepat)**: aktifkan `network.allowed_dashboard_cidrs`
   untuk membatasi dashboard ke ZeroTier/alamat Anda.
3. **Pemilik proyek utama**: pekerjaan utama dapat dimulai — bridge UAEG
   terbukti sehat end-to-end dengan key yang sama; `server_info` adalah titik
   mulai baku setiap sesi baru.
4. **Saran jangka menengah** (di luar patch ini): pertimbangkan `extra=forbid`
   pada model argumen tool (perilaku abaikan-diam-diam parameter asing saat ini
   terdokumentasi), dan penambahan pengujian e2e khusus self-update dalam
   VM systemd-disposable di CI (kontrak sudah mencakup sebagian besar).
