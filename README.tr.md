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

[Belgeler / Wiki](docs/wiki/Home.tr.md)

OTT-play oynatıcılarına uzaktan komut ileten, kendiniz barındırabileceğiniz küçük bir sunucu. Oynatıcılar dışarıya HTTP istekleri gönderdiği için TV'nin dinleyen bir port açması gerekmez. Her oynatıcının kendi kuyruğu ve erişim belirteci vardır; komutları ayrı bir yönetici belirteci gönderir.

İsteğe bağlı [güvenilir kontrol](docs/reliable-control.md), sınırlı yetkili entegrasyon kimlik bilgileri, kalıcı fakat sınırlı komut sonucu saklama, ayarların tam doğrulanmasıyla sürdürülebilir Local/Home uygulaması ve canlı/arşiv oynatımı arasında açık aktarım ekler. Bu özellikler, bu kılavuzu içeren denetleyici ve CLI revizyonlarını gerektirir. Tam ayar doğrulaması ve aktarım için oynatıcı da güncel olmalıdır. Aşağıda sabitlenen eski kurulum revizyonu bu özellikleri içermez. [Mimari şema](docs/assets/reliable-control.svg), ayarların, kimlik bilgilerinin ve işlem durumunun nerede kaldığını gösterir.

Sunucu, isteğe bağlı hizmet keşfi için sürümü sabitlenmiş bir DNS kitaplığıyla tek bir taşınabilir Go yürütülebilir dosyası olarak çalışır. Sürüm paketleri Linux amd64, arm64 ve ARMv7; macOS Intel ve Apple Silicon; Windows amd64 ve arm64 içindir. Konteyner imajları Linux amd64 ve arm64'ü destekler. Kubernetes ve k3s, dahil edilen Helm chart'ını kullanır.

## Sunucuyu başlatma

