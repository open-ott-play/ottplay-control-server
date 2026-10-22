<div lang="id" dir="ltr">

# OTA Android: pemutar biasa dan agen root terpisah

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Untuk **k8a/fire8 dan pemutar Capacitor lainnya**, gunakan pembaru Wi-Fi pemutar normal, bukan perintah agen root:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Tanpa `--version`, beta terbitan terbaru dipilih. Gunakan `full`, bukan `api22`, pada Android 7+. CLI menemukan rilis resmi, memeriksa manifest, menyiapkan APK, lalu memeriksa statusnya sebelum meminta instalasi sekali. Android mungkin meminta konfirmasi pada tablet. Nonaktifkan kios dan buka pengaturan terlindungi secara eksplisit sebelum pemeliharaan; perintah tidak pernah mengubah kebijakan tersebut. Lihat [prosedur CLI lengkap](cli.md#capacitor-apk-updates-over-wi-fi).

Ini memerlukan APK yang sudah terinstal dengan dukungan `AppUpdate`, Wi-Fi, dan pemutar berjalan yang terhubung ke pengontrol. Pembaruan biasa berikutnya tidak memerlukan USB, tetapi tidak dapat menjangkau tablet offline atau WebView yang berhenti. Instalasi pertama, pembaru tidak tersedia, aplikasi rusak, atau jaringan hilang masih dapat memerlukan pemulihan lokal. Tidak ada penjadwal rilis otomatis atau pemasang senyap umum yang tersirat.

Sisa panduan menjelaskan **agen pemeliharaan a1 ber-root yang berbeda**, terbatas pada instalasi Matricom/API19 yang disiapkan `play.ott.kitkat`. Agen itu belum terinstal maupun dapat digunakan pada perangkat Fire/Capacitor biasa.

Setelah inisialisasi USB root satu kali, agen Android independen dapat menerima pembaruan bertanda tangan melalui koneksi HTTPS keluarnya. WebView yang berfungsi tidak diperlukan untuk memperbarui dirinya. Agen 0.1.2 juga mendukung bundel APK dan helper sentuh sistem yang terkoordinasi, dengan berkas pemulihan tersimpan dan verifikasi sebelum komit.

Panduan ini menjelaskan seluruh jalur. Untuk kolom manifest yang tepat, batas, dan kebutuhan perangkat keras, gunakan [referensi agen native](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Yang diperbarui

![Arsitektur OTA: byte rilis yang ditinjau ditandatangani, diterbitkan melalui HTTPS langsung, diverifikasi agen independen, lalu diperiksa setelah instalasi. Penyebaran web terpisah.](images/ota-architecture.svg)

- **Agen native:** `kind: agent` mengganti binari ARMv7 miliknya. Jalur ini juga bekerja saat OTT dinonaktifkan dan aplikasi HOME lain digunakan.
- **Hanya APK:** `kind: apk` mengganti pembungkus sambil mempertahankan datanya. OTT harus aktif di latar depan. Helper tertanam kandidat harus sama persis dengan helper sistem terinstal.
- **APK dan helper sentuh:** `kind: bundle` memperbarui kedua komponen sebagai pasangan. Agen lama harus lebih dahulu menerima pembaruan agen saja yang mendukung bundel.
- **Pemutar yang di-host:** sebarkan artefak web terverifikasi pada origin yang ada, lalu muat ulang dan periksa pemutar aktif. Pembaruan APK tidak menyebarkan JavaScript yang di-host. Lihat [alur rilis](release-workflow.md).
- **Pengontrol dan CLI:** sebarkan pengontrol dan instal seluruh modul CLI secara terpisah. Memperbarui salah satunya tidak mengganti kode pada tablet.

Firmware, kernel, WebView sistem, perantara root, dan skrip startup berada di luar format OTA ini. [Referensi native](../native/android-agent/README.md#bootstrap) menjelaskan batas inisialisasi dan pemulihan. Operasi OTA yang didukung tidak memerlukan ADB jaringan atau shell jarak jauh sebarang.

## Dari rilis yang ditinjau ke instalasi terkonfirmasi

1. **Pilih byte terverifikasi.** Gabungkan perubahan yang ditinjau setelah CI lulus. Unduh artefak yang dimaksud dan verifikasi revisi sumber, bukti rilis yang tidak berubah, serta checksum. Ekstrak binari `agent` dari arsip native; arsip itu sendiri bukan muatan agen yang dapat dipasang.
2. **Siapkan dan tandatangani.** Gunakan kunci Ed25519 privat yang ada dan kunci publiknya telah dipatok saat inisialisasi. Jaga kunci dan kredensial perangkat tetap privat.
3. **Terbitkan dan periksa transport.** Terbitkan hanya muatan dan `signed.json` pada jalur HTTPS yang tidak berubah. Keduanya harus mengembalikan byte langsung dengan HTTP 200; pembaru menolak pengalihan. Jalankan `verify` dari alat pengemasan.
4. **Kirim satu pembaruan.** Catat ID permintaan dan periksa status setelah pengakuan. Respons hilang atau tidak pasti bukan alasan mengulang operasi secara membabi buta.
5. **Konfirmasikan efeknya.** Bandingkan hash dan versi terinstal yang baru dibaca dengan rilis siap pakai. Pada pemutar aktif, periksa juga target kios, antrean, dan kemajuan pemutaran. Gambar dan suara fisik memerlukan pengamatan.

Contoh rilis agen; ganti dengan versi yang dipilih dan jalur nyata:

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.4 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.4/agent \
  --manifest-url https://updates.example/0.1.2-beta.4/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.4

# Publish the prepared agent and signed.json, then verify the published bytes.
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.4

ott --receipt --timeout 40 a1 android update \
  https://updates.example/0.1.2-beta.4/signed.json ENVELOPE_SHA256
ott --timeout 30 a1 android status
ott a1 android operation REQUEST_ID
```

`ENVELOPE_SHA256` adalah `envelope_sha256` alat pengemasan, bukan hash muatan. `accepted` berarti diterima, bukan instalasi berhasil. Penggantian diri dapat meninggalkan tanda terima operasi lama sebagai `unknown`; runtime baru, versi agen yang diharapkan, dan hash terinstal yang cocok menjadi bukti instalasi. [Referensi CLI](cli.md#independent-android-agent) menjelaskan perutean native independen dan penanganan tanda terima.

## Cara transaksi APK/helper pulih

![Transaksi APK/helper: pemeriksaan awal, simpan pasangan lama, terapkan, verifikasi, komit; kegagalan memulihkan pasangan lama dan pemulihan yang belum selesai tetap memblokir perubahan aplikasi.](images/ota-transaction.svg)

Bundel berisi tepat `app.apk` dan `ott-touch-guard`. Alat pengemasan dapat membuatnya langsung dari APK pembungkus bertanda tangan dengan `--kind bundle --apk`. Helper tertanam diekstrak sehingga kedua salinan yang dikirim cocok. Lihat [perintah bundel dan persyaratan](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Sebelum mengubah berkas, agen memerlukan pasangan terinstal yang cocok, ruang kosong cukup, pemutar OTT responsif di latar depan, partisi sistem hanya baca, dan dukungan downgrade APK yang diumumkan. Untuk kios terkunci, agen memeriksa ID kanal TV yang sama atau ID VPortal berurutan; panjang antrean yang sama saja tidak cukup. Antrean terkunci yang tidak didukung ditolak. Aplikasi OTT yang sengaja dinonaktifkan juga ditolak, mempertahankan aplikasi HOME pengguna saat ini.

Berkas pemulihan dan transaksi tertunda dipersistenkan sebelum aplikasi dihentikan. Agen menunggu aplikasi dan helper lama berhenti, mengganti helper secara atomik, mengembalikan `/system` menjadi hanya baca, menginstal APK, dan memeriksa hasil. Kegagalan penerapan atau verifikasi memicu pemulihan pasangan sebelumnya. Pemulihan startup mengembalikan berkas tanpa meluncurkan OTT atau mengambil alih HOME.

Jika pemulihan tidak selesai, `updates.recovery_failed` bernilai benar. Diagnostik dan perbaikan bertanda tangan untuk agen saja tetap tersedia; perubahan aplikasi dan watchdog pemutaran diblokir. Transaksi tersimpan tidak boleh dihapus untuk memaksa instalasi. Pemulihan binari tidak membatalkan migrasi data aplikasi, dan firmware masih dapat menolak downgrade. Lihat [batas pemulihan](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Hasil dan catatan penerimaan

![Grafik validasi: 488 tes CLI, 576 tes perkakas rilis, dan 6 tes pengemas OTA; tablet referensi menjalankan agen baru terverifikasi, tetapi penerimaan fisik APK/helper masih tertunda.](images/ota-validation.svg)

Audit implementasi 14 Oktober 2026 lulus 488 tes CLI (satu dilewati), 576 tes perkakas rilis, enam tes pengemas OTA, pemeriksaan native Go race/statis, dan build ARMv7. Jumlah ini menyatakan ukuran rangkaian, bukan cakupan atau kinerja. Tes injeksi kegagalan menguji arsip tidak valid, transaksi terputus, pemulihan sebagian, perubahan target, dan pembatasan pemulihan. Tinjauan independen juga menemukan dan memperbaiki pemeriksaan identitas antrean yang kurang serta validasi APK/helper yang berbeda antara host dan perangkat.

Sebagaimana diperiksa pada 15 Oktober 2026:

- Implementasi OTA digabung dalam [PR pengontrol #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) dan disertakan dalam [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Paket agen bertanda tangan saat ini dan sebelumnya diterbitkan pada [host pengiriman OTA](https://android-updates.ottplay.here.now/). Byte muatan dan amplopnya berhasil diunduh dan dibandingkan melalui HTTPS langsung.
- Tablet referensi diperbarui dari `0.1.1-beta.21` ke `0.1.2-beta.4` melalui kanal OTA independen. Versi agen berjalan dan SHA-256 terinstal cocok dengan rilis terpilih; runtime berubah, sedangkan ID boot OS tetap sama. OTT masih tidak memiliki proses aplikasi berjalan, dan tidak ada operasi peluncuran aplikasi atau pemilihan HOME yang dikirim.
- Status native melaporkan hash helper sentuh sistem, tidak ada bundel tertunda, dan tidak ada kegagalan pemulihan. Metadata APK tidak tersedia, sehingga pengamatan ini tidak menetapkan versi atau hash APK terinstal.
- Transaksi APK/helper terkoordinasi memiliki validasi sisi host; instalasi fisik, pemulihan downgrade, gambar, dan audio belum diterima pada tablet referensi.

Untuk setiap peluncuran berikutnya, simpan sumber/checksum rilis, hash amplop yang disiapkan, ID permintaan, status native sebelum/sesudah, dan hasil penerimaan fisik terpisah. Unduhan berhasil atau tanda terima perintah tidak dapat menggantikan pengamatan tersebut.

</div>
