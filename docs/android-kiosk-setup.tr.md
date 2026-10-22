# Android tablet: katı Plex kiosku ve başlangıç

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB kılavuzu](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB yöneticisi](android-usb-manager.md)

Özel bir tablet ayrı ayrı doğrulanmış üç ayar gerektirir: içerik kuyruğu, OTT'nin katı kiosk ilkesi ve Android başlangıcı. APK yüklemek veya ekran kapatma/sürdürmeyi doğrulamak, işletim sistemi yeniden başlatıldığında açılışı kanıtlamaz.

## Tanımlama ve kurulum

1. Fiziksel tableti, OTT takma adı/UUID'sini, USB yöneticisi takma adını, seri numarasını, modeli, Android ID ve SDK'yı kaydedin. OTT ve USB adları farklı olabilir; kimliği takma addan çıkarmayın ve başka tabletin tokenını kullanmayın.
2. SDK ile uyumlu resmî APK varyantını kullanın, sürüm SHA-256'sını ve paket/imza kimliğini doğrulayın, `install -r` ile yükleyin. Oynatıcı verilerini koruyun. USB'yi [paylaşılan yönetici](android-usb-manager.md) yönetiyorsa onu kullanın; rakip ADB sunucusu veya cihazı belirtilmemiş `adb` komutları çalıştırmayın.
3. Oynatıcıyı kendi denetleyici kimlik bilgileriyle eşleştirin ve `ott TABLET` ile doğrulayın. İstenen adlandırılmış ön ayarı yükleyip `ott TABLET load PRESET verify` çalıştırın. Ev ağı ön ayarlarıyla geliştirme bilgisayarının localhost ön ayarı birbirinin yerine kullanılamaz.

## Plex'i tabletten doğrulama

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Kitaplık kimliğini, başlığını ve öğe sayısını doğrulayın. Yönetici bilgisayarından başarılı istek, tablet bağlantısını kanıtlamaz. Başarısızlıkta tablette kayıtlı adresi, yönlendirmeyi, TLS desteğini ve kimlik bilgilerini kontrol edin. Sunucunun geçerli HTTPS uç noktasını tercih edin. Eski firmware özel LAN HTTP gerektiriyorsa yalnızca bu kurulumla sınırlayın, tokenların ve medyanın şifrelenmeden taşındığını kaydedin; ortak ön ayarı değiştirmeyin veya sertifika doğrulamasını genel olarak kapatmayın. Tanılama veya belgelerde token ya da özel medya URL'si yayımlamayın.

Plex sunucu adresini değiştirmek seçili kitaplık köklerini temizler. **Plex** sağlayıcısını seçin, **Plex settings → Folders** açın, istenen kitaplık satırını işaretleyin (yalnızca **Browse folders** değil), **Ok** ve sonra **Save and open library** seçin. `1.1.58-beta.19` oynatıcıda sunucu bilgileri tek başına klasör seçiciyi açabilirken `ready` false kalır; kuyruk komutlarından önce seçimi tamamlayın. Sırf `ready: true` için M3U'ya geçmeyin: Plex kuyruğu orada oynayabilir ama `kiosk on --strict`, bağlı medyası olmayan bekleyen M3U ilkesi kaydedebilir. Bu, Plex kuyruğunu kilitlemez veya geri yüklemez.

## Kuyruğu başlatma ve kilitleme

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

Sıralı kuyruk için `plex shuffle` yerine `plex library 'LIBRARY NAME'` kullanın. Shuffle bir sıra seçer; kiosk onu tekrarlar. İstenen öğeleri, `enabled: true`, `strict: true` değerlerini ve doğru Plex medya kuyruğuna bağlı ilkeyi doğrulayın. Kanalı veya medyası olmayan `waiting` ilkesi kabul edilmiş kiosk değildir. İlerleyen konum/kare sayacıyla en az iki medya örneği gözlemleyin ve ekranı inceleyin. İlerleme olmadan onay veya `playing` etiketi yeterli değildir. Öğe sonunda doğal geçişi test edin; tam döngü testi uygulanabilir değilse sınırı açıkça kaydedin.

