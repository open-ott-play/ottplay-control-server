# Image transportasi USB Android

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Direktori ini berisi templat penerapan dan [`Dockerfile`](Dockerfile) untuk kontainer transportasi pengelola bersama. Pengelola Python berjalan pada host USB. Image menyediakan ADB, Python dan alat untuk adaptor pemeliharaan atomik; tidak memuat registri perangkat kedua atau daemon pemulihan. Lihat [panduan operator](../../docs/android-usb-manager.md) untuk penyiapan, perintah, migrasi, batas pemulihan dan pembatalan perubahan.

Tanpa checkout Git, unduh `ottplay-control-server-deployment.tar.gz` dan `release-manifest.json` dari [rilis](https://github.com/open-ott-play/ottplay-control-server/releases) yang sama. Verifikasi digest arsip dan ekstrak ke direktori baru sesuai [Mendapatkan paket penerapan](../../docs/android-usb-manager.md#get-the-deployment-package). Paket memuat resep ini dan enam skrip host/jarak jauh di `tools/`; pertahankan struktur direktorinya. Paket lama, termasuk `v0.1.5-beta.6`, tidak memiliki skrip tersebut dan memerlukan arsip sumber yang sesuai. Paket tidak menyertakan image transportasi USB yang sudah dibangun. Aset OCI rilis control-server memiliki tujuan berbeda.

## Asal sumber dan artefak

Basisnya adalah `python:3.12.14-slim-trixie`, dipatok ke `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. Basis, instruksi APT dan pemasangan `websocket-client==1.9.0` yang dikunci dengan hash telah ditinjau dalam [PR 128 pada revisi b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Teks instruksi dipertahankan agar cache build yang tersedia dapat digunakan kembali. Paket WebSocket opsional mendukung klien pemeliharaan umum yang ada; adaptor CDP yang dimigrasikan juga dapat hanya memakai pustaka standar Python.

Resep ini menghapus paket `pip` terpasang setelah memasang wheel terkunci, mengecualikan kode pengelola dan pemulihan PR 128, menjalankan ADB di latar depan dan menonaktifkan pemeriksaan kesehatan turunan. Menghapus pemasang runtime mengurangi permukaan serangan yang tersedia; bukan bukti pemindaian kerentanan tanpa temuan.

Artefak PR 128 yang ditinjau adalah `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Artefak itu memuat titik masuk berbeda dan pemeriksaan kesehatan heartbeat yang tidak kompatibel. Itu artefak referensi, bukan hasil build Dockerfile ini. Jangan mengaitkan ID image, asal sumber atau hasil pemindaiannya dengan resep ini.

Repositori APT tidak dipatok ke snapshot. Karena itu, build ulang bersih pada masa mendatang dapat memilih paket OS lebih baru meski digest basis dan hash wheel Python sama. Saat mengualifikasi build, catat revisi sumber tepat, builder/platform, ID image hasil, versi paket dan pemindaian. Konfigurasi produksi harus menggunakan ID image tak berubah yang dikualifikasi itu atau digest-nya.

## Membangun tanpa mengganggu host USB yang sibuk

Utamakan build dan pemindaian pada builder terpisah. Host tujuan hanya memerlukan image yang dikualifikasi, bukan unduhan paket atau perangkat build.

Saat menggunakan kembali builder lokal yang telah disiapkan, pastikan image basis tepat tersedia dan lapisan pemasangan APT serta wheel tersimpan di cache. Memuat hanya image akhir PR 128 tidak membuktikan kedua kondisi tersebut. Dari akar repositori atau direktori penerapan hasil ekstraksi, percobaan yang sengaja offline adalah:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` berlaku pada langkah build; bukan jaminan umum bahwa builder tidak pernah menghubungi registri saat mencari basis. Pastikan resolusi basis lokal builder sebelum memakai prosedur ini pada host terbatas. Jika lapisan APT atau wheel tidak tersedia, hentikan percobaan alih-alih diam-diam mengizinkan build ulang lewat jaringan pada host USB.

BuildKit menyertakan mode jaringan langkah build dalam kunci cache. Cache yang dibuat dengan mode jaringan bawaan karena itu mungkin tidak cocok pada percobaan offline di atas. Gunakan kembali hanya setelah memeriksa basis tepat dan rantai instruksinya; berhenti jika salah satu langkah pemasangan paket dijalankan alih-alih melaporkan `CACHED`. Penghapusan `pip` terakhir secara eksplisit memakai `RUN --network=none`; tidak memerlukan jaringan meski langkah cache sebelumnya memakai mode aslinya.

Sebelum mengubah konfigurasi produksi, periksa versi Python dan ADB, pindai image hasil yang tepat, verifikasi transfer berkas biner dan staging APK, lalu lakukan pemeriksaan penerimaan identitas perangkat, otorisasi kunci, tidur dan boot dalam panduan. Komunikasi ADB 34 dengan Android lama harus diuji; nomor versi saja tidak membuktikan kompatibilitas KitKat atau Fire. Simpan image saat ini dan cadangan penerapan privat sampai pembatalan perubahan tidak diperlukan lagi.

## Anggaran runtime

Pengelola host, bukan Dockerfile ini, menetapkan memori 256 MiB, memori plus swap 256 MiB dan CPU shares 128, tanpa kuota keras CPU atau PID. Pengelola memberlakukan `--no-healthcheck`, `--network none`, sistem berkas akar hanya-baca, penghapusan capabilities dan pemetaan simpul USB tepat. `/tmp` adalah tmpfs terbatas yang memakai anggaran memori sama. Adaptor staging menulis per 1 MiB dan membatasi satu payload hingga 128 MiB; operasi berbagi kunci pengelola.

Docker mendokumentasikan perbedaan antara [kontrol memori, swap dan CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) serta [opsi `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
