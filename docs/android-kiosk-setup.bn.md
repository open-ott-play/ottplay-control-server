# Android ট্যাবলেট: Plex কঠোর কিয়স্ক ও স্টার্টআপ

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB নির্দেশিকা](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB ম্যানেজার](android-usb-manager.md)

একটি নির্দিষ্ট ট্যাবলেটের তিনটি সেটিং আলাদা করে যাচাই করতে হয়: কনটেন্ট কিউ, OTT-এর কঠোর কিয়স্ক নীতি ও Android স্টার্টআপ। APK ইনস্টল বা স্ক্রিন বন্ধ/পুনরারম্ভ যাচাই করা OS রিবুটের পরে অ্যাপ চালু হওয়া প্রমাণ করে না।

## শনাক্তকরণ ও ইনস্টল

1. বাস্তব ট্যাবলেট, OTT alias/UUID, USB ম্যানেজারের alias, সিরিয়াল, মডেল, Android ID ও SDK লিখে রাখুন। OTT ও USB নাম আলাদা হতে পারে; নাম থেকে পরিচয় অনুমান করবেন না বা অন্য ট্যাবলেটের টোকেন ব্যবহার করবেন না।
2. SDK-সামঞ্জস্যপূর্ণ অফিসিয়াল APK ভ্যারিয়েন্ট নিন, রিলিজ SHA-256 ও প্যাকেজ/স্বাক্ষরের পরিচয় যাচাই করে `install -r` দিয়ে ইনস্টল করুন। প্লেয়ারের তথ্য রাখুন। USB যদি [শেয়ার করা ম্যানেজারের](android-usb-manager.md) অধীনে থাকে, সেটিই ব্যবহার করুন; প্রতিদ্বন্দ্বী ADB সার্ভার বা ডিভাইস নির্দিষ্ট না করা `adb` কমান্ড চালাবেন না।
3. প্লেয়ারকে তার নিজস্ব কন্ট্রোলার পরিচয়পত্র দিয়ে জুড়ে `ott TABLET` যাচাই করুন। কাঙ্ক্ষিত নামযুক্ত প্রিসেট লোড করে `ott TABLET load PRESET verify` চালান। বাড়ির নেটওয়ার্কের প্রিসেট ও ডেভেলপমেন্ট মেশিনের localhost প্রিসেট পরস্পর বদলযোগ্য নয়।

## ট্যাবলেট থেকেই Plex যাচাই

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

লাইব্রেরির ID, শিরোনাম ও আইটেমসংখ্যা যাচাই করুন। প্রশাসকের কম্পিউটারের সফল অনুরোধ ট্যাবলেটের সংযোগ প্রমাণ করে না। ব্যর্থ হলে ট্যাবলেটে সংরক্ষিত ঠিকানা, রাউটিং, TLS সমর্থন ও পরিচয়পত্র দেখুন। সার্ভারের বৈধ HTTPS endpoint বেছে নিন। পুরোনো ফার্মওয়্যারে ব্যক্তিগত LAN HTTP লাগলে এই ইনস্টলেশনেই সীমিত রাখুন, টোকেন ও মিডিয়া এনক্রিপশনহীনভাবে যাচ্ছে তা নথিভুক্ত করুন; শেয়ার করা প্রিসেট বদলাবেন না বা সার্বিক সার্টিফিকেট যাচাই বন্ধ করবেন না। ডায়াগনস্টিকস বা নথিতে টোকেন ও ব্যক্তিগত মিডিয়া URL প্রকাশ করবেন না।

Plex সার্ভারের ঠিকানা বদলালে নির্বাচিত লাইব্রেরি রুট মুছে যায়। **Plex** প্রোভাইডার বেছে **Plex settings → Folders** খুলুন, কাঙ্ক্ষিত লাইব্রেরির সারি টিক দিন (শুধু **Browse folders** নয়), **Ok**, তারপর **Save and open library** বাছুন। প্লেয়ার `1.1.58-beta.19`-এ শুধু সার্ভারের পরিচয়পত্র ফোল্ডার নির্বাচক খুললেও `ready` false থাকতে পারে; কিউ কমান্ডের আগে নির্বাচন শেষ করুন। শুধু `ready: true` পেতে M3U-তে বদলাবেন না: সেখানে Plex কিউ চললেও `kiosk on --strict` যুক্ত মিডিয়াবিহীন অপেক্ষমান M3U নীতি সেভ করতে পারে। সেটি Plex কিউ লক বা পুনরুদ্ধার করে না।

## কিউ চালু ও লক

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

ক্রমযুক্ত কিউয়ের জন্য `plex shuffle`-এর বদলে `plex library 'LIBRARY NAME'` ব্যবহার করুন। Shuffle একবার ক্রম বেছে নেয়; কিয়স্ক তা পুনরাবৃত্তি করে। কাঙ্ক্ষিত আইটেম, `enabled: true`, `strict: true` ও সঠিক Plex কিউয়ের সঙ্গে নীতির যোগ নিশ্চিত করুন। চ্যানেল বা মিডিয়াবিহীন `waiting` নীতি গৃহীত কিয়স্ক নয়। অবস্থান/ফ্রেমসংখ্যা এগোচ্ছে এমন অন্তত দুটি নমুনা দেখুন এবং স্ক্রিন পরীক্ষা করুন। অগ্রগতিহীন স্বীকৃতি বা `playing` লেবেল যথেষ্ট নয়। আইটেম শেষে স্বাভাবিক পরিবর্তন পরীক্ষা করুন; পুরো লুপ পরীক্ষা অসুবিধাজনক হলে সেই সীমা স্পষ্ট লিখুন।

