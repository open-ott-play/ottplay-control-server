<div lang="tr" dir="ltr">

# Android OTA: normal oynatıcılar ve ayrı root ajanı

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

**k8a/fire8 ve diğer Capacitor oynatıcıları** için root ajanı komutu yerine normal oynatıcının Wi-Fi güncelleyicisini kullanın:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

`--version` verilmezse yayımlanmış en son beta seçilir. Android 7+ üzerinde `api22` yerine `full` kullanın. CLI resmi sürümü bulur, manifestini denetler, APK’yi hazırlar ve kurulumu bir kez istemeden önce durumunu sorgular. Android tablette onay isteyebilir. Bakımdan önce kiosku açıkça kapatıp korunan ayarların kilidini açın; komut bu politikaları asla değiştirmez. [Tam CLI yordamına](cli.md#capacitor-apk-updates-over-wi-fi) bakın.

Önceden yüklenmiş `AppUpdate` destekli APK, Wi-Fi ve denetleyiciye bağlı çalışan oynatıcı gerekir. Normal sonraki güncellemeler USB gerektirmez, ancak çevrimdışı tablete veya durmuş WebView’a erişemez. İlk kurulum, kullanılamayan güncelleyici, bozuk uygulama veya ağ kaybı yine yerel kurtarma gerektirebilir. Otomatik sürüm zamanlayıcısı ya da genel sessiz yükleyici ima edilmez.

Kılavuzun kalanı, hazırlanmış Matricom/API19 `play.ott.kitkat` kurulumuyla sınırlı **farklı bir root erişimli a1 bakım ajanını** anlatır. Normal Fire/Capacitor cihazlarında önceden kurulu veya kullanılabilir değildir.

Bir kerelik root USB başlangıç kurulumundan sonra bağımsız Android ajanı, dışarı giden HTTPS bağlantısıyla imzalı güncellemeler alabilir. Kendisini güncellemek için çalışan WebView gerekmez. 0.1.2 ajanı ayrıca kayıtlı kurtarma dosyaları ve kesinleştirmeden önce doğrulama içeren, APK ile sistem dokunma yardımcısından oluşan koordineli paketi destekler.

Bu kılavuz tüm yolu açıklar. Kesin manifest alanları, sınırlar ve donanım gerekleri için [yerel ajan başvurusunu](../native/android-agent/README.md#signed-updates-over-wi-fi) kullanın.

## Neler güncellenir

![OTA mimarisi: incelenmiş sürüm baytları imzalanır, doğrudan HTTPS ile yayımlanır, bağımsız ajan tarafından doğrulanır ve kurulumdan sonra denetlenir. Web dağıtımı ayrıdır.](images/ota-architecture.svg)

- **Yerel ajan:** `kind: agent` kendi ARMv7 ikili dosyasını değiştirir. OTT kapalıyken ve başka HOME uygulaması kullanılırken de çalışır.
- **Yalnızca APK:** `kind: apk` verileri koruyarak sarmalayıcıyı değiştirir. OTT etkin ve ön planda olmalıdır. Adayın gömülü yardımcısı kurulu sistem yardımcısıyla tam eşleşmelidir.
- **APK ve dokunma yardımcısı:** `kind: bundle` iki bileşeni çift olarak günceller. Eski ajan önce paket destekleyen yalnızca-ajan güncellemesini almalıdır.
- **Barındırılan oynatıcı:** doğrulanmış web çıktısını mevcut origin’e dağıtın, sonra etkin oynatıcıyı yeniden yükleyip inceleyin. APK güncellemesi barındırılan JavaScript’i dağıtmaz. [Sürüm iş akışına](release-workflow.md) bakın.
- **Denetleyici ve CLI:** denetleyiciyi dağıtıp tüm CLI modül kümesini ayrı kurun. Birini güncellemek tabletteki kodu değiştirmez.

Ürün yazılımı, çekirdek, sistem WebView, root aracısı ve başlangıç betikleri bu OTA biçiminin dışındadır. [Yerel başvuru](../native/android-agent/README.md#bootstrap) başlangıç ve kurtarma sınırlarını açıklar. Desteklenen OTA işlemleri ağ ADB’si veya keyfî uzak kabuk gerektirmez.

## İncelenmiş sürümden doğrulanmış kuruluma

1. **Doğrulanmış baytları seçin.** İncelenmiş değişiklikleri CI geçtikten sonra birleştirin. İstenen sürüm çıktısını indirip kaynak revizyonunu, değişmez sürüm kanıtını ve sağlama toplamını doğrulayın. Yerel arşivden `agent` ikili dosyasını çıkarın; arşivin kendisi kurulabilir ajan yükü değildir.
2. **Hazırlayıp imzalayın.** Açık anahtarı başlangıçta sabitlenen mevcut özel Ed25519 anahtarını kullanın. Anahtarı ve cihaz kimlik bilgilerini gizli tutun.
3. **Yayımlayıp aktarımı denetleyin.** Yalnızca yükü ve `signed.json` dosyasını değişmez HTTPS yollarında yayımlayın. İkisi de baytlarını HTTP 200 ile doğrudan döndürmelidir; güncelleyici yönlendirmeleri reddeder. Paketleme aracının `verify` komutunu çalıştırın.
4. **Bir güncelleme gönderin.** İstek ID’sini kaydedip onaydan sonra durumu inceleyin. Kayıp veya belirsiz yanıt işlemi körlemesine tekrarlama nedeni değildir.
5. **Etkiyi doğrulayın.** Yeni okunan kurulu özet ve sürümleri hazırlanan sürümle karşılaştırın. Etkin oynatıcıda kiosk hedefini, kuyruğu ve ilerleyen oynatmayı da denetleyin. Fiziksel görüntü ve ses gözlem gerektirir.

Ajan sürümü örneği; seçilen sürümü ve gerçek yolları yerleştirin:

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

`ENVELOPE_SHA256`, yük özeti değil paketleme aracının `envelope_sha256` değeridir. `accepted` kabul anlamındadır, kurulumun başarılı olduğu anlamında değildir. Kendi kendini değiştirmek eski işlem makbuzunu `unknown` bırakabilir; yeni çalışma zamanı, beklenen ajan sürümü ve eşleşen kurulu özet kurulum kanıtını sağlar. [CLI başvurusu](cli.md#independent-android-agent) bağımsız yerel yönlendirmeyi ve makbuz işlemeyi açıklar.

## APK/yardımcı işlemi nasıl kurtarılır

![APK/yardımcı işlemi: ön denetim, eski çifti saklama, uygulama, doğrulama, kesinleştirme; hatalar eski çifti geri yükler ve tamamlanmamış kurtarma uygulama değişikliklerini engeller.](images/ota-transaction.svg)

Paket tam olarak `app.apk` ve `ott-touch-guard` içerir. Paketleme aracı `--kind bundle --apk` ile doğrudan imzalı sarmalayıcı APK’den oluşturabilir. Gömülü yardımcı çıkarıldığı için teslim edilen iki kopya eşleşir. [Paket komutu ve gereklere](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates) bakın.

Dosyaları değiştirmeden önce ajan eşleşen kurulu çift, yeterli boş alan, ön planda yanıt veren OTT oynatıcı, salt okunur sistem bölümü ve bildirilen APK sürüm düşürme desteği ister. Kilitli kioskta aynı TV kanal ID’sini veya sıralı VPortal ID’lerini doğrular; kuyruk uzunluklarının eşitliği yetmez. Desteklenmeyen kilitli kuyruklar reddedilir. Bilerek kapatılmış OTT uygulaması da reddedilir ve kullanıcının mevcut HOME uygulaması korunur.

Kurtarma dosyaları ve bekleyen işlem uygulama durmadan önce kalıcılaştırılır. Ajan uygulama ve eski yardımcının durmasını bekler, yardımcıyı atomik değiştirir, `/system` bölümünü yeniden salt okunur yapar, APK’yi kurup sonucu denetler. Uygulama veya doğrulama hatası önceki çiftin geri yüklenmesini tetikler. Başlangıç kurtarması OTT’yi açmadan veya HOME’u devralmadan dosyaları geri yükler.

Kurtarma tamamlanamazsa `updates.recovery_failed` doğrudur. Tanılama ve imzalı yalnızca-ajan onarımı erişilebilir kalır; uygulama değişiklikleri ve oynatma bekçisi engellenir. Kurulumu zorlamak için kaydedilmiş işlem silinmemelidir. İkili kurtarma uygulama verisi geçişlerini geri almaz ve ürün yazılımı sürüm düşürmeyi hâlâ reddedebilir. [Kurtarma sınırlarına](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates) bakın.

## Sonuçlar ve kabul kaydı

![Doğrulama grafiği: 488 CLI testi, 576 sürüm aracı testi ve 6 OTA paketleme testi; referans tablet doğrulanmış yeni ajanı çalıştırır, ancak APK/yardımcı fiziksel kabulü beklemededir.](images/ota-validation.svg)

14 Ekim 2026 uygulama denetimi, 488 CLI testini (biri atlandı), 576 sürüm aracı testini, altı OTA paketleme testini, yerel Go race/statik kontrollerini ve ARMv7 derlemesini geçti. Sayılar kapsam veya performansı değil test kümesi büyüklüğünü anlatır. Hata enjeksiyon testleri geçersiz arşivleri, kesilmiş işlemleri, kısmi geri yüklemeyi, hedef değişikliklerini ve kurtarma engellerini çalıştırır. Bağımsız inceleme ayrıca yetersiz kuyruk kimliği denetimini ve host/cihaz APK-yardımcı doğrulama uyumsuzluğunu yakalayıp düzeltti.

15 Ekim 2026 denetimine göre:

- OTA uygulaması [denetleyici PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) içinde birleştirildi ve [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4) sürümüne dahil edildi.
- Güncel ve önceki imzalı ajan paketleri [OTA dağıtım sunucusunda](https://android-updates.ottplay.here.now/) yayımlandı. Yük ve zarf baytları doğrudan HTTPS ile başarıyla indirilip karşılaştırıldı.
- Referans tablet bağımsız OTA kanalıyla `0.1.1-beta.21` sürümünden `0.1.2-beta.4` sürümüne geçti. Çalışan ajan sürümü ve kurulu SHA-256 seçili sürümle eşleşti; çalışma zamanı değişti, OS açılış ID’si aynı kaldı. OTT’nin hâlâ çalışan uygulama süreci yoktu; uygulama açma veya HOME seçimi gönderilmedi.
- Yerel durum sistem dokunma yardımcısı özetini, bekleyen paket olmadığını ve kurtarma hatası olmadığını bildirdi. APK meta verileri yoktu; bu gözlem kurulu APK sürümünü veya özetini kanıtlamaz.
- Koordineli APK/yardımcı işleminin host tarafı doğrulandı; fiziksel kurulum, sürüm düşürme kurtarması, görüntü ve ses referans tablette henüz kabul edilmedi.

Sonraki her dağıtımda sürüm kaynağını/sağlama toplamlarını, hazırlanan zarf özetini, istek ID’sini, önceki/sonraki yerel durumu ve ayrı fiziksel kabul sonucunu saklayın. Başarılı indirme veya komut makbuzu bu gözlemlerin yerini tutamaz.

</div>
