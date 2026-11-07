# Security Review — UAEG v0.3.2 (audit P0)

**Tanggal:** 2026-11-07/08 · **Scope:** perubahan v0.3.2 + verifikasi batas keamanan yang diwarisi · **Metode:** review kode + pengujian aktif (unit, E2E, live smoke, fault injection)

---

## 1. Temuan pada audit ini

| ID | Temuan | Severity | Status |
|----|--------|----------|--------|
| SEC-1 | `upgrade.sh` v0.3.1 menjalankan `git checkout` di working tree operator → gagal pada file untracked (insiden) + **salah klasifikasi error** ("tag tidak ditemukan") yang mengarahkan operator ke diagnosis keliru | P0 | **FIXED v0.3.2** — staging terisolasi; klasifikasi akurat (lihat `ROOT_CAUSE_ANALYSIS.md`) |
| SEC-2 | Verifikasi U-4 membandingkan config mentah: kunci absen vs default eksplisit dianggap "berubah" → bisa memicu **rollback palsu** (availability) | P2 | **FIXED v0.3.2** — normalisasi absent=default |
| SEC-3 | `--keep-backups 0` dapat menghapus **semua** recovery point segera setelah upgrade | P2 | **FIXED v0.3.2** — clamp ≥ 1 + validasi numerik |

Tidak ditemukan temuan P0/P1 baru pada area yang diaudit (di bawah).

## 2. Batas autentikasi MCP (diverifikasi aktif)

- Bearer key `uaeg_...`: generate → verifikasi → dipakai; **tanpa** key →
  HTTP 401 (live smoke #7); key salah → 401 (#8).
- Session MCP Streamable HTTP di-enforce: request `tools/call` tanpa
  `mcp-session-id` ditolak "Missing session ID" (live smoke #9).
- Revokasi key: diuji unit (`test_unit_core`) — key revoked tetap revoked
  setelah upgrade (U-4 membandingkan `(key_id, status)`).
- Log/redaksi: `test_unit_security_redaction` lulus; T-11 membuktikan
  secret tidak pernah muncul di log/RECORD/state JSON; `read_state`
  hanya membaca identifier publik (key_id/name/prefix/status), tidak
  pernah verifier/hash password.
- **Catatan desain (bukan cacat, sesuai PRD):** agent terautentikasi
  memperoleh eksekusi penuh host (root pada VPS). Batasan direktori
  (`restricted_paths`) bersifat **advisory** dan didokumentasikan sebagai
  bukan sandbox (`docs/LIMITATIONS.md`, `test_unit_containment`) —
  konsisten dengan peringatan README.

## 3. Dashboard (diverifikasi aktif)

- Alur wajib: halaman login **sebelum** dashboard (E2E 23 test lulus:
  login benar/salah, logout, sesi kedaluwarsa, cookie, CSRF, rate limit,
  rotasi password mencabut sesi tanpa restart).
- Bind publik (`0.0.0.0`/`::`) **tanpa** password: ditolak fail-closed —
  di `config.py` (validasi wildcard) + serve-time + installer
  (`--dashboard-bind public` menolak sebelum reconfigure) + **upgrade**
  (T-GUARD: upgrade ditolak bila publik tanpa password).
- Bind publik **dengan** password: HTTPS + login dipaksa (E2E).
- Bind loopback tanpa password: hanya halaman info lokal (by design,
  tidak dapat dijangkau dari luar host) — bukan pelanggaran "no publicly
  reachable unauthenticated dashboard".

## 4. Keamanan proses upgrade (perubahan v0.3.2)

- **Kode yang diunduh tidak dieksekusi buta:** aset rilis diverifikasi
  checksum oleh operator (`sha256sum -c`, alur dokumentasi), source
  instalasi diambil dari **tag immutable** yang diverifikasi ada
  (ls-remote pra-clone) dan staging clone diverifikasi berada **persis di
  commit tag** (HEAD == `refs/tags/<tag>^{commit}`) sebelum installer
  dijalankan.
- Staging: `mktemp -d` (mode 0700) di `/tmp`, dihapus saat selesai
  (trap EXIT); tidak pernah di dalam working tree operator.
- Remote yang dipakai selalu **dicetak dan dicatat** di RECORD.json
  (prioritas: `--repo` eksplisit > origin clone operator > default
  publik resmi) — mencegah sumber senyap yang tidak terdokumentasi.
- Tidak ada operasi destruktif (`reset --hard`, `clean -fd`, forced
  checkout) terhadap repo operator — kelas operasi ini **hilang total**
  dari script.
- Backup material sensitif: direktori `0700`, tidak pernah di-commit
  (T-11; `.gitignore` repo tidak meng-track direktori data).

## 5. TLS / jaringan

- MCP listener: HTTPS (self-signed default; certbot/manual didukung),
  `/healthz` publik read-only.
- Dashboard listener terpisah (default 3903), TLS menyatu dengan MCP saat
  publik.
- Sertifikat self-signed: `chmod 0600` pada key (installer).
- Peringatan jujur: klien hosted menolak self-signed — installer
  merekomendasikan `--domain` (Let's Encrypt) untuk koneksi agent nyata.

## 6. Kesenjangan yang diwarisi (bukan regresi v0.3.2; risiko residual)

1. **Tidak ada sandbox OS** untuk eksekusi agent (by design, PRD FR-010).
   Mitigasi: kunci + revokasi + audit log + README warning eksplisit.
2. Sertifikat manual/certbot bergantung operator (renewal hook tersedia).
3. `apt-get` installer berjalan sebagai root (wajar untuk installer OS,
   tapi menambah permukaan kepercayaan supply-chain Debian).
4. Release assets dilindungi checksum SHA-256 (sidecar) — tanpa signature
   GPG. Penyempurnaan possible future work, tidak memblokir rilis ini
   (checksum + HTTPS GitHub + tag immutable).

## 7. Kesimpulan

Tidak ada temuan keamanan P0/P1 yang terbuka. Perbaikan v0.3.2
**menaikkan** postur keamanan (menghilangkan mutasi repo operator,
klasifikasi kegagalan akurat, verifikasi commit tag, retensi backup
aman). **GO dari perspektif keamanan** untuk rilis v0.3.2.
