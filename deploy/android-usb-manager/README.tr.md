# Android USB aktarım imajı

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Bu dizin, paylaşılan yöneticinin aktarım konteynerine ait dağıtım şablonlarını ve [`Dockerfile`](Dockerfile) dosyasını içerir. Python yöneticisi USB ana makinesinde çalışır. İmaj ADB, Python ve atomik bakım bağdaştırıcısının araçlarını sağlar; ikinci bir cihaz kaydı veya kurtarma arka plan hizmeti içermez. Hazırlama, komutlar, geçiş, kurtarma sınırları ve geri alma için [operatör kılavuzuna](../../docs/android-usb-manager.md) bakın.

Git çalışma kopyası yoksa aynı [sürümden](https://github.com/open-ott-play/ottplay-control-server/releases) `ottplay-control-server-deployment.tar.gz` ve `release-manifest.json` indirin. Arşivin özetini doğrulayın ve [Dağıtım paketini alma](../../docs/android-usb-manager.md#get-the-deployment-package) bölümüne göre yeni bir dizine açın. Paket, bu tarifi ve `tools/` altındaki altı ana makine/uzak betiğini içerir; dizin yapısını koruyun. `v0.1.5-beta.6` dahil eski paketlerde bu betikler yoktur; eşleşen kaynak arşivi gerekir. Paket, oluşturulmuş USB aktarım imajını içermez. Kontrol sunucusunun OCI sürüm varlığı farklı amaçlıdır.

## Kaynak ve çıktı kökeni

Temel `python:3.12.14-slim-trixie` olup `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b` değerine sabitlenmiştir. Temel, APT talimatı ve hash ile kilitlenen `websocket-client==1.9.0` kurulumu [PR 128, b4181bf revizyonunda](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager) incelenmiştir. Mevcut derleme önbelleğinin yeniden kullanımını sağlamak için talimat metni korunmuştur. İsteğe bağlı WebSocket paketi mevcut genel bakım istemcilerini destekler; taşınmış CDP bağdaştırıcıları yalnızca Python standart kütüphanesini de kullanabilir.

Bu tarif kilitli wheel kurulduktan sonra kurulu `pip` paketini kaldırır, PR 128'in yönetici ve kurtarma kodunu dışarıda bırakır, ADB'yi ön planda başlatır ve devralınan sağlık kontrollerini kapatır. Çalışma zamanı yükleyicisini kaldırmak kullanılabilir saldırı yüzeyini azaltır; açık taramasının temiz olduğunun kanıtı değildir.

İncelenen PR 128 çıktısı `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a` idi. Farklı bir giriş noktası ve uyumsuz bir heartbeat sağlık kontrolü içerir. Bir referans çıktısıdır; bu Dockerfile'ın derlemesi değildir. İmaj kimliğini, kaynak kökenini veya tarama sonuçlarını bu tarife atfetmeyin.

APT depoları anlık görüntülere sabitlenmemiştir. Dolayısıyla gelecekteki temiz yeniden derleme, aynı temel özeti ve Python wheel hash'iyle bile daha yeni işletim sistemi paketleri seçebilir. Derlemeyi onaylarken tam kaynak revizyonunu, derleyici/platformu, oluşan imaj kimliğini, paket sürümlerini ve taramayı kaydedin. Üretim yapılandırması bu onaylı, değişmez imaj kimliğini veya özetini kullanmalıdır.

## Meşgul USB ana makinesini etkilemeden derleme

Derlemeyi ve taramayı ayrı bir derleyicide yapmayı tercih edin. Hedef ana makine yalnızca onaylı imaja ihtiyaç duyar; paket indirmeleri veya derleme araç zinciri gerekmez.

Hazırlanmış yerel derleyiciyi yeniden kullanırken önce tam temel imajın mevcut olduğunu, APT ve wheel kurulum katmanlarının önbellekte bulunduğunu doğrulayın. Yalnızca son PR 128 imajını yüklemek bu koşulların hiçbirini kanıtlamaz. Depo kökünde veya açılmış dağıtım dizininde bilinçli bir çevrimdışı deneme şöyledir:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` derleme adımlarına uygulanır; derleyicinin temeli çözümlerken hiçbir zaman kayıt sunucusuna bağlanmayacağına dair genel garanti değildir. Sınırlı bir ana makinede bu yordamı kullanmadan önce yerel temel çözümlemesini doğrulayın. APT veya wheel katmanı eksikse USB ana makinesinde sessizce ağ üzerinden yeniden derlemeye izin vermek yerine denemeyi durdurun.

BuildKit derleme adımının ağ modunu önbellek anahtarına dahil eder. Bu yüzden varsayılan ağ modunda oluşturulan önbellek yukarıdaki çevrimdışı denemede eşleşmeyebilir. Ancak tam temelini ve talimat zincirini kontrol ettikten sonra yeniden kullanın; paket kurulum adımlarından biri `CACHED` bildirmek yerine çalışırsa durun. Son `pip` kaldırma adımı açıkça `RUN --network=none` kullanır; önceki önbellek adımları kendi asıl modlarını kullansa bile ağa ihtiyaç duymaz.

Üretim yapılandırmasını değiştirmeden önce Python ve ADB sürümlerini kontrol edin, oluşan tam imajı tarayın, ikili dosya aktarımını ve APK hazırlamayı doğrulayın; ardından kılavuzdaki cihaz kimliği, anahtar yetkilendirmesi, uyku ve açılış kabul kontrollerini yapın. ADB 34'ün eski Android cihazıyla iletişimi test edilmelidir; sürüm numarası tek başına KitKat veya Fire uyumluluğunu kanıtlamaz. Geri almaya gerek kalmayana kadar mevcut imajı ve özel dağıtım yedeğini saklayın.

## Çalışma zamanı bütçesi

Bu Dockerfile değil, ana makine yöneticisi 256 MiB bellek, bellek artı takas için 256 MiB ve CPU shares 128 uygular; katı CPU veya PID kotası yoktur. `--no-healthcheck`, `--network none`, salt okunur kök dosya sistemi, kaldırılmış capabilities ve kesin USB düğüm eşlemelerini zorunlu kılar. `/tmp`, aynı bellek bütçesini tüketen sınırlı bir tmpfs'dir. Hazırlama bağdaştırıcısı 1 MiB'lık yazmalar kullanır ve tek yükü 128 MiB ile sınırlar; işlemler yöneticinin kilidini paylaşır.

Docker, [bellek, takas ve CPU-share kontrolleri](https://docs.docker.com/engine/containers/resource_constraints/) ile [`--no-healthcheck` seçeneği](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck) arasındaki ayrımı belgeler.
