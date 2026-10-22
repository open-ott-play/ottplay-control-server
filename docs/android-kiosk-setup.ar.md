<div lang="ar" dir="rtl">

# جهاز أندرويد اللوحي: كشك Plex الصارم وبدء التشغيل

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[دليل ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [مدير USB](android-usb-manager.md)

يحتاج الجهاز اللوحي المخصص إلى ثلاثة إعدادات تُتحقق منها مستقلة: قائمة المحتوى، وسياسة كشك OTT الصارمة، وبدء تشغيل أندرويد. تثبيت APK أو التحقق من إطفاء الشاشة واستئنافها لا يثبت بدء التطبيق بعد إعادة تشغيل النظام.

## تحديد الهوية والتثبيت

1. سجّل الجهاز الفعلي، واسم OTT المستعار/UUID، واسم مدير USB المستعار، والرقم التسلسلي والطراز وAndroid ID وSDK. قد تختلف أسماء OTT وUSB؛ لا تستنتج الهوية من اسم ولا تعِد استخدام رمز جهاز آخر.
2. استخدم نسخة APK الرسمية المتوافقة مع SDK، وتحقق من SHA-256 المنشور وهوية الحزمة والتوقيع، وثبّت باستخدام `install -r`. احتفظ ببيانات المشغّل. استخدم [المدير المشترك](android-usb-manager.md) حين يملك USB؛ لا تشغّل خادم ADB منافساً أو أوامر `adb` دون تحديد الجهاز.
3. اقرن المشغّل ببيانات اعتماده الخاصة بالمتحكّم وتحقق باستخدام `ott TABLET`. حمّل الإعداد المسبق المسمّى المطلوب ونفّذ `ott TABLET load PRESET verify`. إعداد الشبكة المنزلية وإعداد localhost لجهاز التطوير غير قابلين للتبادل.

## التحقق من Plex من الجهاز اللوحي

<div dir="ltr">

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

</div>

تحقق من معرّف المكتبة وعنوانها وعدد عناصرها. نجاح طلب من حاسوب المسؤول لا يثبت اتصال الجهاز اللوحي. عند الفشل افحص عنوانه المحفوظ والتوجيه ودعم TLS وبيانات الاعتماد. فضّل عنوان HTTPS الصالح للخادم. إذا تطلب برنامج ثابت قديم HTTP داخل LAN خاصة، فاحصره في هذا التثبيت وسجّل أن الرموز والوسائط تنتقل دون تشفير؛ لا تعدّل إعداداً مسبقاً مشتركاً أو تعطّل التحقق من الشهادات عالمياً. لا تنشر أبداً الرموز أو عناوين الوسائط الخاصة في التشخيص أو التوثيق.

يؤدي تغيير عنوان خادم Plex إلى مسح جذور المكتبات المختارة. اختر المزوّد **Plex** وافتح **Plex settings → Folders**، وحدد صف المكتبة المطلوبة (لا **Browse folders** وحده)، ثم **Ok** و**Save and open library**. في المشغّل `1.1.58-beta.19` قد تفتح بيانات الخادم وحدها منتقي المجلدات بينما يبقى `ready` بقيمة false؛ أكمل الاختيار قبل أوامر القائمة. لا تستبدل المزوّد بـM3U لمجرد الحصول على `ready: true`: قد تعمل قائمة Plex هناك، لكن `kiosk on --strict` قد يحفظ سياسة M3U منتظرة بلا وسائط مرتبطة. ذلك لا يقفل قائمة Plex ولا يستعيدها.

## بدء القائمة وقفلها

<div dir="ltr">

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

</div>

للقائمة المرتبة استخدم `plex library 'LIBRARY NAME'` بدلاً من `plex shuffle`. يختار Shuffle ترتيباً واحداً ويكرره الكشك. تحقق من العناصر المطلوبة و`enabled: true` و`strict: true` ومن ارتباط السياسة بقائمة Plex الصحيحة. سياسة `waiting` بلا قناة أو وسائط لا تمثل كشكاً مقبولاً. راقب عينتين على الأقل تتقدم فيهما مواضع التشغيل/عدادات الإطارات وافحص الشاشة. الإقرار أو وسم `playing` بلا تقدم غير كافٍ. اختبر الانتقال الطبيعي عند نهاية العنصر؛ إن كان اختبار دورة كاملة غير عملي فسجّل الحد صراحةً.

يتحكم الكشك الصارم بواجهة المشغّل. ليس تسجيلاً بصفة مالك جهاز أندرويد ولا دليلاً على حظر Home وRecents والإشعارات وSettings. احتفظ بمشغّل النظام ومسار صيانة بحضور مشغّل بشري.

## تمكين بدء التشغيل منفصلاً

ابنِ [OTT Home 0.2.0](../native/android-home/README.md) وتحقق منه مع الاحتفاظ بمفتاح التوقيع الحالي للتحديثات. ثبّت `play.ott.home` بجانب `play.ott.foss`. افتح `play.ott.home/.StartupActivity` وفعّل **Start OTT after reboot**. الإعداد معطّل افتراضياً. لا يغيّر ملف المشغّل الرسمي أو توقيع OTA أو بيانات الملفات التعريفية أو تعيين زر Home.

إذا وفّر البرنامج الثابت منتقي Home، يمكنك أيضاً اختيار **OTT Home → Always**. حيث لا يوجد المنتقي (بما فيه برنامج Fire القديم المختبَر)، يعمل السماح بالبدء بصورة مستقلة. تحتفظ التثبيتات التي ضُبط فيها Home الافتراضي مسبقاً بسلوكها. عطّل الآليتين للتراجع الكامل.

خيار `recover_on_boot` في مدير USB المشترك آلية ثالثة مستقلة: يحتاج NAS وكابل USB، ويعالج إقلاعاً جديداً فقط ولا يعيد تشغيل التطبيق المتعطل باستمرار. ليس بديلاً عن البدء المستقل. عند اختبار البدء المستقل اترك سياسة الاسترداد معطلة حتى لا تخفي فشل تشغيل التطبيق المرافق. احترم أقفال الشاشة الآمنة وافتحها بالطريقة المعتادة عند الطلب.

## القبول والتسليم

احفظ الأدلة سراً دون بيانات اعتماد في الإيصالات العامة:

- إصدارات APK وبصماتها ومطابقة الهوية الفعلية/OTT/USB والإعداد المسبق المختار.
- مكتبة Plex وعدد العناصر وترتيبها والسياسة الصارمة وتقدم التشغيل الأولي.
- معرّف الإقلاع قبل إعادة تشغيل حقيقية للنظام وبعدها؛ ثم `sys.boot_completed=1`.
- عدم تشغيل المشغّل يدوياً أو استرداده عبر USB بين إعادة التشغيل والملاحظة.
- نشاط OTT في المقدمة وإعادة الاتصال بالمتحكّم واستعادة القائمة/السياسة الصارمة وتقدم التشغيل. نتيجة `requested` من مستقبل الإقلاع تعني إرسال الطلب فقط.
- اختبار منفصل لزر الطاقة: الشاشة مطفأة والصوت متوقف ثم فتح عادي للقفل واستئناف.
- الحدود الدقيقة: ضرورة فتح القفل الآمن أو استرداد بمساعدة USB أو مخارج فعلية غير مختبرة أو أخطاء بث أو نفاد ذاكرة. لا تصف هذه الحالات بأنها مقبولة بالكامل.

قبل تسليم الجهاز التالي، كرر قائمة الفحص نفسها بدلاً من نسخ ادعاء نجاح الجهاز السابق. قد تحتاج أجهزة Fire القديمة إلى `hls.js` لـM3U بسبب تعطل HLS الأصلي للمصنّع؛ وهذا لا يثبت استقرار تشغيل Plex.

## الصيانة والتراجع

<div dir="ltr">

```sh
ott TABLET kiosk off
```

</div>

افتح **OTT Startup** وعطّل **Start OTT after reboot** وأعد اختيار Home الأصلي إن تغيّر. عند الحاجة أزل فقط `play.ott.home` عبر نقل USB المتحقق منه؛ احتفظ بـ`play.ott.foss` وبياناته. تعطيل البدء لا يغيّر القائمة ولا يعطّل الكشك تلقائياً.

</div>
