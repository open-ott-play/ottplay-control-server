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

<div dir="rtl" lang="fa">

[مستندات / Wiki](docs/wiki/Home.fa.md)

سروری کوچک برای میزبانی شخصی که فرمان‌های راه‌دور را به پخش‌کننده‌های OTT-play می‌رساند. پخش‌کننده‌ها درخواست HTTP خروجی می‌فرستند؛ بنابراین تلویزیون نیازی به بازکردن درگاه شنونده ندارد. هر پخش‌کننده صف و توکن دسترسی خود را دارد؛ توکن جداگانهٔ مدیر برای ارسال فرمان به کار می‌رود.

قابلیت اختیاری [کنترل قابل‌اعتماد](docs/reliable-control.md)، اطلاعات دسترسی یکپارچه‌سازی با دامنهٔ محدود، نگهداری پایدار اما محدود نتایج فرمان، اعمال ازسرگرفتنی تنظیمات Local/Home با بررسی دقیق تنظیمات و تحویل صریح میان پخش زنده و آرشیو را اضافه می‌کند. این قابلیت‌ها به بازبینی‌هایی از کنترل‌کننده و CLI نیاز دارند که این راهنما را داشته باشند. بررسی دقیق و تحویل پخش به پخش‌کنندهٔ به‌روز هم نیاز دارند. نسخهٔ قدیمی تثبیت‌شده برای نصب در پایین، این قابلیت‌ها را ندارد. [نمودار معماری](docs/assets/reliable-control.svg) نشان می‌دهد تنظیمات، اطلاعات دسترسی و وضعیت عملیات کجا باقی می‌مانند.

سرور به‌صورت یک فایل اجرایی قابل‌حمل Go اجرا می‌شود و برای کشف اختیاری خدمات از نسخهٔ ثابت کتابخانهٔ DNS استفاده می‌کند. بسته‌ها برای Linux با معماری‌های amd64، arm64 و ARMv7؛ macOS با Intel و Apple Silicon؛ و Windows با amd64 و arm64 ارائه می‌شوند. تصویرهای کانتینر از Linux amd64 و arm64 پشتیبانی می‌کنند. Kubernetes و k3s از Helm chart همراه استفاده می‌کنند.

## راه‌اندازی سرور

