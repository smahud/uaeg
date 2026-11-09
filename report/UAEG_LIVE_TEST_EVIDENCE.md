# UAEG — Bukti Uji Live 2026-11-10 (Sanitized)

| | |
|---|---|
| **Isi** | Perintah uji yang telah dibersihkan, lingkungan, hasil, exit code, dan hal yang belum diuji |
| **Redaksi** | Semua log bukti disaring: tidak memuat key, Authorization header, cookie, private key, maupun token. Marker uji tidak mengandung rahasia |
| **Log mentah (tersaring)** | `report/evidence/live-audit-2026-11-10/` |
| **Terkait** | [UAEG_LIVE_BRIDGE_AUDIT.md](UAEG_LIVE_BRIDGE_AUDIT.md) · [UAEG_LIVE_FINDINGS_AND_FIXES.md](UAEG_LIVE_FINDINGS_AND_FIXES.md) · [UAEG_DEPLOYMENT_AND_RECOVERY.md](UAEG_DEPLOYMENT_AND_RECOVERY.md) |

---

## 1. Lingkungan pengujian

**Klien (agent cloud):** sandbox Linux, Python 3.12.16, SDK `mcp` 1.27.0
(`streamablehttp_client`), TLS tervalidasi penuh (tidak ada `curl -k` pada
jalur produksi; satu pengecualian dijelaskan di §2-A). Key dibaca dari secret
store lokal (chmod 600), tidak pernah berada di command line, URL, log, atau
commit.

**Server (VPS):** `microtix` — Debian 13 trixie, kernel 6.12.115+deb13.1-amd64,
16 vCPU/1,9 GiB/122 G, systemd 257; UAEG v0.4.1 (pra-audit) → v0.4.1+audit1
(pasca 04:17 WIB); unit `uaeg.service` (root, KillMode=control-group);
`0.0.0.0:3902` MCP-TLS, `0.0.0.0:3903` dashboard-TLS (Let's Encrypt,
kedaluwarsa 2027-02-05).

**Jendela waktu:** 2026-11-10 03:14 – 04:28 WIB. Marker unik per sesi uji,
contoh: `UAEG_AUDIT_20261110_a76a7c96`.

## 2. Hasil per kelompok uji

### A. Perintah sinkron (`shell_exec`) — semua melalui UAEG, bukan shell lokal

| Uji | Perintah (ringkas) | Hasil |
|---|---|---|
| Marker | `echo <MARKER>_SYNC` | exit 0, stdout berisi marker |
| Quoting | `printf '%s\|%s\|%s\n' "a b" 'c;d' "it's"` | stdout `a b\|c;d\|it's` — exit 0 |
| Unicode | `printf 'héllo wörld 你好 🚀 ✓\n'` | round-trip utuh |
| Env inline | `AUDIT_VAR=<m> bash -c 'echo env=$AUDIT_VAR'` | `env=<m>` |
| Env param | `shell_exec(env={"UAEG_AUDIT_ENVVAR":"set_via_env_param"})` | terbaca oleh perintah |
| cwd | `pwd` + `cwd=/tmp` | `/tmp` |
| Exit code gagal | `echo out; echo err >&2; exit 7` | exit 7, stdout/stderr terpisah |
| Perintah hilang | `uaeg_audit_nonexistent_cmd_xyz` | exit 127, pesan bash di stderr |
| Timeout | `sleep 30` + `timeout_seconds=3` | `timed_out=true`, durasi 3002 ms, exit null, `ok=false` |
| Truncation | `seq 1 400000` | `stdout_bytes=2688895`, `stdout_truncated=true`, payload 1 MiB |
| Paralel | 5× `shell_exec` serentak | 5 output berbeda masing-masing benar |

Catatan redaksi/lingkungan: pemeriksaan health lokal di VPS memakai
`curl -sk https://127.0.0.1:3902/...` (flag `-k` HANYA untuk probe loopback
dengan sertifikat hostname publik — verifikasi TLS penuh tetap digunakan untuk
semua koneksi lintas jaringan klien↔VPS).

### B. File & direktori (hanya di direktori uji khusus)

| Langkah | Perintah | Hasil |
|---|---|---|
| Buat | `mkdir -m 700 -p /tmp/UAEG_AUDIT_<m>` | exit 0 |
| Tulis | `printf '<m>_FILE' > .../marker.txt` | exit 0, mode `-rw-r--r--` |
| Baca (panggilan terpisah) | `cat .../marker.txt` | isi persis `<m>_FILE` |
| Hapus | `rm -rf /tmp/UAEG_AUDIT_<m> && test ! -e ...` | `gone` |

Tidak ada file konfigurasi/proyek/data pengguna yang disentuh sebagai uji.

