# Root Cause Analysis — Insiden Upgrade v0.3.1 (VPS MicroTix)

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
