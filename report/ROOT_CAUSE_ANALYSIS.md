# Root Cause Analysis — Insiden 2: Konfirmasi Upgrade v0.3.4 (2026-11-08)

**Operator transcript (VPS MicroTix, root, /opt/uaeg v0.3.3 -> v0.3.4):**

```
UPGRADE PLAN
  installation   : /opt/uaeg (v0.3.3)
  target         : v0.3.4 (v0.3.4, commit 4bda64a)
  data           : /var/lib/uaeg (1 agent keys)
  configuration  : /etc/uaeg/config.json (preserved as-is - not modified)
  backup         : /var/backups/uaeg/upgrade-20261108-203855 (consistent DB + config + app)
  service        : controlled restart of uaeg (downtime ~15-60 s)

Continue with the upgrade to v0.3.4? [y/N] y
ERROR: cancelled by the operator
```

Operator menjawab `y`; upgrade ditolak dengan alasan yang salah.

## Root cause

`updater.sh` (v0.3.4) membaca jawaban konfirmasi y/N dari **stdin skrip**:

```bash
read -r -p "Continue with the upgrade to v$TARGET_VERSION? [y/N] " reply
[[ "$reply" =~ ^[Yy]$ ]] || { rm -f "$pre"; die "cancelled by the operator"; }
```

Kontrak yang benar: jawaban harus dibaca dari **keyboard operator**. stdin
bukan keyboard pada banyak bentuk eksekusi nyata:

| Bentuk eksekusi | Apa yang dibaca `read` | Akibat di v0.3.4 |
|---|---|---|
| `bash upgrade.sh` interaktif (stdin = tty) | keyboard operator | bekerja (satu-satunya bentuk yang benar) |
| stdin terputus (`< /dev/null`, sudo/CI) | EOF seketika | **mati diam-diam**: `set -e` membunuh skrip pada `read` yang gagal — rc=1 TANPA pesan error |
| skrip lewat stdin (`curl ... \| bash`) | baris skrip berikutnya | **false cancellation** — `reply` berupa teks skrip, regex gagal, salah lapor "cancelled by the operator"; prompt tidak tampil; dan `BASH_SOURCE[0]` unbound (crash `set -u`) di line 59 sebelum prompt |
| pipe otomasi (`printf 'y\n' \| bash ...`) | jawaban terpipah | bekerja — inilah mengapa test --yes/terpipah + CI tidak pernah menangkapnya |

Dua cacat pelaporan sekaligus (keduanya melanggar semangat U-2/U-10 yang
dipelajari dari insiden v0.3.1 "jangan salah melaporkan penyebab"):
1. Penolakan dikaitkan ke operator padahal penyebabnya input yang tidak
   terbaca ("cancelled by the operator" setelah operator menjawab y).
2. Kegagalan baca input tidak dilaporkan sama sekali (silent death).

**Test gap:** seluruh suite kontrak memakai `--yes` atau stdin terpipah;
jalur prompt interaktif tidak punya satu pun assertion otomatis — bug ini
lolos ke rilis v0.3.4 melalui celah itu.

## Reproduksi (bukti fail-first)

