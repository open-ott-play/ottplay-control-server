<div lang="tr" dir="ltr">

# Güvenilir denetim ve oynatmayı devretme

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Belge dizini](wiki/Home.md)

Bu özellikler, bu kılavuzu içeren bir revizyondaki denetleyici ve CLI gerektirir. Ayarların tam karşılaştırılması ve devir için oynatıcının da güncel olması gerekir. Eski sürüm arşivleri ve kurulum kılavuzundaki tarihî CLI revizyonu bu özellikleri kendiliğinden kazanmaz. `preset_session.py` dahil **tüm `cli/*.py` dosyalarını birlikte** kurun; özel yapılandırmayı koruyun.

![Denetim, kontrol noktası ve devir akışı](assets/reliable-control.svg)

## Sınırlı kalıcı sonuçları etkinleştirme

Bu isteğe bağlı bloğu özel sunucu yapılandırmasına ekleyin, doğrulayın ve sunucuyu yeniden başlatın. Hizmet hesabının yazabildiği yerel kalıcı bir dosya sisteminde mutlak yol seçin. Unix üzerinde dizin özel (0700), mevcut günlük ve kilit dosyaları normal özel dosyalar (0600) olmalıdır. Windows üzerinde dizin ACL’sini hizmet hesabıyla sınırlayın. Bir günlüğün sahibi tek bir süreçtir.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

Sunucu eksik dizinleri 0700 kipinde oluşturur. Sınırlar 1–4096 kayıt, 60–86400 saniye ve diskte 4 MiB’dir. Saklama süresi `command_ttl_seconds` değerini kapsamalıdır. Dolu günlük, geçerli kayıtları atmak yerine yeni anahtarlı işlemleri reddeder. Bozuk dosyalar, güvensiz Unix dizini, başka bir yazıcı veya başarısız kalıcı yazma güvenli biçimde reddedilir. Varsayılan, mevcut bellek içi komut yoludur.

Paketlenmiş systemd birimi `/var/lib/ottplay-control` dizinini 0700 kipinde oluşturur. Compose için UID 65532’nin sahip olduğu bir ana makine dizini hazırlayın, `CONTROL_SERVER_STATE` değerini mutlak yoluna ayarlayın ve `deploy/compose.yml` ile `deploy/compose.journal.yml` dosyalarını birleştirin. İkincisi yalnızca yazılabilir durum bağlamasını ekler; kök dosya sistemi salt okunur kalır. Helm için kalıcı bir hacim talebini ayrıca hazırlayın ve `journalExistingClaim=CLAIM_NAME` ayarlayın. Talep `/var/lib/ottplay-control` konumuna bağlanır; yukarıda gösterilen özel alt dizini kullanın. Chart tek replikayı, `Recreate` ve `fsGroupChangePolicy: OnRootMismatch` ayarlarını korur; böylece yeniden bağlama mevcut özel dizinlerin izinlerini özyinelemeli genişletmez. Seçilen depolama sürücüsünün bu sahiplik davranışını desteklediğini doğrulayın. Chart talebi oluşturmaz/silmez ve bağlamayı etkinleştirmek günlüğü etkinleştirmez: bunu hâlâ özel sunucu yapılandırması belirler.

`POST /api/requests` için `action` ve `params` yanına `idempotency_key` ekleyin. Eski komut POST’ları bunun yerine `Idempotency-Key` kabul eder. Anahtarlar 16–80 ASCII harfi, rakam, alt çizgi veya tire içerir; her niyet için yeni rastgele anahtar üretin. Aynı sahip/anahtar, aynı cihaz, eylem ve normalleştirilmiş JSON yüküyle yeniden kuyruğa almadan ilk ID’yi döndürür. Yükün değiştirilmesi 409 döndürür. Anahtarların kapsamı cihaz değil, operatör ID’si (veya yönetici) temelindedir. Saklama süresi dolunca anahtar yeni niyet olarak kabul edilebilir: istemciler eski niyeti bu pencere dışında yeniden denememelidir.

`GET /api/operations?device_id=DEVICE` günlüğün etkinliğini, sınırlarını ve komut TTL’sini bildirir. Bir işlemi okumak için `&key=KEY` ekleyin:

