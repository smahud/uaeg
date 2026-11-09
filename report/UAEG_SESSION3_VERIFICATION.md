# UAEG — Laporan Verifikasi Sesi 3 & Penjelasan Error install.sh

| | |
|---|---|
| **Tanggal** | 2026-11-10 (±05:44–06:30 WIB) |
| **Eksekutor** | Sesi audit lanjutan (verifikasi ulang menyeluruh atas pekerjaan sesi 1–2) |
| **Objek** | VPS live `host.zerotier.my.id` (UAEG 0.4.1+audit1) · repo `github.com/smahud/uaeg` · kanal rilis publik |
| **Status akhir** | **VPS live: PASS (sehat, ter-patch, siap dipakai)** · **Repo: fix F1–F8 sudah di `origin/main`** · **Rilis v0.4.2: BLOCKED menunggu signing key operator (bukan masalah kualitas)** |
| **Bukti** | [evidence/session3-verification/SESSION3_SUMMARY.md](evidence/session3-verification/SESSION3_SUMMARY.md) + log pytest/kontrak + 5 laporan sesi sebelumnya |

---

## 1. Ringkasan eksekutif

1. **UAEG live terbukti berfungsi penuh dari luar**: handshake MCP over HTTPS sukses,
   13 tools ditemukan, perintah benar-benar dieksekusi di VPS `microtix` sebagai
   `root@microtix` (Debian 13) — bukan di sandbox auditor (identitas dibedakan
   eksplisit, lihat E3).
2. **VPS menjalankan `0.4.1+audit1`** — hasil patch audit live sesi-2 (commit
   `8369e6e`, dideploy 2026-11-10 04:17 WIB via `updater.sh --source` scope-wrapped,
   `RECORD.json result=success`), yang isinya identik dengan kode `origin/main`
   saat ini (perbedaan hanya string versi).
3. **Semua temuan audit F1–F8 sudah diperbaiki, teruji, dan terverifikasi ulang
   secara independen di sesi ini**: 197 unit + 18 regresi v042 + 21 e2e pytest LULUS,
   suite kontrak upgrade **290 PASS / 0 FAIL**, dan probe live (healthz, doctor,
   key continuity, pencabutan key, journal) semuanya sesuai harapan.
4. **Error `install.sh` yang Anda temui bukan bug — justru pengaman yang bekerja
   benar** (guard anti-downgrade). Tidak ada satu byte pun berubah di `/opt/uaeg`
   akibat perintah itu. Penjelasan & opsi: §2.
5. **Rilis `v0.4.2` resmi BLOCKED** karena private key penandatanganan rilis
   (`uaeg-release-4`) tidak tersedia bagi agent (sesuai kebijakan; agent tidak
   memalsukan tanda tangan). Setelah operator menandatangani & mempublikasikan,
   `install.sh`/`upgrade.sh` akan meng-upgrade bersih melewati `0.4.1+audit1`.

---

## 2. Error `install.sh` Anda — akar masalah & tindakan

### 2.1 Apa yang terjadi

```
==> existing installation detected at /opt/uaeg - safe upgrade via the verified release channel
...
ERROR: target v0.4.1 is OLDER than the installed v0.4.1+audit1 (downgrade).
```

- `install.sh` mendeteksi instalasi eksisting → masuk jalur **safe upgrade via
  verified release channel** (manifest bertanda tangan → sha256 updater → updater
  meng-clone tag `v0.4.1`).
- Updater membandingkan versi: **terpasang `0.4.1+audit1` > target rilis `v0.4.1`**
  (dalam PEP 440, `0.4.1+audit1` adalah `0.4.1` + segmen lokal; pembanding versi
  updater memperlakukan segmen lokal sebagai lebih baru).
- Guard anti-downgrade menolak **sebelum mutasi apa pun** — staging hanya di
  `/tmp/uaeg-bootstrap.*/`, `/opt/uaeg` tidak tersentuh (service tetap aktif,
  healthz tetap `ok` — diverifikasi ulang di sesi ini).

### 2.2 Mengapa VPS "lebih baru" dari rilis

