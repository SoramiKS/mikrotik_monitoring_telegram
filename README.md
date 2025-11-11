
# Dokumentasi Proyek Monitoring Jaringan via SNMP & Telegram

Dokumen ini memberikan penjelasan komprehensif mengenai arsitektur, cara kerja, dan panduan penggunaan untuk proyek sistem monitoring jaringan.

---

## 1. Identitas Proyek

- **Nama Proyek**: Sistem Monitoring Jaringan Berbasis SNMP
- **Bahasa Pemrograman**: Python 3
- **Teknologi Utama**:
  - **`pysnmp`**: Untuk komunikasi dan pengambilan data dari perangkat jaringan melalui protokol SNMP.
  - **`python-telegram-bot`**: Untuk membangun antarmuka bot di Telegram sebagai sarana interaksi pengguna.
  - **`requests`**: Digunakan untuk mengirim notifikasi ke API Telegram.
  - **`python-dotenv`**: Untuk manajemen variabel lingkungan dan konfigurasi sensitif.
- **Tujuan Proyek**: Menyediakan sistem pemantauan otomatis untuk perangkat jaringan (seperti router MikroTik). Sistem ini memonitor metrik vital seperti utilisasi CPU, penggunaan RAM, status antarmuka (up/down), dan total trafik data. Sistem juga mampu mengirimkan peringatan proaktif melalui Telegram jika terjadi anomali (misalnya, CPU terlalu tinggi atau antarmuka mati) dan menyajikan laporan harian serta bulanan.
- **Arsitektur**: Proyek ini terdiri dari dua komponen utama yang berjalan sebagai proses terpisah:
  1.  **Collector (`monitoring.py`)**: Sebuah skrip yang berjalan di latar belakang secara terus-menerus untuk melakukan polling data dari perangkat yang terdaftar.
  2.  **Bot Interface (`bot.py`)**: Sebuah bot Telegram yang berfungsi sebagai dasbor interaktif bagi pengguna untuk meminta data live, ringkasan harian, atau laporan bulanan.
  
  Kedua komponen ini berkomunikasi secara tidak langsung melalui **file JSON** yang disimpan di dalam sistem file, yang berfungsi sebagai basis data sederhana untuk menyimpan status terkini dan data historis.

---

## 2. Struktur Direktori

Berikut adalah penjelasan mengenai struktur file dan direktori dalam proyek:

```
.
├── .env                  # File konfigurasi untuk menyimpan variabel rahasia (API token, dll.)
├── bot.py                # Entry point untuk menjalankan Telegram Bot.
├── devices.json          # Konfigurasi daftar perangkat jaringan yang akan dimonitor.
├── monitoring.py         # Skrip utama untuk proses polling dan pengumpulan data SNMP.
├── requirements.txt      # Daftar dependensi paket Python yang dibutuhkan.
├── logs/                 # Direktori untuk menyimpan arsip laporan data.
│   └── {device_name}/    # Sub-direktori per perangkat.
│       └── {year-month}/ # Sub-direktori per bulan.
│           ├── daily_summary.jsonl # Ringkasan data harian (format JSON Lines).
│           └── {year-month}.tar.gz # Arsip data bulanan.
├── script_logs/          # Log operasional dari skrip monitoring.py.
│   └── monitoring_script.log # File log utama, dengan rotasi harian.
└── status/               # Direktori untuk menyimpan data "live" atau status terkini.
    ├── {device_name}.json    # Status SNMP terakhir dari setiap perangkat.
    ├── daily_accumulator.json # Akumulasi data untuk laporan harian.
    └── script_state.json     # Status internal skrip (misal: bulan terakhir laporan dibuat).
```

---

## 3. Penjelasan Teknis

### Alur Kerja Program