- `pending`: kabul edildi; henüz saklanan yanıt yok.
- `responded`: doğrulanmış oynatıcı yanıtı geldi; `result_status`, `ok`, `rejected` veya `unsupported` olur. İlk sonuç gövdesi normal biçimde zaman aşımına uğramaya devam eder.
- `acknowledged`: eski bir tüketici komutu onayladı. Bu, görünür oynatmayı veya bir ayar değişikliğinin kaydedildiğini kanıtlamaz.
- `unknown`: süre doldu veya yanıt saklanmadan sunucu yeniden başladı. Sunucu yeniden başlatmadan sonra bu komutu **asla yeniden yürütmez**.

Tekrarlanan anahtarlı POST, `status: retained`, ilk ID ve durumu döndürür. Normal yeni POST’lar `status: queued` değerini korur. Günlük kayıtları yalnızca sahip, cihaz, eylem, rastgele ID’ler, yük özeti, durum ve son süreleri içerir. Komut yükleri, sağlayıcı URL’leri, token’lar, ekran görüntüleri veya yanıt gövdeleri içermez. Bekleyen komutlar kalıcılaştırılmaz. Bu, sınırlı yinelenen işlem önleme ve sonuç takibidir; tam bir kez yürütme, kalıcı iş kuyruğu veya çok sunuculu yüksek erişilebilirlik değildir.

## Local/Home planlama, uygulama, sürdürme ve doğrulama

