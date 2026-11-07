# Full Audit — UAEG v0.3.2 (P0)

**Tanggal:** 2026-11-07/08 · **Baseline diaudit:** main `4f5ac50` (pasca-tag v0.3.1) · **Patch:** `5dfcb0a` (v0.3.2) · **Mode:** autonomous, end-to-end, evidence-driven

---

## 1. Kebenaran repositori (repository truth) yang ditetapkan sebelum audit

| Item | Nilai terverifikasi |
|------|---------------------|
| Default branch | `main` |
| HEAD saat audit dimulai | `4f5ac50` ("ci(release): auto-publish install.sh/upgrade.sh assets on every tag") |
| Tag | v0.1.0, v0.1.1, v0.2.0, v0.3.0, v0.3.1 — **semua valid & reachable** |
| v0.3.1 menunjuk | `225110b` (ancestor of main — sehat) |
| GitHub Releases | v0.1.0, v0.1.1, v0.3.1 terbit; **v0.2.0 & v0.3.0 tidak punya Release** (historis; workflow `release.yml` sejak `4f5ac50` menjamin aset pada setiap tag ke depan) |
| `releases/latest` | → v0.3.1 (benar saat itu) |
| Aset v0.3.1 | install.sh + .sha256, upgrade.sh + .sha256 (4 aset lengkap) |
| Versi paket | pyproject `0.3.1` == `__version__` == pin installer (konsisten) |
| CI | workflow `ci.yml` (test ×3 Python, upgrade-contract, installer smoke) + `release.yml` (publish aset per tag, verifikasi pin versi, smoke-check URL unduhan) |

**Kesimpulan penting:** klaim "tag v0.3.1 tidak ditemukan" pada transkrip
operator **salah** — tag ada dan release-nya lengkap. Kegagalan murni
lokal (lihat `ROOT_CAUSE_ANALYSIS.md`). Rekonsiliasi rilis tidak
diperlukan; proses publikasi (release.yml) sudah benar dan dipertahankan.

## 2. Peta arsitektur yang diverifikasi

```
Cloud AI Agent ──HTTPS/MCP (Streamable HTTP, :3902)──► Auth Bearer (keys DB)
   ──► Tool Dispatch (13 tool) ──► Native Execution (sync exec / PTY / background jobs)
   ──► SQLite (WAL) + audit logs ──► JSON-RPC response (SSE)

Browser ──► Dashboard listener (:3903) ──► Login (scrypt) ──► Session (idle 30m / absolute 12h)
   ──► Dashboard APIs (overview, keys, jobs, exec logs)

GitHub tag push ──► release.yml ──► Release + aset + .sha256 ──► operator unduh + checksum
   ──► upgrade.sh: deteksi → backup konsisten → staging clone tag immutable
   ──► install.sh (upgrade in-place, config dipertahankan) → verifikasi U-4
   ──► (gagal) rollback otomatis / (sukses) RECORD + retensi
```

## 3. Hasil audit per area

| Area | Metode | Hasil |
|------|--------|-------|
| Kode sumber & arsitektur | review penuh `upgrade.sh` (1103→1208 baris), `install.sh`, `uninstall.sh`, workflow, `src/uaeg/*` terpilih | Insiden P0 di `materialize_source()` (FIXED); lainnya sehat |
| Konsistensi PRD/implementasi | PRD/ACCEPTANCE vs kode vs test | 13 tool MCP sesuai daftar; login-before-dashboard dipertahankan; traceability lama tetap berlaku |
| CLI & lifecycle | `uaeg version/generate/serve/pass/doctor/config/keys` (live smoke + unit + e2e) | OK; `pass` interaktif-getpass + konfirmasi + tanpa cetak password |
| Install/upgrade/rollback/uninstall | suite 19 grup + repro + uninstall review | OK setelah patch; uninstall default mempertahankan data (hanya `--purge` menghapus) |
| Aset rilis, tag, checksum, Actions | API GitHub + release.yml review | OK; pin versi diverifikasi workflow |
| MCP server & tool | live smoke 13 tool + 9 e2e | OK (session enforcement, error paths, cancel, resize, reconnect-by-new-request) |
| Dashboard login/session/API/UI | 23 e2e | OK (login/salah/logout/expired/cookie/CSRF/rate-limit/rotasi) |
| SQLite schema & persistensi | U-4 quick_check + snapshot API + T-05..T-09 | OK; migrasi tidak disentuh patch (kompatibel) |
| Background jobs & PTY | live smoke + unit executor | OK; proses dibatalkan **tidak pernah** dilaporkan running (diverifikasi eksplisit) |
| Konfigurasi/jaringan/TLS/systemd | review + guard test | OK; wildcard tanpa password ditolak di semua jalur |
| Batas keamanan & secret | SECURITY_REVIEW.md | 3 temuan (P0/P2/P2) — semua FIXED |
| Test/dokumentasi/deployment | docs vs perilaku | 2 inkonsistensi dokumen (git pull, klaim rollback app) — FIXED |

## 4. Daftar temuan lengkap

| ID | Temuan | Severity | Status |
|----|--------|----------|--------|
| F-1 | Checkout di working tree operator + salah klasifikasi error (insiden MicroTix) | **P0** | FIXED (`5dfcb0a`, T-17/T-18 kunci regresi) |
| F-2 | Guard dirty-tree `--untracked-files=no` meloloskan file unduhan (penyebab F-1 lolos guard) | P1 | FIXED (guard dihapus — tree tak pernah disentuh; T-14) |
| F-3 | Resolusi tag dari tag lokal usang (bukan remote) | P1 | FIXED (ls-remote selalu; T-13/T-17) |
| F-4 | U-4 false-positive config parsial → rollback palsu | P2 | FIXED (normalisasi default) |
| F-5 | `--keep-backups 0` menghapus semua recovery point | P2 | FIXED (clamp ≥1) |
| F-6 | Pesan fetch menampilkan URL `--repo` padahal fetch ke origin clone | P3 | FIXED (repo_url nyata dicetak/dicatat) |
| F-7 | Docs INSTALL menyarankan `sudo git pull` (tidak perlu & membingungkan) | P3 | FIXED |
| F-8 | Kontrak v1 mengklaim rollback penuh tanpa menjelaskan pemulihan app pasca-pruning | P3 | FIXED (kontrak v2 + hint di `--rollback`) |
| F-9 | v0.2.0/v0.3.0 tanpa GitHub Release (aset 404 untuk versi historis) | P3 | Diterima (historis; release.yml mencegah terulang; tag immutable tidak ditulis ulang) |
| F-11 | **CI kontrak upgrade gagal di main sejak era v0.3.1** (T-15; akar: `actions/checkout` shallow → push test remote ditolak "shallow update not allowed" → remote tanpa tag) — juga kelas "shallow clone" dari daftar investigasi insiden | P1 | **FIXED** — `fetch-depth: 0` di ci.yml + self-heal shallow/tagless di test suite; CI main `f0980cf` **semua job hijau** |
| F-10 | Deployment live MicroTix belum dieksekusi (tak ada akses VPS dari audit) | — | PENDING operator (lihat PRODUCTION_READINESS.md) |

Tidak ada temuan P0/P1 yang tersisa terbuka.

## 5. Cakupan sengaja di luar patch ini (scope discipline)

- Tidak ada dependency baru, tidak ada redesign subsistem yang berfungsi.
- Fitur produk tidak ditambah/dikurangi; tidak ada requirement yang
  dihapus demi lolos test — sebaliknya test baru menaikkan jaminan.
- Migrasi DB tidak diubah (schema stabil sejak v0.2.0; patch tidak
  menyentuh `db.py`).
