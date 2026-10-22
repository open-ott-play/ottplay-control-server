<div lang="id" dir="ltr">

# Kontrol andal dan pengalihan pemutaran

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Indeks dokumentasi](wiki/Home.md)

Fitur ini memerlukan pengontrol dan CLI dari revisi yang memuat panduan ini. Perbandingan pengaturan secara persis dan pengalihan juga memerlukan pemutar yang diperbarui. Arsip rilis lama dan revisi CLI historis dalam panduan instalasi tidak otomatis memperoleh fitur ini. Instal **semua berkas `cli/*.py` bersama-sama**, termasuk `preset_session.py`; pertahankan konfigurasi privat.

![Alur kontrol, titik pemeriksaan, dan pengalihan](assets/reliable-control.svg)

## Mengaktifkan hasil persisten yang dibatasi

Tambahkan blok opsional ini ke konfigurasi server privat, lalu validasi dan mulai ulang server. Pilih jalur absolut pada sistem berkas lokal persisten yang dapat ditulis akun layanan. Di Unix, direktorinya harus privat (0700); berkas jurnal dan kunci yang sudah ada harus berupa berkas biasa privat (0600). Di Windows, batasi ACL direktori ke akun layanan. Satu proses memiliki satu jurnal.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Server membuat direktori yang belum ada dengan mode 0700. Batasnya 1–4096 rekaman, 60–86400 detik, dan 4 MiB pada disk. Retensi harus mencakup `command_ttl_seconds`. Jurnal penuh menolak operasi berkunci baru alih-alih membuang rekaman yang masih berlaku. Berkas rusak, direktori Unix tidak aman, penulis lain, atau kegagalan penulisan persisten menyebabkan penolakan yang aman. Jalur perintah dalam memori yang sudah ada tetap menjadi default.

Unit systemd yang disertakan membuat `/var/lib/ottplay-control` dengan mode 0700. Untuk Compose, sediakan direktori host milik UID 65532, atur `CONTROL_SERVER_STATE` ke jalur absolutnya, dan gabungkan `deploy/compose.yml` dengan `deploy/compose.journal.yml`. Berkas terakhir hanya menambahkan mount status yang dapat ditulis; sistem berkas root tetap hanya baca. Untuk Helm, sediakan klaim persisten secara terpisah dan atur `journalExistingClaim=CLAIM_NAME`. Klaim dipasang di `/var/lib/ottplay-control`; gunakan subdirektori privat yang ditunjukkan di atas. Chart mempertahankan satu replika, `Recreate`, dan `fsGroupChangePolicy: OnRootMismatch`, sehingga pemasangan ulang tidak memperluas izin direktori privat yang sudah dibuat secara rekursif. Pastikan driver penyimpanan yang dipilih mendukung semantik kepemilikan ini. Chart tidak membuat/menghapus klaim, dan mengaktifkan mount tidak mengaktifkan jurnal: konfigurasi server privat tetap mengendalikannya.

Untuk `POST /api/requests`, tambahkan `idempotency_key` di samping `action` dan `params`. POST perintah lama menerima `Idempotency-Key` sebagai gantinya. Kunci berisi 16–80 huruf ASCII, angka, garis bawah, atau tanda hubung; buat kunci acak baru untuk setiap maksud. Pemilik/kunci yang sama dengan perangkat, tindakan, dan muatan JSON ternormalisasi yang sama mengembalikan ID awal tanpa mengantrekan ulang. Perubahan muatan mengembalikan 409. Kunci dibatasi berdasarkan ID operator (atau administrator), bukan perangkat. Setelah retensi berakhir, kunci dapat diterima sebagai maksud baru: klien tidak boleh mencoba ulang maksud lama di luar jendela tersebut.

`GET /api/operations?device_id=DEVICE` melaporkan apakah jurnal aktif, batasnya, dan TTL perintah. Tambahkan `&key=KEY` untuk membaca operasi:

