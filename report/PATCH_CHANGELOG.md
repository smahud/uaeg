# Patch Changelog — v0.4.1 → v1.0.0

**Date:** 2026-11-10 · Fokus: rilis stable pertama (milestone
operator-approved) via **rotasi kunci penandatanganan 4→5** — kunci privat
`uaeg-release-4` tidak lagi tersedia bagi publisher (bentuk operasional
sama dengan rotasi 1→2, 2→3, 3→4; `docs/BOOTSTRAP.md`; otorisasi operator
2026-11-10). Tidak ada perubahan perilaku aplikasi; konten patch F1–F8
sudah ter-deploy live sebagai `0.4.1+audit1` (target_git_sha `8369e6e`).

## A. Rotasi keyring (operator-visible)

### `install.sh` / `upgrade.sh`
- `_TRUSTED_KEY_5` (`uaeg-release-5`) ditambahkan ke keyring ter-embed;
  kunci publik baru di `release/keys/uaeg-release-5.pub.pem`
  (fingerprint `b8e0460b6149a429`).
- `upgrade.sh`: `BOOTSTRAP_VERSION` 4→5; `BOOTSTRAP_KEY_IDS` +
  `uaeg-release-5`; fingerprint key-5 di `--bootstrap-info`;
  `KEY_DIR/5.pub`.
- `install.sh`: `trusted/5.pub`.

### Dokumentasi
- `docs/BOOTSTRAP.md`: paragraf "Rotation 4 -> 5 at v1.0.0" + daftar kunci
  terkini.
- `docs/UPGRADE_CONTRACT.md`: bagian "v5 (UAEG v1.0.0)".
- `README.md`, `.github/workflows/release.yml`: teks kunci penandatangan
  diperbarui (stale sejak v0.4.0).
- `report/RELEASE_1.0.0_RUNBOOK.md`, `report/UAEG_LIVE_RELEASE_DECISION.md`:
  resolusi penandatanganan + addendum sesi 4.

## B. Manifest rilis

- `release/manifests/v1.0.0/*` — manifest + tanda tangan Ed25519
  (`uaeg-release-5`) + sidecar sha256; diverifikasi
  `release/verify_release.py` (lokal + CI sebelum publikasi).

## C. Bukti

- `report/evidence/key_rotation_v100.txt` — lintas-cek: manifest v1.0.0 vs
  key-4 (harus GAGAL, penolakan terdokumentasi) dan vs key-5 (harus OK);
  manifest historis vs kunci historis (tetap OK).

# Patch Changelog — v0.4.0 → v0.4.1

**Date:** 2026-11-09 · Fokus: penutupan penuh Insiden 3
(`install.sh --repair` dari checkout basi → `DashboardConfig ... unexpected
keyword argument 'tls'` → repair gagal pasca-mutasi) + rotasi kunci rilis
3→4. Semua perubahan tercakup oleh regresi permanen T-53..T-57 +
`tests/test_unit_regression_v041.py`, dan seluruh suite penuh lulus
(125 unit + 32 e2e + **290 kontrak PASS / 0 FAIL**).

## A. Preflight pre-mutation di installer (root cause Insiden 3)

### install.sh
- **Guard 1 — urutan versi (exit 5):** `--repair` menolak menjalankan source
  LEBIH LAMA dari aplikasi terpasang, SEBELUM service dihentikan dan sebelum
  satu byte pun diganti (pesan eksplisit + jalur pemulihan). Varian
  standalone (installer ter-pin rilis lamanya) ditolak pre-download —
  kanal rilis mati pun tidak pernah disentuh (T-55 membuktikan dengan
  UAEG_RELEASE_API_BASE mati). Escape hatch resmi: `UAEG_ALLOW_DOWNGRADE=1`.
- **Guard 2 — probe skema ter-staging (`CONFIG_INCOMPATIBLE`):** source
  di-stage (tar, tanpa .git/.venv) dan konfigurasi YANG ADA divalidasi
  dengan `Config.load()` dari `$stage/src` via `PYTHONPATH` ter-inject —
  versi boleh bohong (pohon campuran), skema tidak. Probe memverifikasi
  origin `uaeg.__file__` (RUNTIME_MISMATCH, exit 7).