[Sürümlerden](https://github.com/open-ott-play/ottplay-control-server/releases) uygun arşivi indirin, sürüm manifestosuna göre doğrulayın ve açın. Windows'ta aşağıdaki komutlarda `ottplay-control-server.exe` kullanın.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init`, Unix'te yalnızca sahibinin erişebildiği yeni bir dosyada rastgele ve birbirinden farklı kimlik bilgileri oluşturur. Mevcut dosya veya sembolik bağlantının üzerine yazmayı reddeder ve kimlik bilgilerini asla yazdırmaz. Dosyayı gizli tutun; Windows'ta ACL'sini sunucuyu çalıştıran hesapla sınırlayın. `config.example.json` alanları belgeler ve bilerek geçersiz yer tutucu belirteçler içerir.

Varsayılan adres `0.0.0.0:8081`'dir (LAN dahil tüm IPv4 arayüzleri). TV'leri sunucunun LAN adresi ve portuyla bağlayın. Mevcut yapılandırmalar açıkça belirtilmiş `listen` değerini korur; daha önce yalnızca loopback üzerinden erişilen kurulumu açmak için bunu `0.0.0.0:8081` yapın veya `--listen 0.0.0.0:8081` kullanın. Oynatıcı sayfasının **tam origin değerini** `allowed_origins` listesine ekleyin; örneğin `http://192.168.1.20:8443`. Şema, ana makine adı ve port eşleşmelidir; yol eklemeyin. Boş origin listesi yalnızca tarayıcı dışı istemcilere izin verir. Yapılandırma değişiklikleri yeniden başlatınca geçerli olur. Cihaz ve yönetici kimlik bilgileri yine gereklidir.

Güvenilir LAN'daki eski TV tarayıcıları için HTTP desteklenir. Bağlantı güvenilmeyen bir ağdan geçiyorsa HTTPS kullanın: `serve --config config.json --tls-cert server.crt --tls-key server.key` veya TLS'yi ters vekilde sonlandırın. HTTPS oynatıcı sayfaları tarayıcıda düz HTTP sunucusuna bağlanamaz. Yerel uygulamalar da işletim sistemlerinin taşıma politikasını korur; bu ayar politikayı gevşetmez.

Doğrudan HTTPS, dinleyiciyi açmadan önce verilen sertifika zincirini doğrular: RSA anahtarları en az 2048 bit, ECDSA anahtarları en az 224 bit olmalıdır; Ed25519 da desteklenir. Yükseltmeden önce yetersiz anahtarlı sertifikayı değiştirin. [TLS sertifika politikası ve kapsamına](docs/security-design.md#direct-https-certificate-policy) bakın.

## Oynatıcıyı bağlama

Komut sunucusu bağlantısını içeren bir oynatıcı sürümü kullanın. **Ayarlar → Uzaktan kontrol** bölümünü açıp şunları girin:

1. **Sunucu adresi:** sunucunun IP'si veya ana makine adı, isteğe bağlı portla birlikte; ya da tam `http://` / `https://` adresi. Şemasız ana makine varsayılan olarak HTTP portu 8081'i kullanır.
2. **Erişim kodu:** `devices` içindeki oynatıcı kaydının `token` değeri; asla `admin_token` değil.
3. **Bağlan:** sorgulamayı etkinleştirin ve aynı penceredeki durumu kontrol edin.

Oynatıcı bu bağlantıyı yalnızca mevcut kurulumda saklar; ayar yedekleri ve bulut aktarımına dahil etmez. Ayrı yerel HTTP dinleyicisine gerek yoktur. Web tarayıcıları izin verilen bir CORS origin gerektirir; `Origin: null` kullanan paketlenmiş TV sayfaları, belirlenmiş cihaz/bootstrap yolları için açık `allow_null_origin` ayarını kullanabilir. Bu, yönetici veya tanılama operatörü yollarını açmaz.

## Terminalden kumanda

[Resimli Android OTA kılavuzu](docs/android-ota.md), olağan Capacitor Wi-Fi güncellemelerini (`ott DEVICE update --variant api22|full`, varsayılan en yeni beta; `--version` bir sürüm seçer) ayrı root yetkili ajandan ayırır. İmzalı ajan ve APK/yardımcı güncellemelerini, kurtarmayı ve kurulum sonucunun doğrulanmasını açıklar.

[Android ADB kılavuzu](docs/android-adb.md), iş istasyonu veya Synology üzerinden USB erişimini, ekran ve klavye kontrolünü, ekran görüntülerini, günlükleri, APK kurulumunu ve normal yeniden başlatmayı, eski tabletlerin sınırlamalarıyla birlikte ele alır.

İsteğe bağlı [DNS-SD keşfi ve eşleştirme](docs/discovery.md), oynatıcıların denetleyiciyi ağ alanları üzerinden bulmasını ve `ott approve NAME CODE` sonrasında bireysel cihaz belirteci almasını sağlar. DNS yalnızca hizmet meta verilerini içerir. Mevcut elle yapılandırılmış bağlantılar, keşif açılmadan çalışmaya devam eder.

Oynatıcı takma adları, canlı ses düzeyi, kanal arama, güncel EPG, sağlayıcı seçimi ve desteklenen sağlayıcı yapılandırması için Python CLI kullanın: [CLI kılavuzu](docs/cli.md). Sunucu ve oynatıcı, istek/yanıt uzantısını içermelidir.

Python CLI, yerel sunucu arşivinden ayrı olarak kaynak çalışma kopyasından kurulur. [Kurulum ve yapılandırma adımları](docs/cli.md#installation-and-connection), macOS/Linux ve Windows'u, Python/PATH kurulumunu ve gerekli CLI modüllerini kapsar. Workbench ve çevrimdışı doğrulama içeren incelenmiş bir 0.1.1 geliştirme commit'ini sabitler; kararlı CLI v0.1.0 ve yerel sunucu arşivleri bu komutları içermez. Mevcut çalışma kopyasını, başlatıcıyı veya özel yapılandırmayı değiştirmeden [CLI'yi güncelleyin ya da geri alın](docs/cli.md#update-select-or-roll-back-the-cli). Yerel başlatıcılar, denetleyici olmadan kanalları, güncel programları ve oynatılabilir arşivleri aramak için salt okunur [çalma listesi çözümleyicisini](docs/cli.md#local-playlist-resolver) kullanabilir. Kurulum ve yapılandırmadan sonra:

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

`a1`, kayıtlı bir oynatıcı için örnek takma addır; bilgisayarın ana makine adı değildir. [Kayıt ve eşleştirme](docs/cli.md#register-and-connect-a-player), [örneklerle tüm komutlar](docs/cli.md#commands), [bağlantıyı kesme ve erişimi iptal etme](docs/cli.md#disconnect-deregister-or-revoke-a-player) ve [sorun giderme](docs/cli.md#troubleshooting) bölümlerine bakın.

Sürekli açık bir oynatıcıda, uygulamadan çıkmadan oynatmayı durdurmak ve sonra uyandırmak için [standby ve go](docs/cli.md#stop-playback-while-keeping-the-player-online) kullanın. `stop`, `standby` için takma ad olarak kalır; `start` ve `wake`, `go` için takma adlardır. Uzaktan kontrol bağlı kalmalıdır; bu nedenle bekleme modu sıfır ağ trafiği vaat etmez. `ott a1`, oynatıcının bildirdiği işlemleri gösterir. Kabul edilen komut, kapanmanın veya oynatmaya başlamanın tamamlandığını doğrulamaz.

[En boy oranı komutları](docs/cli.md#aspect-ratio-fit-and-fill), güncel CLI ve denetleyici ile `aspect` yeteneğini bildiren bir oynatıcı gerektirir. Değişiklik onaydan sonra uygulanır; modu ve kaydedilip kaydedilmediğini doğrulamak için `aspect` değerini yeniden okuyun. Fill, görüntüyü germek yerine orantılı kırpar.

Uzaktan [ekran görüntüleri](docs/cli.md#remote-screenshots), güncel CLI ve denetleyici ile desteklenen yakalama bağdaştırıcısına sahip oynatıcı gerektirir. Oynatıcının Uzaktan kontrol bağlantısını açmak, o denetleyiciye ekran görüntüsü ve tanılama yetkisi verir; yerel ekran görüntüleri ayrı izin anahtarı gerektirmez. Tarayıcıların yerel kaynak seçicisi yine gereklidir. Görseller ayarları, PIN ekranlarını ve görünür kimlik bilgilerini içerebilir; yalnızca güvendiğiniz denetleyiciye bağlanın. `ott a1`, kullanılabilirliği ve gereken kaynak seçimini bildirir. Ekran görüntüleri, yapılandırılmış telemetri toplamadan ayrıdır.

Bir başlıkla eşleşen tüm VPortal videolarını karıştırıp tekrarlı oynatmak için:

```sh
ott l vpr "wedding"
```

`vpr`, bütün seçimi bir kez karıştırır, sonra o kuyruğu tekrarlar. Yeniden karıştırmak için tekrar çalıştırın. `vp`, eşleşmeleri katalog sırasıyla oynatır; `vp --list`, oynatmayı değiştirmeden listeler. [VPortal komutlarına](docs/cli.md#commands) bakın.

Oynatıcının kayıtlı Plex yapılandırmasıyla açıkça belirlenmiş bir Plex sırası için:

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

CLI varsayılan olarak 45 saniyeye kadar bekler. `ott -t 45 l plex shuffle "Tom and Jerry"` yazmak aynı yanıt zaman aşımını açıkça belirtir; videoların ne kadar oynatılacağını sınırlamaz. Büyük Plex kitaplıklarını toplamak, kısa bir 10–15 saniyelik zaman aşımından uzun sürebilir. `-t SECONDS` (veya `--timeout SECONDS`) seçeneğini oynatıcı takma adından önce koyun. Önizleme örnekleri ve oynatma isteği zaman aşımına uğrarsa yapılacaklar için [Plex kitaplığı zaman aşımlarına](docs/cli.md#plex-libraries-by-name) bakın.

Plex kuyrukları `plex_queue` uygulayan CLI, denetleyici ve oynatıcı gerektirir. `plex play --shuffle ID...`, verilen ID'leri bir kez karıştırır. Mevcut kiosk politikası tekrarlamıyorsa kuyruk son öğeden sonra durur. Kuyruk varken olağan `next` ve `prev` ona yönelir; `plex stop` kuyruğu temizler. Önizleme hiçbir zaman sağlayıcıyı değiştirmez veya mevcut oynatmanın yerine geçmez. Sınırlar ve hazır olma durumu için [Plex kuyruk komutlarına](docs/cli.md#ordered-plex-queues) bakın. Kitaplık arama ayrıca `plex_library` bildiren güncel oynatıcı ve denetleyici gerektirir; oynatıcının kayıtlı Plex bağlantısını kullanır, belirsiz veya eksik seçimleri reddeder. [Ada göre Plex kitaplıklarına](docs/cli.md#plex-libraries-by-name) bakın.

### Yerel video profillerini kaydetme

Güncel oynatıcı ve denetleyiciyle yerel dosya/klasörler `offline` sağlayıcısını kullanır:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Önce dosya/klasörleri oynatıcıda bağlayın. Kaydetme, oynatmayı başlatmadan yapılandırmayı ve seçili videoyu kaydeder. Dışa aktarma yalnızca tanımları taşır; medya ve işletim sistemi/tarayıcı erişim izinleri cihazda kalır. Başka kurulum yerel bağlama gerektirir ve yüklemek oynatmanın doğrulandığı anlamına gelmez. Kurulum, yetenekler, taşınabilir biçim sınırları ve sorun giderme için [yerel video profillerine](docs/cli.md#local-videos-profiles) bakın.

### Mevcut öğeyi profiller arasında kaydetme

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Sık kullanılanlar normal yeniden başlatmalarda hedef oynatıcıda korunur. Birini seçmek kayıtlı sağlayıcıyı/profili yükler ve yeni bağlantıyı çözümler; değişmiş kaynak veya eksik öğe, başka kanal/filmle değiştirilmeden hata verir. CLI, denetleyici ve oynatıcı `favorites` desteklemelidir; `ott l`, kullanılabilir işlemleri gösterir. Takma adlar, sınırlar, onayın anlamı ve sorun giderme için [profiller arası sık kullanılanlara](docs/cli.md#favorites-across-profiles) bakın.

### Kayıtlı yapılandırmayı yükleme

Adlandırılmış yapılandırmaları `~/.config/ottplay-control/cli.json` dosyasının özel `presets` bölümünde saklayın. Her yapılandırma numaralı M3U profilleri (çalma listesi ve arşiv saatleri), Plex kimlik bilgileri ve yükleme bitince seçilecek M3U profilini içerir. [Yapılandırma örneğine](docs/cli.md#named-setups) bakın.

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI önce tüm yapılandırmayı doğrular, sonra her sonraki değişiklikten önce oynatıcının onayını bekler. Sağlayıcı yüklemesini yönetir, Plex ve M3U yuvalarını kaydeder ve yapılandırılan M3U profilini seçer. Hata, sırayı durdurur ve doğrulanmış adımları bildirir; sonucu belirsiz yazma asla otomatik tekrarlanmaz. Ek profil hizmeti veya denetleyici yeniden başlatması gerekmez. Bu yapılandırmayı gizli tutun: sağlayıcı kimlik bilgilerini içerir.

## Komut gönderme

Bu örnek, yönetici belirtecini URL'ye veya komut satırı argümanlarına koymadan yerel özel yapılandırmadan okur:

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

Diğer komutlar numara veya adla kanal seçer, rastgele kanal seçer, sağlayıcı değiştirir, mesaj gösterir, desteklendiğinde M3U listesini değiştirir veya oynatıcıdan çıkar. Kullanılabilirlik oynatıcı platformuna ve mevcut sağlayıcıya bağlıdır. [API sözleşmesine](docs/api.md) bakın.

Bağımsız VPortal sağlayıcısını destekleyen bir oynatıcıda `ott a1 provider
vportal`, onun kendi 15 profilini seçer. `profile N vportal LINK`, `profile N
name NAME` veya `name` ve `vportal` içeren özel bir `profile-config N FILE` kullanın, ardından `profile N` ile yapılandırılan yuvayı seçin. Mevcut M3U profilleri ayrı kalır. `profiles`, hesap bağlantılarını veya anahtarları açığa çıkarmadan meta verileri bildirir.

VPortal filmi veya bölüm kuyruğunu başlattıktan sonra `ott a1 kiosk on` bu seçimi kilitler. Oynatıcı, yeniden yüklemelerde kuyruğu ve konumunu korur, kurtarma için medya URL'lerini yeniden çözümler. Başka başlık seçmek veya profilleri değiştirmek için `kiosk off`, kilitlemek için ardından `kiosk on` kullanın. Kiosk makbuzu başlığı ve bölüm sayısını gösterir; sağlayıcı istekleri ve akış URL'leri oynatıcıda kalır.

Çocukların izlemesi için `ott a1 kiosk on --strict` kullanın: yerel dokunuş videonun alt çubuğunu gösterir. Güncel core oynatıcılar, mevcut VPortal/Plex bölümünde gezinmek için zaman çizelgesine dokunmaya veya sürüklemeye izin verir; diğer yerel kontroller kilitli kalır. Eski oynatıcılar, kanal kioskları ve zaman çizelgesi hareketlerini yakalayan sarmalayıcılar salt okunur alt çubuğu korur. Mevcut kilit hedefini korur. Denetleyiciden `ott a1 kiosk off` ile açın. Android web sitelerinde sistemden çıkışı engellemek, PIN ile ayrı uygulama sabitlemesi gerektirir; [kiosk moduna](docs/cli.md#kiosk-mode) bakın.

## Uzaktan tanılama

`doctor`, UI/medya `inspect`, işlem makbuzu sorgulama, kanıt paketleri, `health`, web/yerel `media-progress` ve çevrimdışı `report verify` için [salt okunur workbench hızlı başlangıcıyla](docs/workbench.md#quick-start) başlayın. Mevcut yönetici CLI yapılandırmasını ve oynatıcı takma adlarını kullanır. Yerel yol, ayrıca hazırlanan Android bakım ajanı içindir; Tauri oynatıcıları web incelemesini kullanır. Raporlar dekoder ilerlemesini fiziksel ekran/ses doğrulamasından ayırır. Eksik yetenekler, kullanılamayan yollar ve eksik kanıtlar için [workbench sorun gidermeye](docs/workbench.md#troubleshooting) bakın.

İsteğe bağlı [protokol 2 tanılaması](docs/remote-diagnostics.md), her çalışma ortamı için açıkça etkinleştirilen, ayrı ve sınırlı operatör kimlik bilgileriyle sınırlı yapılandırılmış telemetri sağlayan oturumlar sunar. Etkin oynatıcı bağlantısı ek güven sorusu olmadan yerel yetki verir; her yakalamanın oturum süresi yine sınırlıdır. Tanılama kontrol sorgulaması, mevcut komut kuyruğundan bağımsızdır.

[Tanılama CLI ve MCP kılavuzu](docs/diagnostics-cli.md), kurulumu, operatör kimlik bilgilerini, bağlantıyı, her tanılama komutunu/aracını, yakalama ve onarım örneklerini, çalışma ortamı erişiminin iptalini ve sorun gidermeyi kapsar. `ott diagnostics --help` bu ayrı komut ailesini listeler; olağan oynatıcı takma adları ve yönetici kimlik bilgileri tanılama erişimi vermez.

## Dağıtım paketleri

- [Android tablet: katı Plex kiosk ve başlangıç](docs/android-kiosk-setup.md)
- [Paylaşılan Android USB yönetimi ve açılış kurtarma](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS ve Windows](docs/deployment.md)
- [Yapılandırma, teslim anlamı ve sınırlar](docs/api.md)
- [Sürüm ve doğrulama süreci](docs/development.md)

Kuyruklar sınırlıdır, varsayılan olarak 60 saniye sonra sona erer ve bellekte bulunur. Sunucuyu yeniden başlatmak bekleyen komutları siler. Tek replika çalıştırın; bağımsız replikalar kuyruk paylaşmaz. Oynatıcı teslim denemesini onaylar, gerçek oynatmayı veya donanım ses düzeyini değil. Ağ yeniden denemeleri çalışan oynatıcı oturumu içinde yinelenmekten arındırılır; uygulama yeniden başlatmaları arasında değil.

Sunucu, [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) içindeki isteğe bağlı `local_proxy.py` örneğinin komut API'sini, ayrı yönetici/cihaz kimlik bilgileri ve onaylı teslimle izler. Eski vekil ayrı bileşen olarak kalır. Bu proje medyaya vekillik etmez veya oynatıcının HLS, EPG ya da sağlayıcı ağ iletişimini değiştirmez.

## Lisans

MIT. Dahil edilen sürüm araçlarının atıfları `NOTICE` ve `LICENSES/` içinde kayıtlıdır.

## Proje bakımı

[Katkı ve test gereksinimlerine](CONTRIBUTING.md), [güvenlik bildirim politikasına](SECURITY.md), [güvenlik tasarımına](docs/security-design.md) ve [OpenSSF kanıtları ile kalan ölçütlere](docs/openssf-evidence.md) bakın.

Plex kuyrukları güncel oynatıcılarda 500 ID'ye kadar kabul eder. Bir kez karıştırmak için `ott f10 plex play --shuffle ID...`, ardından Plex sağlayıcısı seçiliyken kuyruğu tekrarlamak ve kilitlemek için `ott f10 kiosk on --strict` kullanın. `ott f10 kiosk off` kilidi açar. Eski oynatıcılar bildirdikleri kuyruk sınırını korur.

## Sürüm adayı fuzzing'i

Herkese açık sürüm adayları, [fuzzing kılavuzunda](docs/fuzzing.md) açıklanan girdi sınırlarında sınırlı yerel fuzzing çalıştırır. Mutasyon kampanyaları PR, beta veya gecelik derlemelerde çalışmaz. Kılavuz kapsamı, bütçeleri, kanıtları ve regresyonların yeniden üretilmesini açıklar.

## Topluluk ve güvenlik kanıtları

[Katkılar](CONTRIBUTING.md), [yönetişim](GOVERNANCE.md), [Davranış Kuralları](CODE_OF_CONDUCT.md), [yol haritası](ROADMAP.md) ve [OpenSSF kanıtlarına](docs/openssf-evidence.md) bakın. Kanıt dizini, desteklenen güvenlik profillerini, gerçek test sonuçlarını ve kalan Silver gereksinimlerini ayırır.