`report/evidence/incident_repro_confirm_v034.txt` — updater v0.3.4 PUBLISHED
bytes (sha256 `5b719117...` == aset rilis v0.3.4) vs worktree v0.3.5,
terhadap instalasi terisolasi (v0.3.3 -> v0.3.4, 1 agent key — persis state
operator), terminal operator disimulasikan dengan pty sungguhan (`script(1))`:

- **pre-A** (jawab `y` di terminal, stdin terputus): rc=1, upgrade ditolak —
  insiden terreproduksi dengan byte yang persis dipublikasikan.
- **pre-B** (updater via stdin ala `curl|bash`): crash `BASH_SOURCE[0]:
  unbound variable`.
- **post-A/post-B** (v0.3.5): prompt TAMPIL, jawaban `y` terbaca dari
  `/dev/tty`, upgrade selesai — kunci agent identik, `UPGRADE SUCCESSFUL`.
- **post-D** (jawab `n`): penolakan akurat "cancelled by the operator"
  (perilaku benar yang dipertahankan).
- **post-F** (tanpa terminal, tanpa jawaban): pesan akurat "no confirmation
  was received ... pass --yes" (dulu: mati diam-diam).

## Fix (v0.3.5)

`confirm_ask()` di `updater.sh` dan `install.sh` (dokumentasi kontrak:
UPGRADE_CONTRACT.md **U-15**):
1. stdin adalah terminal -> baca stdin (run interaktif normal).
2. ada terminal pengendali -> baca `/dev/tty` (curl|bash, stdin terputus,
   sudo); prompt tetap tampil (dibuktikan T-32; suppression bug tertangani
   dengan probe subshell agar `2>/dev/null` tidak menelan prompt).
3. tidak ada terminal -> terima satu baris terpipah (otomasi) atau laporkan
   kegagalan dengan jujur.
Jawaban `y`/`yes` (huruf besar/kecil, CR/spasi di-trim) diterima; baris
kosong = tidak (default `[y/N]`). Dua situs konfirmasi updater (upgrade +
rollback) dan satu situs installer (OS tidak didukung) diganti. `BASH_SOURCE`
diberi fallback `:-bash` sehingga eksekusi via stdin tidak crash.

**Regresi permanen:** T-32..T-38 (tests/test_upgrade_contract.sh) — pty
sungguhan, keenam bentuk eksekusi, assertion pada rc + versi + kunci +
pesan + TAMPILNYA prompt + TIDAK adanya false "cancelled".

---

# Root Cause Analysis — Insiden Upgrade v0.3.1 (VPS MicroTix)

---

# Root Cause Analysis — v0.3.4 round-2 findings

## R2.1 V-1/V-4 — PTY final output & exit status lost (P1)

**Symptom:** after a session's process exited, `shell_output` returned
"unknown or expired session_id" and `exit_code` was always `null`
(`shell_close` likewise).

**Root cause (two independent defects):**
1. `_on_readable` treats master-fd EIO/EOF as session exit and calls
   `_mark_exited`, which popped the session from `self.sessions` — in the
   SAME event-loop batch as the final `feed()`. The ring buffer still held
   the tail bytes, but the registry no longer resolved the id, so the data
   was unreachable by design.
2. Every `waitpid` site discarded the child's status variable (`pid, _ =`),
   so nothing ever set `session.exit_code`.

**Fix:** capture the status at every reap site (`_set_exit_code`); keep the
closed session in the registry for a bounded grace window (60 s) with lazy
expiry; `call_later` drops it afterwards. Reproduced before the fix (5 ms
and 200 ms polling both failed to ever see the tail); regression tests
`test_pty_final_output_and_exit_code_survive_process_exit`,
`test_pty_close_reports_exit_code`, `test_pty_closed_session_expires_after_grace`.

## R2.2 D-1 — dashboard auth silently disabled by file deletion (P1)

**Symptom:** `rm /var/lib/uaeg/dashboard-pass.json` at runtime turned the
password-protected dashboard (any bind, including public) into the
unauthenticated legacy mode — no restart needed.

**Root cause:** the auth middleware re-evaluates
`PasswordManager.is_configured()` on every request (that is what makes
`uaeg pass` take effect without restart). `_refresh()` mapped "file gone"
to `record=None, broken=False`, i.e. "never configured" — the fail-open
direction. A corrupt file correctly mapped to `broken=True` (fail closed),
so only the DELETATION path was wrong. The startup guard
(`_check_dashboard_public_guard`) runs once, so runtime deletions bypassed
it entirely.

**Threat model relevance:** UAEG agents run as root BY DESIGN; any
authenticated (possibly compromised) agent could execute the `rm` and gain
an unauthenticated key-issuance endpoint — equivalent to persistent access.

**Fix:** sticky `_ever_configured` state — once a password has been observed,
a missing file is treated as broken: authentication stays enforced,
verification fails closed, `generation()` returns `None` which invalidates
all live sessions, and login returns 503 with repair instructions until the
operator restores the file or runs `sudo uaeg pass`. Recovery tested.

## R2.3 V-2 — job shim zombies

**Root cause:** `spawn()` created the shim with `subprocess.Popen` and
dropped the reference; Python reaps `_active` Popen objects only when the
NEXT Popen runs, so on an idle server the dead shim stays in `Z` state
indefinitely (one zombie per finished job).

**Fix:** keep the Popen handle on `JobRuntime`; reap deterministically
(bounded off-loop wait) from the watcher's final-state branch and from
`cancel()`.

## R2.4 V-3 — orphaned process on DB-insert failure

**Root cause:** ordering `Popen` → `insert_job` → watcher. A failed insert
(disk full / db locked) left a running, unrecorded, unkillable-via-UAEG
process, and the raw `sqlite3.OperationalError` escaped the tool layer
(which maps only `JobError`).

**Fix:** kill + reap the shim on insert failure and raise `JobError` with
the underlying reason.

## R2.5 MCP-D1 — cross-key resource access

**Root cause (design gap, not a bug):** the trusted-operator MVP stored
`key_id` on every session/job row but never compared it on access; tools
resolving only by id allowed any key to drive another key's terminal
(including raw PTY input injection) and to enumerate/cancel its jobs.

**Fix:** ownership enforced at the manager layer (`owner_key_id` parameter
+ `_gate()`); listing tools filter by caller. The operator surface
(dashboard, CLI, containment) is unaffected. LIMITATIONS.md #9 updated —
this was a documented limitation that round 2 promoted to an enforced
invariant (the documented single-key deployment is unaffected).

## R2.6 Key rotation (release engineering)

The v0.3.3 release manifest's Ed25519 signature was verified against the
committed `uaeg-release-1` public key — the pair is real — but the private
half exists nowhere this audit can reach (repository, full git blob scan,
sandbox, GitHub). Signing v0.3.4 therefore required rotating to
`uaeg-release-2` via the documented exceptional procedure. Consequences are
fail-closed and operator-visible (see PATCH_CHANGELOG §E); the bootstrap
keyring trusts both keys so historical manifests still verify.


**Tanggal:** 2026-11-07/08 · **Severity: P0** · **Ditemukan pada:** `upgrade.sh` v0.3.1 (tag `v0.3.1`, commit `225110b`) · **Diperbaiki pada:** v0.3.2 (commit `5dfcb0a`)

---

## 1. Transkrip insiden (operator, root@microtix:~/uaeg)

```
$ curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
$ curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
$ sha256sum -c upgrade.sh.sha256
upgrade.sh: OK
$ bash upgrade.sh

==> UAEG safe-upgrade installer
==> mengambil update terbaru dari origin
==> target rilis terbaru: v0.3.1

kesalahan:
Berkas pohon kerja tak terlacak berikut
akan ditimpa oleh checkout:
upgrade.sh

Mohon pindahkan atau hapus sebelum
Anda berganti cabang.

ERROR: tag/ref 'v0.3.1'
tidak ditemukan setelah fetch
```

Gejala: upgrade gagal (exit 1) **dengan pesan yang salah** — operator
diarahkan menyimpulkan tag `v0.3.1` hilang dari remote, padahal tag tersebut
**ada** (terverifikasi: `git ls-remote` GitHub menampilkan `v0.3.1`, dan
GitHub Release v0.3.1 terbit dengan aset lengkap pada 2026-11-07T22:15:21Z).

## 2. Reproduksi (evidence: `report/evidence/`, dijalankan ulang terisolasi)

Skenario operator direkonstruksi persis di lingkungan terisolasi
(`/home/z/my-project/scripts/repro_incident.sh`):

1. Bare remote berisi histori sampai `v0.3.0`; operator `git clone`
   (tag lokal usang: hanya `v0.3.0`).
2. Remote maju ke `v0.3.1` (simulasi rilis) — clone operator TIDAK maju.
3. Operator mengunduh aset rilis **ke dalam direktori clone-nya**:
   `upgrade.sh` + `upgrade.sh.sha256` menjadi file **untracked** di working
   tree (persis alur `curl -fsSLO` dari `~/uaeg`).
4. `sha256sum -c` lolos — aset utuh.
5. `bash upgrade.sh` (via `unshare --map-root-user`, skrip **byte-identical**
   dengan aset rilis v0.3.1 — tanpa patch apa pun).

Hasil reproduksi (output lengkap: lihat langkah audit dalam
`PATCH_CHANGELOG.md` §evidence):

```
==> mengambil update terbaru dari origin
==> target rilis terbaru: v0.3.1
error: The following untracked working tree files would be overwritten by checkout:
	upgrade.sh
Please move or remove them before you switch branches.
Aborting
ERROR: tag/ref 'v0.3.1' tidak ditemukan setelah fetch
exit code = 1
```

**100% identik dengan transkrip operator** (pesan git berbahasa Inggris pada
reproduksi karena locale; milik operator berbahasa Indonesia — isi sama).

## 3. Root cause (dua cacat berlapis)

### RC-1 — Cacat desain: mutasi working tree operator (penyebab utama)

`materialize_source()` v0.3.1 memilih "git checkout mode" ketika
`upgrade.sh` berada di dalam clone git operator:

```bash
elif [[ -f "$SCRIPT_DIR/pyproject.toml" && -d "$SCRIPT_DIR/.git" ]]; then
  ...
  git -C "$SCRIPT_DIR" -c advice.detachedHead=false checkout -q --detach "$TARGET_REF" \
    || die "tag/ref '$TARGET_REF' tidak ditemukan setelah fetch"
```

`git checkout v0.3.1` dijalankan **di dalam working tree operator**. Tag
`v0.3.1` melacak file `upgrade.sh` (fitur baru v0.3.1), sedangkan di working
tree operator `upgrade.sh` hasil unduhan berstatus **untracked**. Git menolak
menimpa file untracked → checkout gagal.

Guard "tree kotor" yang ada pun tidak menangkapnya karena sengaja hanya
memeriksa file **tracked**:

```bash
git -C "$SCRIPT_DIR" status --porcelain --untracked-files=no
```

— unduhan operator (untracked) lolos dari guard, lalu menggagalkan checkout.

### RC-2 — Cacat pelaporan: salah klasifikasi kegagalan (penyebab pesan menyesatkan)

`|| die "tag/ref '$TARGET_REF' tidak ditemukan setelah fetch"` mengubah
**semua** kegagalan checkout menjadi "tag tidak ditemukan". Konflik
untracked-file, file kotor, shallow clone, HEAD detached — semuanya
dilaporkan sebagai tag hilang. Pelacakan masalah operator jadi keliru arah
(mencari masalah di remote/rilis, padahal masalahnya lokal).

## 4. Kondisi pemicu (semua harus ada)

| # | Kondisi | Status pada insiden |
|---|---------|---------------------|
| 1 | `upgrade.sh` dijalankan dari dalam clone git UAEG | ✅ `~/uaeg` |
| 2 | Ada instalasi UAEG terdeteksi (guard lolos) | ✅ `/opt/uaeg` |
| 3 | Aset rilis diunduh ke dalam clone (untracked) | ✅ `curl -fsSLO` di `~/uaeg` |
| 4 | Tag target melacak `upgrade.sh` | ✅ sejak v0.3.1 |
| 5 | Tag lokal usang (clone dibuat sebelum rilis) | ✅ clone era v0.3.0 |

Kombinasi ini adalah **alur resmi yang didokumentasikan** (docs/INSTALL.md
v0.3.1 mengarahkan persis seperti ini), sehingga insiden bukan salah pakai
operator melainkan cacat produk.

## 5. Dampak

- Upgrade tidak bisa dijalankan dengan alur dokumentasi resmi (P0).
- Pesan error menyesatkan → diagnosis salah arah (P0, diagnostics).
- Tidak ada kerusakan data: kegagalan terjadi **sebelum** backup/installer
  dijalankan (verifikasi: 0 direktori backup tercipta; DB dan konfigurasi
  tak tersentuh; working tree operator tetap utuh).

## 6. Perbaikan (v0.3.2) — ringkas

1. **Source rilis SELALU di-fetch ke staging clone terisolasi**
   (`mktemp -d`, mode 0700) — tidak pernah lagi checkout/fetch/reset/clean
   di working tree operator. Rincian: `PATCH_CHANGELOG.md`.
2. **Resolusi tag dari remote** (`git ls-remote`), bukan tag lokal yang
   mungkin usang; tag diverifikasi **sebelum** clone (pesan akurat untuk
   "tag tidak ada" vs "jaringan gagal" vs "clone tidak berada di commit tag").
3. Jalur `|| die "... tidak ditemukan setelah fetch"` **dihapus** — tidak
   ada lagi operasi checkout yang bisa salah diklasifikasi.
4. Kontrak upgrade dinaikkan ke **v2** (docs/UPGRADE_CONTRACT.md) dan
   regresi dikunci permanen oleh test T-17 (skenario insiden persis),
   T-13/T-14 (tree operator tak tersentuh), T-18 (pesan akurat),
   T-19 (standalone penuh).

## 7. Verifikasi perbaikan terhadap skenario insiden

`repro_fix.sh` (aset = upgrade.sh v0.3.2, skenario identik §2):

```
==> meng-clone tag v0.3.2 dari <remote> ke staging terisolasi
==> clone operator ... TIDAK disentuh (tetap pada kondisi semula)
...
UPGRADE BERHASIL: v0.3.0 -> v0.3.2
exit code = 0
operator tree HEAD after: main @ 7117b8b (identik dengan sebelum)
operator tree status   : ?? upgrade.sh ?? upgrade.sh.sha256 (utuh)
keys preserved         : k-test-1:active
semua check zero-data-loss lolos
```

Ditegakkan permanen oleh **T-17** dalam `tests/test_upgrade_contract.sh`
(95 PASS / 0 FAIL — `report/evidence/upgrade_contract_suite_v0.3.2.txt`).

---

# Root Cause Analysis — Insiden 3: `install.sh --repair` Gagal, `DashboardConfig.__init__() got an unexpected keyword argument 'tls'` (2026-11-08)

**Severity:** P0 · **Layanan terdampak:** dashboard publik `host.zerotier.my.id:3903` (proyek P0-3 tertunda karena insiden ini) · **Status:** root cause TERKONFIRMASI dengan reproduksi penuh; perbaikan dirilis di **v0.4.1**.

## 1. Symptom (transkrip operator)

```
$ bash install.sh --repair
...
CONFIG_ERROR: existing configuration is invalid: configuration contains an
unknown or mistyped field: DashboardConfig.__init__() got an unexpected
keyword argument 'tls'. Remove fields that do not belong to this UAEG version.
ERROR: configuration could not be written - see the error above
WARNING: installation failed - rolling back to the previous working state
```

Repair gagal; aplikasi dipulihkan oleh rollback; dashboard 3903 tetap tidak
dapat diakses. Keanehan kunci: **v0.4.0 seharusnya MENGENAL field `tls`**
(ditambahkan sejak v0.3.0) — jadi mengapa skema yang menolaknya?

## 2. Bukti lapangan

- `GET https://host.zerotier.my.id:3902/healthz` (2026-11-09 02:41 UTC):
  `{"status":"ok","service":"UAEG","version":"0.4.0","uptime_seconds":22775}`
  — service HIDUP pada v0.4.0, uptime ±6,3 jam (konsisten dengan
  deploy v0.4.0 → --repair gagal → rollback → restart).
- Port 3903: connection refused (dashboard tidak pernah kembali).
- Flag `--repair` **hanya ada sejak install.sh v0.4.0** (v0.2.0–v0.3.5:
  `unknown option: --repair`, diverifikasi dari tag historis) — sehingga
  checkout tempat operator menjalankan perintah memuat install.sh era v0.4.0.

## 3. Root cause (berlapis)

**RC-1 (mekanisme langsung) — urutan operasi installer v0.4.0: mutate-first,
validate-later.** Alur in-place v0.4.0: stop service → backup → **salin
source checkout ke /opt/uaeg** → `pip install -e` (venv kini menunjuk kode
checkout) → **baru kemudian** validator konfigurasi berjalan. Validator
memuat `uaeg.config` **dari venv** — yaitu kode yang BARU SAJA disalin —
lalu memuat konfigurasi lama dengan skema tersebut.

**RC-2 (kondisi) — checkout sumber BASI/CAMPURAN.** `--repair` dijalankan
dari pohon sumber pra-v0.3.0 (`src/uaeg/config.py` tanpa field
`DashboardConfig.tls`) yang memuat install.sh era v0.4.0 — pohon campuran
(mis. `install.sh` baru diunduh ke clone lama, atau pull parsial).
`DashboardConfig` pra-v0.3.0 + konfigurasi produksi yang MEMUAT
`dashboard.tls` → `TypeError: unexpected keyword argument 'tls'` —
**persis** pesan produksi (dibungkus `ConfigError` oleh `from_dict`).

**RC-3 (pengungkit) — tidak ada pemeriksaan urutan versi di jalur --repair.**
`updater.sh` (jalur safe-upgrade) menolak downgrade (exit 5) sejak v0.3.4 —
tetapi jalur `--repair` in-place v0.4.0 tidak punya pemeriksaan setara, dan
validator skema berjalan SETELAH aplikasi diganti. Interpretasi juga tidak
disanitasi (`$VENV_DIR/bin/python` dipanggil langsung; PYTHONPATH/cwd
operator dapat membayangi).

**Kombinasi ketiganya** menjelaskan seluruh gejala: repair "seharusnya
harmless" justru mengganti aplikasi baru dengan kode lama, gagal pada
validasi pasca-mutasi, memicu rollback, dan meninggalkan service dalam
kondisi terhenti sementara (dashboard 3903 tetap down; MCP kembali setelah
restart oleh operator/rollback).

## 4. Reproduksi (fail-first, aset publik)

`report/evidence/incident_repro_v041.txt` (skenario penuh;
`scripts` pengerjaan di luar repo):

- **[A] Insiden:** baseline v0.4.0 terpasang (konfigurasi membawa
  `dashboard.tls`), lalu `bash install.sh --repair` dari checkout campuran
  (install.sh v0.4.0 + source pra-v0.3.0) dengan installer **v0.4.0 yang
  dipublikasikan**:
  ```
  CONFIG_ERROR: existing configuration is invalid: configuration contains an
  unknown or mistyped field: DashboardConfig.__init__() got an unexpected
  keyword argument 'tls'. ...
  WARNING: installation failed - rolling back to the previous working state
       -> restored previous application files
  ```
  rc=1 — **pesan produksi direproduksi byte demi byte**; rollback
  mutate-first-then-fail terkonfirmasi.
- **[B] Perbaikan:** installer **v0.4.1** ditempatkan di checkout basi yang
  SAMA, perintah yang SAMA:
  ```
  ERROR: REFUSING to deploy OLDER source over the installed application.
    installed application : v0.4.0
    source to be deployed : v0.2.0
  This is exactly the mechanism of the 2026-11-08 production incident ...
  NOTHING was changed.
  ```
  rc=5; **pohon aplikasi byte-identical, konfigurasi byte-identical**
  (hash diverifikasi sebelum/sesudah).

## 5. Fix (v0.4.1) — preflight pre-mutation di install.sh

1. **Guard 1 — urutan versi (exit 5):** `--repair` dari sumber LEBIH LAMA
   dari aplikasi terpasang ditolak SEBELUM service dihentikan / file apa pun
   diganti. Varian standalone (installer ter-pin lama) juga ditolak
   pre-download. Jalur resmi downgrade tersurat:
   `UAEG_ALLOW_DOWNGRADE=1` (di-propagate `updater.sh --allow-downgrade`).
2. **Guard 2 — probe skema ter-staging:** source di-stage ke direktori
   sementara dan `uaeg.config.Config.load()` dijalankan TERHADAP
   KONFIGURASI YANG ADA dengan `PYTHONPATH=$stage/src` — ketidakcocokan
   skema (klas insiden) ditolak sebagai `CONFIG_INCOMPATIBLE` pre-mutation.
   Probe juga memverifikasi origin `uaeg.__file__` (RUNTIME_MISMATCH,
   exit 7) terhadap sumber ter-stage.
3. **Penulis konfigurasi tersanitasi + bukti origin (exit 9):** semua step
   Python yang menyentuh konfigurasi kini berjalan via `app_python()`
   (`env -u PYTHONPATH … -P`) dan memverifikasi modul `uaeg` yang dimuat
   berasal dari `$INSTALL_DIR/src`.
4. **Stop-service kini hard-fail** (bukan `|| true`): file aplikasi tidak
   pernah diganti di bawah service yang masih hidup.
5. **Verifikasi listener dashboard positif:** setelah health-check MCP,
   installer membuktikan dashboard menjawab pada bind efektifnya
   (menggunakan konfigurasi efektif, bukan flag invokasi) — regresi P0-3
   class tertangkap sebelum installer menyatakan sukses.

Ditegakkan permanen oleh **T-53..T-57** (33 assertion) +
`tests/test_unit_regression_v041.py` (7 test) — lihat TEST_RESULTS.md.