### C. Background job

| Uji | Hasil |
|---|---|
| `shell_exec(background=true)` `sleep 2; echo <m>_BG_DONE` | `job_id` + pid dikembalikan |
| `job_status` | `running` → metadata lengkap (key_id/key_name milik pemanggil) |
| `job_wait` (30 s) | `completed`, exit 0 |
| `job_output` | berisi marker; `offset/available` konsisten |
| Job gagal `sleep 1; exit 9` | status `failed`, exit 9 tercatat |
| `job_cancel` pada `sleep 300` | `cancelled`; status akhir `cancelled` |
| `job_list` | memuat job milik key; pembatasan limit bekerja |
| `job_wait` timeout 2 s pada job 6 s | mengembalikan status `running` tepat waktu; `wait` penuh kemudian `completed` |
| Orphan lintas koneksi | job dilaunch → klien diputus → klien baru (key sama) membaca output marker |
| **AT-11 (pascapatch)** | job `sleep 25; echo AT11_JOB_SURVIVED` di scope `uaeg-job-<id>.scope` → `systemctl restart uaeg` → service pulih 2 s → **job `completed` exit 0 dengan marker** |

### D. Terminal interaktif (PTY)

| Uji | Hasil |
|---|---|
| `shell_open` | session_id, pid, ukuran awal 120×30 |
| `shell_input(data="echo <m>_PTY", add_newline=true)` | `bytes_written` sesuai |
| `shell_output(since_seq=0)` | marker + prompt; kursor `seq` monoton |
| `shell_resize(120,40)` → `stty size` | `40 120` terbaca di output |
| `shell_sessions` | sesi terdaftar dengan `key_id` pemilik |
| `shell_close` | `status=closed` |
| `shell_output` setelah close | buffer akhir masih terbaca (`alive=false`, `exit_code=-1`) — grace window 60 s (desain) |
| Pascapatch (0.4.1+audit1) | round-trip penuh open→input→output→close lulus ulang |

### E. Reconnect & autentikasi

| Uji | Hasil |
|---|---|
| Klien baru, key sama (lintas probe & pascapatch, ≥6 siklus) | initialize + `server_info` + tool bekerja; TANPA key baru |
| Setelah `systemctl restart` (deployment & AT-11) | reconnect sukses dengan key sama |
| Bearer hilang (curl tanpa header) | HTTP 401 `missing bearer token` + `WWW-Authenticate` |
| Bearer salah (satu permintaan negatif, token jelas invalid) | HTTP 401 `invalid or revoked bearer token` |
| `server_info` | selalu melaporkan `key.key_id=k-837c0f289a4a` (identitas konsisten) |

### F. Keamanan (kode + lab + live)

| Area | Bukti |
|---|---|
| Bearer per-request; revoke instan | [analisis kode] `BearerAuthMiddleware` memverifikasi per request via lookup verifier aktif; [lab] e2e hardening eksisting + regresi F1 |
| Fail-closed identitas tak teresolusi (F1) | [lab] 12 tool menolak; [live] pemanggilan sah tetap lulus |
| Key hanya menyimpan SHA-256 verifier | [analisis kode] `db.py`/`security.py`; [live] `uaeg keys list` hanya menampilkan prefix |
| Dashboard: scrypt + session server-side + cookie HttpOnly/SameSite=lax/secure + CSRF token + origin check + throttle per-IP (5 gagal/15 mnt, lockout 15 mnt) + CIDR fail-closed + fail-closed password-hilang | [analisis kode] `dashauth.py`/`dashboard.py`; [lab] suite dashboard-auth & hardening; [live] anon `/api/overview` 401, `/login` 200 |
| Redaksi secret di audit log/journal/metadata | [analisis kode] `security.redact_secrets` (PEM, bearer, URL-password, token dikenal, `mysql -p…`); [lab] tes redaksi; [live] log bukti tersaring |
| TLS | [live] rantai & SAN valid, TLS1.2+, kedua port; [analisis kode] pre-validation saat startup, fail-closed |
| Containment revoke (job/sesi milik key dihentikan) | [lab] e2e eksisting; TIDAK dijalankan pada key produksi (lihat §4) |
| Advisory restricted paths ≠ sandbox | [analisis kode] `runtime.py` eksplisit "NOT a security boundary" — sesuai penegasan master prompt |

### G. Systemd / lifecycle