- **`app_python()` — interpreter tersanitasi:** semua step Python yang
  menyentuh konfigurasi (`dashauth`, penulis konfigurasi, pembaca port
  efektif) kini `env -u PYTHONPATH "$VENV_DIR/bin/python" -P` — PYTHONPATH
  operator / paket liar di cwd tidak dapat membayangi (T-57).
- **Bukti origin penulis konfigurasi (exit 9):** heredoc penulis
  konfigurasi memverifikasi `uaeg.__file__` berada di bawah
  `$INSTALL_DIR/src` sebelum menulis apa pun — mismatch paket = CONFIG_ERROR
  yang jelas, bukan skema asing yang menulis konfigurasi.
- **Stop-service hard-fail:** gagal menghentikan `uaeg` = kegagalan
  pre-mutation (sebelumnya `|| true` — file aplikasi bisa diganti di bawah
  service hidup).
- **Verifikasi listener dashboard positif:** setelah health-check MCP,
  installer membuktikan dashboard menjawab pada bind/port/scheme efektif
  (15×1s) — kegagalan mengembalikan rc≠0 + journalctl, bukan sukses palsu.
  Ringkasan akhir + panduan ufw kini mengikuti bind efektif (termasuk `::`).
- **Pesan `--repair` di usage** mendokumentasikan kedua guard.

### updater.sh
- **Propagasi persetujuan downgrade:** `--allow-downgrade` operator
  diekspor sebagai `UAEG_ALLOW_DOWNGRADE=1` ke installer anak (guard 1
  installer menghormati persetujuan; guard 2 tetap mutlak — persetujuan
  menurunkan versi bukan persetujuan merusak konfigurasi).

## B. Perbaikan bug terpisah yang ditemukan audit

- **URL pemulihan `latest-mismatch` tidak ada (v0.4.0 as-published):** tiga
  pesan pemulihan install.sh + satu upgrade.sh menunjuk ke
  `.../releases/download/latest-mismatch/...` yang tidak pernah ada.
  Diperbaiki ke alias terdokumentasi `.../releases/latest/download/...`
  via `RELEASE_LATEST_BASE` (tetap menghormati mirror
  `UAEG_RELEASE_DOWNLOAD_BASE`).

## C. Rilis / kepercayaan

- **Rotasi kunci rilis 3→4** (BOOTSTRAP_VERSION 4; keyring install.sh +
  upgrade.sh kini 4 kunci — historis tetap diverifikasi untuk pin/downgrade).
  `release/keys/uaeg-release-4.pub.pem`; manifest v0.4.1 ditandatangani
  uaeg-release-4. Dokumen BOOTSTRAP.md diperbarui (rotasi + catatan
  hardening v0.4.1).
- Versi: pyproject/`__version__`/pin installer → 0.4.1; README, CI, dan
  T-39 disinkronkan (T-39 kini membaca versi dari pyproject, bukan
  hard-code).

## D. Regresi permanen

- **T-53:** --repair sumber lama → exit 5, nol mutasi (tree hash +
  config sha + kunci + jumlah backup diverifikasi).
- **T-54:** versi-sama tetapi skema lama (field `tls` dihapus dari salinan)
  → `CONFIG_INCOMPATIBLE` + `unexpected keyword argument 'tls'`, nol mutasi.
- **T-55:** standalone ter-pin lama → exit 5 sebelum menyentuh jaringan.
- **T-56:** `UAEG_ALLOW_DOWNGRADE=1` + skema kompatibel → repair sukses,
  kunci + konfigurasi operator dipertahankan.
- **T-57:** PYTHONPATH berisi paket uaeg pra-v0.3.0 → tetap sukses dengan
  skema benar (sanitasi terbukti).
- **test_unit_regression_v041.py (7):** bentuk konfigurasi produksi
  diterima; replika skema lama menolak persis seperti produksi; pesan
  ConfigError menyebut field; kontrak probe (origin + load) sukses; helper
  test selalu membawa `dashboard.tls`.

---

# Patch Changelog — v0.3.4 → v0.3.5

