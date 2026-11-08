# Production Readiness — UAEG v0.3.2

---

# Production Readiness — v0.3.4

**Decision: GO for the v0.3.4 patch release.** Live VPS deployment is
**PENDING OPERATOR ACTION** (no live access from this audit environment;
the verified command sequence is below and nothing in it is untested).

## Why GO

- The reported P0 incident (v0.3.1 upgrade failure) is fixed and
  INDEPENDENTLY verified against the real published release channel
  (18/18 assertions, real signature chain, populated installation,
  zero data loss, operator tree untouched).
- Round-2 full-system audit: every confirmed defect fixed at root cause
  with pinning regression tests; no unresolved P0/P1 defects remain.
- Full matrix green on the tagged state: 118 unit/regression, 32 e2e,
  155 contract assertions.
- Release artifacts verified end-to-end from public URLs (see
  RELEASE_VERIFICATION.md).

## Operator runbook — MicroTix VPS (existing installation, currently ≤ v0.3.3)

The v0.3.1-era failure state means the operator's last upgrade never
completed, so the bootstrap is downloaded FRESH — the key rotation is
therefore transparent for this host:

```bash
# 0) pre-flight (record state for the log)
uaeg version; sudo uaeg keys list; sudo uaeg doctor

# 1) one-time bootstrap download (fresh — trusts both release keys)
cd ~
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
sha256sum -c upgrade.sh.sha256        # must print: upgrade.sh: OK

# 2) upgrade (backup + verify + auto-rollback are automatic)
sudo bash upgrade.sh

# 3) post-flight verification
uaeg version                           # uaeg 0.3.4
sudo uaeg doctor                       # all checks passed
sudo uaeg keys list                    # existing agent keys intact
systemctl status uaeg                  # active
```

Notes:
- The upgrade refuses to run while a PUBLIC dashboard bind has no password
  (if applicable: run `sudo uaeg pass` first — MCP agent keys are not
  affected).
- Rollback if anything looks wrong: `sudo bash upgrade.sh --rollback`
  (works offline from the cached updater after the first upgrade).
- Operators who instead hold a v0.3.3-era bootstrap will get the documented
  fail-closed key-rotation refusal — re-download once (step 1).

## Live deployment status

- [x] Patch published and verified from public URLs.
- [ ] Executed on the MicroTix VPS by the operator (no live access from
      this environment; nothing was invented). Expected evidence after the
      operator runs it: the post-flight block above.


**Tanggal:** 2026-11-07/08 · **Keputusan: GO untuk rilis v0.3.2** · **Deployment live MicroTix: PENDING (operator)**

---

## 1. Definition of Done (dari audit P0) — status per butir

| Butir | Status | Bukti |
|-------|--------|-------|
| Kegagalan `upgrade.sh` yang dilaporkan direproduksi & diperbaiki | ✅ | Reproduksi identik (exit 1, pesan sama) → perbaikan → skenario sama sukses; `ROOT_CAUSE_ANALYSIS.md` |
| Upgrade standalone dari direktori arbitrer | ✅ | T-15, T-19 (upgrade PENUH tanpa repo lokal) |
| Working tree git operator tidak dimodifikasi destruktif | ✅ | Tidak ada operasi mutasi sama sekali; T-13/T-14/T-17 membandingkan HEAD/branch/status byte-for-byte |
| Aset rilis, tag, dan versi konsisten | ✅ | release.yml verifikasi pin + smoke URL; versi 0.3.2 seragam; `RELEASE_VERIFICATION.md` |
| Key agent existing selamat dari upgrade | ✅ | U-4 (himpunan `(key_id,status)` identik) pada semua upgrade test + live |
| Database & konfigurasi tetap utuh | ✅ | U-4 quick_check + counts + effective-config; snapshot API konsisten |
| Login dashboard bekerja end-to-end | ✅ | 23 test E2E (login/salah/logout/expired/cookie/CSRF/rate-limit/rotasi) |
| Dashboard publik butuh autentikasi aman | ✅ | Wildcard tanpa password ditolak di config/serve/installer/upgrade (T-GUARD); publik+dikit password → HTTPS+login |
| Semua tool MCP lolos integration test | ✅ | 13/13 tool di e2e (9 test) + live smoke |
| Background jobs & PTY bekerja | ✅ | live smoke (open/input/output/resize/close/sessions; submit/status/list/cancel/output/wait; proses mati tak dilaporkan running) + unit executor |
| Instalasi fresh sukses | ✅ | T-01 + CI installer smoke + live smoke (venv+pip nyata) |
| Upgrade instalasi existing sukses | ✅ | T-05/T-13/T-14/T-17/T-19 |
| Rollback setelah upgrade gagal sukses | ✅ | T-08 (otomatis, exit 2, app lama hidup) + T-09 (manual) |
| CI & regression test lolos | ✅ | 95 PASS/0 FAIL (kontrak); 81 unit; 9+23 e2e |
| Tidak ada defek P0/P1 tersisa | ✅ | FULL_AUDIT.md §4 (F-1..F-3 FIXED; sisanya P2/P3 FIXED atau diterima eksplisit) |
| Artefak rilis terverifikasi independen | ✅ | `RELEASE_VERIFICATION.md` (unduh publik, checksum, fresh-install + upgrade smoke memakai aset terbit) |
| Dokumentasi sesuai implementasi | ✅ | Kontrak v2, INSTALL.md, README — selaras |
| Deployment produksi terverifikasi / ditandai pending | ⚠️ **PENDING** | Tidak ada akses SSH ke VPS MicroTix dari lingkungan audit ini — perintah operator disediakan §3 |
| Laporan di-commit di `report/` | ✅ | 8 dokumen audit ini |