1.  **Inisialisasi**: Skrip `monitoring.py` dimulai, membaca daftar perangkat dari `devices.json` dan memuat status terakhir dari direktori `status/`.
2.  **Polling Loop**: Skrip masuk ke dalam *loop* tak terbatas yang berjalan setiap `CHECK_INTERVAL` detik (default 60 detik).
3.  **Pengambilan Data Paralel**: Dalam setiap iterasi, skrip menggunakan `ThreadPoolExecutor` untuk melakukan polling ke semua perangkat secara bersamaan (paralel), sehingga efisien.
4.  **Query SNMP**: Untuk setiap perangkat, skrip mengirimkan permintaan SNMP (`getCmd`) untuk mengambil data OID (Object Identifier) yang relevan, seperti:
    - Utilisasi CPU.
    - Total dan RAM terpakai.
    - Status operasional setiap antarmuka (up/down).
    - Counter total byte masuk (`ifInOctets`) dan keluar (`ifOutOctets`).
5.  **Deteksi Perubahan & Peringatan**:
    - Data yang baru diambil dibandingkan dengan status sebelumnya (yang disimpan di `status/{device_name}.json`).
    - **Interface Down**: Jika sebuah antarmuka terdeteksi `down` selama **dua kali pengecekan berturut-turut**, sebuah notifikasi peringatan akan dikirim ke Telegram. Ini mencegah *false positives* akibat fluktuasi sesaat.
    - **Interface Up**: Jika antarmuka yang sebelumnya `down` kembali `up`, notifikasi pemulihan akan dikirim.
    - **CPU/RAM High**: Jika penggunaan CPU atau RAM melebihi ambang batas yang ditentukan di `devices.json`, notifikasi peringatan juga dikirim.
6.  **Penyimpanan Data**:
    - Status terbaru dari setiap perangkat (termasuk nilai counter terakhir) disimpan kembali ke `status/{device_name}.json`.
    - Perubahan data (selisih byte masuk/keluar, event up/down) diakumulasikan ke dalam file `status/daily_accumulator.json`.
7.  **Laporan Harian & Bulanan**:
    - **Harian**: Setiap tengah malam, data dari `daily_accumulator.json` difinalisasi dan disimpan sebagai ringkasan harian di `logs/{device_name}/{year-month}/daily_summary.jsonl`.
    - **Bulanan**: Di awal bulan baru, skrip akan memproses semua ringkasan harian dari bulan sebelumnya untuk menghasilkan laporan bulanan. Laporan ini dikirim ke Telegram, dan direktori log bulan tersebut diarsipkan menjadi file `.tar.gz` untuk menghemat ruang.

### Komponen Bot (`bot.py`)

Bot berfungsi sebagai antarmuka pengguna. Ketika pengguna mengirim perintah (misalnya `/status`), bot akan:
1. Membaca file yang relevan dari direktori `status/` atau `logs/`.
2. Memformat data JSON menjadi pesan yang mudah dibaca.
3. Mengirimkan pesan tersebut sebagai balasan di Telegram.

### Konfigurasi Penting

- **`.env`**: File ini **wajib** ada dan berisi:
  - `TELEGRAM_BOT_TOKEN`: Token API untuk bot Telegram Anda.
  - `TELEGRAM_CHAT_IDS`: Daftar ID chat Telegram (dipisahkan koma) yang akan menerima notifikasi.
- **`devices.json`**: File ini adalah jantung dari konfigurasi monitoring. Setiap objek dalam array JSON mewakili satu perangkat dengan properti seperti:
  - `name`: Nama unik untuk perangkat.
  - `ip`: Alamat IP perangkat.
  - `community`: SNMP community string (biasanya 'public' atau string custom).
  - `oids`: OID spesifik untuk CPU dan RAM (bisa berbeda antar vendor).
  - `interfaces`: Daftar *index* dan nama antarmuka yang ingin dimonitor.
  - `use_64bit_counters`: Setel ke `true` untuk antarmuka berkecepatan tinggi (di atas 100Mbps) untuk menghindari masalah *counter wrap*.

---

## 4. Instruksi Penggunaan

