# Runbook Deployment — UAEG v0.3.0 di VPS MicroTix (Debian 13)

Runbook ini mengeksekusi master prompt P0 Anda langkah demi langkah, di VPS yang
**sudah menjalankan UAEG v0.2.0**. Estimasi waktu: 15–25 menit.

> Prinsip: **zero data loss**. Semua agent key, konfigurasi, job, dan log dipertahankan.
> Setiap langkah punya verifikasi; jika gagal, ada prosedur rollback (langkah R).

---

## Pra-deployment (wajib, 5 menit)

```bash
# 1. Backup database + konfigurasi + file TLS (konsisten, service tetap jalan)
sudo systemctl stop uaeg
sudo cp -a /var/lib/uaeg /var/lib/uaeg.bak-$(date +%Y%m%d-%H%M%S)
sudo cp -a /etc/uaeg   /etc/uaeg.bak-$(date +%Y%m%d-%H%M%S)
sudo systemctl start uaeg

# 2. Catat identitas instalasi lama (evidence)
uaeg version                 # harus v0.2.0
sudo uaeg keys list          # CATAT jumlah key aktif + key_id (mis. 2 key)
sudo uaeg status             # CATAT port MCP & dashboard yang terpasang
sudo sqlite3 /var/lib/uaeg/uaeg.db "PRAGMA quick_check;"   # harus: ok
```

> Catat hasil perintah di atas — akan dibandingkan setelah upgrade.

## Langkah 1 — Ambil & deploy v0.3.0 (upgrade in-place, idempotent)

```bash
cd /opt
sudo git clone https://github.com/smahud/uaeg.git uaeg-src   # atau: cd uaeg && git pull
cd uaeg-src
sudo git checkout af809db        # commit deployment v0.3.0 (atau 'main' terbaru)
```

Jalankan installer **mode upgrade** (pertahankan semua nilai lama — jangan beri flag
port/domain, sehingga konfigurasi lama dipertahankan):

```bash
sudo ./install.sh --yes
```

Installer otomatis: mendeteksi instalasi lama → backup aplikasi (rollback safety) →
**mempertahankan config lama** → restart service terkontrol → cek health.

**Verifikasi zero-data-loss (langkah 2 master prompt §11):**

```bash
uaeg version                    # harus v0.3.0
sudo uaeg keys list             # jumlah key & key_id HARUS sama dengan pra-deploy
sudo uaeg status                # port & listener sama seperti sebelumnya
sudo sqlite3 /var/lib/uaeg/uaeg.db "PRAGMA quick_check;"   # ok
```

**Verifikasi MCP masih berfungsi (MicroTix agent tidak terganggu):**

```bash
sudo uaeg doctor                # semua check harus lulus
# uji koneksi agen yang sudah ada dari platform hosted Anda (tool server_info), ATAU:
curl -sk -X POST https://127.0.0.1:3902/mcp \
  -H "Authorization: Bearer <UAEG_AGENT_KEY_LAMA_ANDA>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}' \
  | grep serverInfo             # harus keluar serverInfo UAEG
```

> Sampai titik ini dashboard masih **legacy mode** (tanpa login, hanya private bind) —
> perilaku identik v0.2.0. Aman untuk berhenti di sini bila perlu.

## Langkah 2 — Set password dashboard (interaktif, BERSAMA OPERATOR)

```bash
sudo uaeg pass
```

```
UAEG Dashboard Password Setup

Enter new password:
Confirm new password:

Password updated successfully.
```

Aturan: minimal 12 karakter, diketik dua kali, input tersembunyi. **Password dibuat
oleh operator — jangan di-generate/dicetak otomatis oleh tool apa pun.** Tidak perlu
restart service; sesi lama otomatis dibatalkan; **agent key MCP tidak terpengaruh.**

Verifikasi cepat (opsional, dari VPS):

```bash
curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" http://127.0.0.1:3903/
# harus: 302 -> .../login   (login langsung aktif tanpa restart)
```

## Langkah 3 — Aktifkan dashboard publik (0.0.0.0 + HTTPS + password)