## 2. Risiko tersisa (terbuka, jujur)

| Risiko | Tingkat | Catatan |
|--------|---------|---------|
| Deployment live belum dieksekusi | Sedang | Patch teruji penuh di lingkungan setara; operator tinggal menjalankan satu perintah (§3) |
| Self-signed TLS ditolak platform hosted | Rendah | By design; gunakan `--domain` (Let's Encrypt) — sudah direkomendasikan installer |
| Eksekusi agent = akses penuh host (tanpa sandbox) | Diterima | PRD FR-010/FR-013; mitigasi: keys, revokasi, audit log, warning README |
| Aset rilis tanpa signature GPG | Rendah | Checksum SHA-256 + HTTPS + tag immutable; future work |
| v0.2.0/v0.3.0 tanpa GitHub Release | Diterima | Historis; tag immutable tidak ditulis ulang; upgrade by-tag tetap berfungsi |

## 3. Prosedur upgrade MicroTix VPS (operator, satu perintah)

Prasyarat verifikasi pra-deploy (jalankan sebagai root di VPS):

```bash
uaeg version                          # catat versi terpasang (mis. 0.3.0/0.3.1)
sudo uaeg keys list                   # catat jumlah & status key
systemctl status uaeg                 # service sehat
df -h /var/backups                    # ruang cukup (± app+data ×1.2 + 50 MiB)
```

Upgrade (alur yang GAGAL pada v0.3.1 — kini aman dari direktori mana pun,
termasuk `~/uaeg` yang masih berisi upgrade.sh lama):

```bash
cd ~/uaeg   # opsional — direktori mana pun kini setara
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh
curl -fsSLO https://github.com/smahud/uaeg/releases/latest/download/upgrade.sh.sha256
sha256sum -c upgrade.sh.sha256
sudo bash upgrade.sh
```

Yang otomatis dilakukan script: deteksi instalasi → backup konsisten
(DB+config+app) → staging clone tag v0.3.2 (clone `~/uaeg` Anda **tidak
disentuh**) → upgrade in-place (konfigurasi lama dipertahankan) →
verifikasi zero-data-loss (key identik, DB utuh, config sama, password
dashboard tetap valid, /healthz OK) → gagal = rollback otomatis.

Verifikasi pasca-upgrade:

```bash
uaeg version                          # harus uaeg 0.3.2
sudo uaeg doctor
sudo uaeg keys list                   # key agent harus tetap lengkap & valid
systemctl status uaeg
curl -sk https://127.0.0.1:3902/healthz
```

Rollback bila diperlukan: `sudo bash upgrade.sh --rollback`

## 4. Keputusan akhir

**GO** — v0.3.2 layak rilis: seluruh butir rilis-blocking terverifikasi
dengan bukti eksekusi. Deployment live MicroTix **PENDING operator**
(satu perintah di atas); tidak ada defek P0/P1 yang menahan rilis.
