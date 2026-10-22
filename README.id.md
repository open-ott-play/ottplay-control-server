# OTT-play Control Server

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15562/badge)](https://www.bestpractices.dev/projects/15562)

[![Fuzzing: RC only](https://img.shields.io/badge/Fuzzing-RC%20only-blue)](docs/fuzzing.md)

[Dokumentasi / Wiki](docs/wiki/Home.id.md)

Server kecil yang dapat dihosting sendiri untuk mengirim perintah jarak jauh ke pemutar OTT-play. Pemutar mengirim permintaan HTTP keluar, sehingga TV tidak perlu membuka porta penerima koneksi. Setiap pemutar memiliki antrean dan token akses sendiri; token administrator terpisah digunakan untuk mengirim perintah.

Fitur [kontrol andal](docs/reliable-control.md) opsional menambahkan kredensial integrasi dengan cakupan terbatas, penyimpanan hasil perintah yang persisten namun dibatasi, penerapan konfigurasi Local/Home yang dapat dilanjutkan dengan verifikasi pengaturan secara tepat, serta serah terima eksplisit antara pemutaran langsung dan arsip. Fitur ini memerlukan revisi pengontrol dan CLI yang memuat panduan ini. Verifikasi tepat dan serah terima juga memerlukan pemutar terbaru. Revisi instalasi lama yang dipatok di bawah tidak mencakup fitur tersebut. [Diagram arsitektur](docs/assets/reliable-control.svg) memperlihatkan tempat pengaturan, kredensial, dan status operasi disimpan.

Server berjalan sebagai satu berkas eksekusi Go portabel, dengan versi pustaka DNS yang dipatok untuk penemuan layanan opsional. Paket rilis tersedia untuk Linux amd64, arm64 dan ARMv7; macOS Intel dan Apple Silicon; serta Windows amd64 dan arm64. Citra kontainer mendukung Linux amd64 dan arm64. Kubernetes dan k3s menggunakan chart Helm yang disertakan.

## Memulai server

Unduh arsip yang sesuai dari [Rilis](https://github.com/open-ott-play/ottplay-control-server/releases), verifikasi dengan manifes rilis, lalu ekstrak. Di Windows, gunakan `ottplay-control-server.exe` pada perintah di bawah.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` membuat kredensial acak dan berbeda dalam berkas baru yang hanya dapat diakses pemiliknya pada Unix. Perintah ini menolak menimpa berkas atau tautan simbolis yang sudah ada dan tidak pernah mencetak kredensial. Jaga berkas tetap privat; di Windows, batasi ACL-nya pada akun yang menjalankan server. `config.example.json` menjelaskan bidang-bidangnya dan sengaja memuat token pengganti yang tidak valid.

Alamat bawaan adalah `0.0.0.0:8081` (semua antarmuka IPv4, termasuk LAN). Hubungkan TV memakai alamat LAN dan porta server. Konfigurasi yang sudah ada mempertahankan nilai `listen` eksplisitnya; ubah menjadi `0.0.0.0:8081` atau gunakan `--listen 0.0.0.0:8081` untuk membuka instalasi yang sebelumnya hanya menerima loopback. Tambahkan **origin halaman yang persis** dari pemutar ke `allowed_origins`, misalnya `http://192.168.1.20:8443`. Skema, nama host, dan porta harus cocok; jangan tambahkan jalur. Daftar origin kosong hanya mengizinkan klien nonbrowser. Perubahan konfigurasi berlaku setelah mulai ulang. Kredensial perangkat dan administrator tetap diperlukan.

HTTP didukung untuk peramban TV lama pada LAN tepercaya. Gunakan HTTPS jika koneksi melewati jaringan tidak tepercaya: `serve --config config.json --tls-cert server.crt --tls-key server.key`, atau akhiri TLS pada proksi balik. Halaman pemutar HTTPS tidak dapat terhubung ke server HTTP biasa di peramban. Aplikasi native juga tetap mengikuti kebijakan transportasi sistem operasinya; pengaturan ini tidak melonggarkannya.

HTTPS langsung memvalidasi rantai sertifikat sebelum membuka listener: kunci RSA minimal 2048 bit, kunci ECDSA minimal 224 bit, atau Ed25519. Ganti sertifikat dengan kunci terlalu kecil sebelum meningkatkan versi. Lihat [kebijakan sertifikat TLS dan cakupannya](docs/security-design.md#direct-https-certificate-policy).

## Menghubungkan pemutar

Gunakan rilis pemutar yang memiliki fitur koneksi server perintah. Buka **Pengaturan → Kontrol jarak jauh** dan masukkan:

1. **Alamat server:** IP atau nama host server, dengan porta opsional, atau alamat lengkap `http://` / `https://`. Host tanpa skema memakai HTTP porta 8081 secara bawaan.
2. **Kode akses:** `token` dari entri pemutar tersebut dalam `devices`, jangan pernah memakai `admin_token`.
3. **Hubungkan:** aktifkan polling dan periksa status pada dialog yang sama.

Pemutar menyimpan koneksi ini hanya dalam instalasi saat ini dan mengecualikannya dari cadangan pengaturan/transfer awan. Listener HTTP lokal terpisah tidak diperlukan. Peramban web membutuhkan origin CORS yang diizinkan; halaman TV terpaket dengan `Origin: null` dapat memakai pengaturan eksplisit `allow_null_origin` untuk rute perangkat/bootstrap yang ditetapkan. Ini tidak mengaktifkan rute administrator atau operator diagnostik.

## Remote di terminal

[Panduan Android OTA bergambar](docs/android-ota.md) membedakan pembaruan Capacitor biasa melalui Wi-Fi (`ott DEVICE update --variant api22|full`, beta terbaru secara bawaan; `--version` memilih rilis) dari agen terpisah dengan akses root. Panduan menjelaskan pembaruan agen dan APK/helper yang ditandatangani, pemulihan, dan verifikasi hasil instalasi.

[Panduan Android ADB](docs/android-adb.md) membahas akses USB melalui workstation atau Synology, kontrol layar dan keyboard, tangkapan layar, log, instalasi APK, dan mulai ulang normal, termasuk batasan tablet lama.

[Penemuan dan pemasangan DNS-SD](docs/discovery.md) opsional memungkinkan pemutar menemukan pengontrol melalui domain jaringannya dan menerima token perangkat individual setelah `ott approve NAME CODE`. DNS hanya berisi metadata layanan. Koneksi manual yang ada tetap bekerja tanpa mengaktifkan penemuan.

Gunakan CLI Python untuk alias pemutar, volume langsung, pencarian saluran, EPG saat ini, pemilihan penyedia, dan konfigurasi penyedia yang didukung: [panduan CLI](docs/cli.md). Server dan pemutar harus sama-sama menyertakan ekstensi permintaan/respons.

CLI Python dipasang dari salinan kode sumber, terpisah dari arsip server native. [Langkah instalasi dan konfigurasi](docs/cli.md#installation-and-connection) meliputi macOS/Linux dan Windows, penyiapan Python/PATH, dan modul CLI yang diperlukan. Langkah ini mematok commit sumber pengembangan 0.1.1 yang telah ditinjau, dengan workbench dan verifikasi offline; CLI stabil v0.1.0 serta arsip server native tidak menyertakan perintah tersebut. [Perbarui atau kembalikan versi CLI](docs/cli.md#update-select-or-roll-back-the-cli) tanpa mengganti salinan kerja, peluncur, atau konfigurasi privat yang ada. Peluncur lokal dapat memakai [resolver playlist](docs/cli.md#local-playlist-resolver) hanya-baca untuk mencari saluran, acara saat ini, dan arsip yang dapat diputar tanpa pengontrol. Setelah terpasang dan dikonfigurasi:

```sh
ott --help                 # all CLI command families
ott devices                # controller registrations and last contact
ott a1                     # this player's status and available controls
ott a1 prev                # previous channel in the active category (previous also works)
ott a1 next                # next channel, wrapping at the end of the category
ott a1 channels "РЕН"       # list channel-name matches without playing
ott a1 "РЕН ТВ HD"          # search and play a matching channel
ott a1 volume 35            # vol/v are equivalent command aliases
ott a1 aspect               # current Fit/Fill mode and saved-setting readback
ott a1 aspect fill          # fill the screen proportionally, cropping the edges
ott a1 aspect "Fit to screen" # full mode name; preserve the whole picture
ott a1 restart             # reload the player
ott a1 restart stream      # restart only the current stream
ott a1 standby             # stop playback and enter player standby; stay connected
ott a1 go                  # leave standby and start configured playback (start/wake also work)
ott a1 screenshot          # save a PNG; browser builds need a selected capture source
ott a1 shot -o screen.png  # short alias; never overwrites an existing file
ott a1 test run media-progress --report ./a1-progress
ott report verify ./a1-progress --json  # re-evaluate saved evidence offline
```

`a1` adalah contoh alias pemutar terdaftar, bukan nama host komputer. Lihat [pendaftaran dan pemasangan](docs/cli.md#register-and-connect-a-player), [semua perintah beserta contoh](docs/cli.md#commands), [pemutusan dan pencabutan akses](docs/cli.md#disconnect-deregister-or-revoke-a-player), serta [pemecahan masalah](docs/cli.md#troubleshooting).

Untuk pemutar yang selalu aktif, gunakan [standby dan go](docs/cli.md#stop-playback-while-keeping-the-player-online) untuk menghentikan pemutaran dan mengaktifkannya nanti tanpa keluar dari aplikasi. `stop` tetap menjadi alias `standby`; `start` dan `wake` tetap menjadi alias `go`. Kontrol jarak jauh harus tetap terhubung, jadi siaga tidak menjanjikan nol lalu lintas jaringan. `ott a1` menampilkan operasi yang diumumkan pemutar. Perintah yang diterima bukan konfirmasi bahwa penghentian atau awal pemutaran telah selesai.

[Perintah rasio aspek](docs/cli.md#aspect-ratio-fit-and-fill) memerlukan CLI dan pengontrol terbaru serta pemutar yang mengumumkan kemampuan `aspect`. Perubahan diterapkan setelah konfirmasi; baca `aspect` lagi untuk memastikan mode dan apakah telah disimpan. Fill memotong secara proporsional, bukan meregangkan gambar.

[Tangkapan layar](docs/cli.md#remote-screenshots) jarak jauh memerlukan CLI dan pengontrol terbaru serta pemutar dengan adaptor penangkapan yang didukung. Mengaktifkan koneksi Kontrol jarak jauh pemutar memberikan izin tangkapan layar dan diagnostik kepada pengontrol tersebut; tangkapan native tidak membutuhkan sakelar izin terpisah. Peramban tetap memerlukan pemilih sumber lokalnya. Gambar dapat berisi pengaturan, layar PIN, dan kredensial terlihat, jadi hubungkan hanya ke pengontrol tepercaya. `ott a1` melaporkan ketersediaan dan pemilihan sumber yang diperlukan. Tangkapan layar terpisah dari pengumpulan telemetri terstruktur.

Untuk mengacak semua video VPortal yang cocok dengan judul dan memutarnya berulang:

```sh
ott l vpr "wedding"
```

`vpr` mengacak seluruh pilihan sekali, lalu mengulang antrean itu. Jalankan lagi untuk pengacakan baru. `vp` memutar hasil sesuai urutan katalog; `vp --list` menampilkannya tanpa mengubah pemutaran. Lihat [perintah VPortal](docs/cli.md#commands).

Untuk urutan Plex eksplisit yang memakai konfigurasi Plex tersimpan di pemutar:

```sh
ott l plex preview 78777 78776 78775  # check availability without starting playback
ott l plex play 78777 78776 78775     # starts the first item from zero, in this order
ott l plex queue                     # current item and preparation/playback state
ott l plex next                      # advance once; no wrap at the last item
ott l plex stop                      # stop and clear this queue
ott l plex libraries                 # list libraries (requires plex_library support)
ott l plex shuffle "Tom and Jerry"   # shuffle one complete library, up to 500 items
ott l plex library --preview id:16    # read-only check of an explicit library
```

CLI menunggu hingga 45 detik secara bawaan. Menulis `ott -t 45 l plex shuffle "Tom and Jerry"` menyatakan batas waktu respons yang sama secara eksplisit; ini tidak membatasi durasi video diputar. Mengumpulkan pustaka Plex besar dapat memerlukan waktu lebih lama daripada batas singkat 10–15 detik. Letakkan `-t SECONDS` (atau `--timeout SECONDS`) sebelum alias pemutar. Lihat [batas waktu pustaka Plex](docs/cli.md#plex-libraries-by-name) untuk contoh pratinjau dan tindakan jika permintaan pemutaran kehabisan waktu.

Antrean Plex memerlukan CLI, pengontrol, dan pemutar yang mengimplementasikan `plex_queue`. `plex play --shuffle ID...` mengacak ID yang diberikan sekali. Antrean berhenti setelah item terakhir kecuali kebijakan kios yang ada mengulanginya. Selama antrean ada, `next` dan `prev` biasa mengarah padanya; `plex stop` menghapusnya. Pratinjau tidak pernah mengganti penyedia atau menggantikan pemutaran saat ini. Lihat [perintah antrean Plex](docs/cli.md#ordered-plex-queues) untuk batasan dan kesiapan. Pencarian pustaka juga memerlukan pemutar dan pengontrol terbaru yang mengumumkan `plex_library`; fitur ini memakai koneksi Plex tersimpan di pemutar dan menolak pilihan ambigu atau tidak lengkap. Lihat [pustaka Plex berdasarkan nama](docs/cli.md#plex-libraries-by-name).

### Menyimpan profil video lokal

Dengan pemutar dan pengontrol terkini, berkas/folder lokal memakai penyedia `offline`:

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Hubungkan berkas/folder pada pemutar terlebih dahulu. Simpan mencatat konfigurasi dan video terpilih tanpa memulai pemutaran. Ekspor hanya memindahkan deskripsi; media dan izin akses OS/peramban tetap di perangkat. Instalasi lain memerlukan pengaitan lokal, dan memuat tidak berarti pemutaran telah dikonfirmasi. Lihat [profil video lokal](docs/cli.md#local-videos-profiles) untuk penyiapan, kemampuan, batas format portabel, dan pemecahan masalah.

### Menyimpan item saat ini lintas profil

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Favorit bertahan pada pemutar tujuan melalui mulai ulang normal. Memilih favorit memuat penyedia/profil tersimpan dan menyelesaikan tautan baru; sumber yang berubah atau item hilang menghasilkan kegagalan tanpa mengganti dengan saluran/film lain. CLI, pengontrol, dan pemutar harus mendukung `favorites`; `ott l` menampilkan operasi yang tersedia. Lihat [favorit lintas profil](docs/cli.md#favorites-across-profiles) untuk alias, batasan, makna konfirmasi, dan pemecahan masalah.

### Memuat konfigurasi tersimpan

Simpan konfigurasi bernama di bagian privat `presets` pada `~/.config/ottplay-control/cli.json`. Setiap konfigurasi memuat profil M3U bernomor (playlist dan jam arsip), kredensial Plex, dan profil M3U yang dipilih setelah pemuatan selesai. Lihat [contoh konfigurasi](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI memvalidasi seluruh konfigurasi dahulu, lalu menunggu konfirmasi pemutar sebelum mengirim perubahan berikutnya. Ia menangani pemuatan penyedia, menyimpan Plex dan slot M3U, serta memilih profil M3U yang dikonfigurasi. Kesalahan menghentikan urutan dan melaporkan langkah yang dikonfirmasi; penulisan dengan hasil tidak pasti tidak pernah diulang otomatis. Layanan profil tambahan atau mulai ulang pengontrol tidak diperlukan. Jaga konfigurasi tetap privat: di dalamnya ada kredensial penyedia.

## Mengirim perintah

Contoh ini membaca token administrator dari konfigurasi lokal privat tanpa memasukkannya ke URL atau argumen baris perintah:

```python
import json
import urllib.request

with open("config.json", encoding="utf-8") as stream:
    config = json.load(stream)
request = urllib.request.Request(
    "http://127.0.0.1:8081/api/webhook/commands?device_id=living-room",
    data=json.dumps({"command": "set_volume", "volume": 35}).encode(),
    headers={
        "Authorization": "Bearer " + config["admin_token"],
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.status)
```

Perintah lain memilih saluran berdasarkan nomor atau nama, memilih saluran acak, mengganti penyedia, menampilkan pesan, mengubah playlist M3U jika didukung, atau keluar dari pemutar. Ketersediaan bergantung pada platform pemutar dan penyedia saat ini. Lihat [kontrak API](docs/api.md).

Pada pemutar yang mendukung penyedia VPortal mandiri, `ott a1 provider
vportal` memilih 15 profil khususnya. Gunakan `profile N vportal LINK`, `profile N
name NAME`, atau berkas privat `profile-config N FILE` berisi `name` dan `vportal`, kemudian pilih slot yang dikonfigurasi dengan `profile N`. Profil M3U yang ada tetap terpisah. `profiles` melaporkan metadata tanpa membocorkan tautan akun atau kunci.

Setelah memulai film atau antrean episode VPortal, `ott a1 kiosk on` mengunci pilihan itu. Pemutar mempertahankan antrean dan posisinya melalui muat ulang dan menyelesaikan ulang URL media untuk pemulihan. Gunakan `kiosk off` untuk memilih judul lain atau mengubah profil, lalu `kiosk on` untuk menguncinya. Tanda terima kios menampilkan judul dan jumlah episode; permintaan penyedia dan URL aliran tetap di pemutar.

Gunakan `ott a1 kiosk on --strict` untuk pemutaran bagi anak: ketukan lokal menampilkan bilah bawah video. Pemutar core terbaru memungkinkan mengetuk atau menyeret garis waktu untuk mencari posisi dalam episode VPortal/Plex saat ini; kontrol lokal lainnya tetap terkunci. Pemutar lama, kios saluran, dan pembungkus yang mencegat gestur garis waktu mempertahankan bilah bawah hanya-baca. Kunci yang ada tetap mempertahankan targetnya. Lepaskan melalui pengontrol dengan `ott a1 kiosk off`. Pada situs Android, perlindungan keluar melalui sistem memerlukan penyematan aplikasi terpisah dengan PIN; lihat [mode kios](docs/cli.md#kiosk-mode).

## Diagnostik jarak jauh

Mulailah dari [panduan cepat workbench hanya-baca](docs/workbench.md#quick-start) untuk `doctor`, `inspect` UI/media, pencarian tanda terima operasi, paket bukti, `health`, `media-progress` web/native, dan `report verify` offline. Panduan ini memakai konfigurasi CLI administrator serta alias pemutar yang ada. Jalur native diperuntukkan bagi agen pemeliharaan Android yang disiapkan terpisah; pemutar Tauri memakai inspeksi web. Laporan membedakan kemajuan dekoder dari verifikasi fisik layar/suara. Lihat [pemecahan masalah workbench](docs/workbench.md#troubleshooting) untuk kemampuan yang hilang, jalur tidak tersedia, dan bukti tidak lengkap.

[Diagnostik protokol 2](docs/remote-diagnostics.md) opsional menyediakan sesi per-runtime yang diaktifkan secara eksplisit, dengan kredensial operator terpisah bercakupan terbatas dan telemetri terstruktur yang dibatasi. Koneksi pemutar yang aktif memberi otorisasi lokal tanpa permintaan kepercayaan baru; setiap penangkapan tetap memiliki masa sesi terbatas. Polling kontrol diagnostik tidak bergantung pada antrean perintah yang ada.

[Panduan CLI diagnostik dan MCP](docs/diagnostics-cli.md) membahas instalasi, kredensial operator, koneksi, setiap perintah/alat diagnostik, contoh penangkapan dan perbaikan, pencabutan runtime, dan pemecahan masalah. `ott diagnostics --help` mencantumkan keluarga perintah terpisah ini; alias pemutar biasa dan kredensial administrator tidak memberi akses diagnostik.

## Paket penerapan

- [Tablet Android: kios Plex ketat dan startup](docs/android-kiosk-setup.md)
- [Pengelolaan USB Android bersama dan pemulihan boot](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS, dan Windows](docs/deployment.md)
- [Konfigurasi, semantik pengiriman, dan batasan](docs/api.md)
- [Proses rilis dan validasi](docs/development.md)

Antrean dibatasi, kedaluwarsa setelah 60 detik secara bawaan, dan berada di memori. Memulai ulang server membuang perintah tertunda. Jalankan satu replika; replika independen tidak berbagi antrean. Pemutar mengonfirmasi upaya pengiriman, bukan pemutaran atau volume perangkat keras yang terverifikasi. Pengulangan jaringan dideduplikasi dalam satu sesi pemutar yang berjalan, bukan lintas mulai ulang aplikasi.

Server ini mengikuti API perintah contoh opsional `local_proxy.py` di [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), dengan kredensial administrator/perangkat terpisah dan pengiriman berkonfirmasi. Proksi lama tetap menjadi komponen terpisah. Proyek ini tidak memproksi media atau mengubah jaringan HLS, EPG, maupun penyedia pemutar.

## Lisensi

MIT. Atribusi alat rilis yang disertakan tercatat dalam `NOTICE` dan `LICENSES/`.

## Pemeliharaan proyek

Lihat [persyaratan kontribusi dan pengujian](CONTRIBUTING.md), [kebijakan pelaporan keamanan](SECURITY.md), [desain keamanan](docs/security-design.md), dan [bukti OpenSSF serta kriteria tersisa](docs/openssf-evidence.md).

Antrean Plex menerima hingga 500 ID pada pemutar saat ini. Gunakan `ott f10 plex play --shuffle ID...` untuk mengacak sekali, lalu `ott f10 kiosk on --strict` saat penyedia Plex dipilih untuk mengulang dan mengunci antrean. `ott f10 kiosk off` melepas kunci. Pemutar lama mempertahankan batas antrean yang diumumkannya.

## Fuzzing kandidat rilis

Kandidat rilis publik menjalankan fuzzing native terbatas pada batas masukan yang dijelaskan dalam [panduan fuzzing](docs/fuzzing.md). Kampanye mutasi tidak dijalankan pada PR, beta, atau build malam. Panduan mencakup ruang lingkup, anggaran, bukti, dan reproduksi regresi.

## Komunitas dan bukti keamanan

Lihat [kontribusi](CONTRIBUTING.md), [tata kelola](GOVERNANCE.md), [Kode Etik](CODE_OF_CONDUCT.md), [peta jalan](ROADMAP.md), dan [bukti OpenSSF](docs/openssf-evidence.md). Indeks bukti membedakan profil keamanan yang didukung, hasil pengujian aktual, dan persyaratan Silver yang tersisa.