### Prasyarat Sistem

- Python 3.8 atau lebih baru.
- Akses jaringan dari server ke perangkat target pada port UDP 161 (SNMP).

### Instalasi

1.  Clone repositori ini.
2.  Buat dan isi file `.env` berdasarkan contoh atau kebutuhan.
    ```bash
    # Contoh isi file .env
    TELEGRAM_BOT_TOKEN="12345:your-secret-token"
    TELEGRAM_CHAT_IDS="-10012345678,987654321"
    CHECK_INTERVAL=60
    ```
3.  Edit `devices.json` untuk menambahkan perangkat yang ingin Anda monitor.
4.  Install semua dependensi Python:
    ```bash
    pip install -r requirements.txt
    ```

### Menjalankan Proyek

Proyek ini membutuhkan dua proses yang berjalan secara simultan. Gunakan dua terminal terpisah atau jalankan sebagai *background service* (misalnya dengan `systemd` atau `supervisor`).

1.  **Jalankan Skrip Monitoring**:
    ```bash
    python monitoring.py
    ```
    Terminal ini akan menampilkan log dari proses polling data.

2.  **Jalankan Telegram Bot**:
    ```bash
    python bot.py
    ```
    Setelah ini berjalan, bot Anda akan aktif di Telegram dan siap menerima perintah.

### Perintah Penting di Bot

- `/status`: Menampilkan status live (Up/Down) semua antarmuka.
- `/cpu` atau `/ram`: Menampilkan rata-rata penggunaan CPU & RAM saat ini.
- `/interfaces`: Menampilkan ringkasan trafik dan event untuk semua antarmuka.
- `/summary`: Memberikan ringkasan penggunaan CPU & RAM untuk hari ini.
- `/report`: Meminta laporan bulanan terakhir untuk semua perangkat.
- `/device <nama>`: Menampilkan status detail untuk satu perangkat spesifik.
- `/uptime`: Menampilkan perkiraan uptime perangkat.
- `/help`: Menampilkan daftar semua perintah yang tersedia.

---

## 5. Catatan Tambahan

### Troubleshooting Umum

- **SNMP Unreachable**: Pastikan alamat IP, community string, dan versi SNMP sudah benar. Periksa juga *firewall* di antara server monitoring dan perangkat target.
- **Bot Tidak Merespon**: Pastikan `TELEGRAM_BOT_TOKEN` valid dan skrip `bot.py` sedang berjalan tanpa error.
- **Data Tidak Muncul**: Pastikan skrip `monitoring.py` berjalan. Cek log di `script_logs/monitoring_script.log` untuk melihat apakah ada error saat polling data.

### Keamanan

- **SNMP Community**: Proyek ini menggunakan SNMPv2c yang mengirimkan *community string* dalam bentuk teks biasa. Ini kurang aman. Untuk lingkungan produksi yang serius, sangat disarankan untuk meng-upgrade skrip agar mendukung **SNMPv3** yang menyediakan enkripsi dan autentikasi.
- **Akses Server**: Lindungi akses ke server tempat skrip ini berjalan, karena file `.env` dan `devices.json` berisi informasi sensitif.

### Potensi Pengembangan Lanjutan

- **Migrasi Database**: Mengganti penyimpanan berbasis file JSON dengan database Time-Series seperti **InfluxDB** atau **Prometheus** untuk skalabilitas dan performa query yang lebih baik.
- **Visualisasi Data**: Membuat dasbor web (misalnya dengan **Grafana**, **Flask**, atau **Django**) untuk visualisasi data yang lebih kaya.
- **Dukungan SNMPv3**: Menambahkan dukungan untuk SNMPv3 untuk meningkatkan keamanan.
- **Auto-Discovery**: Menambahkan fitur untuk memindai jaringan dan menemukan perangkat baru secara otomatis.
- **Notifikasi Fleksibel**: Menambahkan kanal notifikasi lain selain Telegram, seperti Email atau Slack.
