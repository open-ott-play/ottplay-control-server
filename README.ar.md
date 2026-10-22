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

<div dir="rtl" lang="ar">

[الوثائق / Wiki](docs/wiki/Home.ar.md)

خادم صغير تستضيفه بنفسك لإيصال أوامر التحكم عن بُعد إلى مشغّلات OTT-play. ترسل المشغّلات طلبات HTTP صادرة، لذا لا يحتاج التلفاز إلى فتح منفذ للاستماع. لكل مشغّل طابور ورمز وصول خاصان به؛ ويُستخدم رمز منفصل للمسؤول لإرسال الأوامر.

تضيف ميزة [التحكم الموثوق](docs/reliable-control.md) الاختيارية بيانات اعتماد للتكامل بصلاحيات محدودة، وحفظًا دائمًا لنتائج الأوامر ضمن حدود، وتطبيقًا قابلًا للاستئناف لإعدادات Local/Home مع التحقق الدقيق منها، وتسليمًا صريحًا بين البث المباشر وتشغيل الأرشيف. تتطلب هذه الميزات مراجعات لوحدة التحكم وCLI تحتوي هذا الدليل. ويتطلب التحقق الدقيق والتسليم مشغّلًا محدّثًا أيضًا. لا تتضمن مراجعة التثبيت القديمة المثبّتة أدناه هذه الميزات. يوضح [مخطط البنية](docs/assets/reliable-control.svg) أماكن بقاء الإعدادات وبيانات الاعتماد وحالة العمليات.

يعمل الخادم كملف Go تنفيذي محمول واحد، مع إصدار مثبّت من مكتبة DNS لاكتشاف الخدمات اختياريًا. تتوفر حزم الإصدار لأنظمة Linux بمعماريات amd64 وarm64 وARMv7؛ وmacOS على Intel وApple Silicon؛ وWindows بمعماريتي amd64 وarm64. تدعم صور الحاويات Linux على amd64 وarm64. يستخدم Kubernetes وk3s مخطط Helm المرفق.

## بدء الخادم