Katı kiosk oynatıcı arayüzünü denetler. Android cihaz sahibi kaydı değildir; Home, Recents, bildirimler ve Settings'in engellendiğini kanıtlamaz. Sistem başlatıcısını ve gözetimli bakım yolunu koruyun.

## Başlangıcı ayrı etkinleştirme

Güncellemeler için mevcut imza anahtarını koruyarak [OTT Home 0.2.0](../native/android-home/README.md) oluşturun ve doğrulayın. `play.ott.home` uygulamasını `play.ott.foss` yanına yükleyin. `play.ott.home/.StartupActivity` açıp **Start OTT after reboot** etkinleştirin. Tercih varsayılan olarak kapalıdır. Resmî oynatıcı ikili dosyasını, OTA imzasını, profil verilerini veya Home düğmesi atamasını değiştirmez.

Home seçicisi olan firmware'de isteğe bağlı olarak **OTT Home → Always** de seçin. Seçici yoksa (test edilen eski Fire firmware'i dahil) açılış izni bağımsız başlangıç sağlar. Önceden varsayılan Home olarak ayarlanmış kurulumlar davranışını korur. Tam geri alma için iki mekanizmayı da kapatın.

Paylaşılan USB yöneticisinin `recover_on_boot` ayarı üçüncü bağımsız mekanizmadır: NAS ve USB kablosu gerekir, yalnızca yeni açılışı ele alır ve çöken uygulamayı sürekli yeniden başlatmaz. Özerk başlangıcın yerine geçmez. Özerk başlangıç testinde kurtarmayı kapalı tutun; eşlikçi uygulamanın eksik başlatmasını gizlemesin. Güvenli ekran kilitlerine uyun; istenirse normal biçimde açın.

## Kabul ve teslim

Kanıtları özel saklayın; herkese açık makbuzlarda kimlik bilgisi bulunmasın:

- APK sürümleri, hash'ler, fiziksel/OTT/USB kimlik eşlemesi ve seçili ön ayar.
- Plex kitaplığı, öğe sayısı/sırası, katı ilke ve başlangıçtaki ilerleyen oynatma.
- Gerçek işletim sistemi yeniden başlatmasından önce/sonra açılış kimliği; sonrasında `sys.boot_completed=1`.
- Yeniden başlatma ile gözlem arasında elle oynatıcı açma veya USB kurtarma olmaması.
- OTT ön plan etkinliği, denetleyiciye yeniden bağlantı, geri yüklenmiş kuyruk/katı ilke ve ilerleyen oynatma. Açılış alıcısının `requested` sonucu yalnızca gönderimdir.
- Ayrı güç düğmesi testi: ekran kapalı, ses durmuş, normal kilit açma ve sürdürme.
- Kesin sınırlar: güvenli kilit açma gereği, USB destekli kurtarma, denenmemiş fiziksel çıkışlar, akış hataları veya bellek tükenmesi. Bunları tam kabul edilmiş saymayın.

Sonraki tableti teslim etmeden aynı listeyi uygulayın; önceki cihazın başarısını kopyalamayın. Eski Fire tabletler, üreticinin yerel HLS'si çökebildiği için M3U'da `hls.js` gerektirebilir; bu Plex oynatma kararlılığını kanıtlamaz.

## Bakım ve geri alma

```sh
ott TABLET kiosk off
```

**OTT Startup** açın, **Start OTT after reboot** kapatın ve değişmişse asıl Home seçimini geri yükleyin. Gerekirse doğrulanmış USB aktarımı üzerinden yalnızca `play.ott.home` kaldırın; `play.ott.foss` ve verilerini koruyun. Başlangıcı kapatmak kuyruğu değiştirmez veya kiosku otomatik kapatmaz.