Sesi audit sebelumnya menemukan & memperbaiki 8 temuan (F1–F8) langsung di VPS
(dengan persetujuan alur tugas), dengan nomor versi `0.4.1+audit1`. Kode yang sama
sudah di-merge ke `origin/main` (PR #1, merge `705c792`), tetapi **belum ada tag
rilis yang memuatnya** — rilis terbaru tetap `v0.4.1`. Jadi: VPS menjalankan kode
main + sufiks versi deploy, sementara kanal rilis masih menawarkan `v0.4.1` "polos".

### 2.3 Opsi Anda (urut rekomendasi)

| # | Opsi | Keterangan |
|---|---|---|
| 1 | **Tidak melakukan apa-apa** (direkomendasikan) | `0.4.1+audit1` = kode main terverifikasi (semua tes hijau, live sehat). Tunggu rilis `v0.4.2` resmi. |
| 2 | **Terbitkan v0.4.2 sebagai operator** | Merge PR #2 (`release/v0.4.2-prep`, CI 5/5 hijau) → tandatangani manifest dengan private key `uaeg-release-4` (`python3 release/make_release.py --key <key>`) → push tag → CI mempublikasikan setelah verifikasi ulang. Ikuti [RELEASE_v0.4.2_RUNBOOK.md](RELEASE_v0.4.2_RUNBOOK.md). Setelah itu `./install.sh` akan upgrade normal (`0.4.2 > 0.4.1+audit1`). |
| 3 | `sudo bash upgrade.sh --version 0.4.1 --allow-downgrade` | **TIDAK disarankan** — ini akan menurunkan VPS ke v0.4.1 dan **menghapus perbaikan keamanan F1** (fail-closed identitas key → menutup celah eskalasi level operator), **F4/F5** (ketahanan job/updater terhadap restart service), dan **F6** (higiene log). |
| 4 | `./install.sh --repair` dari checkout lama `~/uaeg` | Sama saja akan ditolak guard versi (sumber `0.4.1` < terpasang `0.4.1+audit1`, exit 5 pra-mutasi). Setelah v0.4.2 rilis, refresh checkout itu dulu. |

Catatan tambahan: checkout `~/uaeg` di VPS adalah sisa era v0.4.1 — setelah v0.4.2
terbit, segarkan (`git fetch && git checkout v0.4.2` atau unduh ulang `install.sh`
baru) agar tidak mengulang kelas insiden "stale checkout" yang pernah terjadi
(2026-11-08, sudah ditutup oleh guard pra-mutasi v0.4.1).

---

## 3. Kondisi terkini (hasil verifikasi sesi ini)

### 3.1 VPS live (`host.zerotier.my.id`)

| Aspek | Temuan |
|---|---|
| Versi / kesehatan | `0.4.1+audit1` · healthz `ok` · aktif sejak 04:27 WIB (restart AT-11 proof) |
| MCP | `:3902` HTTPS, TLS LE valid s.d. 2027-02-05, 401 tanpa bearer, 13 tools lengkap |
| Dashboard | `:3903` HTTPS, `/login` 200, `/api/overview` anonim 401, password+TLS enforced (doctor) |
| Eksekusi | `root@microtix`, Debian 13, kernel 6.12.115+deb13.1 |
| Keys | 1 aktif (`k-c9f935ef39a2` — key sesi ini), 2 revoked; **key lama diuji → 401 per-request** ✓ |
| Job survival | Job berjalan di `system.slice/uaeg-job-*.scope` (F5 aktif); AT-11: job selamat dari restart service nyata, output `AT11_JOB_SURVIVED`, exit 0 |
| Journal | 0 error pada proses patched (2 error terakhir berasal dari proses lama pra-restart — bentuk noise SSE yang difilter F6) |
| Backup | 4 snapshot upgrade; termasuk bukti auto-rollback jujur (lihat §4 item D) |

### 3.2 Repository (`smahud/uaeg`)

| Ref | Isi | Status |
|---|---|---|
| `origin/main` (`705c792`) | Semua fix F1–F8 + 5 laporan live-audit + bukti | Merge PR #1 ✓ |
| `audit/live-2026-11-10` (`121a7ca`) | Head PR #1 (sudah merge) | tertutup |
| `audit/live-2026-11-10-deploy` (`8369e6e`) | **Persis kode di VPS** (+ sufiks `0.4.1+audit1`) | arsip deploy |
| `release/v0.4.2-prep` (`7bdf0a7`) | Bump `0.4.2` + runbook penandatanganan + addendum keputusan rilis | **PR #2 menunggu merge + tanda tangan operator** |
| Tag `v0.4.1` | Rilis publik terbaru (tanpa F1–F8) | inilah sumber error downgrade Anda |

Verifikasi silang: `target_git_sha 8369e6e` di `RECORD.json` VPS == sha branch
deploy di GitHub == kode yang di-audit ulang di sesi ini (diff kode deploy vs main:
hanya string versi + pin installer).

### 3.3 Verifikasi ulang independen (sandbox auditor, checkout `origin/main`)

| Suite | Hasil |
|---|---|
| pytest unit + regresi (non-e2e) | **197 passed** |
| `tests/test_unit_regression_v042.py` (regresi F1–F7) | **18 passed** |
| pytest e2e smoke + hardening | **21 passed** |
| `tests/test_upgrade_contract.sh` (T-01..T-57) | **290 PASS / 0 FAIL** |

(e2e installer-repair tidak dijalankan lokal karena butuh lingkungan root/systemd;
jalur itu tercakup CI GitHub — 5/5 job sukses pada PR #1 & PR #2.)

---

## 4. Disposisi item audit 7.1–7.5 (reproduce / refute + status perbaikan)

| # | Kecurigaan tugas | Verdikt | Bukti kunci |
|---|---|---|---|
| A | `owner_key_id=None` dapat eskalasi ke level operator | **Dikonfirmasi (pra-patch) → SUDAH DIFIKSIKAN (F1)** | Lapisan tool kini fail-closed: identitas key tak ter-resolve ⇒ tolak (bukan teruskan `None` yang dianggap operator). Regresi `test_f1_*` lulus; manager tetap operator-aware untuk dashboard/CLI (by design, terdokumentasi) |
| B | `uaeg status`/`doctor` tidak murni read-only | **DITOLAK untuk kode saat ini** | Analisis batas fungsi ketat: `cmd_doctor`/`cmd_status` tanpa panggilan tulis apa pun; `cfg.save()` hanya di `cmd_config set`. (Ditambah F2: doctor kini menampilkan exposure bind asli `0.0.0.0` + peringatan) |
| C | Proses self-update ikut terbunuh saat service restart (cgroup) | **Dikonfirmasi — terjadi LIVE saat deploy-1 (job shim mati saat service stop) → DIFIKSIKAN (F4+F5)** | F4: updater mendeteksi cgroup service & re-exec ke scope transient; F5: tiap job di-scope-kan. Bukti live: deploy job di scope, AT-11 restart-survival |
| D | Backup SQLite harus memuat WAL; kegagalan restore tak boleh dilaporkan sukses | **Sudah benar — terverifikasi kode + LIVE** | Backup memakai online backup API (`src.backup(dst)`, konsisten transaksional, menangkap isi WAL; file `-wal`/`-shm` live sengaja di-skip). Bukti live: deploy-1 gagal (versi `zaudit1` non-PEP440) → `result:"rolled-back"`, `rollback_verified:true`, service pulih — dilaporkan jujur |
| E | Setelah upgrade, key yang sama harus tetap autentikasi | **Terverifikasi LIVE dua kali** | Sesi-2: key sama tetap aktif pasca-deploy (keys_before==keys_after). Sesi-3: key baru hasil rotasi autentikasi normal; key lama dicabut → 401 per-request |
| F | Restricted paths hanyalah advisory, bukan sandbox | **Dikonfirmasi sebagai desain — terdokumentasi jujur** | `check_restricted_cwd` eksplisit "advisory, not a security boundary" (hanya untuk key mode `restricted`, hanya cek cwd); LIMITATIONS.md #1/#2/#10 menyatakan tidak ada sandbox & tidak ada RBAC. Operator tidak boleh mengandalkannya sebagai boundary |
| G | Aset rilis harus terikat digest tepercaya | **Sudah benar — terverifikasi** | Rantai: keyring Ed25519 embedded di `install.sh` → verifikasi tanda tangan manifest (terlihat di output install.sh Anda) → sha256+size updater vs manifest → updater meng-clone tag via HTTPS → CI memverifikasi ulang sebelum publish. Suite kontrak T-01..T-57 (290/290) mencakup penolakan tamper/foreign-key/manifest-hilang |

---

## 5. Matriks acceptance (per kelompok uji)

| Kelompok | Cakupan | Status | Bukti |
|---|---|---|---|
| A — perintah sinkron | A1–A10: marker, quoting, unicode, env inline/param, cwd, command-not-found, timeout, truncation, param ekstra | **PASS** | sesi-2 `02_capabilities/02b_retest` + verifikasi ulang sesi-3 |
| B — operasi file | B1–B4 (hanya `/tmp/uaeg-audit-test`) | **PASS** | sesi-2 `02_capabilities` |
| C — job background | C1–C14: start/status/wait/output, fail-path, cancel + status pasca-cancel, list, orphan, wait-timeout | **PASS** | sesi-2 `02_capabilities/02b_retest` |
| D — PTY | D1–D7: open/input/output/resize(+bukti stty)/sessions/close/output-pasca-close | **PASS** | sesi-2 `02_capabilities/02b_retest` |
| E — koneksi ulang key sama | job/sessions lintas sesi; 30+ inisialisasi ulang sukses di sesi-3 | **PASS** | sesi-2 `E2–E5` + seluruh sesi-3 |
| Dashboard | login 200, API anonim 401, logout, lockout Retry-After, CSRF, cap 1 MiB | **PASS** | probe live sesi-3 + browser-audit 20261109 + e2e hardening 21/21 |
| Destruktif — restart service | wajib di VM sekali-pakai | **PASS (live, terkendali)** | restart diperlukan untuk deploy patch + AT-11 proof; service pulih otomatis |
| Destruktif — rollback/upgrade gagal | wajib di VM sekali-pakai | **PASS (live tak-sengaja + lab)** | deploy-1 gagal → auto-rollback live terverifikasi; suite kontrak 290/290 (fault-injection) |
| Destruktif — VM Debian sekali-pakai ber-systemd | sesuai mandat tugas | **SKIPPED** | sandbox auditor tanpa systemd; jalur ini tercakup CI + suite kontrak + bukti live di atas |
| Rilis v0.4.2 resmi | tag + manifest bertanda tangan | **BLOCKED** | menunggu private key `uaeg-release-4` di tangan operator (tidak dipalsukan) |

---

## 6. Langkah operator berikutnya (ringkas)

1. **Merge PR #2** (`release/v0.4.2-prep` → `main`) — konten sudah CI-hijau 5/5.
2. **Tandatangani & terbitkan v0.4.2**: `python3 release/make_release.py --key <uaeg-release-4.private.pem>` → push tag `v0.4.2` → CI memverifikasi ulang signature+hash lalu mempublikasikan (detail lengkap: [RELEASE_v0.4.2_RUNBOOK.md](RELEASE_v0.4.2_RUNBOOK.md)).
3. **Upgrade VPS**: cukup jalankan ulang `./install.sh` (atau `upgrade.sh`) — `0.4.2 > 0.4.1+audit1`, guard akan lolos; key, konfigurasi, dan data dipertahankan (terbukti pada seluruh upgrade sebelumnya).
4. **Segarkan checkout `~/uaeg`** di VPS setelah rilis (mencegah insiden stale-checkout).
5. Simpan private key rilis di tempat aman (out-of-band); rotasi key tidak diperlukan untuk rilis ini.

## 7. Batas kejujuran (apa yang TIDAK diklaim)

- Rilis `v0.4.2` **belum ada** — sampai operator menandatangani, `install.sh`
  akan terus menolak "downgrade" ke v0.4.1. Ini status, bukan kegagalan.
- Pengujian kelas VM-sekali-pakai ber-systemd **tidak dijalankan** di sesi ini
  (sandbox tanpa systemd) — dikompensasi CI + suite kontrak + bukti live AT-11/rollback.
- UAEG adalah gateway eksekusi root **tanpa sandbox** (per PRD): keamanan bergantung
  pada kerahasiaan key agent + TLS + revocation — tidak ada klaim "sepenuhnya aman".
- Dua error journal terakhir (SSE-writer) berasal dari proses pra-patch; proses
  saat ini bersih, namun pemantauan `journalctl -u uaeg` tetap disarankan.

---

## 8. Tindak lanjut sesi 3 (merge, commit, push — atas perintah operator)

Setelah verifikasi di atas, atas instruksi operator ("lanjutkan merge, commit,
and push; bump version ke 1.0.0 jika diperlukan jika project sudah benar-benar
final dan lulus semua uji"):

1. **PR #2 di-merge** ke `main` (merge commit `4e40c66`).
2. **Versi di-bump ke `1.0.0`** (pyproject, `__init__.py`, `install.sh`, README)
   — melompati rilis 0.4.2 terpisah; lihat addendum §7 di
   `UAEG_LIVE_RELEASE_DECISION.md` untuk rasional dan catatan kejujuran.
3. **Runbook dirilis-ulang** sebagai `RELEASE_1.0.0_RUNBOOK.md`.
4. **Suite penuh dijalankan ulang pada tree 1.0.0** sebelum push (hasil di
   `evidence/session3-verification/`).
5. **Push `main`** — CI 5/5 harus hijau pada HEAD baru.
6. **Tag `v1.0.0` + manifest bertanda tangan tetap langkah operator** (private
   key `uaeg-release-4` tidak pernah berada di tangan agent; tidak ada tanda
   tangan palsu). Setelah terbit: `sudo bash install.sh` di VPS meng-upgrade
   `0.4.1+audit1` → `1.0.0` dengan key yang sama.