**Date:** 2026-11-08 · Fokus: perbaikan end-to-end Insiden 2 (konfirmasi
upgrade salah menolak jawaban operator `y`). Semua perubahan tercakup oleh
regresi permanen T-32..T-38 (tests/test_upgrade_contract.sh) dan/atau suite
penuh (191 PASS / 0 FAIL), kecuali ditandai lain.

## A. Interaksi operator (root cause Insiden 2)

### updater.sh
- **Konfirmasi y/N kini dibaca dari keyboard operator (`confirm_ask()`),**
  bukan dari stdin skrip. Urutan resolusi: stdin-jika-tty -> `/dev/tty`
  (bentuk `curl|bash`, stdin terputus `< /dev/null`, sudo/CI) -> satu baris
  terpipah (otomasi `printf 'y\n' | ...`). Dua situs diganti: konfirmasi
  upgrade ("Continue with the upgrade to vX.Y.Z?") dan konfirmasi rollback
  ("Continue with the rollback?").
- **Pelaporan penolakan jujur:** jawaban `n`/baris kosong -> "cancelled by
  the operator" / "aborted" (dipertahankan); TIDAK ADA jawaban (EOF/tanpa
  terminal) -> pesan eksplisit "no confirmation was received ... pass --yes
  for an unattended upgrade. Nothing was changed." — menggantikan (a) salah
  lapor "cancelled by the operator" dan (b) kematian diam-diam `set -e`
  pada `read` EOF tanpa pesan apa pun.
- **Jawaban diperluas:** `y`/`yes` huruf besar/kecil, spasi/CR di-trim
  (CRLF-safe). Baris kosong tetap = tidak (default `[y/N]`).
- **`BASH_SOURCE` fallback (`:-bash`)** pada `UPDATER_SELF` dan `SCRIPT_DIR`:
  updater yang dieksekusi via stdin (`cat updater.sh | bash -s -- ...`)
  tidak lagi crash `BASH_SOURCE[0]: unbound variable` di bawah `set -u`.

### install.sh
- **`confirm_ask()` yang sama** untuk prompt "Continue anyway?" (OS tidak
  didukung) — kelas bug identik pada jalur fresh-install standalone.

## B. Kontrak & dokumentasi

- **docs/UPGRADE_CONTRACT.md:** klausul baru **U-15** (konfirmasi interaktif
  wajib dibaca dari keyboard operator; pelaporan penolakan wajib akurat) +
  catatan perubahan kontrak v3.1 (klarifikasi semantik; `contract_version`
  RECORD tetap 3 — tanpa perubahan antarmuka).
- README.md: current release -> v0.3.5.

## C. Test permanen (anti-recurrence)

- **tests/test_upgrade_contract.sh T-32..T-38** (7 grup, ~40 assertion):
  T-32 insiden persis (y di terminal + stdin terputus -> upgrade HARUS
  berjalan, prompt HARUS tampil, tanpa false "cancelled"); T-33 bentuk
  curl|bash (tanpa crash); T-34 jawab n (penolakan akurat); T-35 tanpa
  terminal + tanpa jawaban (pesan akurat + arahan --yes); T-36 otomasi
  terpipah tetap diterima; T-37 konfirmasi rollback (n -> aborted; y via
  terminal -> rollback berjalan); T-38 probe helper byte-exact dari kedua
  skrip.
- **assert_in/assert_not_in kini memakai separator `--`** (grep) — pola
  assertion yang diawali `--` (mis. `--yes`) tidak lagi tertelan sebagai
  opsi grep.

## D. Tidak berubah (dijaga)

- `upgrade.sh` (bootstrap operator) **byte-stable** — sha256 identik dengan
  aset v0.3.4 (prinsip "download once" BOOTSTRAP.md tetap terpenuhi).
- Semua flag, exit code (0/1/2/3/5), format `RECORD.json`, jalur backup/
  rollback/verifikasi zero-data-loss: tidak diubah.
- Kunci agent, verifikator, dashboard password, DB: tidak tersentuh
  perubahan ini (hanya interaksi prompt).

---

# Patch Changelog — v0.3.2 → v0.3.3