Prasyarat: **domain VPS sudah menunjuk ke IP publik** (untuk sertifikat tepercaya via
Let's Encrypt). Ganti `gw.example.com` dengan domain Anda.

```bash
sudo ./install.sh --dashboard-bind public --domain gw.example.com --yes
```

Installer memverifikasi password sudah ada (menolak jika belum), lalu men-set
`dashboard.bind=0.0.0.0`, `dashboard.tls=true` (sertifikat yang sama dengan MCP), dan
merestart service.

Alternatif tanpa installer (urutan penting, tiap langkah tervalidasi):

```bash
sudo uaeg config set dashboard.allow_public true
sudo uaeg config set dashboard.tls true
sudo uaeg config set dashboard.bind 0.0.0.0
sudo systemctl restart uaeg
```

Jika hanya punya IP publik tanpa domain: gunakan `--public-ip <IP> --cert/--key`
(sertifikat tepercaya untuk IP) — **jangan** memakai self-signed untuk akses produksi
dari internet, dan **jangan pernah menonaktifkan TLS**.

## Langkah 4 — Firewall

```bash
# MCP (seharusnya sudah terbuka)
sudo ufw allow 3902/tcp
# Dashboard publik
sudo ufw allow 3903/tcp
sudo ufw status
```

(Opsional, disarankan) Batasi dashboard ke IP operator Anda saja:
`sudo uaeg config set network.allowed_dashboard_cidrs '["1.2.3.4/32"]'` lalu restart.)

## Langkah 5 — Uji dari jaringan EKSTERNAL (laptop/HP, bukan dari VPS)

Buka browser di perangkat luar:

1. `https://gw.example.com:3903` → **halaman login UAEG** harus muncul
   (gembok TLS hijau/terpercaya, bukan peringan sertifikat).
2. Password salah → pesan generik. 5× salah → sementara terkunci (429).
3. Password benar → dashboard penuh (Keys/Permissions/Sessions/Logs/Server).
4. Refresh halaman → sesi tetap aktif.
5. Klik **Sign out** → kembali ke halaman login; API menjadi 401.
6. `https://gw.example.com:3903/api/keys` tanpa login (tab baru/incognito) → 401.
7. Pastikan agent MicroTix masih terhubung (panggil `server_info` dari agen, atau cek
   tab Sessions/Logs di dashboard — aktivitas agen tetap terlihat).

Rotasi password (uji sesi lama dicabut): jalankan `sudo uaeg pass` lagi di VPS →
browser yang sedang login akan ditendang ke halaman login pada request berikutnya.

## Checklist penerimaan akhir (master prompt §13)

| # | Kriteria | Verifikasi |
|---|---|---|
| 1-2 | `sudo uaeg pass` interaktif, dua kali input | Langkah 2 |
| 3-5 | Login page → dashboard → logout mengakhiri sesi | Langkah 5.1–5.5 |
| 6 | Semua API dashboard terlindungi | Langkah 5.6 (401) |
| 7-8 | Bind `0.0.0.0`; akses eksternal via HTTPS | Langkah 3 & 5.1 |
| 9-10 | MCP key lama tidak hilang; agent tetap terkoneksi | Langkah 1 verifikasi + 5.7 |
| 11 | Restart/upgrade tidak merusak state | `sudo systemctl restart uaeg` → ulangi verifikasi keys/doctor |
| 12 | Repository terbarui | commit `af809db` di GitHub |
| 13 | Laporan pengujian di `report/` | `report/DASHBOARD_AUTH_DEPLOYMENT.md` |

## R — Rollback (jika ada langkah gagal)

```bash
# Aplikasi & konfigurasi
sudo systemctl stop uaeg
sudo rm -rf /opt/uaeg
sudo mv /opt/uaeg.upgrade-bak-<timestamp> /opt/uaeg        # backup otomatis installer
sudo cp /etc/uaeg.bak-<timestamp>/config.json /etc/uaeg/config.json
sudo systemctl start uaeg

# Data (hanya jika benar-benar diperlukan — database tidak pernah diubah oleh v0.3.0)
sudo systemctl stop uaeg
sudo rm -rf /var/lib/uaeg && sudo mv /var/lib/uaeg.bak-<timestamp> /var/lib/uaeg
sudo systemctl start uaeg
```

Menonaktifkan dashboard publik tanpa rollback penuh:

```bash
sudo uaeg config set dashboard.bind 127.0.0.1
sudo uaeg config set dashboard.allow_public false
sudo uaeg config set dashboard.tls false
sudo systemctl restart uaeg
```

---

## Catatan keamanan

- Password dashboard disimpan sebagai hash **scrypt** di
  `/var/lib/uaeg/dashboard-pass.json` (0600) — tidak ada plaintext di disk/log/audit.
- Password dashboard ≠ agent key MCP: rotasi password tidak menyentuh autentikasi MCP.
- Password lama tidak diminta oleh `uaeg pass` karena perintah dijalankan sebagai root
  lokal di VPS (privilege administrator).
- Sesi berbasis cookie `HttpOnly` + `SameSite=Lax` (+ `Secure` pada HTTPS), kedaluwarsa
  30 menit idle / 12 jam absolut, dan seluruh sesi dibatalkan saat password diganti.
