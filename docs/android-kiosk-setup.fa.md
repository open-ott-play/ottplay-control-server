<div lang="fa" dir="rtl">

# تبلت اندروید: کیوسک سخت‌گیرانهٔ Plex و راه‌اندازی

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[راهنمای ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [مدیر USB](android-usb-manager.md)

تبلت اختصاصی به سه تنظیمِ جداگانه بررسی‌شده نیاز دارد: صف محتوا، سیاست کیوسک سخت‌گیرانهٔ OTT و راه‌اندازی اندروید. نصب APK یا بررسی خاموش‌شدن صفحه/ادامه، راه‌اندازی پس از بازراه‌اندازی سیستم‌عامل را ثابت نمی‌کند.

## شناسایی و نصب

1. تبلت فیزیکی، نام مستعار/UUID در OTT، نام مستعار مدیر USB، شماره‌سریال، مدل، Android ID وSDK را ثبت کنید. نام‌های OTT وUSB ممکن است متفاوت باشند؛ هویت را از نام نتیجه نگیرید و توکن تبلت دیگری را به کار نبرید.
2. گونهٔ رسمی APK سازگار با SDK را بگیرید، SHA-256 انتشار و هویت بسته/امضا را بررسی و با `install -r` نصب کنید. داده‌های پخش‌کننده را نگه دارید. اگر USB در اختیار [مدیر مشترک](android-usb-manager.md) است، از آن استفاده کنید؛ سرور ADB رقیب یا دستورهای `adb` بدون تعیین دستگاه اجرا نکنید.
3. پخش‌کننده را با اطلاعات اختصاصی کنترل‌کنندهٔ خودش جفت و با `ott TABLET` بررسی کنید. پیش‌تنظیم نام‌دار موردنظر را بارگذاری و `ott TABLET load PRESET verify` را اجرا کنید. پیش‌تنظیم شبکهٔ خانگی و localhost رایانهٔ توسعه جایگزین هم نیستند.

## بررسی Plex از تبلت

<div dir="ltr">

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

</div>

شناسه، عنوان و تعداد اقلام کتابخانه را بررسی کنید. موفقیت درخواست از رایانهٔ مدیر اتصال تبلت را ثابت نمی‌کند. هنگام خطا آدرس ذخیره‌شده، مسیریابی، پشتیبانی TLS و اطلاعات ورود را روی تبلت بررسی کنید. نقطهٔ پایانی HTTPS معتبر سرور را ترجیح دهید. اگر firmware قدیمی HTTP در LAN خصوصی لازم دارد، آن را به همین نصب محدود کنید، انتقال بدون رمزنگاری توکن و رسانه را ثبت کنید و پیش‌تنظیم مشترک یا بررسی سراسری گواهی را تغییر ندهید. هرگز توکن یا URL رسانهٔ خصوصی را در عیب‌یابی یا مستندات منتشر نکنید.

تغییر آدرس سرور Plex ریشه‌های کتابخانهٔ انتخاب‌شده را پاک می‌کند. ارائه‌دهندهٔ **Plex** را انتخاب، **Plex settings → Folders** را باز و ردیف کتابخانهٔ مطلوب را علامت بزنید (نه فقط **Browse folders**)، سپس **Ok** و**Save and open library** را بزنید. در پخش‌کنندهٔ `1.1.58-beta.19` اطلاعات سرور به‌تنهایی ممکن است انتخاب‌گر پوشه را باز کند ولی `ready` همچنان false بماند؛ پیش از دستورهای صف انتخاب را کامل کنید. فقط برای رسیدن به `ready: true` به M3U نروید: صف Plex شاید آنجا پخش شود ولی `kiosk on --strict` می‌تواند سیاست M3U منتظر و بدون رسانهٔ پیوندخورده ذخیره کند. این کار صف Plex را قفل یا بازیابی نمی‌کند.

## شروع و قفل صف

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

برای صف مرتب، به‌جای `plex shuffle` از `plex library 'LIBRARY NAME'` استفاده کنید. Shuffle یک بار ترتیب را انتخاب می‌کند و کیوسک همان را تکرار می‌کند. اقلام مطلوب، `enabled: true`، `strict: true` و اتصال سیاست به صف درست Plex را تأیید کنید. سیاست `waiting` بدون کانال یا رسانه کیوسک پذیرفته‌شده نیست. دست‌کم دو نمونه با پیشرفت موقعیت/شمارندهٔ فریم ببینید و نمایشگر را بررسی کنید. تأیید یا برچسب `playing` بدون پیشرفت کافی نیست. گذار طبیعی در پایان قلم را آزمایش کنید؛ اگر بررسی دور کامل عملی نیست، این حد را صریح بنویسید.

کیوسک سخت‌گیرانه رابط پخش‌کننده را کنترل می‌کند. ثبت مالک دستگاه اندروید یا اثبات مسدودشدن Home، Recents، اعلان‌ها وSettings نیست. راه‌انداز سیستم و مسیر نگهداری با حضور اپراتور را حفظ کنید.

## فعال‌کردن مستقل راه‌اندازی

[OTT Home 0.2.0](../native/android-home/README.md) را بسازید و بررسی کنید و کلید امضای فعلی را برای به‌روزرسانی نگه دارید. `play.ott.home` را کنار `play.ott.foss` نصب کنید. `play.ott.home/.StartupActivity` را باز و**Start OTT after reboot** را فعال کنید. این گزینه پیش‌فرض خاموش است. باینری رسمی، امضای OTA، داده‌های نمایه یا تخصیص دکمهٔ Home را تغییر نمی‌دهد.

اگر firmware انتخاب‌گر Home دارد، می‌توانید **OTT Home → Always** را نیز انتخاب کنید. بدون انتخاب‌گر (از جمله Fire قدیمی آزمایش‌شده)، اجازهٔ شروع پس از بوت مستقل عمل می‌کند. نصب‌هایی که قبلاً Home پیش‌فرض شده‌اند رفتار خود را حفظ می‌کنند. برای بازگشت کامل هر دو سازوکار را خاموش کنید.

`recover_on_boot` مدیر USB مشترک سازوکار مستقل سوم است: به NAS و کابل USB نیاز دارد، فقط بوت تازه را رسیدگی می‌کند و برنامهٔ ازکارافتاده را پیوسته راه نمی‌اندازد. جایگزین شروع مستقل نیست. در آزمون شروع مستقل، بازیابی را خاموش بگذارید تا اجرا نشدن برنامهٔ همراه را نپوشاند. به قفل امن صفحه احترام بگذارید و در صورت درخواست به روش معمول باز کنید.

## پذیرش و تحویل

شواهد را خصوصی ذخیره کنید و در رسید عمومی اطلاعات ورود نگذارید:

- نسخه‌ها و هش‌های APK، نگاشت هویت فیزیکی/OTT/USB و پیش‌تنظیم انتخابی.
- کتابخانهٔ Plex، تعداد/ترتیب اقلام، سیاست سخت‌گیرانه و پیشرفت پخش اولیه.
- شناسهٔ بوت پیش و پس از بازراه‌اندازی واقعی سیستم‌عامل؛ سپس `sys.boot_completed=1`.
- نبود اجرای دستی پخش‌کننده یا بازیابی USB میان بازراه‌اندازی و مشاهده.
- فعالیت OTT در پیش‌زمینه، اتصال دوبارهٔ کنترل‌کننده، بازیابی صف/سیاست و پیشرفت پخش. نتیجهٔ `requested` گیرندهٔ بوت فقط ارسال درخواست است.
- آزمون جداگانهٔ دکمهٔ توان: صفحه خاموش، صدا متوقف، بازکردن معمول قفل و ادامه.
- حدود دقیق: نیاز به بازکردن قفل امن، بازیابی با USB، خروج‌های فیزیکی آزمایش‌نشده، خطای جریان یا کمبود حافظه. این موارد را کاملاً پذیرفته‌شده ننامید.

پیش از تحویل تبلت بعدی، همین فهرست را تکرار کنید و موفقیت دستگاه قبلی را کپی نکنید. Fire قدیمی شاید برای M3U به `hls.js` نیاز داشته باشد چون HLS بومی سازنده ممکن است از کار بیفتد؛ این پایداری پخش Plex را ثابت نمی‌کند.

## نگهداری و بازگشت

<div dir="ltr">

```sh
ott TABLET kiosk off
```

</div>

**OTT Startup** را باز، **Start OTT after reboot** را خاموش و در صورت تغییر، Home اصلی را بازگردانید. در صورت نیاز تنها `play.ott.home` را از راه USB تأییدشده حذف کنید؛ `play.ott.foss` و داده‌هایش را نگه دارید. خاموش‌کردن راه‌اندازی، صف را تغییر نمی‌دهد و کیوسک را خودکار خاموش نمی‌کند.

</div>