نزّل الأرشيف المناسب من [الإصدارات](https://github.com/open-ott-play/ottplay-control-server/releases)، وتحقّق منه باستخدام بيان الإصدار، ثم فك ضغطه. على Windows استخدم `ottplay-control-server.exe` في الأوامر التالية.

</div>

<div dir="ltr">

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

</div>

<div dir="rtl" lang="ar">

ينشئ `init` بيانات اعتماد عشوائية ومختلفة في ملف جديد لا يسمح على Unix إلا للمالك بالوصول إليه. يرفض الكتابة فوق ملف أو رابط رمزي موجود، ولا يطبع بيانات الاعتماد مطلقًا. حافظ على خصوصية الملف؛ وعلى Windows قيّد ACL بالحساب الذي يشغّل الخادم. يوثّق `config.example.json` الحقول ويتضمّن عمدًا رموزًا بديلة غير صالحة.

العنوان الافتراضي هو `0.0.0.0:8081`، أي جميع واجهات IPv4 بما فيها الشبكة المحلية. صِل أجهزة التلفاز بعنوان الخادم المحلي ومنفذه. تحتفظ الإعدادات القائمة بقيمة `listen` الصريحة؛ غيّرها إلى `0.0.0.0:8081` أو استخدم `--listen 0.0.0.0:8081` لإتاحة تثبيت كان مقصورًا على loopback. أضف **أصل صفحة المشغّل الدقيق** إلى `allowed_origins`، مثل `http://192.168.1.20:8443`. يجب تطابق المخطط واسم المضيف والمنفذ؛ لا تُلحق مسارًا. تسمح قائمة الأصول الفارغة بالعملاء غير المتصفحات فقط. تسري تغييرات الإعداد بعد إعادة التشغيل. تظل بيانات اعتماد الجهاز والمسؤول مطلوبة.

يُدعم HTTP لمتصفحات التلفاز القديمة على شبكة محلية موثوقة. استخدم HTTPS إذا عبر الاتصال شبكة غير موثوقة: `serve --config config.json --tls-cert server.crt --tls-key server.key`، أو أنهِ TLS عند وكيل عكسي. لا تستطيع صفحات المشغّل المفتوحة عبر HTTPS الاتصال بخادم HTTP عادي داخل المتصفح. تحتفظ التطبيقات الأصلية أيضًا بسياسة النقل الخاصة بنظام التشغيل؛ ولا يخفف هذا الإعداد تلك السياسة.

يتحقق HTTPS المباشر من سلسلة الشهادات المقدمة قبل فتح المستمع: يجب ألا تقل مفاتيح RSA عن 2048 بت، أو ECDSA عن 224 بت؛ ويدعم Ed25519 أيضًا. استبدل الشهادة ذات المفتاح القصير قبل الترقية. راجع [سياسة شهادات TLS ونطاقها](docs/security-design.md#direct-https-certificate-policy).

## توصيل المشغّل

استخدم إصدار مشغّل يتضمن الاتصال بخادم الأوامر. افتح **الإعدادات → التحكم عن بُعد** وأدخل:

1. **عنوان الخادم:** عنوان IP أو اسم المضيف، مع منفذ اختياري، أو عنوان `http://` / `https://` كامل. يستخدم اسم المضيف وحده HTTP على المنفذ 8081 افتراضيًا.
2. **رمز الوصول:** قيمة `token` الخاصة بسجل هذا المشغّل في `devices`، وليس `admin_token` أبدًا.
3. **اتصال:** فعّل الاستطلاع وتحقّق من الحالة في مربع الحوار نفسه.

يحفظ المشغّل هذا الاتصال في التثبيت الحالي فقط ويستبعده من نسخ الإعدادات الاحتياطية والنقل السحابي. لا يحتاج إلى مستمع HTTP المحلي المنفصل. تتطلب متصفحات الويب أصل CORS مسموحًا؛ ويمكن لصفحات التلفاز المعبأة ذات `Origin: null` استخدام الإعداد الصريح `allow_null_origin` للمسارات المحددة للأجهزة والتهيئة الأولية. لا يتيح ذلك مسارات المسؤول أو مشغّل التشخيص.

## جهاز تحكم في الطرفية

يميّز [دليل Android OTA المصوّر](docs/android-ota.md) تحديثات Capacitor العادية عبر Wi-Fi (`ott DEVICE update --variant api22|full`، أحدث بيتا افتراضيًا؛ ويختار `--version` إصدارًا) عن الوكيل المنفصل ذي صلاحيات root. ويشرح تحديثات الوكيل وAPK/helper الموقّعة، والاستعادة، والتحقق من النتيجة المثبّتة.

يغطي [دليل Android ADB](docs/android-adb.md) الوصول عبر USB بواسطة محطة عمل أو Synology، والتحكم بالشاشة ولوحة المفاتيح، ولقطات الشاشة، والسجلات، وتثبيت APK، وإعادة التشغيل العادية، بما في ذلك قيود الأجهزة اللوحية القديمة.

يتيح [اكتشاف DNS-SD والاقتران](docs/discovery.md) الاختياريان للمشغّلات العثور على وحدة التحكم عبر نطاق الشبكة والحصول على رمز جهاز فردي بعد `ott approve NAME CODE`. لا يحتوي DNS إلا على بيانات الخدمة الوصفية. تواصل الاتصالات اليدوية القائمة العمل دون تفعيل الاكتشاف.

استخدم Python CLI لأسماء المشغّلات البديلة، ومستوى الصوت الحالي، والبحث عن القنوات، وEPG الحالي، واختيار المزوّد وإعداد المزوّدين المدعومين: [دليل CLI](docs/cli.md). يجب أن يتضمن الخادم والمشغّل امتداد الطلب/الاستجابة.

يُثبّت Python CLI من نسخة عمل للمصدر، منفصلًا عن أرشيف الخادم الأصلي. تغطي [خطوات التثبيت والإعداد](docs/cli.md#installation-and-connection) macOS/Linux وWindows، وإعداد Python/PATH، ووحدات CLI المطلوبة. تثبّت الخطوات التزام مصدر تطوير 0.1.1 خضع للمراجعة ويتضمن workbench والتحقق دون اتصال؛ لا يحتوي CLI المستقر v0.1.0 ولا أرشيفات الخادم الأصلي على هذه الأوامر. يمكنك [تحديث CLI أو الرجوع بإصداره](docs/cli.md#update-select-or-roll-back-the-cli) دون استبدال نسخة عمل أو مشغّل إطلاق أو إعداد خاص موجود. تستطيع مشغّلات الإطلاق المحلية استخدام [محلّل قوائم التشغيل](docs/cli.md#local-playlist-resolver) للقراءة فقط للبحث عن القنوات والبرامج الحالية والأرشيفات القابلة للتشغيل دون وحدة تحكم. بعد التثبيت والإعداد:

</div>

<div dir="ltr">

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

</div>

<div dir="rtl" lang="ar">

`a1` مثال لاسم بديل لمشغّل مسجّل، وليس اسم مضيف حاسوب. راجع [التسجيل والاقتران](docs/cli.md#register-and-connect-a-player)، و[جميع الأوامر مع أمثلة](docs/cli.md#commands)، و[قطع الاتصال وإلغاء الوصول](docs/cli.md#disconnect-deregister-or-revoke-a-player)، و[استكشاف الأخطاء](docs/cli.md#troubleshooting).

للمشغّل الذي يعمل باستمرار، استخدم [standby وgo](docs/cli.md#stop-playback-while-keeping-the-player-online) لإيقاف التشغيل وإيقاظه لاحقًا دون الخروج من التطبيق. يبقى `stop` اسمًا بديلًا لـ`standby`؛ ويبقى `start` و`wake` اسمين بديلين لـ`go`. يجب أن يظل التحكم عن بُعد متصلًا، لذلك لا يضمن الاستعداد انعدام حركة الشبكة. يعرض `ott a1` العمليات التي يعلنها المشغّل. قبول الأمر لا يؤكد اكتمال الإيقاف أو بدء التشغيل.

تتطلب [أوامر نسبة الأبعاد](docs/cli.md#aspect-ratio-fit-and-fill) تحديث CLI ووحدة التحكم ومشغّلًا يعلن قدرة `aspect`. يُطبَّق التغيير بعد الإقرار؛ اقرأ `aspect` مرة أخرى للتحقق من الوضع وما إذا كان قد حُفظ. يقتص Fill الصورة مع الحفاظ على التناسب بدل تمديدها.

تتطلب [لقطات الشاشة](docs/cli.md#remote-screenshots) عن بُعد تحديث CLI ووحدة التحكم ومشغّلًا بمحوّل التقاط مدعوم. تفعيل اتصال التحكم عن بُعد للمشغّل يجيز اللقطات والتشخيص لذلك المتحكم؛ ولا تحتاج اللقطات الأصلية إلى مفتاح إذن منفصل. ما زالت المتصفحات تحتاج إلى اختيار المصدر محليًا. قد تتضمن الصور إعدادات المشغّل وشاشات PIN وبيانات اعتماد ظاهرة، لذا اتصل فقط بوحدة تحكم موثوقة. يوضح `ott a1` التوفر وأي اختيار مصدر مطلوب. لقطات الشاشة منفصلة عن جمع القياسات المنظمة.

لخلط جميع فيديوهات VPortal المطابقة لعنوان وتشغيلها بتكرار:

</div>

<div dir="ltr">

```sh
ott l vpr "wedding"
```

</div>

<div dir="rtl" lang="ar">

يخلط `vpr` التحديد الكامل مرة واحدة ثم يكرر ذلك الطابور. أعد تشغيله للحصول على ترتيب جديد. يشغّل `vp` النتائج بترتيب الفهرس؛ ويسردها `vp --list` دون تغيير التشغيل. راجع [أوامر VPortal](docs/cli.md#commands).

لتسلسل Plex محدد صراحةً باستخدام إعداد Plex المحفوظ في المشغّل:

</div>

<div dir="ltr">

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

</div>

<div dir="rtl" lang="ar">

ينتظر CLI حتى 45 ثانية افتراضيًا. تجعل كتابة `ott -t 45 l plex shuffle "Tom and Jerry"` مهلة الاستجابة نفسها صريحة؛ ولا تحد مدة تشغيل الفيديوهات. قد يستغرق جمع مكتبات Plex الكبيرة أكثر من مهلة قصيرة تبلغ 10–15 ثانية. ضع `-t SECONDS` أو `--timeout SECONDS` قبل الاسم البديل للمشغّل. راجع [مهل مكتبات Plex](docs/cli.md#plex-libraries-by-name) لأمثلة المعاينة والتصرف عند انتهاء مهلة طلب تشغيل.

تتطلب طوابير Plex وجود CLI ووحدة تحكم ومشغّل يطبقون `plex_queue`. يخلط `plex play --shuffle ID...` المعرّفات المقدمة مرة واحدة. يتوقف الطابور بعد آخر عنصر ما لم تكرره سياسة كشك قائمة. ما دام الطابور موجودًا، يعمل `next` و`prev` العاديان عليه؛ ويمسحه `plex stop`. لا تبدّل المعاينة المزوّد ولا تستبدل التشغيل الحالي أبدًا. راجع [أوامر طابور Plex](docs/cli.md#ordered-plex-queues) للحدود والجاهزية. يتطلب البحث عن المكتبات أيضًا تحديث المشغّل ووحدة التحكم وإعلانهما `plex_library`؛ ويستخدم اتصال Plex المحفوظ في المشغّل ويرفض الاختيارات الغامضة أو غير المكتملة. راجع [مكتبات Plex بالاسم](docs/cli.md#plex-libraries-by-name).

### حفظ ملفات تعريف الفيديو المحلي

مع مشغّل ووحدة تحكم حديثين، تستخدم الملفات/المجلدات المحلية المزوّد `offline`:

</div>

<div dir="ltr">

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

</div>

<div dir="rtl" lang="ar">

اربط الملفات/المجلدات على المشغّل أولًا. يسجّل الحفظ إعداداتها والفيديو المختار دون بدء التشغيل. ينقل التصدير الأوصاف فقط؛ تبقى الوسائط وأذونات نظام التشغيل/المتصفح على الجهاز. يحتاج التثبيت الآخر إلى ربط محلي، ولا يعني التحميل تأكيد التشغيل. راجع [ملفات تعريف الفيديو المحلي](docs/cli.md#local-videos-profiles) للإعداد والإمكانات وحدود التنسيق المحمول واستكشاف الأخطاء.

### حفظ العنصر الحالي عبر ملفات التعريف

</div>

<div dir="ltr">

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

</div>

<div dir="rtl" lang="ar">

تبقى المفضلات على المشغّل الهدف بعد عمليات إعادة التشغيل العادية. يؤدي اختيار إحداها إلى تحميل المزوّد/ملف التعريف المحفوظ وحل رابط جديد؛ ويتسبب المصدر المتغير أو العنصر المفقود في فشل دون استبداله بقناة أو فيلم آخر. يجب أن يدعم CLI ووحدة التحكم والمشغّل `favorites`؛ ويعرض `ott l` العمليات المتاحة. راجع [المفضلات عبر ملفات التعريف](docs/cli.md#favorites-across-profiles) للأسماء البديلة والحدود ومعنى الإقرار واستكشاف الأخطاء.

### تحميل إعداد محفوظ

خزّن الإعدادات المسماة في القسم الخاص `presets` من `~/.config/ottplay-control/cli.json`. يحتوي كل إعداد ملفات تعريف M3U مرقّمة (قائمة التشغيل وساعات الأرشيف)، وبيانات اعتماد Plex، وملف M3U الذي يُختار عند انتهاء التحميل. راجع [مثال الإعداد](docs/cli.md#named-setups).

</div>

<div dir="ltr">

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

</div>

<div dir="rtl" lang="ar">

يتحقق CLI من الإعداد كاملًا أولًا، ثم ينتظر إقرار المشغّل قبل إرسال كل تغيير تالٍ. يعالج تحميل المزوّد، ويحفظ Plex وخانات M3U، ويختار ملف M3U المحدد. يوقف الخطأ التسلسل ويبلّغ بالخطوات المؤكدة؛ ولا تُكرر كتابة غير مؤكدة تلقائيًا مطلقًا. لا حاجة إلى خدمة ملفات تعريف إضافية أو إعادة تشغيل المتحكم. حافظ على خصوصية هذا الإعداد: فهو يحتوي بيانات اعتماد المزوّدين.

## إرسال أمر

يقرأ هذا المثال رمز المسؤول من الإعداد المحلي الخاص دون وضعه في URL أو معاملات سطر الأوامر:

</div>

<div dir="ltr">

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

</div>

<div dir="rtl" lang="ar">

تختار الأوامر الأخرى قناة برقمها أو اسمها، أو قناة عشوائية، أو تبدّل المزوّدين، أو تعرض رسالة، أو تغيّر قائمة M3U عند دعمها، أو تغادر المشغّل. يعتمد التوفر على منصة المشغّل والمزوّد الحالي. راجع [عقد API](docs/api.md).

على مشغّل يدعم مزوّد VPortal المستقل، يختار `ott a1 provider
vportal` ملفات التعريف الخمسة عشر الخاصة به. استخدم `profile N vportal LINK` أو `profile N
name NAME` أو ملفًا خاصًا `profile-config N FILE` يحتوي `name` و`vportal`، ثم اختر الخانة المعدة عبر `profile N`. تبقى ملفات M3U الحالية منفصلة. يبلّغ `profiles` بالبيانات الوصفية دون كشف روابط الحساب أو المفاتيح.

بعد بدء فيلم أو طابور حلقات VPortal، يقفل `ott a1 kiosk on` ذلك الاختيار. يحتفظ المشغّل بالطابور وموضعه عبر إعادة التحميل ويحل عناوين الوسائط مجددًا للاستعادة. استخدم `kiosk off` لاختيار عنوان آخر أو تغيير ملفات التعريف، ثم `kiosk on` للقفل. يعرض إيصال الكشك العنوان وعدد الحلقات؛ وتبقى طلبات المزوّد وعناوين التدفقات على المشغّل.

استخدم `ott a1 kiosk on --strict` لتشغيل موجّه للأطفال: تُظهر لمسة محلية الشريط السفلي للفيديو. تسمح مشغّلات core المحدّثة بلمس خطه الزمني أو سحبه للتنقل داخل حلقة VPortal/Plex الحالية؛ وتبقى بقية أدوات التحكم المحلية مقفلة. تحتفظ المشغّلات القديمة وأكشاك القنوات والأغلفة التي تعترض إيماءات الخط الزمني بشريط للقراءة فقط. يحتفظ القفل القائم بهدفه. حرّره من المتحكم عبر `ott a1 kiosk off`. على مواقع Android، تتطلب الحماية من الخروج عبر النظام تثبيت التطبيق منفصلًا باستخدام PIN؛ راجع [وضع الكشك](docs/cli.md#kiosk-mode).

## التشخيص عن بُعد

ابدأ بـ[دليل workbench السريع للقراءة فقط](docs/workbench.md#quick-start) لأوامر `doctor` و`inspect` للواجهة/الوسائط، والبحث عن إيصالات العمليات، وحزم الأدلة، و`health`، و`media-progress` للويب/التنفيذ الأصلي، و`report verify` دون اتصال. يستخدم إعداد CLI المسؤول والأسماء البديلة القائمة. المسار الأصلي مخصص لوكيل صيانة Android المُجهّز منفصلًا؛ وتستخدم مشغّلات Tauri فحص الويب. تميّز التقارير تقدم فك الترميز من التحقق الفعلي للشاشة/الصوت. راجع [استكشاف أخطاء workbench](docs/workbench.md#troubleshooting) للقدرات المفقودة والمسارات غير المتاحة والأدلة غير المكتملة.

توفر [تشخيصات البروتوكول 2](docs/remote-diagnostics.md) الاختيارية جلسات تُفعّل صراحةً لكل بيئة تشغيل، ببيانات اعتماد منفصلة ومحدودة للمشغّل وقياسات منظمة محدودة. يوفر اتصال المشغّل المفعّل التفويض المحلي دون سؤال ثقة جديد؛ وتظل لكل عملية التقاط مدة جلسة محدودة. استطلاع تحكم التشخيص مستقل عن طابور الأوامر الحالي.

يغطي [دليل CLI التشخيص وMCP](docs/diagnostics-cli.md) التثبيت وبيانات اعتماد المشغّل وإعداد الاتصال وكل أمر/أداة تشخيص وأمثلة الالتقاط والإصلاح وإلغاء وصول بيئة التشغيل واستكشاف الأخطاء. يسرد `ott diagnostics --help` هذه العائلة المنفصلة من الأوامر؛ ولا تمنح الأسماء البديلة العادية أو بيانات اعتماد المسؤول وصولًا تشخيصيًا.

## حزم النشر

- [جهاز لوحي Android: كشك Plex الصارم وبدء التشغيل](docs/android-kiosk-setup.md)
- [إدارة Android USB المشتركة واستعادة الإقلاع](docs/android-usb-manager.md)
- [Docker / Compose وKubernetes وk3s وsystemd وmacOS وWindows](docs/deployment.md)
- [الإعداد ودلالات التسليم والحدود](docs/api.md)
- [عملية الإصدار والتحقق](docs/development.md)

الطوابير محدودة، وتنتهي افتراضيًا بعد 60 ثانية، وتُحفظ في الذاكرة. تؤدي إعادة تشغيل الخادم إلى إسقاط الأوامر المعلقة. شغّل نسخة واحدة؛ فالنسخ المستقلة لا تتشارك الطوابير. يقرّ المشغّل بمحاولة إرسال، لا بتشغيل مؤكد أو مستوى صوت فعلي للعتاد. تُزال تكرارات محاولات الشبكة داخل جلسة مشغّل نشطة، وليس عبر إعادة تشغيل التطبيق.

يتبع هذا الخادم API الأوامر للمثال الاختياري `local_proxy.py` في [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss)، مع بيانات اعتماد منفصلة للمسؤول/الجهاز وتسليم مؤكّد. يبقى الوكيل القديم مكوّنًا منفصلًا. لا يوكّل هذا المشروع الوسائط ولا يغيّر اتصالات HLS أو EPG أو مزوّدي المشغّل.

## الترخيص

MIT. تُسجّل نسب أدوات الإصدار المضمّنة في `NOTICE` و`LICENSES/`.

## صيانة المشروع

راجع [متطلبات المساهمة والاختبار](CONTRIBUTING.md)، و[سياسة الإبلاغ الأمني](SECURITY.md)، و[التصميم الأمني](docs/security-design.md)، و[أدلة OpenSSF والمعايير المتبقية](docs/openssf-evidence.md).

تقبل طوابير Plex حتى 500 معرّف في المشغّلات الحالية. استخدم `ott f10 plex play --shuffle ID...` للخلط مرة واحدة، ثم `ott f10 kiosk on --strict` مع اختيار مزوّد Plex لتكرار الطابور وقفله. يحرر `ott f10 kiosk off` القفل. تحتفظ المشغّلات القديمة بحدود الطابور التي تعلنها.

## الاختبار التشويشي لمرشحي الإصدار

يشغّل مرشحو الإصدار العامون اختبارًا تشويشيًا أصليًا محدودًا على حدود الإدخال الموصوفة في [دليل الاختبار التشويشي](docs/fuzzing.md). لا تعمل حملات الطفرات على PR أو إصدارات بيتا أو البناء الليلي. يغطي الدليل النطاق والميزانيات والأدلة وإعادة إنتاج الانحدارات.

## المجتمع وأدلة الأمان

راجع [المساهمة](CONTRIBUTING.md)، و[الحوكمة](GOVERNANCE.md)، و[قواعد السلوك](CODE_OF_CONDUCT.md)، و[خارطة الطريق](ROADMAP.md)، و[أدلة OpenSSF](docs/openssf-evidence.md). يميّز فهرس الأدلة الملفات الأمنية المدعومة ونتائج الاختبارات الفعلية ومتطلبات Silver المتبقية.

</div>