- `pending`: diterima; belum ada respons yang disimpan.
- `responded`: respons pemutar yang tervalidasi telah tiba; `result_status` adalah `ok`, `rejected`, atau `unsupported`. Isi hasil awal tetap kedaluwarsa secara normal.
- `acknowledged`: konsumen lama mengakui perintah. Ini tidak membuktikan pemutaran yang terlihat atau bahwa perubahan pengaturan tersimpan.
- `unknown`: tenggat lewat atau server dimulai ulang sebelum respons disimpan. Server **tidak pernah memutar ulang** perintah ini setelah mulai ulang.

POST berkunci yang diulang mengembalikan `status: retained`, ID awal, dan status. POST baru biasa tetap menggunakan `status: queued`. Rekaman jurnal hanya berisi pemilik, perangkat, tindakan, ID acak, digest muatan, status, dan tenggat. Tidak ada muatan perintah, URL penyedia, token, tangkapan layar, atau isi respons. Perintah tertunda tidak dipersistenkan. Ini adalah deduplikasi dan pelacakan hasil yang dibatasi, bukan eksekusi tepat satu kali, antrean kerja persisten, atau ketersediaan tinggi multiserver.

## Merencanakan, menerapkan, melanjutkan, dan memverifikasi Local/Home

Perilaku `ott PLAYER load PRESET` yang ada dipertahankan. Untuk alur baru:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` berjalan offline: memvalidasi preset lokal dan mencantumkan slot pengaturan serta profil aktif yang diinginkan tanpa mencetak isinya. Perintah ini tidak memeriksa pemutar. `verify` melakukan perbandingan persis hanya baca dan memeriksa profil M3U terpilih. Kode keluar bukan nol dikembalikan jika ada perbedaan atau perbandingan tidak didukung.

`apply` memerlukan jurnal server. Perintah ini membuat titik pemeriksaan privat dan membandingkan setiap penyedia terkonfigurasi sebelum perubahan pertama. Setiap penyimpanan membandingkan byte tersimpan dengan nilai yang diharapkan melalui `settings_compare`; kecocokan melewati penulisan. Verifikasi akhir mencakup Plex, semua slot M3U, slot Stalker opsional, dan profil M3U terpilih. Perbandingan mengembalikan boolean, tidak pernah rahasia tersimpan.

Titik pemeriksaan default berada di bawah `~/.config/ottplay-control/checkpoints/`. Gunakan `--checkpoint /absolute/private/path/session.json` setelah mode untuk memilih berkas lain. Titik pemeriksaan berisi perangkat, nama preset, hash revisi server/preset, nama langkah selesai, dan satu kunci operasi acak tertunda beserta tenggat. Tidak ada nilai pengaturan. Berkas diganti secara atomik setelah fsync dan dikunci terhadap penulis CLI bersamaan. Pastikan preset lokal yang cocok tetap tersedia. Di Unix, direktori harus privat (0700), dengan berkas biasa bermode 0600. Di Windows, batasi ACL direktori yang diwarisi ke pengguna yang menjalankan CLI; bit mode Unix tidak memberlakukan izin akses Windows.

`resume` memvalidasi server, perangkat, dan revisi preset yang sama, lalu membaca hasil tertunda. Respons tersimpan memungkinkan pemeriksaan ulang segera. Hasil tidak pasti menghentikan pengiriman perubahan sampai tenggat perintah sebelumnya, batas waktu permintaan, dan sedikit margin berlalu. Jalankan `resume` lagi setelah jendela itu. Pengaturan tersimpan diperiksa ulang bahkan untuk langkah yang selesai, agar perubahan pengguna tidak dianggap sebagai penulisan sebelumnya yang berhasil. Perintah relatif tidak pernah diulang secara membabi buta. Pemilihan penyedia/profil secara absolut boleh diulang setelah masa pengaman.

`apply` baru tidak pernah menimpa titik pemeriksaan yang ada. Gunakan kembali melalui `resume`, atau pilih berkas baru untuk preset yang berubah. Perangkat terpisah memiliki titik pemeriksaan terpisah. Tidak ada transaksi atau rollback yang mencakup semua pemutar; penulisan sebelumnya yang berhasil tetap ada jika langkah berikutnya gagal. `verified` mengonfirmasi nilai pemutar tersimpan saat dibandingkan, bukan keberhasilan pemuatan jaringan atau keluaran video/audio fisik.

## Berikan hanya kewenangan yang diperlukan kepada integrasi

Gunakan [operator perintah biasa](security-design.md#scoped-command-operators) untuk HA, jembatan RF, atau agen, bukan membagikan `admin_token`. Izinkan ID perangkat yang tepat dan operasi seperti `input.channel_up` dan `input.channel_down`. Operasi pengaturan memerlukan izin eksplisit `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings`, dan `provider_settings` untuk alur preset ini. Pengalihan memerlukan `playback_handoff_export` pada sumber dan `playback_handoff_import` pada tujuan. Entri operator menggabungkan perangkat dan tindakan yang dicantumkan; gunakan identitas operator terpisah jika operasi yang sama tidak boleh berlaku pada setiap perangkat yang tercantum. Token dan persetujuan diagnostik tetap menjadi kewenangan terpisah.

Operator tidak dapat memeriksa antrean pemutar, mengirim respons pemutar, mencantumkan semua perangkat, membaca keluaran debug admin, atau membaca hasil operator lain. Mempersempit cakupan tindakan juga mencegah pembacaan operasi yang disimpannya. Menghapus operator dan memulai ulang mencabut kredensialnya. Jangan gunakan kembali ID operator untuk pihak lain selama rekaman jurnal lamanya masih ada.

## Melanjutkan pada pemutar lain

```sh
ott handoff living-room bedroom
```

Sumber mengekspor referensi siaran langsung/arsip versi 1: penyedia, identitas sumber opak, sidik jari item opak `channel-v1:<8 hex>.<8 hex>`, dan untuk arsip, waktu program saat ini. ID penyedia mentah dan URL stream tetap lokal. Tujuan harus memiliki **sumber/akun terkonfigurasi yang sama**, menemukan satu item cocok yang unik dalam katalognya, dan memperoleh URL baru dengan kredensial lokalnya sendiri. Pengontrol tidak menyalin URL yang dapat diputar atau mengubah kredensial tujuan. Sumber tetap memutar. Hasil CLI berhasil berarti tujuan telah mengirim permintaan, bukan layar atau pengeras suara fisiknya sudah diverifikasi.

Versi 1 mendukung katalog siaran langsung/arsip `m3u`, `xtream`, dan `stalker` jika pemutar dapat menyediakan identitas yang diperlukan. Item tidak dikenal/ambigu, sumber tidak cocok, dan penolakan kebijakan lokal gagal tanpa pencocokan cadangan berdasarkan nama. VOD/serial, pemetaan antar akun berbeda, menjeda sumber, dan sinkronisasi riwayat/favorit berkelanjutan tidak termasuk kontrak pertama ini.

## Latihan validasi dan pemulihan

Jalankan `go test -race ./...`, `go vet ./...`, dan `python3 -m unittest discover -s tests`. Pengujian mencakup balasan hilang setelah penulisan berhasil, perbandingan persis sebelum melanjutkan, penyimpangan konfigurasi, tenggat operasi tidak diketahui, dua penulis, jurnal rusak, pembacaan lintas operator, dan muatan pengalihan yang rusak. Pada pemutar staging, hentikan juga CLI setelah menyimpan pengaturan, mulai ulang pengontrol, lanjutkan, dan verifikasi; uji pengalihan antara sumber yang cocok dan tidak cocok. Konfirmasikan video/audio secara terpisah.

Untuk validasi lokal lintas repositori, dengan salinan kerja FOSS terkini dan dependensi pengembangannya terinstal, jalankan:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Uji smoke membangun server Go ini, mengompilasi seluruh proyek TypeScript FOSS, dan menjalankan transport nyata, penangan perintah, identitas sumber, serta codec playlist bawaan terhadap CLI Python. Uji ini memeriksa pengaturan persis ketiga penyedia yang didukung, ketidakcocokan, pengalihan langsung dengan ID baris lokal berbeda, offset pencarian arsip, kegagalan resolusi URL, dan validasi format transmisi yang ketat. Hanya penyimpanan, kebijakan penyedia, dan antarmuka host pemutaran yang merupakan pengganti uji; browser, keluaran media fisik, atau akses akun nyata tidak diverifikasi. Uji mengikat loopback, memakai berkas sementara privat, tidak menghubungi URL stream, dan menghapus berkas/server saat keluar.

</div>
