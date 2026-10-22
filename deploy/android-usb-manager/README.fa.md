<div lang="fa" dir="rtl">

# تصویر انتقال USB اندروید

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

این پوشه قالب‌های استقرار و [`Dockerfile`](Dockerfile) کانتینر انتقال مدیر مشترک را در بر دارد. مدیر Python روی میزبان USB اجرا می‌شود. تصویر، ADB وPython و ابزارهای رابط نگهداری اتمی را فراهم می‌کند؛ دفتر ثبت دستگاه دوم یا سرویس پس‌زمینهٔ بازیابی ندارد. برای آماده‌سازی، دستورها، مهاجرت، حدود بازیابی و بازگشت به [راهنمای اپراتور](../../docs/android-usb-manager.md) مراجعه کنید.

بدون نسخهٔ کاری Git، فایل‌های `ottplay-control-server-deployment.tar.gz` و`release-manifest.json` را از همان [انتشار](https://github.com/open-ott-play/ottplay-control-server/releases) بگیرید. چکیدهٔ بایگانی را بررسی و طبق [دریافت بستهٔ استقرار](../../docs/android-usb-manager.md#get-the-deployment-package) در پوشه‌ای تازه استخراج کنید. بسته شامل این دستور ساخت و شش اسکریپت میزبان/راه‌دور در `tools/` است؛ ساختار پوشه‌ها را نگه دارید. بسته‌های قدیمی، از جمله `v0.1.5-beta.6`، این اسکریپت‌ها را ندارند و به بایگانی منبع متناظر نیاز دارند. بسته شامل تصویر ساخته‌شدهٔ انتقال USB نیست. خروجی OCI انتشار سرور کنترل کاربرد دیگری دارد.

## منشأ منبع و خروجی‌ها

پایه `python:3.12.14-slim-trixie` است و روی `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b` تثبیت شده است. پایه، دستور APT و نصب قفل‌شده با هشِ `websocket-client==1.9.0` در [PR 128، بازبینی b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager) بررسی شده‌اند. متن دستورها برای استفادهٔ دوباره از کش ساخت موجود حفظ شده است. بستهٔ اختیاری WebSocket از کارخواه‌های عمومی نگهداری موجود پشتیبانی می‌کند؛ رابط‌های مهاجرت‌یافتهٔ CDP می‌توانند فقط از کتابخانهٔ استاندارد Python نیز استفاده کنند.

این دستور ساخت پس از نصب wheel قفل‌شده، بستهٔ نصب‌شدهٔ `pip` را حذف می‌کند، کد مدیر و بازیابی PR 128 را کنار می‌گذارد، ADB را در پیش‌زمینه اجرا و بررسی‌های سلامت ارث‌رسیده را غیرفعال می‌کند. حذف نصب‌کننده از محیط اجرا سطح حملهٔ در دسترس را کاهش می‌دهد؛ اثبات‌کنندهٔ اسکن بدون آسیب‌پذیری نیست.

خروجی بررسی‌شدهٔ PR 128 برابر `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a` بود. نقطهٔ ورود متفاوت و بررسی سلامت heartbeat ناسازگار دارد. خروجی مرجع است، نه ساخت همین Dockerfile. شناسهٔ تصویر، منشأ منبع یا نتایج اسکن آن را به این دستور ساخت نسبت ندهید.

مخزن‌های APT به snapshot تثبیت نشده‌اند. بنابراین ساخت پاک در آینده ممکن است حتی با چکیدهٔ پایه و هش wheel Python یکسان، بسته‌های جدیدتر سیستم‌عامل را انتخاب کند. هنگام تأیید ساخت، بازبینی دقیق منبع، سازنده/سکو، شناسهٔ تصویر نهایی، نسخهٔ بسته‌ها و اسکن را ثبت کنید. تنظیمات تولید باید همان شناسهٔ تغییرناپذیرِ تأییدشده یا چکیده‌اش را به کار برد.

## ساخت بدون اختلال در میزبان USB مشغول

ترجیحاً ساخت و اسکن را روی سازنده‌ای جدا انجام دهید. میزبان مقصد فقط به تصویر تأییدشده نیاز دارد، نه دریافت بسته‌ها یا زنجیرهٔ ابزار ساخت.

برای استفادهٔ دوباره از سازندهٔ محلی آماده، نخست وجود دقیق تصویر پایه و کش شدن لایه‌های نصب APT وwheel را بررسی کنید. بارگذاری فقط تصویر نهایی PR 128 هیچ‌یک از این دو شرط را ثابت نمی‌کند. از ریشهٔ مخزن یا پوشهٔ استقرار استخراج‌شده، تلاش عمداً آفلاین چنین است:

<div dir="ltr">

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

</div>

`--network=none` به گام‌های ساخت اعمال می‌شود؛ تضمین کلی نیست که سازنده هنگام یافتن پایه هرگز به رجیستری متصل نشود. پیش از اجرای این روش روی میزبان محدود، یافتن محلی پایه را تأیید کنید. اگر لایهٔ APT یا wheel موجود نیست، تلاش را متوقف کنید و بی‌سروصدا اجازهٔ بازسازی شبکه‌ای روی میزبان USB ندهید.

BuildKit حالت شبکهٔ گام ساخت را در کلید کش می‌گنجاند. پس کش ایجادشده در حالت شبکهٔ پیش‌فرض ممکن است در تلاش آفلاین بالا تطبیق نیابد. تنها پس از بررسی پایهٔ دقیق و زنجیرهٔ دستورها دوباره از آن استفاده کنید و اگر هر گام نصب بسته به‌جای اعلام `CACHED` اجرا شد، توقف کنید. حذف نهایی `pip` صریحاً از `RUN --network=none` استفاده می‌کند؛ حتی اگر گام‌های کش‌شدهٔ پیشین حالت اصلی را به کار برند، شبکه لازم ندارد.

پیش از تغییر تنظیمات تولید، نسخه‌های Python وADB را بررسی، همان تصویر نهایی را اسکن، انتقال فایل دودویی و آماده‌سازی APK را تأیید کنید؛ سپس آزمون‌های پذیرش هویت دستگاه، مجوز کلیدها، خواب و راه‌اندازی راهنما را انجام دهید. ارتباط ADB 34 با اندروید قدیمی باید آزموده شود؛ شمارهٔ نسخه به‌تنهایی سازگاری KitKat یا Fire را ثابت نمی‌کند. تصویر کنونی و پشتیبان خصوصی استقرار را تا زمانی که بازگشت دیگر لازم نیست نگه دارید.

## بودجهٔ منابع اجرا

مدیر میزبان، نه این Dockerfile، حافظهٔ 256 MiB، مجموع حافظه وswap برابر 256 MiB وCPU shares برابر 128 را بدون سهمیهٔ سخت CPU یا PID اعمال می‌کند. `--no-healthcheck`، `--network none`، فایل‌سیستم ریشهٔ فقط‌خواندنی، حذف capabilities و نگاشت دقیق گره‌های USB را الزام می‌کند. `/tmp` یک tmpfs محدود است که همان بودجهٔ حافظه را مصرف می‌کند. رابط آماده‌سازی در قطعات 1 MiB می‌نویسد و هر محموله را به 128 MiB محدود می‌کند؛ عملیات قفل مشترک مدیر را به کار می‌برند.

Docker تفاوت [کنترل‌های حافظه، swap و سهم‌های CPU](https://docs.docker.com/engine/containers/resource_constraints/) و[گزینهٔ `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck) را مستند کرده است.

</div>
