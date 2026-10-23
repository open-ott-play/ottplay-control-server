# Tablet Android: kios Plex ketat dan startup

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Panduan ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Pengelola USB](android-usb-manager.md)

Tablet khusus memerlukan tiga pengaturan yang diverifikasi terpisah: antrean konten, kebijakan kios ketat OTT dan startup Android. Memasang APK atau menguji layar mati/lanjut tidak membuktikan startup setelah OS dimulai ulang.

## Identifikasi dan pemasangan

1. Catat tablet fisik, alias/UUID OTT, alias pengelola USB, nomor seri, model, Android ID dan SDK. Alias OTT dan USB dapat berbeda; jangan menyimpulkan identitas dari alias atau memakai token tablet lain.
2. Gunakan varian APK resmi yang cocok dengan SDK, verifikasi SHA-256 rilis serta identitas paket/tanda tangan, lalu pasang dengan `install -r`. Pertahankan data pemutar. Gunakan [pengelola bersama](android-usb-manager.md) jika menguasai USB; jangan jalankan server ADB pesaing atau perintah `adb` tanpa perangkat sasaran jelas.
3. Pasangkan pemutar memakai kredensial pengontrolnya sendiri dan periksa `ott TABLET`. Muat preset bernama yang dimaksud dan jalankan `ott TABLET load PRESET verify`. Preset jaringan rumah dan preset localhost mesin pengembangan tidak dapat saling menggantikan.

## Verifikasi Plex dari tablet

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Periksa ID pustaka, judul dan jumlah item. Permintaan berhasil dari komputer administrator tidak membuktikan konektivitas tablet. Saat gagal, periksa alamat tersimpan, perutean, dukungan TLS dan kredensial pada tablet. Utamakan endpoint HTTPS server yang valid. Jika firmware lama memerlukan HTTP LAN privat, batasi pada instalasi ini, catat bahwa token dan media ditransfer tanpa enkripsi, serta jangan ubah preset bersama atau nonaktifkan verifikasi sertifikat secara global. Jangan pernah terbitkan token atau URL media privat dalam diagnostik atau dokumentasi.

Pada pemutar baru yang menampilkan `categories` di `caps`, Plex membuka semua video tanpa mewajibkan pemilihan folder. Gunakan `ott TABLET categories` dan `ott TABLET category all`; lihat [kategori media](cli.md#media-categories-and-stable-video-selection). Langkah pemilihan folder di bawah berlaku untuk versi pemutar lama yang disebutkan secara eksplisit.

Mengubah alamat server Plex menghapus akar pustaka yang dipilih. Pilih penyedia **Plex**, buka **Plex settings → Folders**, centang baris pustaka yang dimaksud (bukan hanya **Browse folders**), pilih **Ok**, lalu **Save and open library**. Pada pemutar `1.1.58-beta.19`, kredensial server saja dapat membuka pemilih folder sementara `ready` tetap false; selesaikan pemilihan sebelum perintah antrean. Jangan beralih ke M3U hanya demi `ready: true`: antrean Plex dapat diputar di sana, tetapi `kiosk on --strict` dapat menyimpan kebijakan M3U menunggu tanpa media terikat. Itu tidak mengunci atau memulihkan antrean Plex.

## Mulai dan kunci antrean

```sh
ott TABLET kiosk off
ott TABLET plex shuffle 'LIBRARY NAME'
ott TABLET plex status
ott TABLET debug --engine
# Continue only after the intended queue is playing.
ott TABLET kiosk on --strict
ott TABLET plex status
ott TABLET kiosk status
ott TABLET debug --engine
```

Untuk antrean berurutan gunakan `plex library 'LIBRARY NAME'` sebagai ganti `plex shuffle`. Shuffle memilih satu urutan; kios mengulanginya. Konfirmasi item yang dimaksud, `enabled: true`, `strict: true` dan kebijakan yang terikat ke antrean media Plex yang benar. Kebijakan `waiting` tanpa saluran atau media bukan kios yang diterima. Amati sedikitnya dua sampel dengan posisi/hitungan bingkai maju dan periksa layar. Tanda terima atau label `playing` tanpa kemajuan tidak cukup. Uji perpindahan alami di akhir item; bila menguji seluruh putaran tidak praktis, catat batas itu secara eksplisit.

Kios ketat mengendalikan antarmuka pemutar. Ini bukan pendaftaran pemilik perangkat Android atau bukti bahwa Home, Recents, notifikasi dan Settings diblokir. Pertahankan launcher sistem dan jalur pemeliharaan dengan pendampingan.

## Aktifkan startup secara terpisah

Bangun dan verifikasi [OTT Home 0.2.0](../native/android-home/README.md), pertahankan kunci penandatanganan yang ada untuk pembaruan. Pasang `play.ott.home` berdampingan dengan `play.ott.foss`. Buka `play.ott.home/.StartupActivity` dan aktifkan **Start OTT after reboot**. Preferensi ini mati secara default. Tidak mengubah biner resmi pemutar, tanda tangan OTA, data profil atau penetapan tombol Home.

Jika firmware menyediakan pemilih Home, boleh pilih juga **OTT Home → Always**. Jika pemilih tidak tersedia (termasuk firmware Fire lama yang diuji), izin boot menyediakan startup mandiri. Instalasi yang sebelumnya memakai Home default mempertahankan perilakunya. Nonaktifkan kedua mekanisme untuk rollback penuh.

`recover_on_boot` pengelola USB bersama adalah mekanisme ketiga yang independen: perlu NAS dan kabel USB, hanya menangani boot baru dan tidak terus memulai ulang aplikasi macet. Bukan pengganti startup mandiri. Dalam pengujian startup mandiri, matikan kebijakan pemulihan agar tidak menutupi kegagalan peluncuran aplikasi pendamping. Hormati layar kunci aman; buka secara normal jika diminta.

## Penerimaan dan penyerahan

Simpan bukti secara privat, tanpa kredensial dalam tanda terima publik:

- Versi APK, hash, pemetaan identitas fisik/OTT/USB dan preset pilihan.
- Pustaka Plex, jumlah/urutan item, kebijakan ketat dan pemutaran awal yang maju.
- ID boot sebelum/sesudah OS benar-benar dimulai ulang; sesudahnya `sys.boot_completed=1`.
- Tanpa peluncuran pemutar manual atau pemulihan USB di antara reboot dan pengamatan.
- Aktivitas OTT di latar depan, koneksi ulang pengontrol, antrean/kebijakan ketat pulih dan pemutaran maju. Hasil `requested` penerima boot hanya berarti pengiriman.
- Uji tombol daya terpisah: layar mati, audio berhenti, buka kunci biasa dan lanjutkan.
- Batas tepat: perlu buka kunci aman, pemulihan dibantu USB, jalan keluar fisik belum diuji, kegagalan stream atau kehabisan memori. Jangan sebut kasus itu diterima sepenuhnya.

Sebelum menyerahkan tablet berikut, ulangi daftar periksa ini alih-alih menyalin keberhasilan perangkat sebelumnya. Fire lama mungkin perlu `hls.js` untuk M3U karena HLS bawaan vendor dapat macet; ini tidak membuktikan kestabilan Plex.

## Pemeliharaan dan rollback

```sh
ott TABLET kiosk off
```

Buka **OTT Startup**, nonaktifkan **Start OTT after reboot**, dan pulihkan pilihan Home semula jika berubah. Jika perlu, hapus hanya `play.ott.home` melalui transportasi USB terverifikasi; pertahankan `play.ott.foss` dan datanya. Menonaktifkan startup tidak mengubah antrean atau otomatis menonaktifkan kios.
