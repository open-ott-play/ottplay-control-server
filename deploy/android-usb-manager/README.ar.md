<div lang="ar" dir="rtl">

# صورة نقل USB لأندرويد

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

يحتوي هذا المجلد على قوالب النشر وملف [`Dockerfile`](Dockerfile) لحاوية النقل الخاصة بالمدير المشترك. يعمل مدير Python على مضيف USB. توفر الصورة ADB وPython وأدوات مهايئ الصيانة الذرية؛ ولا تحتوي على سجل أجهزة ثانٍ أو خدمة استرداد خلفية. راجع [دليل المشغّل](../../docs/android-usb-manager.md) للإعداد والأوامر والترحيل وحدود الاسترداد والتراجع.

من دون نسخة عمل Git، نزّل `ottplay-control-server-deployment.tar.gz` و`release-manifest.json` من [الإصدار](https://github.com/open-ott-play/ottplay-control-server/releases) نفسه. تحقق من بصمة الأرشيف واستخرجه إلى مجلد جديد كما يوضح [الحصول على حزمة النشر](../../docs/android-usb-manager.md#get-the-deployment-package). تضم الحزمة هذه الوصفة والبرامج النصية الستة للمضيف والطرف البعيد ضمن `tools/`؛ حافظ على بنية المجلدات. تفتقر الحزم الأقدم، ومنها `v0.1.5-beta.6`، إلى هذه البرامج وتحتاج بدلاً منها إلى أرشيف المصدر المطابق. لا تتضمن الحزمة صورة نقل USB مبنية. ملف OCI المنشور لخادم التحكم مخصص لغرض آخر.

## منشأ المصدر والملفات الناتجة

الأساس هو `python:3.12.14-slim-trixie`، مثبّت على `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. روجع الأساس وتعليمة APT وتثبيت `websocket-client==1.9.0` المقفل بالبصمة في [PR 128 عند المراجعة b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). حُفظ نص التعليمات لإتاحة إعادة استخدام ذاكرة بناء مؤقتة متاحة. تدعم حزمة WebSocket الاختيارية عملاء الصيانة العامة الحاليين؛ ويمكن لمهايئات CDP المرحّلة استخدام مكتبة Python القياسية وحدها أيضاً.

تحذف هذه الوصفة حزمة `pip` المثبتة بعد تثبيت ملف wheel المقفل، وتستبعد شيفرة المدير والاسترداد من PR 128، وتشغّل ADB في المقدمة وتعطّل فحوص الصحة الموروثة. إزالة المثبّت أثناء التشغيل تقلل سطح الهجوم المتاح؛ لكنها ليست دليلاً على خلو فحص الثغرات من النتائج.

كان ناتج PR 128 الذي خضع للمراجعة هو `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. يحتوي على نقطة دخول مختلفة وفحص صحة بنبضات دورية غير متوافق. إنه ملف مرجعي، وليس بناءً لهذا Dockerfile. لا تنسب معرّف صورته أو منشأ مصدره أو نتائج فحصه إلى هذه الوصفة.

مستودعات APT ليست مثبّتة على لقطات. لذلك قد تختار إعادة بناء نظيفة لاحقاً حزم نظام أحدث حتى مع بصمة الأساس وبصمة Python wheel نفسيهما. عند تأهيل بناء، سجّل مراجعة المصدر الدقيقة والباني/المنصة ومعرّف الصورة الناتج وإصدارات الحزم والفحص. يجب أن تستخدم إعدادات الإنتاج معرّف الصورة الثابت المؤهّل هذا أو بصمته.

## البناء دون إزعاج مضيف USB المشغول

يُفضّل البناء والفحص على باني منفصل. يحتاج المضيف الهدف إلى الصورة المؤهّلة فقط، لا إلى تنزيل الحزم أو سلسلة أدوات البناء.

عند إعادة استخدام باني محلي مجهّز، تحقق أولاً من توفر صورة الأساس الدقيقة وتخزين طبقات تثبيت APT وwheel مؤقتاً. تحميل الصورة النهائية لـPR 128 وحدها لا يثبت أياً من الشرطين. من جذر المستودع أو مجلد النشر المستخرج، تكون محاولة مقصودة بلا اتصال كما يلي:

<div dir="ltr">

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

</div>

ينطبق `--network=none` على خطوات البناء؛ وليس ضماناً عاماً بألا يتصل الباني بسجل صور عند تحديد الأساس. أكد تحديد الأساس محلياً قبل استعمال الإجراء على مضيف محدود الموارد. إذا غابت طبقة APT أو wheel، أوقف المحاولة بدلاً من السماح بصمت بإعادة بناء عبر الشبكة على مضيف USB.

يضمّن BuildKit وضع شبكة خطوة البناء في مفتاح الذاكرة المؤقتة. لذا قد لا تتطابق ذاكرة أنشئت بوضع الشبكة الافتراضي مع المحاولة غير المتصلة أعلاه. أعد استخدامها فقط بعد التحقق من الأساس الدقيق وسلسلة التعليمات، وتوقف إذا نُفذت أي خطوة تثبيت حزم بدلاً من الإبلاغ عن `CACHED`. تستخدم إزالة `pip` النهائية صراحةً `RUN --network=none`؛ ولا تحتاج شبكة حتى إن استخدمت الخطوات السابقة المخزّنة مؤقتاً وضعها الأصلي.

قبل تغيير إعدادات الإنتاج، افحص إصداري Python وADB، وافحص الصورة الناتجة نفسها، وتحقق من نقل الملفات الثنائية وتجهيز APK، ثم نفّذ اختبارات قبول هوية الجهاز وتفويض المفاتيح والسكون والإقلاع في الدليل. يجب اختبار اتصال ADB 34 بجهاز أندرويد أقدم؛ فرقم الإصدار وحده لا يثبت التوافق مع KitKat أو Fire. احتفظ بالصورة الحالية ونسخة النشر الاحتياطية الخاصة حتى لا تعود بحاجة إلى التراجع.

## ميزانية موارد التشغيل

يطبّق مدير المضيف، وليس Dockerfile هذا، ذاكرة 256 MiB ومجموع ذاكرة وتبديل 256 MiB وحصص CPU shares بقيمة 128، دون حصة صارمة للمعالج أو PID. يفرض `--no-healthcheck` و`--network none` ونظام ملفات جذرياً للقراءة فقط وإسقاط capabilities وتعيينات دقيقة لعقد USB. يمثل `/tmp` نظام tmpfs محدوداً يستهلك ميزانية الذاكرة نفسها. يستخدم مهايئ التجهيز كتابات بحجم 1 MiB ويحد الحمولة الواحدة إلى 128 MiB؛ وتتشارك العمليات قفل المدير.

يوثق Docker الفرق بين [ضوابط الذاكرة والتبديل وحصص المعالج](https://docs.docker.com/engine/containers/resource_constraints/) و[خيار `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).

</div>