آرشیو مناسب را از [انتشارها](https://github.com/open-ott-play/ottplay-control-server/releases) دریافت کنید، آن را با مانیفست انتشار بررسی و سپس استخراج کنید. در Windows در فرمان‌های زیر از `ottplay-control-server.exe` استفاده کنید.

</div>

<div dir="ltr">

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

</div>

<div dir="rtl" lang="fa">

`init` اطلاعات دسترسی تصادفی و متفاوتی در فایل تازه ایجاد می‌کند که در Unix فقط مالک می‌تواند به آن دسترسی داشته باشد. از بازنویسی فایل یا پیوند نمادین موجود خودداری می‌کند و هرگز اطلاعات دسترسی را چاپ نمی‌کند. فایل را خصوصی نگه دارید؛ در Windows، ACL آن را به حسابی محدود کنید که سرور را اجرا می‌کند. `config.example.json` فیلدها را توضیح می‌دهد و عمداً توکن‌های جایگزین نامعتبر دارد.

آدرس پیش‌فرض `0.0.0.0:8081` است؛ یعنی همهٔ رابط‌های IPv4، از جمله LAN. تلویزیون‌ها را با آدرس LAN و درگاه سرور وصل کنید. پیکربندی‌های موجود مقدار صریح `listen` خود را حفظ می‌کنند؛ برای در دسترس گذاشتن نصبی که قبلاً فقط روی loopback بود، آن را به `0.0.0.0:8081` تغییر دهید یا از `--listen 0.0.0.0:8081` استفاده کنید. **مبدأ دقیق صفحهٔ پخش‌کننده** را به `allowed_origins` اضافه کنید؛ برای نمونه `http://192.168.1.20:8443`. طرح، نام میزبان و درگاه باید یکسان باشند؛ مسیر اضافه نکنید. فهرست مبدأ خالی فقط کلاینت‌های غیرمرورگری را مجاز می‌کند. تغییر تنظیمات پس از راه‌اندازی مجدد اعمال می‌شود. اطلاعات دسترسی دستگاه و مدیر همچنان ضروری‌اند.

HTTP برای مرورگرهای قدیمی تلویزیون در LAN قابل‌اعتماد پشتیبانی می‌شود. اگر اتصال از شبکهٔ غیرقابل‌اعتماد عبور می‌کند، HTTPS به کار ببرید: `serve --config config.json --tls-cert server.crt --tls-key server.key`، یا TLS را در پراکسی معکوس خاتمه دهید. صفحه‌های HTTPS پخش‌کننده در مرورگر نمی‌توانند به سرور HTTP ساده متصل شوند. برنامه‌های بومی هم سیاست انتقال سیستم‌عامل را حفظ می‌کنند؛ این تنظیم آن را آسان‌تر نمی‌کند.

HTTPS مستقیم پیش از بازکردن شنونده، زنجیرهٔ گواهی ارائه‌شده را بررسی می‌کند: کلید RSA باید دست‌کم ۲۰۴۸ بیت و ECDSA دست‌کم ۲۲۴ بیت باشد؛ Ed25519 هم پذیرفته می‌شود. پیش از ارتقا، گواهی با کلید کوتاه را عوض کنید. [سیاست گواهی TLS و دامنهٔ آن](docs/security-design.md#direct-https-certificate-policy) را ببینید.

## اتصال پخش‌کننده

از انتشاری استفاده کنید که اتصال به سرور فرمان را دارد. **تنظیمات → کنترل از راه دور** را باز کنید و وارد کنید:

1. **آدرس سرور:** IP یا نام میزبان، با درگاه اختیاری، یا آدرس کامل `http://` / `https://`. نام میزبان تنها، به‌طور پیش‌فرض HTTP روی درگاه ۸۰۸۱ را به کار می‌برد.
2. **کد دسترسی:** مقدار `token` ورودی همان پخش‌کننده در `devices`؛ هرگز `admin_token` را وارد نکنید.
3. **اتصال:** پرس‌وجوی دوره‌ای را روشن کنید و وضعیت همان پنجره را بررسی کنید.

پخش‌کننده این اتصال را فقط در نصب فعلی ذخیره می‌کند و آن را در پشتیبان تنظیمات یا انتقال ابری نمی‌گنجاند. شنوندهٔ جداگانهٔ HTTP محلی لازم نیست. مرورگرهای وب به مبدأ CORS مجاز نیاز دارند؛ صفحه‌های بسته‌بندی‌شدهٔ تلویزیون با `Origin: null` می‌توانند تنظیم صریح `allow_null_origin` را برای مسیرهای مشخص دستگاه/راه‌اندازی اولیه به کار ببرند. این کار مسیرهای مدیر یا اپراتور عیب‌یابی را فعال نمی‌کند.

## کنترل از پایانه

[راهنمای مصور Android OTA](docs/android-ota.md) به‌روزرسانی معمول Capacitor از راه Wi-Fi را (`ott DEVICE update --variant api22|full`، پیش‌فرض تازه‌ترین بتا؛ `--version` انتشار را انتخاب می‌کند) از عامل جداگانه با دسترسی root متمایز می‌کند. به‌روزرسانی امضاشدهٔ عامل و APK/helper، بازیابی و بررسی نتیجهٔ نصب را توضیح می‌دهد.

[راهنمای Android ADB](docs/android-adb.md) دسترسی USB از راه ایستگاه کاری یا Synology، کنترل صفحه و صفحه‌کلید، عکس صفحه، گزارش‌ها، نصب APK و راه‌اندازی مجدد عادی، از جمله محدودیت تبلت‌های قدیمی را پوشش می‌دهد.

[کشف و جفت‌سازی DNS-SD](docs/discovery.md) اختیاری به پخش‌کننده‌ها اجازه می‌دهد کنترل‌کننده را از دامنهٔ شبکه بیابند و پس از `ott approve NAME CODE` توکن دستگاه اختصاصی بگیرند. DNS فقط فرادادهٔ سرویس دارد. اتصال‌های دستی موجود بدون فعال‌کردن کشف نیز کار می‌کنند.

Python CLI را برای نام‌های مستعار پخش‌کننده، بلندی صدای زنده، جست‌وجوی کانال، EPG جاری، انتخاب ارائه‌دهنده و تنظیم ارائه‌دهندگان پشتیبانی‌شده به کار ببرید: [راهنمای CLI](docs/cli.md). سرور و پخش‌کننده هر دو باید افزونهٔ درخواست/پاسخ را داشته باشند.

Python CLI از نسخهٔ کاری کد منبع، جدا از آرشیو سرور بومی نصب می‌شود. [مراحل نصب و تنظیم](docs/cli.md#installation-and-connection) macOS/Linux و Windows، راه‌اندازی Python/PATH و ماژول‌های لازم CLI را پوشش می‌دهد. این مراحل به یک commit بازبینی‌شدهٔ توسعهٔ 0.1.1 با workbench و بررسی آفلاین ثابت شده‌اند؛ CLI پایدار v0.1.0 و آرشیوهای سرور بومی این فرمان‌ها را ندارند. بدون جایگزینی نسخهٔ کاری، راه‌انداز یا تنظیمات خصوصی موجود، [CLI را به‌روز کنید یا برگردانید](docs/cli.md#update-select-or-roll-back-the-cli). راه‌اندازهای محلی می‌توانند با [حل‌کنندهٔ فهرست پخش](docs/cli.md#local-playlist-resolver) فقط‌خواندنی، کانال‌ها، برنامه‌های جاری و آرشیو قابل‌پخش را بدون کنترل‌کننده جست‌وجو کنند. پس از نصب و تنظیم:

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

<div dir="rtl" lang="fa">

`a1` نمونهٔ نام مستعار یک پخش‌کنندهٔ ثبت‌شده است، نه نام میزبان رایانه. [ثبت و جفت‌سازی](docs/cli.md#register-and-connect-a-player)، [همهٔ فرمان‌ها با مثال](docs/cli.md#commands)، [قطع اتصال و لغو دسترسی](docs/cli.md#disconnect-deregister-or-revoke-a-player) و [عیب‌یابی](docs/cli.md#troubleshooting) را ببینید.

برای پخش‌کننده‌ای که همیشه روشن است، با [standby و go](docs/cli.md#stop-playback-while-keeping-the-player-online) پخش را متوقف و بعداً بیدار کنید، بدون خروج از برنامه. `stop` همچنان نام مستعار `standby` است؛ `start` و `wake` نام‌های مستعار `go` هستند. کنترل از راه دور باید متصل بماند، پس آماده‌به‌کار به معنی ترافیک شبکهٔ صفر نیست. `ott a1` عملیات اعلام‌شدهٔ پخش‌کننده را نشان می‌دهد. پذیرش فرمان، پایان خاموش‌شدن یا شروع پخش را تأیید نمی‌کند.

[فرمان‌های نسبت تصویر](docs/cli.md#aspect-ratio-fit-and-fill) به CLI و کنترل‌کنندهٔ به‌روز و پخش‌کننده‌ای با قابلیت اعلام‌شدهٔ `aspect` نیاز دارند. تغییر پس از تأیید اعمال می‌شود؛ `aspect` را دوباره بخوانید تا حالت و ذخیره‌شدن آن را بررسی کنید. Fill تصویر را متناسب می‌بُرد، نه اینکه آن را بکشد.

[عکس صفحه](docs/cli.md#remote-screenshots) از راه دور به CLI و کنترل‌کنندهٔ به‌روز و پخش‌کننده‌ای با آداپتور ثبت پشتیبانی‌شده نیاز دارد. روشن‌کردن اتصال کنترل از راه دور، به آن کنترل‌کننده مجوز عکس صفحه و عیب‌یابی می‌دهد؛ عکس بومی به کلید مجوز جداگانه نیاز ندارد. مرورگرها همچنان انتخابگر محلی منبع را لازم دارند. تصاویر ممکن است تنظیمات، صفحه‌های PIN و اطلاعات دسترسی قابل‌دیدن را در بر بگیرند؛ فقط به کنترل‌کنندهٔ قابل‌اعتماد وصل شوید. `ott a1` دسترس‌پذیری و انتخاب منبع لازم را گزارش می‌کند. عکس صفحه از گردآوری تله‌متری ساختاریافته جدا است.

برای تصادفی‌کردن همهٔ ویدیوهای VPortal مطابق یک عنوان و پخش تکراری آن‌ها:

</div>

<div dir="ltr">

```sh
ott l vpr "wedding"
```

</div>

<div dir="rtl" lang="fa">

`vpr` کل انتخاب را یک بار تصادفی می‌کند و سپس آن صف را تکرار می‌کند. برای ترتیب تصادفی تازه، دوباره اجرا کنید. `vp` نتایج را به ترتیب فهرست پخش می‌کند؛ `vp --list` بدون تغییر پخش، آن‌ها را فهرست می‌کند. [فرمان‌های VPortal](docs/cli.md#commands) را ببینید.

برای ترتیب صریح Plex با تنظیمات Plex ذخیره‌شدهٔ پخش‌کننده:

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

<div dir="rtl" lang="fa">

CLI به‌طور پیش‌فرض تا ۴۵ ثانیه منتظر می‌ماند. نوشتن `ott -t 45 l plex shuffle "Tom and Jerry"` همان مهلت پاسخ را صریح می‌کند؛ مدت پخش ویدیوها را محدود نمی‌کند. گردآوری کتابخانه‌های بزرگ Plex ممکن است بیش از مهلت کوتاه ۱۰–۱۵ ثانیه طول بکشد. `-t SECONDS` یا `--timeout SECONDS` را پیش از نام مستعار پخش‌کننده قرار دهید. برای مثال پیش‌نمایش و اقدام هنگام پایان مهلت درخواست پخش، [مهلت کتابخانه‌های Plex](docs/cli.md#plex-libraries-by-name) را ببینید.

صف‌های Plex به CLI، کنترل‌کننده و پخش‌کننده با پیاده‌سازی `plex_queue` نیاز دارند. `plex play --shuffle ID...` شناسه‌های داده‌شده را یک بار تصادفی می‌کند. صف پس از آخرین مورد متوقف می‌شود، مگر سیاست کیوسک موجود آن را تکرار کند. تا وقتی صف وجود دارد، `next` و `prev` معمولی روی آن عمل می‌کنند؛ `plex stop` صف را پاک می‌کند. پیش‌نمایش هرگز ارائه‌دهنده را عوض نمی‌کند یا جای پخش جاری نمی‌گیرد. [فرمان‌های صف Plex](docs/cli.md#ordered-plex-queues) را برای محدودیت‌ها و آمادگی ببینید. جست‌وجوی کتابخانه همچنین به پخش‌کننده و کنترل‌کنندهٔ تازه با قابلیت `plex_library` نیاز دارد؛ از اتصال ذخیره‌شدهٔ Plex استفاده می‌کند و انتخاب مبهم یا ناقص را رد می‌کند. [کتابخانه‌های Plex بر اساس نام](docs/cli.md#plex-libraries-by-name) را ببینید.

### ذخیرهٔ پروفایل ویدیوهای محلی

با پخش‌کننده و کنترل‌کنندهٔ فعلی، فایل‌ها/پوشه‌های محلی از ارائه‌دهندهٔ `offline` استفاده می‌کنند:

</div>

<div dir="ltr">

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

</div>

<div dir="rtl" lang="fa">

ابتدا فایل‌ها/پوشه‌ها را روی پخش‌کننده متصل کنید. ذخیره، تنظیمات آن‌ها و ویدیوی انتخابی را بدون شروع پخش ثبت می‌کند. خروجی‌گرفتن فقط شرح‌ها را منتقل می‌کند؛ رسانه و مجوزهای سیستم‌عامل/مرورگر روی دستگاه می‌مانند. نصب دیگر به اتصال محلی نیاز دارد و بارگذاری به معنی تأیید پخش نیست. برای راه‌اندازی، قابلیت‌ها، محدودیت قالب قابل‌حمل و عیب‌یابی، [پروفایل ویدیوهای محلی](docs/cli.md#local-videos-profiles) را ببینید.

### ذخیرهٔ مورد جاری میان پروفایل‌ها

</div>

<div dir="ltr">

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

</div>

<div dir="rtl" lang="fa">

علاقه‌مندی‌ها پس از راه‌اندازی مجدد عادی روی پخش‌کنندهٔ هدف می‌مانند. انتخاب یکی از آن‌ها ارائه‌دهنده/پروفایل ذخیره‌شده را بار می‌کند و پیوند تازه می‌یابد؛ منبع تغییرکرده یا مورد گم‌شده خطا می‌دهد و با کانال/فیلم دیگری جایگزین نمی‌شود. CLI، کنترل‌کننده و پخش‌کننده باید `favorites` را پشتیبانی کنند؛ `ott l` عملیات موجود را نشان می‌دهد. [علاقه‌مندی میان پروفایل‌ها](docs/cli.md#favorites-across-profiles) را برای نام‌های مستعار، محدودیت‌ها، معنای تأیید و عیب‌یابی ببینید.

### بارگذاری تنظیمات ذخیره‌شده

تنظیمات نام‌گذاری‌شده را در بخش خصوصی `presets` از `~/.config/ottplay-control/cli.json` نگه دارید. هر مجموعه شامل پروفایل‌های شماره‌دار M3U (فهرست پخش و ساعت‌های آرشیو)، اطلاعات دسترسی Plex و پروفایل M3U انتخابی پس از اتمام بارگذاری است. [نمونهٔ تنظیمات](docs/cli.md#named-setups) را ببینید.

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

<div dir="rtl" lang="fa">

CLI ابتدا همهٔ تنظیمات را بررسی می‌کند و سپس پیش از هر تغییر بعدی منتظر تأیید پخش‌کننده می‌ماند. بارگذاری ارائه‌دهنده را مدیریت می‌کند، Plex و جایگاه‌های M3U را ذخیره و پروفایل تعیین‌شده را انتخاب می‌کند. خطا دنباله را متوقف و گام‌های تأییدشده را گزارش می‌کند؛ نوشتن با نتیجهٔ نامطمئن هرگز خودکار تکرار نمی‌شود. سرویس پروفایل اضافی یا راه‌اندازی مجدد کنترل‌کننده لازم نیست. این تنظیمات را خصوصی نگه دارید: اطلاعات دسترسی ارائه‌دهندگان را دارند.

## ارسال فرمان

این مثال توکن مدیر را از تنظیمات محلی خصوصی می‌خواند، بدون قراردادن آن در URL یا آرگومان‌های خط فرمان:

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

<div dir="rtl" lang="fa">

فرمان‌های دیگر کانال را با شماره یا نام انتخاب می‌کنند، کانال تصادفی می‌گزینند، ارائه‌دهنده را عوض می‌کنند، پیام نشان می‌دهند، در صورت پشتیبانی فهرست M3U را تغییر می‌دهند یا از پخش‌کننده خارج می‌شوند. دسترس‌پذیری به سکوی پخش‌کننده و ارائه‌دهندهٔ جاری بستگی دارد. [قرارداد API](docs/api.md) را ببینید.

در پخش‌کنندهٔ پشتیبان ارائه‌دهندهٔ مستقل VPortal، فرمان `ott a1 provider
vportal` پانزده پروفایل اختصاصی آن را انتخاب می‌کند. از `profile N vportal LINK`، `profile N
name NAME` یا فایل خصوصی `profile-config N FILE` شامل `name` و `vportal` استفاده کنید، سپس جایگاه تنظیم‌شده را با `profile N` برگزینید. پروفایل‌های M3U موجود جدا می‌مانند. `profiles` فراداده را بدون نمایش پیوند حساب یا کلیدها گزارش می‌کند.

پس از شروع فیلم یا صف قسمت‌های VPortal، `ott a1 kiosk on` آن انتخاب را قفل می‌کند. پخش‌کننده صف و جایگاهش را در بارگذاری‌های مجدد نگه می‌دارد و URL رسانه را برای بازیابی دوباره حل می‌کند. برای انتخاب عنوان دیگر یا تغییر پروفایل، `kiosk off` و سپس برای قفل‌کردن `kiosk on` را به کار ببرید. رسید کیوسک عنوان و تعداد قسمت‌ها را نشان می‌دهد؛ درخواست‌های ارائه‌دهنده و URL جریان‌ها روی پخش‌کننده می‌مانند.

برای پخش مخصوص کودکان از `ott a1 kiosk on --strict` استفاده کنید: لمس محلی نوار پایین ویدیو را نشان می‌دهد. پخش‌کننده‌های core تازه اجازه می‌دهند با لمس یا کشیدن خط زمان، در قسمت جاری VPortal/Plex جابه‌جا شوید؛ کنترل‌های محلی دیگر قفل می‌مانند. پخش‌کننده‌های قدیمی، کیوسک‌های کانال و پوشش‌هایی که حرکت خط زمان را می‌گیرند، نوار فقط‌خواندنی دارند. قفل موجود هدفش را حفظ می‌کند. آن را از کنترل‌کننده با `ott a1 kiosk off` باز کنید. در وب‌سایت‌های Android، جلوگیری از خروج سیستمی نیازمند سنجاق‌کردن جداگانهٔ برنامه با PIN است؛ [حالت کیوسک](docs/cli.md#kiosk-mode) را ببینید.

## عیب‌یابی از راه دور

از [شروع سریع workbench فقط‌خواندنی](docs/workbench.md#quick-start) برای `doctor`، بررسی رابط/رسانه با `inspect`، یافتن رسید عملیات، بسته‌های شواهد، `health`، نسخهٔ وب/بومی `media-progress` و `report verify` آفلاین شروع کنید. تنظیمات CLI مدیر و نام‌های مستعار موجود به کار می‌روند. مسیر بومی مخصوص عامل نگهداری Android است که جداگانه آماده شده؛ پخش‌کننده‌های Tauri از بررسی وب استفاده می‌کنند. گزارش‌ها پیشرفت رمزگشا را از بررسی فیزیکی صفحه/صدا جدا می‌کنند. برای قابلیت مفقود، مسیر ناموجود و شواهد ناقص، [عیب‌یابی workbench](docs/workbench.md#troubleshooting) را ببینید.

[عیب‌یابی پروتکل ۲](docs/remote-diagnostics.md) اختیاری برای هر محیط اجرا نشست‌های صریحاً فعال‌شده با اطلاعات دسترسی جدا و محدود اپراتور و تله‌متری ساختاریافتهٔ محدود ارائه می‌دهد. اتصال فعال پخش‌کننده بدون پرسش اعتماد دوباره مجوز محلی می‌دهد؛ هر ثبت همچنان مدت نشست محدودی دارد. پرس‌وجوی کنترل عیب‌یابی از صف فرمان موجود مستقل است.

[راهنمای CLI عیب‌یابی و MCP](docs/diagnostics-cli.md) نصب، اطلاعات اپراتور، اتصال، هر فرمان/ابزار عیب‌یابی، مثال ثبت و ترمیم، لغو دسترسی محیط اجرا و عیب‌یابی را پوشش می‌دهد. `ott diagnostics --help` این خانوادهٔ جداگانه را فهرست می‌کند؛ نام‌های مستعار معمولی و اطلاعات مدیر دسترسی عیب‌یابی نمی‌دهند.

## بسته‌های استقرار

- [تبلت Android: کیوسک سخت‌گیرانهٔ Plex و راه‌اندازی](docs/android-kiosk-setup.md)
- [مدیریت مشترک Android USB و بازیابی راه‌اندازی](docs/android-usb-manager.md)
- [Docker / Compose، Kubernetes، k3s، systemd، macOS و Windows](docs/deployment.md)
- [تنظیمات، معنای تحویل و محدودیت‌ها](docs/api.md)
- [فرایند انتشار و بررسی](docs/development.md)

صف‌ها محدودند، به‌طور پیش‌فرض پس از ۶۰ ثانیه منقضی می‌شوند و در حافظه‌اند. راه‌اندازی مجدد سرور فرمان‌های در انتظار را حذف می‌کند. یک نمونه اجرا کنید؛ نمونه‌های مستقل صف مشترک ندارند. پخش‌کننده تلاش برای ارسال را تأیید می‌کند، نه پخش تأییدشده یا صدای سخت‌افزار را. تلاش‌های تکراری شبکه در نشست فعال پخش‌کننده حذف تکرار می‌شوند، نه میان راه‌اندازی مجدد برنامه.

سرور از API فرمان مثال اختیاری `local_proxy.py` در [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) پیروی می‌کند، با اطلاعات جداگانهٔ مدیر/دستگاه و تحویل تأییدشده. پراکسی قدیمی جزء جداگانه می‌ماند. این پروژه رسانه را پراکسی نمی‌کند و شبکهٔ HLS، EPG یا ارائه‌دهندگان پخش‌کننده را تغییر نمی‌دهد.

## مجوز

MIT. انتساب ابزارهای انتشار گنجانده‌شده در `NOTICE` و `LICENSES/` ثبت شده است.

## نگهداری پروژه

[شرایط مشارکت و آزمون](CONTRIBUTING.md)، [سیاست گزارش امنیتی](SECURITY.md)، [طراحی امنیت](docs/security-design.md) و [شواهد OpenSSF و معیارهای باقی‌مانده](docs/openssf-evidence.md) را ببینید.

صف‌های Plex در پخش‌کننده‌های فعلی تا ۵۰۰ شناسه می‌پذیرند. برای یک بار تصادفی‌کردن `ott f10 plex play --shuffle ID...` را اجرا کنید، سپس با انتخاب ارائه‌دهندهٔ Plex، از `ott f10 kiosk on --strict` برای تکرار و قفل صف استفاده کنید. `ott f10 kiosk off` قفل را باز می‌کند. پخش‌کننده‌های قدیمی محدودیت صف اعلامی خود را حفظ می‌کنند.

## فازینگ نامزدهای انتشار

نامزدهای انتشار عمومی فازینگ بومی محدود را روی مرزهای ورودی شرح‌داده‌شده در [راهنمای فازینگ](docs/fuzzing.md) اجرا می‌کنند. کارزارهای جهش روی PR، بتا یا ساخت شبانه اجرا نمی‌شوند. راهنما دامنه، بودجه، شواهد و بازتولید پسرفت‌ها را پوشش می‌دهد.

## جامعه و شواهد امنیتی

[مشارکت](CONTRIBUTING.md)، [راهبری](GOVERNANCE.md)، [آیین رفتار](CODE_OF_CONDUCT.md)، [نقشهٔ راه](ROADMAP.md) و [شواهد OpenSSF](docs/openssf-evidence.md) را ببینید. نمایهٔ شواهد پروفایل‌های امنیتی پشتیبانی‌شده، نتایج واقعی آزمون و الزامات باقی‌ماندهٔ Silver را تفکیک می‌کند.

</div>