---

# Patch Changelog — v0.3.3 → v0.3.4

**Date:** 2026-11-08 · Every change is covered by a regression test
(`tests/test_unit_regression_v034.py`, updated e2e/contract assertions) or
the full contract suite, unless marked otherwise. IDs refer to the round-2
audit findings (FULL_AUDIT.md §R2.1).

## A. Execution subsystem (src/uaeg/executor/)

### pty.py
- **V-1 (P1): final output + exit code survive process exit.** `_mark_exited`
  now (a) captures the child's exit status from its `waitpid` (previously
  discarded — `exit_code` was always `None`), and (b) keeps the closed
  session readable for a grace window (`_EXITED_GRACE_S = 60 s`) instead of
  popping it from the registry in the same event-loop batch as the final
  `feed()`; a `loop.call_later` timer (with lazy expiry in `get()` as the
  safety net) drops it afterwards. `shell_output` therefore returns the last
  output chunk with `alive: false` and the real `exit_code`; `shell_sessions`
  shows recently-exited sessions with their exit codes.
- **V-4: `shell_close` reports the child's real exit code** — both
  `waitpid` sites in `_terminate` now record the status
  (`TerminalSession._set_exit_code`).
- **MCP-D2: `resize()` validates cols/rows (2..1000)** like `shell_open`,
  raising a clean `SessionError` instead of pushing nonsense into
  `TIOCSWINSZ` or leaking `struct.error`.
- **C-3: `write()`/`resize()` wrap `OSError`** (EIO/EBADF race on a
  just-closed master) into `SessionError("session is closed")`.
- **C-4: reaper cancellation happens on the loop thread** (moved into the
  `_close_fd` closure dispatched via `call_soon_threadsafe`).
- `open()` counts only LIVE sessions against `exec.max_sessions` (a burst of
  grace-window corpses must not block new terminals); `close_by_key` skips
  already-closed sessions.

### jobs.py
- **V-2: deterministic shim reaping.** `JobRuntime` keeps the `Popen`
  handle; the watcher awaits `_reap_shim()` (bounded 10 s `proc.wait()` off
  the loop) when a job reaches a final state, and `cancel()` reaps the
  killed shim. Finished jobs no longer linger as zombies until an unrelated
  `subprocess` call.
- **V-3: no orphan on DB-insert failure.** `spawn()` wraps `insert_job`; on
  failure it SIGKILLs + reaps the just-spawned shim and raises a clean
  `JobError` (previously: live unmanageable process + raw
  `sqlite3.OperationalError` leaked to the MCP client).