| Uji | Bukti |
|---|---|
| Listener mati → service berhenti (bukan setengah-hidup) | [lab] `test_amain_returns_nonzero_when_listener_crashes` (eksisting) |
| Stop/start bersih | [live] 3× restart terpantau journal (00:52 operator; 04:13 rollback; 04:17 deploy sukses; +04:27 AT-11) |
| Job bertahan restart service | **[live] pascapatch** (AT-11 di §2-C) — pra-patch memang gagal (temuan F5) |
| Reattach/rekonsiliasi job pasca-restart | [live] startup log `startup reconciliation: {...} jobs`; job scope AT-11 ditemukan `completed` pasca-restart; [lab] kontrak T-05..T-09 |
| Rekonsiliasi pasca-reboot (boot-id guard) | [analisis kode] + [lab]; TIDAK mereboot VPS produksi (§4) |
| Zombie | [live] `zombie_count=0` sebelum & sesudah seluruh uji |

### H. Install / upgrade / backup / rollback — lihat dokumen deployment

Ringkas: fresh-install dan upgrade multi-rilis/repair/check/dry-run/rollback
tercakup `tests/test_upgrade_contract.sh` (290/290). Pada live: upgrade
`--source` sukses + **satu kegagalan nyata (versi non-PEP440) yang memicu
auto-rollback sempurna** — detail di
[UAEG_DEPLOYMENT_AND_RECOVERY.md](UAEG_DEPLOYMENT_AND_RECOVERY.md).

## 3. Tabel kriteria penerimaan (master prompt §9)

| Kriteria | Verdict | Bukti kunci |
|---|---|---|
| Koneksi agent → UAEG | **PASS** | initialize + protokol dinegosiasi; 13 tool ditemukan; `server_info` dengan key valid |
| Eksekusi VPS nyata | **PASS** | `id -u=0`/`root`@`microtix` (≠ sandbox); exit code, stdout, stderr akurat; exit 7/127/timeout terpropagasi |
| File dan direktori | **PASS** | marker dibuat/dibaca/dihapus hanya di `/tmp/UAEG_AUDIT_*` |
| Background dan PTY | **PASS** | siklus job penuh (status/wait/output/cancel/list/orphan) + PTY penuh (open/input/output/resize/sessions/close) |
| Reconnect | **PASS** | ≥6 koneksi baru dengan key sama, termasuk setelah restart service |
| Lifecycle systemd | **PASS** (catatan) | stop/start/restart live terbukti; **job bertahan restart terbukti live pascapatch**; reboot fisik VPS TIDAK dilakukan (rekonsiliasi pasca-reboot dibuktikan lewat kode+lab: boot-id guard + kontrak) |
| Kelayakan keamanan | **PASS** (dalam cakupan) | auth/TLS/session/dashboard/ownership lulus sesuai cakupan §2-F; F1 diperbaiki; revocation/rotasi pada key produksi tidak dieksekusi (lab saja) |
| Update dan recovery | **PASS** | live: upgrade sukses + auto-rollback pada kegagalan nyata + key lama reconnect; lab: kontrak 290/290 (install/upgrade/repair/backup/rollback/failure) |
| Dashboard live | **PASS** (batas bukti) | `/login` 200, API anon 401, doctor "all checks passed"; login dengan password produksi TIDAK diuji agent (password tidak diminta); konfirmasi operator tercatat (00:52:38 WIB dari 172.16.100.1) |
| Release | **PASS untuk v0.4.1; v0.4.2 BLOCKED** | aset v0.4.1 terverifikasi hash vs live; manifest+signature konsisten; kunci signing tidak tersedia → rilis berikutnya menunggu operator |
| Patch pada VPS live | **PASS** | 0.4.1+audit1 diterapkan 04:17 WIB; seluruh verifikasi pascapatch lulus (§2 + dokumen deployment) |

Tidak ada SKIPPED yang dihitung sebagai PASS.

## 4. Hal yang belum diuji (dan alasannya)

1. **Login dashboard dengan password produksi** — password tidak diberikan
   kepada agent dan tidak boleh direset untuk memperoleh bukti; keandalan
   lapisan login dibuktikan lewat kode + suite tes + konfirmasi operator.
2. **Revoke/rotate key produksi** — akan memutus satu-satunya jalur kendali
   agent; perilaku diverifikasi di lab (e2e eksisting) dan lewat kode.
3. **Reboot VPS produksi, kegagalan disk penuh, fault-injection fisik** —
   berisiko terhadap host produksi; mekanisme pemulihannya (boot-id guard,
   rollback offline, backup WAL-konsisten) diverifikasi lewat kode + kontrak
   upgrade di lab.
4. **Uji jangka panjang/beban tinggi** (retensi 20.000 baris log pada skala
   nyata, kehabisan memori berbulan-bulan) — di luar jendela audit; batas
   desain dicatat di dokumentasi.
5. **Kombinasi key ganda secara live** (isolasi antar dua key aktif) — lab
   saja; live hanya memiliki satu key aktif.