কঠোর কিয়স্ক প্লেয়ারের UI নিয়ন্ত্রণ করে। এটি Android device-owner নিবন্ধন নয়, বা Home, Recents, নোটিফিকেশন ও Settings বন্ধ থাকার প্রমাণ নয়। বিদ্যমান সিস্টেম লঞ্চার ও উপস্থিত অপারেটরের রক্ষণাবেক্ষণ-পথ বজায় রাখুন।

## আলাদাভাবে স্টার্টআপ চালু

আপডেটের জন্য বিদ্যমান স্বাক্ষর কী রেখে [OTT Home 0.2.0](../native/android-home/README.md) বিল্ড ও যাচাই করুন। `play.ott.foss`-এর পাশে `play.ott.home` ইনস্টল করুন। `play.ott.home/.StartupActivity` খুলে **Start OTT after reboot** চালু করুন। ডিফল্টে সেটি বন্ধ। এটি অফিসিয়াল প্লেয়ার বাইনারি, OTA স্বাক্ষর, প্রোফাইল তথ্য বা Home বোতামের বরাদ্দ বদলায় না।

ফার্মওয়্যারে Home নির্বাচক থাকলে চাইলে **OTT Home → Always**-ও বেছে নিন। নির্বাচক না থাকলে (পরীক্ষিত পুরোনো Fire-সহ) বুটের অনুমতি স্বাধীনভাবে অ্যাপ চালায়। আগে default Home হিসেবে সেট করা ইনস্টলেশন আগের আচরণ রাখে। পূর্ণ রোলব্যাকের জন্য উভয় ব্যবস্থা বন্ধ করুন।

শেয়ার করা USB ম্যানেজারের `recover_on_boot` তৃতীয় স্বাধীন ব্যবস্থা: NAS ও USB তার লাগে, শুধু নতুন বুট সামলায়, ক্র্যাশ করা অ্যাপ অবিরাম পুনরায় চালায় না। এটি স্বয়ংসম্পূর্ণ স্টার্টআপের বিকল্প নয়। তার পরীক্ষায় এই পুনরুদ্ধার বন্ধ রাখুন যাতে সহায়ক অ্যাপের চালু না হওয়া আড়াল না হয়। নিরাপদ লকস্ক্রিন মানুন; চাইলে স্বাভাবিকভাবে আনলক করুন।

## গ্রহণ ও হস্তান্তর

প্রমাণ ব্যক্তিগতভাবে রাখুন, প্রকাশ্য রসিদে পরিচয়পত্র দেবেন না:

- APK সংস্করণ, হ্যাশ, বাস্তব/OTT/USB পরিচয়ের মিল ও নির্বাচিত প্রিসেট।
- Plex লাইব্রেরি, আইটেমসংখ্যা/ক্রম, কঠোর নীতি ও প্রাথমিক এগোতে থাকা প্লেব্যাক।
- প্রকৃত OS রিবুটের আগের ও পরের বুট ID; পরে `sys.boot_completed=1`।
- রিবুট ও পর্যবেক্ষণের মাঝে হাতে প্লেয়ার চালানো বা USB পুনরুদ্ধার না হওয়া।
- OTT ফোরগ্রাউন্ড কার্যকলাপ, কন্ট্রোলারের পুনঃসংযোগ, কিউ/কঠোর নীতি ফিরে আসা ও প্লেব্যাক অগ্রগতি। বুট রিসিভারের `requested` কেবল অনুরোধ পাঠানো বোঝায়।
- আলাদা পাওয়ার বোতাম পরীক্ষা: স্ক্রিন বন্ধ, শব্দ বন্ধ, স্বাভাবিক আনলক ও পুনরারম্ভ।
- সঠিক সীমা: নিরাপদ আনলক দরকার, USB-সহায়ক পুনরুদ্ধার, অপরীক্ষিত বাস্তব বেরোনোর পথ, স্ট্রিম ব্যর্থতা বা মেমরি শেষ। এগুলিকে সম্পূর্ণ গৃহীত বলবেন না।

পরের ট্যাবলেট হস্তান্তরের আগে আগেরটির সাফল্যের দাবি কপি না করে একই তালিকা অনুসরণ করুন। পুরোনো Fire-এ নির্মাতার নেটিভ HLS ক্র্যাশ করলে M3U-এর জন্য `hls.js` লাগতে পারে; সেটি Plex-এর স্থায়িত্ব প্রমাণ করে না।

## রক্ষণাবেক্ষণ ও রোলব্যাক

```sh
ott TABLET kiosk off
```

**OTT Startup** খুলে **Start OTT after reboot** বন্ধ করুন, Home নির্বাচন বদলালে আগেরটি ফেরান। প্রয়োজনে যাচাইকৃত USB দিয়ে শুধু `play.ott.home` আনইনস্টল করুন; `play.ott.foss` ও তথ্য রাখুন। স্টার্টআপ বন্ধ করলে কিউ বদলায় না বা কিয়স্ক স্বয়ংক্রিয়ভাবে বন্ধ হয় না।