Mevcut `ott PLAYER load PRESET` davranışı korunur. Yeni iş akışı için:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` çevrimdışıdır: yerel ön ayarı doğrular, ayar yuvalarını ve istenen etkin profili içeriklerini yazdırmadan listeler. Oynatıcıyı incelemez. `verify` salt okunur tam karşılaştırmalar yapar ve seçilen M3U profilini kontrol eder. Fark varsa veya karşılaştırma desteklenmiyorsa sıfır olmayan kod döndürür.

`apply` sunucu günlüğünü gerektirir. Özel bir kontrol noktası oluşturur ve ilk değişiklikten önce tüm yapılandırılmış sağlayıcıları karşılaştırır. Her kayıt, `settings_compare` üzerinden saklanan baytları beklenen değerlerle karşılaştırır; eşleşme yazmayı atlar. Son doğrulama Plex’i, tüm M3U yuvalarını, isteğe bağlı Stalker yuvalarını ve seçilen M3U profilini kapsar. Karşılaştırmalar saklanan sırları değil, mantıksal değer döndürür.

Varsayılan kontrol noktası `~/.config/ottplay-control/checkpoints/` altındadır. Başka dosya seçmek için kipten sonra `--checkpoint /absolute/private/path/session.json` kullanın. Kontrol noktaları cihazı, ön ayar adını, sunucu/ön ayar revizyon özetini, biten adımları ve bekleyen tek rastgele işlem anahtarı/son süresini içerir. Ayar değerleri içermez. Dosyalar fsync sonrasında atomik değiştirilir ve eşzamanlı CLI yazıcılarına karşı kilitlenir. Eşleşen yerel ön ayarı erişilebilir tutun. Unix üzerinde kontrol noktası dizini özel (0700), normal dosyalar 0600 olmalıdır. Windows üzerinde dizinin devralınan ACL’sini CLI’yi çalıştıran kullanıcıyla sınırlayın; Unix kip bitleri Windows erişim izinlerini uygulamaz.

`resume` aynı sunucuyu, cihazı ve ön ayar revizyonunu doğrular, ardından bekleyen sonucu okur. Saklanan yanıt hemen yeniden kontrole izin verir. Belirsiz sonuç, önceki komut süresi, istek zaman aşımı ve küçük bir pay geçene kadar değişiklik göndermeyi durdurur. Bu aralıktan sonra `resume` komutunu tekrar çalıştırın. Kullanıcı değişikliklerini önceki başarılı yazmalarla karıştırmamak için bitmiş adımlarda bile saklanan ayarları yeniden kontrol eder. Göreli komutu asla körlemesine tekrarlamaz. Mutlak sağlayıcı/profil seçimi koruma süresinden sonra tekrarlanabilir.

Yeni `apply`, mevcut kontrol noktasının üzerine asla yazmaz. `resume` ile yeniden kullanın veya değişen ön ayar için yeni dosya seçin. Ayrı cihazların ayrı kontrol noktaları vardır. Tüm oynatıcıları kapsayan işlem veya geri alma yoktur; sonraki adım başarısız olursa önceki başarılı yazmalar kalır. `verified`, karşılaştırma anındaki saklanan oynatıcı değerlerini doğrular; başarılı ağ yüklemesini veya fiziksel görüntü/ses çıkışını değil.

## Entegrasyonlara yalnızca gerekli yetkiyi verme

HA, RF köprüsü veya ajan için `admin_token` dağıtmak yerine [normal komut operatörleri](security-design.md#scoped-command-operators) kullanın. Tam cihaz ID’lerine ve `input.channel_up`, `input.channel_down` gibi işlemlere izin verin. Bu ön ayar akışında ayar işlemleri açıkça `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` ve `provider_settings` izinlerini gerektirir. Devir, kaynakta `playback_handoff_export`, hedefte `playback_handoff_import` gerektirir. Bir operatör kaydı listelenen cihaz ve eylemleri birleştirir; aynı işlem her cihaz için geçerli olmamalıysa ayrı operatör kimlikleri kullanın. Token’lar ve tanılama onayı ayrı yetkilerdir.

Operatörler oynatıcı kuyruklarını sorgulayamaz, oynatıcı yanıtı gönderemez, tüm cihazları listeleyemez, yönetici hata ayıklama çıktısını veya başka operatörün sonucunu okuyamaz. Eylem kapsamını daraltmak onun saklanan işlemini okumayı da önler. Operatörü kaldırıp yeniden başlatmak kimlik bilgisini iptal eder. Eski günlük kayıtları dururken operatör ID’sini başka aktör için kullanmayın.

## Başka oynatıcıda sürdürme

```sh
ott handoff living-room bedroom
```

Kaynak sürüm 1 canlı/arşiv başvurusu dışa aktarır: sağlayıcı, opak kaynak kimliği, opak `channel-v1:<8 hex>.<8 hex>` öğe parmak izi ve arşiv için geçerli program zamanı. Ham sağlayıcı ID’leri ve akış URL’leri yerelde kalır. Hedef **aynı yapılandırılmış kaynağa/hesaba** sahip olmalı, kendi kataloğunda tek eşleşen öğeyi çözmeli ve kendi yerel kimlik bilgileriyle yeni URL almalıdır. Denetleyici oynatılabilir URL kopyalamaz veya hedef kimlik bilgilerini değiştirmez. Kaynak oynatmayı sürdürür. Başarılı CLI sonucu hedefin isteği gönderdiği anlamına gelir; fiziksel ekranının veya hoparlörlerinin doğrulandığını göstermez.

Sürüm 1, oynatıcı gerekli kimlikleri sağlayabildiğinde `m3u`, `xtream` ve `stalker` canlı/arşiv kataloglarını destekler. Bilinmeyen/belirsiz öğeler, eşleşmeyen kaynaklar ve yerel politika reddi, ada göre yedek eşleştirme olmadan başarısız olur. VOD/diziler, farklı hesap eşlemesi, kaynağı duraklatma ve sürekli geçmiş/favori senkronizasyonu bu ilk sözleşmede yoktur.

## Doğrulama ve kurtarma alıştırmaları

`go test -race ./...`, `go vet ./...` ve `python3 -m unittest discover -s tests` çalıştırın. Testler başarılı yazmadan sonra kaybolan yanıtları, sürdürme öncesi tam karşılaştırmayı, yapılandırma sapmasını, bilinmeyen işlem son sürelerini, iki yazıcıyı, bozuk günlükleri, operatörler arası okumaları ve bozuk devir yüklerini kapsar. Hazırlık oynatıcısında ayrıca ayar kaydından sonra CLI’yi kesin, denetleyiciyi yeniden başlatın, sürdürün ve doğrulayın; eşleşen ve eşleşmeyen kaynaklar arasında devir deneyin. Görüntü/sesi ayrıca doğrulayın.

Güncel FOSS çalışma kopyası ve geliştirme bağımlılıkları kurulu durumdayken yerel depolar arası doğrulama için çalıştırın:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

Duman testi bu Go sunucusunu derler, tüm FOSS TypeScript projesini derler ve gerçek aktarımını, komut işleyicilerini, kaynak kimliğini ve dağıtılan çalma listesi kodeğini Python CLI’ye karşı çalıştırır. Desteklenen üç sağlayıcının tam ayarlarını, uyuşmazlıkları, farklı yerel satır ID’leriyle canlı devri, arşiv arama ofsetlerini, URL çözümleme hatalarını ve sıkı aktarım biçimi doğrulamasını denetler. Yalnızca depolama, sağlayıcı politikası ve oynatma ana makinesi arayüzleri test taklitleridir; tarayıcıyı, fiziksel medya çıkışını veya gerçek hesap erişimini doğrulamaz. Loopback’e bağlanır, özel geçici dosyalar kullanır, akış URL’lerine erişmez ve çıkışta dosyalarını/sunucusunu kaldırır.

</div>