- **MCP-D1 (key scoping):** `status/output/wait/cancel/list` accept
  `owner_key_id` and `_gate()` rejects cross-key access ("belongs to a
  different agent key"); `list()` filters by owner. Dashboard/CLI/containment
  pass `None` (operator level).

### runner.py
- Post-SIGKILL `proc.wait()` is bounded (5 s): a D-state child can no longer
  pin the request forever (exit code reported as absent instead).

## B. Dashboard & auth

### dashauth.py
- **D-1 (P1): fail-closed password-file deletion.** `PasswordManager` tracks
  `_ever_configured`; a file that vanishes at runtime is treated as BROKEN
  (login fails closed, sessions invalidated via generation `None`,
  `is_configured()` stays true) instead of silently reverting the dashboard
  to the legacy unauthenticated mode — including public `0.0.0.0` binds.
  Restoring the file or `sudo uaeg pass` recovers immediately.
- Removed the dead (and misleading) `Session.expires_at` property.

### dashboard.py
- **D-2: chunked bodies honour the 1 MiB cap.** `create_key`, `revoke_key`,
  `rotate_key`, `set_permissions` now read bodies through the capped
  streaming reader `_read_json_capped` (413 on oversize) instead of
  uncapped `request.json()`.
- **C-2: `tls_info()` (blocking openssl) runs via `asyncio.to_thread`** in
  `/api/server` — a slow openssl call can no longer freeze both listeners.

## C. MCP tools (mcp_tools.py)

- **MCP-D1:** every session/job tool passes the caller's key as
  `owner_key_id`; `shell_sessions`/`job_list` filter to the caller's
  resources. Docstrings updated.
- **MCP-D4:** `shell_exec` timeout clamped to 1..3600 s server-side.
- **MCP-D3:** `job_list` limit clamped to 0..500.
- **D-5:** module docstring's audit-coverage claim corrected to the
  truthful tool set.

## D. Config / paths / db

- **D-7 (config.py):** a non-object section value (`"tls": "certbot"`)
  raises `ConfigError` at load (previously silently reverted the whole
  section to defaults). `null` still means "defaults".
- **D-6 (config.py):** `mcp_url()` uses `http://` when
  `UAEG_DEV_INSECURE_HTTP=1` (advertised URL matches the served scheme).
- Dead code removed: `paths.ensure_data_dirs()`, `Store.find_key_by_verifier()`.

## E. Release engineering (bootstrap keyring v2)

- **Signing-key rotation to `uaeg-release-2`** (the uaeg-release-1 private
  key is unavailable to the publisher; rotation is the documented
  exceptional procedure in docs/BOOTSTRAP.md):
  - `upgrade.sh`: `BOOTSTRAP_VERSION=2`; trusted keyring embeds BOTH public
    keys; `verify_manifest_signature()` accepts a signature by ANY trusted
    key; `--bootstrap-info` prints both key ids + fingerprints.
  - `release/verify_release.py` + `release/make_release.py`: verify against
    any committed public key.
  - `release/keys/uaeg-release-2.pub.pem` committed; the private key is
    delivered to the operator out-of-band and never enters the repository.
  - docs updated (BOOTSTRAP.md rotation record, SECURITY.md, README upgrade
    note, LIMITATIONS.md #3 rewritten + #9 key scoping, USAGE.md tool
    contracts).

## F. Documentation truth fixes

- LIMITATIONS.md #3 described the pre-v0.3.0 no-login dashboard — rewritten
  to the shipped contract (legacy mode only when never configured + private
  bind; public bind requires password+TLS; runtime deletion fails closed).
- USAGE.md: exit-code/grace-window behavior, key scoping, clamps, and the
  truthful `ok`-field claim.

## Test accounting

- New: `tests/test_unit_regression_v034.py` (17 tests: V-1, V-4, grace
  expiry, resize validation, write-after-close, V-2 zombie, V-3 orphan,
  key scoping ×2, D-1 ×3, D-2, D-6/D-7 ×3, clamps).
- Updated for the corrected contracts: `test_unit_executor.py`,
  `test_unit_containment.py`, `test_e2e_smoke.py`, `test_e2e_hardening.py`
  (revocation tests now verify cross-key REJECTION + dashboard-level
  oversight), `test_upgrade_contract.sh` (T-26 message match).
- Final state: 118 unit/regression passed · 32 e2e passed · 155 contract
  assertions passed · 18/18 real-asset incident reproduction.


**Date:** 2026-11-08 · **Commit range:** v0.3.2 (9da477e) → v0.3.3 ·
Every change below is covered by a regression test or the extended contract
suite unless marked otherwise.

---

## A. Execution-subsystem fixes (src/uaeg/executor/)

### pty.py
- **B-1 (P1): non-blocking PTY master + queued-input drain.** The master fd
  is set `O_NONBLOCK` at open. `TerminalSession.write()` never blocks: a
  short write or `EAGAIN` queues the remainder in a bounded backlog
  (≤ `_MAX_PENDING_INPUT` = 1 MiB) which `SessionManager` drains via
  `loop.add_writer` when the tty becomes writable; exceeding the backlog
  raises a descriptive `SessionError` (backpressure) instead of hanging the
  service. `_on_readable` treats `BlockingIOError` as a spurious event, not
  an exit. Fixes the whole-service freeze when input is sent to a suspended
  (Ctrl-Z) session. Test: `test_pty_write_to_suspended_session_does_not_block_loop`,
  `test_pty_write_normal_session_delivers_input`.
- **B-6: snapshot iteration in `SessionManager.list()`** (dict-mutation race
  with termination workers).
- **B-7: atomic session close.** `_mark_exited` now performs the
  closed-check-then-set + fd swap under a per-session `threading.Lock`; the
  master fd is set to -1 so no other path can reuse the number; the close
  callback removes both the reader and the input-drain writer and clears the
  backlog.
- **B-8: `read_since` non-positive limit** returns no data with a stable
  (never backwards) cursor.

### jobs.py
- **B-3: cancellation escalation on real liveness.** `cancel()` decides
  SIGTERM→SIGKILL escalation from actual process-group liveness (new
  zombie-aware `_group_alive()` scanning `/proc`), not from the DB status it
  had just written. Test: `test_job_cancel_kills_term_ignoring_command`.
- **B-4: boot-id stamping.** `spawn()` writes the kernel boot id into the
  job dir; `reattach()` marks previous-boot jobs `interrupted`
  unconditionally (pid-recycling guard); legacy rows without a stamp fall
  back to pid liveness. Tests: `test_reattach_marks_previous_boot_job_interrupted`,
  `test_spawn_writes_current_boot_id`.
- **B-5: watcher exception guard.** `_watch` catches and logs non-cancel
  exceptions and keeps polling. Test: `test_job_watcher_survives_check_exception`.

## B. Auth / containment / storage fixes

- **B-2 (containment.py):** `key_resources()` uses `store.open_sessions()`
  (unbounded, filtered server-side conceptually) instead of
  `list_sessions(limit=1000)`. Test:
  `test_containment_key_resources_sees_old_open_sessions` (1100 noise rows).
- **B-12 (cli.py + dashboard.py): key rotation now creates the replacement
  key BEFORE revoking the old one** in both the CLI and dashboard paths — a
  crash can no longer leave zero working keys. Test:
  `test_rotation_failure_keeps_old_key_active`.
- **B-9 (security.py):** new redaction rule for short-form CLI password
  flags (`mysql -ppassword123`, `psql -whunter2`) with an anti-false-positive
  guard (value must contain a digit/symbol or be ≥10 chars; ordinary flags
  like `-print`/`-append` untouched). Tests: parametrized
  `test_redaction_short_flag_passwords`, `test_redaction_preserves_ordinary_flags`.
- **B-10 (cli.py):** `_key_exists()` added; `uaeg config get` distinguishes
  existing-but-null keys from unknown keys (the dead `missing_ok` branch is
  gone). Test: `test_dig_key_exists_semantics`.
- **B-11 (db.py + server.py):** `Store.trim_exec_logs(keep=20_000)` runs once
  at startup; **always commits** (a zero-row delete without commit left a
  dangling write transaction that locked the database for every other writer
  — caught by the v0.3.3 test run itself and fixed). Test:
  `test_trim_exec_logs_retention`.
- **B-13:** dead code removed (`auth.UNPROTECTED_PATHS`, `db._row_to_dict`),
  paths.py docstring corrected (sessions/ is reserved; records live in the
  DB).

## C. MCP tool layer (mcp_tools.py)

- **B-8 follow-ups:** `shell_output` clamps `limit_bytes` to [0, 8 MiB] at
  the tool boundary; `job_wait` clamps `timeout_seconds` to [0, 600] with a
  numeric-coercion fallback (an agent can no longer pin an HTTP worker
  indefinitely). Test: `test_job_wait_timeout_is_clamped_by_tool`.

## D. Dashboard backend (dashboard.py)

- **F-5:** `GET /api/whoami` returns a proper legacy-mode payload
  (`authenticated: false`, guidance) instead of `KeyError` → 500. Test:
  `test_whoami_legacy_mode_returns_payload`.
- **F-6:** `/api/sessions` skips sessions that vanished between `list()` and
  `get()` instead of 500-ing the endpoint. Test:
  `test_sessions_endpoint_skips_vanished_session`.
- **F-10:** dashboard HTML served with `Cache-Control: no-store`.
- **Favicon route** `/favicon.ico` (inline SVG, 200, cacheable) — removes the
  console 404 on every page load.

## E. Dashboard frontend (dashboard_ui.py)

- **F-1 (P2):** connection indicator is now only green when EVERY endpoint
  of the refresh cycle succeeded (`markHealthy()`/`lastTabError`); a passing
  `/api/overview` can no longer mask a failing tab. All five loaders
  migrated.
- **F-2:** `api()` transparently re-fetches the CSRF token once on a 403 for
  state-changing calls and retries — a transient boot-time whoami failure no
  longer degrades the dashboard to read-only (and logout revocation now
  always reaches the server).
- **F-3:** key-generation in-flight guard covers the Enter-key path — double
  submit can no longer create two keys and destroy the first one-time
  secret.
- **F-4:** job-output viewer uses a request-generation counter — stale
  responses from a closed viewer can no longer double-append output.
- **F-7:** tab switches update the URL hash via `history.replaceState`.
- **F-8:** login lockout message surfaces the server's `Retry-After`
  (minutes) instead of "a moment".
- **F-9:** permission-mode toggle reloads the keys table instead of mutating
  a DOM node a periodic re-render may have detached.
- Favicon `<link>` in both templates.

## F. Permanent upgrade architecture (contract v3)

- **`upgrade.sh` REWRITTEN as the stable operator bootstrap** (~380 lines):
  release-channel target resolution (GitHub Releases API — never `main` by
  default), manifest download, **Ed25519 signature verification against the
  embedded trusted keyring**, structural manifest validation (safe updater
  path, version match, interface ≤ supported), updater download with
  **SHA-256 + size verification against the signed manifest**, execution with
  the documented interface env, exit-code propagation, temp cleanup,
  post-run self-hash assertion. `--bootstrap-info` introspection. Offline
  `--rollback`/`--check` from the cached updater. All v0.3.2 flags preserved.
- **`updater.sh` NEW — the versioned safe-upgrade updater** (the v0.3.2
  machinery, contract v3): bootstrap-interface handshake, `UAEG_RELEASE_TAG`
  target trust, self-caching to the upgrade cache (0700, atomic) on success,
  best-effort system bootstrap refresh at `/usr/local/sbin/uaeg-upgrade`,
  extended `RECORD.json` (contract 3, interface, bootstrap provenance). Fully
  usable standalone.
- **Release signing:** `release/keys/uaeg-release-1.pub.pem` (committed);
  `release/make_release.py` (build+sign manifests; private key never
  committed); `release/verify_release.py` (independent verification used by
  CI and operators); signed manifest for v0.3.3 under
  `release/manifests/v0.3.3/`.
- **`.github/workflows/release.yml`:** verifies the manifest signature and
  every asset hash BEFORE publishing; publishes 8 assets (install.sh,
  upgrade.sh, uaeg-updater-0.3.3.sh + sidecars + manifest + signature);
  smoke-checks all documented URLs.
- **`install.sh`:** pins 0.3.3; installs the system bootstrap convenience
  copy `/usr/local/sbin/uaeg-upgrade`.
- **CI (`ci.yml`):** installer smoke patches the new system-bootstrap path;
  regression suite added to the unit-test step.

## G. Tests

- `tests/test_unit_regression_v033.py` — 20 regression tests (B-1..B-12,
  F-5, F-6).
- `tests/test_upgrade_contract.sh` — migrated to the updater (T-01..T-19)
  + new permanent-bootstrap series T-20..T-31 (signature/tamper/foreign-key/
  interface/missing-manifest rejections, multi-release compatibility with a
  CHANGED updater implementation, offline rollback, retry-after-failure).

## H. Documentation

- `docs/BOOTSTRAP.md` (new): architecture, binding interface v1, trust
  model, key-rotation policy, migration, test coverage.
- `docs/UPGRADE_CONTRACT.md`: contract v3 with new clauses U-13 (permanent
  bootstrap) and U-14 (verified authenticity/integrity).
- `docs/INSTALL.md`, `README.md`: one-time bootstrap download flow.
- `docs/SECURITY.md`: release-authenticity section.
- `docs/TROUBLESHOOTING.md`: signature/key-rotation recovery, offline
  rollback.
- `docs/LIMITATIONS.md`: job-output-files-uncapped note (B-11 remainder).

## I. Version

`0.3.2` → `0.3.3` (pyproject.toml, `__version__`, installer pin, README).
