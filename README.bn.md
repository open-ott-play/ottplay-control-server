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

[ডকুমেন্টেশন / Wiki](docs/wiki/Home.bn.md)

নিজে হোস্ট করার উপযোগী ছোট একটি সার্ভার, যা OTT-play প্লেয়ারে দূরবর্তী কমান্ড পৌঁছে দেয়। প্লেয়ার বাইরে HTTP অনুরোধ পাঠায়, তাই টিভিতে শোনার পোর্ট খুলতে হয় না। প্রতিটি প্লেয়ারের নিজস্ব সারি ও অ্যাক্সেস টোকেন থাকে; কমান্ড পাঠাতে আলাদা প্রশাসক টোকেন ব্যবহার হয়।

ঐচ্ছিক [নির্ভরযোগ্য নিয়ন্ত্রণ](docs/reliable-control.md) সীমিত ক্ষমতার ইন্টিগ্রেশন পরিচয়পত্র, সীমাবদ্ধ স্থায়ী কমান্ড-ফল সংরক্ষণ, নির্ভুল সেটিং যাচাইসহ পুনরায় চালিয়ে নেওয়া যায় এমন Local/Home প্রয়োগ এবং সরাসরি/আর্কাইভ প্লেব্যাকের স্পষ্ট হস্তান্তর যোগ করে। এর জন্য এই নির্দেশিকা-সম্বলিত নিয়ন্ত্রক ও CLI সংস্করণ দরকার। নির্ভুল যাচাই ও হস্তান্তরের জন্য হালনাগাদ প্লেয়ারও দরকার। নিচের পুরোনো নির্দিষ্ট ইনস্টলেশন সংস্করণে এগুলো নেই। [স্থাপত্যচিত্র](docs/assets/reliable-control.svg) দেখায় সেটিং, পরিচয়পত্র ও কাজের অবস্থা কোথায় থাকে।

সার্ভার একটিমাত্র বহনযোগ্য Go নির্বাহযোগ্য ফাইল হিসেবে চলে; ঐচ্ছিক সার্ভিস আবিষ্কারে নির্দিষ্ট সংস্করণের DNS লাইব্রেরি ব্যবহার করে। রিলিজ প্যাকেজ Linux amd64, arm64 ও ARMv7; macOS Intel ও Apple Silicon; Windows amd64 ও arm64-এর জন্য আছে। কনটেইনার ইমেজ Linux amd64 ও arm64 সমর্থন করে। Kubernetes ও k3s সঙ্গে দেওয়া Helm chart ব্যবহার করে।

## সার্ভার চালু করা

[রিলিজ](https://github.com/open-ott-play/ottplay-control-server/releases) থেকে উপযুক্ত আর্কাইভ নামান, রিলিজ ম্যানিফেস্টের সঙ্গে যাচাই করে খুলুন। Windows-এ নিচের কমান্ডে `ottplay-control-server.exe` ব্যবহার করুন।

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` নতুন ফাইলে এলোমেলো, আলাদা পরিচয়পত্র তৈরি করে; Unix-এ কেবল মালিকের প্রবেশাধিকার থাকে। বিদ্যমান ফাইল বা প্রতীকী লিংক প্রতিস্থাপন করতে অস্বীকার করে এবং পরিচয়পত্র কখনো ছাপে না। ফাইল ব্যক্তিগত রাখুন; Windows-এ এর ACL সার্ভার চালানো অ্যাকাউন্টে সীমিত করুন। `config.example.json` ক্ষেত্রগুলো ব্যাখ্যা করে এবং ইচ্ছাকৃতভাবে অবৈধ স্থানধারক টোকেন রাখে।

ডিফল্ট ঠিকানা `0.0.0.0:8081`, অর্থাৎ LAN-সহ সব IPv4 ইন্টারফেস। সার্ভারের LAN ঠিকানা ও পোর্ট দিয়ে টিভি সংযুক্ত করুন। বিদ্যমান কনফিগারেশন স্পষ্টভাবে দেওয়া `listen` মান রাখে; আগে শুধু loopback-এ চলা ইনস্টলেশন উন্মুক্ত করতে সেটি `0.0.0.0:8081` করুন বা `--listen 0.0.0.0:8081` ব্যবহার করুন। প্লেয়ার **পৃষ্ঠার হুবহু origin** `allowed_origins`-এ যোগ করুন, যেমন `http://192.168.1.20:8443`। স্কিম, হোস্টনাম ও পোর্ট মিলতে হবে; পাথ যোগ করবেন না। খালি origin তালিকা শুধু ব্রাউজার-বহির্ভূত ক্লায়েন্ট অনুমোদন করে। কনফিগারেশন পরিবর্তন পুনরায় চালুর পর কার্যকর হয়। ডিভাইস ও প্রশাসকের পরিচয়পত্র এখনও বাধ্যতামূলক।

বিশ্বস্ত LAN-এ পুরোনো টিভি ব্রাউজারের জন্য HTTP সমর্থিত। সংযোগ অবিশ্বস্ত নেটওয়ার্ক পার হলে HTTPS ব্যবহার করুন: `serve --config config.json --tls-cert server.crt --tls-key server.key`, অথবা রিভার্স প্রক্সিতে TLS শেষ করুন। HTTPS প্লেয়ার পৃষ্ঠা ব্রাউজারে সাধারণ HTTP সার্ভারে যুক্ত হতে পারে না। নেটিভ অ্যাপও অপারেটিং সিস্টেমের পরিবহন নীতি বজায় রাখে; এই সেটিং তা শিথিল করে না।

সরাসরি HTTPS শ্রোতা খোলার আগে দেওয়া সার্টিফিকেট-শৃঙ্খল যাচাই করে: RSA কী অন্তত 2048 বিট, ECDSA অন্তত 224 বিট হতে হবে; Ed25519-ও সমর্থিত। আপগ্রেডের আগে ছোট কী-যুক্ত সার্টিফিকেট বদলান। [TLS সার্টিফিকেট নীতি ও আওতা](docs/security-design.md#direct-https-certificate-policy) দেখুন।

## প্লেয়ার সংযুক্ত করা

কমান্ড-সার্ভার সংযোগসম্বলিত প্লেয়ার রিলিজ ব্যবহার করুন। **সেটিংস → দূরবর্তী নিয়ন্ত্রণ** খুলে লিখুন:

1. **সার্ভার ঠিকানা:** IP বা হোস্টনাম, প্রয়োজনে পোর্টসহ, অথবা সম্পূর্ণ `http://` / `https://` ঠিকানা। শুধু হোস্ট দিলে ডিফল্ট HTTP পোর্ট 8081 ব্যবহৃত হয়।
2. **অ্যাক্সেস কোড:** `devices`-এ ওই প্লেয়ারের এন্ট্রির `token`; কখনোই `admin_token` নয়।
3. **সংযুক্ত করুন:** পোলিং চালু করে একই ডায়ালগে অবস্থা পরীক্ষা করুন।

প্লেয়ার সংযোগটি শুধু বর্তমান ইনস্টলেশনে রাখে; সেটিংস ব্যাকআপ বা ক্লাউড স্থানান্তরে রাখে না। আলাদা স্থানীয় HTTP শ্রোতা প্রয়োজন নেই। ওয়েব ব্রাউজারে অনুমোদিত CORS origin দরকার; `Origin: null`-যুক্ত প্যাকেজ করা টিভি পৃষ্ঠা নির্ধারিত ডিভাইস/bootstrap রুটের জন্য স্পষ্ট `allow_null_origin` ব্যবহার করতে পারে। এতে প্রশাসক বা ডায়াগনস্টিক অপারেটরের রুট চালু হয় না।

## টার্মিনাল রিমোট

[সচিত্র Android OTA নির্দেশিকা](docs/android-ota.md) সাধারণ Capacitor Wi-Fi আপডেট (`ott DEVICE update --variant api22|full`, ডিফল্ট সর্বশেষ বিটা; `--version` রিলিজ বেছে নেয়) ও আলাদা root-ক্ষমতার এজেন্টের পার্থক্য করে। স্বাক্ষরিত এজেন্ট এবং APK/helper আপডেট, পুনরুদ্ধার ও ইনস্টল হওয়া ফল যাচাই ব্যাখ্যা করে।

[Android ADB নির্দেশিকা](docs/android-adb.md) ওয়ার্কস্টেশন বা Synology দিয়ে USB প্রবেশ, পর্দা ও কিবোর্ড নিয়ন্ত্রণ, স্ক্রিনশট, লগ, APK ইনস্টল ও স্বাভাবিক রিবুট নিয়ে আলোচনা করে, পুরোনো ট্যাবলেটের সীমাবদ্ধতাসহ।

ঐচ্ছিক [DNS-SD আবিষ্কার ও পেয়ারিং](docs/discovery.md) প্লেয়ারকে নেটওয়ার্ক ডোমেন দিয়ে নিয়ন্ত্রক খুঁজতে এবং `ott approve NAME CODE`-এর পর নিজস্ব ডিভাইস টোকেন পেতে দেয়। DNS-এ শুধু সার্ভিস মেটাডেটা থাকে। আবিষ্কার চালু না করেও বিদ্যমান ম্যানুয়াল সংযোগ কাজ করে।

প্লেয়ারের উপনাম, সরাসরি ভলিউম, চ্যানেল অনুসন্ধান, বর্তমান EPG, প্রদানকারী নির্বাচন এবং সমর্থিত প্রদানকারীর কনফিগারেশনে Python CLI ব্যবহার করুন: [CLI নির্দেশিকা](docs/cli.md)। সার্ভার ও প্লেয়ার উভয়ের অনুরোধ/উত্তর এক্সটেনশন থাকতে হবে।

Python CLI সোর্সের কাজের কপি থেকে ইনস্টল হয়, নেটিভ সার্ভার আর্কাইভ থেকে আলাদা। [ইনস্টল ও কনফিগারেশনের ধাপ](docs/cli.md#installation-and-connection) macOS/Linux ও Windows, Python/PATH সেটআপ এবং দরকারি CLI মডিউল নিয়ে আলোচনা করে। এগুলো workbench ও অফলাইন যাচাইসম্বলিত পর্যালোচিত 0.1.1 উন্নয়ন কমিট নির্দিষ্ট করে; স্থিতিশীল CLI v0.1.0 ও নেটিভ সার্ভার আর্কাইভে সেই কমান্ড নেই। বিদ্যমান কাজের কপি, লঞ্চার বা ব্যক্তিগত কনফিগারেশন না বদলিয়ে [CLI আপডেট বা আগের সংস্করণে ফেরান](docs/cli.md#update-select-or-roll-back-the-cli)। স্থানীয় লঞ্চার শুধু-পাঠযোগ্য [প্লেলিস্ট রিজলভার](docs/cli.md#local-playlist-resolver) দিয়ে নিয়ন্ত্রক ছাড়াই চ্যানেল, বর্তমান অনুষ্ঠান ও চালানো যায় এমন আর্কাইভ খুঁজতে পারে। ইনস্টল ও কনফিগারেশনের পর:

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

`a1` নিবন্ধিত প্লেয়ারের উদাহরণ উপনাম, কম্পিউটারের হোস্টনাম নয়। [নিবন্ধন ও পেয়ারিং](docs/cli.md#register-and-connect-a-player), [উদাহরণসহ সব কমান্ড](docs/cli.md#commands), [সংযোগ বিচ্ছিন্ন ও প্রবেশাধিকার প্রত্যাহার](docs/cli.md#disconnect-deregister-or-revoke-a-player), এবং [সমস্যা সমাধান](docs/cli.md#troubleshooting) দেখুন।

সবসময় চালু প্লেয়ারে অ্যাপ থেকে না বেরিয়ে প্লেব্যাক থামিয়ে পরে জাগাতে [standby ও go](docs/cli.md#stop-playback-while-keeping-the-player-online) ব্যবহার করুন। `stop` এখনও `standby`-এর উপনাম; `start` ও `wake`, `go`-এর উপনাম। দূরবর্তী নিয়ন্ত্রণ সংযুক্ত থাকতে হবে, তাই স্ট্যান্ডবাই শূন্য নেটওয়ার্ক ট্র্যাফিকের নিশ্চয়তা নয়। `ott a1` প্লেয়ারের ঘোষিত কাজগুলো দেখায়। কমান্ড গৃহীত হওয়া মানে বন্ধ হওয়া বা প্লেব্যাক শুরু সম্পূর্ণ হয়েছে এমন নিশ্চয়তা নয়।

[অনুপাতের কমান্ড](docs/cli.md#aspect-ratio-fit-and-fill) হালনাগাদ CLI ও নিয়ন্ত্রক এবং `aspect` সক্ষমতা জানানো প্লেয়ার চায়। স্বীকৃতির পর পরিবর্তন কার্যকর হয়; মোড ও সংরক্ষিত হয়েছে কি না নিশ্চিত করতে আবার `aspect` পড়ুন। Fill ছবি টেনে বিকৃত না করে অনুপাত রেখে কাটে।

দূরবর্তী [স্ক্রিনশট](docs/cli.md#remote-screenshots) হালনাগাদ CLI ও নিয়ন্ত্রক এবং সমর্থিত ক্যাপচার অ্যাডাপ্টারযুক্ত প্লেয়ার চায়। প্লেয়ারের দূরবর্তী নিয়ন্ত্রণ সংযোগ চালু করলে সেই নিয়ন্ত্রক স্ক্রিনশট ও ডায়াগনস্টিকের অনুমতি পায়; নেটিভ স্ক্রিনশটের আলাদা অনুমতি সুইচ লাগে না। ব্রাউজারে এখনও স্থানীয় সোর্স নির্বাচন করতে হয়। ছবিতে সেটিংস, PIN পর্দা ও দৃশ্যমান পরিচয়পত্র থাকতে পারে, তাই শুধু বিশ্বস্ত নিয়ন্ত্রকে যুক্ত হন। `ott a1` প্রাপ্যতা ও দরকারি সোর্স নির্বাচন জানায়। স্ক্রিনশট কাঠামোবদ্ধ টেলিমেট্রি সংগ্রহ থেকে আলাদা।

একটি শিরোনামের সঙ্গে মেলা সব VPortal ভিডিও এলোমেলো করে বারবার চালাতে:

```sh
ott l vpr "wedding"
```

`vpr` পুরো নির্বাচন একবার এলোমেলো করে, তারপর সেই সারি পুনরাবৃত্তি করে। নতুন ক্রমের জন্য আবার চালান। `vp` ক্যাটালগের ক্রমে ফল চালায়; `vp --list` প্লেব্যাক না বদলে তালিকা দেখায়। [VPortal কমান্ড](docs/cli.md#commands) দেখুন।

প্লেয়ারের সংরক্ষিত Plex কনফিগারেশন দিয়ে নির্দিষ্ট Plex ক্রমের জন্য:

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

CLI ডিফল্টে সর্বোচ্চ 45 সেকেন্ড অপেক্ষা করে। `ott -t 45 l plex shuffle "Tom and Jerry"` লিখলে একই উত্তর-সময়সীমা স্পষ্ট হয়; ভিডিও কতক্ষণ চলবে তা সীমিত হয় না। বড় Plex লাইব্রেরি সংগ্রহে 10–15 সেকেন্ডের ছোট সময়সীমার বেশি লাগতে পারে। `-t SECONDS` বা `--timeout SECONDS` প্লেয়ারের উপনামের আগে দিন। প্রিভিউ উদাহরণ ও প্লেব্যাক অনুরোধের সময় শেষ হলে করণীয় জানতে [Plex লাইব্রেরির সময়সীমা](docs/cli.md#plex-libraries-by-name) দেখুন।

Plex সারির জন্য `plex_queue` বাস্তবায়নকারী CLI, নিয়ন্ত্রক ও প্লেয়ার দরকার। `plex play --shuffle ID...` দেওয়া ID একবার এলোমেলো করে। বিদ্যমান কিয়স্ক নীতি পুনরাবৃত্তি না করলে সারি শেষ আইটেমে থামে। সারি থাকা অবস্থায় সাধারণ `next` ও `prev` সেটির ওপর কাজ করে; `plex stop` সারি মুছে দেয়। প্রিভিউ কখনো প্রদানকারী বদলায় না বা চলতি প্লেব্যাক প্রতিস্থাপন করে না। সীমা ও প্রস্তুতি [Plex সারির কমান্ড](docs/cli.md#ordered-plex-queues)-এ আছে। লাইব্রেরি খুঁজতে `plex_library` ঘোষণাকারী হালনাগাদ প্লেয়ার ও নিয়ন্ত্রকও দরকার; এটি প্লেয়ারের সংরক্ষিত Plex সংযোগ ব্যবহার করে এবং দ্ব্যর্থক বা অসম্পূর্ণ নির্বাচন প্রত্যাখ্যান করে। [নাম দিয়ে Plex লাইব্রেরি](docs/cli.md#plex-libraries-by-name) দেখুন।

### স্থানীয় ভিডিও প্রোফাইল সংরক্ষণ

বর্তমান প্লেয়ার ও নিয়ন্ত্রকে স্থানীয় ফাইল/ফোল্ডার `offline` প্রদানকারী ব্যবহার করে:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

আগে প্লেয়ারে ফাইল/ফোল্ডার যুক্ত করুন। সংরক্ষণ কনফিগারেশন ও নির্বাচিত ভিডিও লিখে রাখে, প্লেব্যাক শুরু করে না। এক্সপোর্ট শুধু বর্ণনা স্থানান্তর করে; মিডিয়া ও OS/ব্রাউজারের অনুমতি ডিভাইসে থাকে। অন্য ইনস্টলেশনে স্থানীয় বাঁধাই লাগে, আর লোড মানেই নিশ্চিত প্লেব্যাক নয়। সেটআপ, সক্ষমতা, বহনযোগ্য বিন্যাসের সীমা ও সমস্যা সমাধানে [স্থানীয় ভিডিও প্রোফাইল](docs/cli.md#local-videos-profiles) দেখুন।

### প্রোফাইলের মধ্যে বর্তমান আইটেম সংরক্ষণ

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

পছন্দের আইটেম লক্ষ্য প্লেয়ারে স্বাভাবিক রিস্টার্টের পরও থাকে। একটি বাছলে সংরক্ষিত প্রদানকারী/প্রোফাইল লোড হয় এবং নতুন লিংক খোঁজা হয়; সোর্স বদলে গেলে বা আইটেম না থাকলে অন্য চ্যানেল/চলচ্চিত্র বসানোর বদলে ব্যর্থ হয়। CLI, নিয়ন্ত্রক ও প্লেয়ারে `favorites` সমর্থন চাই; `ott l` উপলব্ধ কাজ দেখায়। উপনাম, সীমা, স্বীকৃতির অর্থ ও সমস্যা সমাধানে [প্রোফাইলের মধ্যে পছন্দের আইটেম](docs/cli.md#favorites-across-profiles) দেখুন।

### সংরক্ষিত সেটআপ লোড

নামযুক্ত সেটআপ `~/.config/ottplay-control/cli.json`-এর ব্যক্তিগত `presets` অংশে রাখুন। প্রতিটিতে নম্বরযুক্ত M3U প্রোফাইল (প্লেলিস্ট ও আর্কাইভের ঘণ্টা), Plex পরিচয়পত্র এবং লোড শেষে বেছে নেওয়া M3U প্রোফাইল থাকে। [কনফিগারেশন উদাহরণ](docs/cli.md#named-setups) দেখুন।

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI আগে পুরো সেটআপ যাচাই করে, তারপর পরের পরিবর্তন পাঠানোর আগে প্লেয়ারের স্বীকৃতি অপেক্ষা করে। এটি প্রদানকারী লোড সামলায়, Plex ও M3U স্লট সংরক্ষণ করে এবং নির্ধারিত M3U প্রোফাইল বেছে নেয়। ত্রুটি ক্রম থামায় ও নিশ্চিত ধাপ জানায়; অনিশ্চিত ফলের লেখার কাজ কখনো স্বয়ংক্রিয়ভাবে আবার করা হয় না। অতিরিক্ত প্রোফাইল সার্ভিস বা নিয়ন্ত্রক রিস্টার্ট লাগে না। কনফিগারেশন ব্যক্তিগত রাখুন: এতে প্রদানকারীর পরিচয়পত্র আছে।

## কমান্ড পাঠানো

এই উদাহরণ স্থানীয় ব্যক্তিগত কনফিগারেশন থেকে প্রশাসক টোকেন পড়ে, URL বা কমান্ড লাইনের আর্গুমেন্টে রাখে না:

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

অন্যান্য কমান্ড নম্বর বা নাম দিয়ে চ্যানেল বেছে নেয়, এলোমেলো চ্যানেল নেয়, প্রদানকারী বদলায়, বার্তা দেখায়, সমর্থিত হলে M3U প্লেলিস্ট বদলায় বা প্লেয়ার থেকে বের হয়। প্রাপ্যতা প্লেয়ারের প্ল্যাটফর্ম ও বর্তমান প্রদানকারীর ওপর নির্ভর করে। [API চুক্তি](docs/api.md) দেখুন।

স্বতন্ত্র VPortal প্রদানকারী সমর্থিত হলে `ott a1 provider
vportal` তার নিজস্ব 15 প্রোফাইল বেছে নেয়। `profile N vportal LINK`, `profile N
name NAME` বা `name` ও `vportal`-যুক্ত ব্যক্তিগত `profile-config N FILE` ব্যবহার করুন, তারপর `profile N` দিয়ে কনফিগার করা স্লট বাছুন। বিদ্যমান M3U প্রোফাইল আলাদা থাকে। `profiles` অ্যাকাউন্ট লিংক বা কী প্রকাশ না করে মেটাডেটা দেখায়।

VPortal চলচ্চিত্র বা পর্বের সারি শুরু হলে `ott a1 kiosk on` সেই নির্বাচন লক করে। প্লেয়ার রিলোডের পর সারি ও অবস্থান রাখে, এবং পুনরুদ্ধারের জন্য মিডিয়া URL আবার নির্ণয় করে। অন্য শিরোনাম বা প্রোফাইল বাছতে `kiosk off`, তারপর লক করতে `kiosk on` ব্যবহার করুন। কিয়স্ক রসিদ শিরোনাম ও পর্বের সংখ্যা দেখায়; প্রদানকারীর অনুরোধ ও স্ট্রিম URL প্লেয়ারেই থাকে।

শিশুদের প্লেব্যাকে `ott a1 kiosk on --strict` ব্যবহার করুন: স্থানীয় ট্যাপ ভিডিওর নিচের বার দেখায়। হালনাগাদ core প্লেয়ারে টাইমলাইন ট্যাপ বা টেনে বর্তমান VPortal/Plex পর্বে এগোনো-পেছানো যায়; অন্য স্থানীয় নিয়ন্ত্রণ লক থাকে। পুরোনো প্লেয়ার, চ্যানেল কিয়স্ক ও টাইমলাইনের অঙ্গভঙ্গি আটকানো র‍্যাপার শুধু-পাঠযোগ্য বার রাখে। বিদ্যমান লক লক্ষ্য ধরে রাখে। নিয়ন্ত্রক থেকে `ott a1 kiosk off` দিয়ে খুলুন। Android ওয়েবসাইটে সিস্টেম দিয়ে বের হওয়া ঠেকাতে PIN-সহ আলাদা অ্যাপ পিনিং লাগে; [কিয়স্ক মোড](docs/cli.md#kiosk-mode) দেখুন।

## দূরবর্তী ডায়াগনস্টিকস

`doctor`, UI/মিডিয়া `inspect`, কাজের রসিদ খোঁজা, প্রমাণের বান্ডল, `health`, ওয়েব/নেটিভ `media-progress` ও অফলাইন `report verify`-এর জন্য [শুধু-পাঠযোগ্য workbench দ্রুত শুরু](docs/workbench.md#quick-start) দিয়ে শুরু করুন। এটি বিদ্যমান প্রশাসক CLI কনফিগারেশন ও প্লেয়ার উপনাম ব্যবহার করে। নেটিভ পথ আলাদাভাবে প্রস্তুত Android রক্ষণাবেক্ষণ এজেন্টের জন্য; Tauri প্লেয়ার ওয়েব পরিদর্শন ব্যবহার করে। প্রতিবেদন ডিকোডারের অগ্রগতি ও প্রকৃত পর্দা/শব্দ যাচাই আলাদা করে। অনুপস্থিত সক্ষমতা, অনুপলব্ধ পথ বা অসম্পূর্ণ প্রমাণের জন্য [workbench সমস্যা সমাধান](docs/workbench.md#troubleshooting) দেখুন।

ঐচ্ছিক [প্রোটোকল 2 ডায়াগনস্টিকস](docs/remote-diagnostics.md) প্রতিটি রানটাইমে স্পষ্টভাবে চালু করা সেশন, আলাদা সীমিত অপারেটর পরিচয়পত্র ও সীমিত কাঠামোবদ্ধ টেলিমেট্রি দেয়। সক্রিয় প্লেয়ার সংযোগ নতুন আস্থার প্রশ্ন ছাড়াই স্থানীয় অনুমতি দেয়; প্রতিটি ক্যাপচারের সেশনের মেয়াদ তবুও সীমিত। ডায়াগনস্টিক নিয়ন্ত্রণ পোলিং বিদ্যমান কমান্ড সারি থেকে স্বাধীন।

[ডায়াগনস্টিক CLI ও MCP নির্দেশিকা](docs/diagnostics-cli.md) ইনস্টল, অপারেটর পরিচয়পত্র, সংযোগ, প্রতিটি ডায়াগনস্টিক কমান্ড/টুল, ক্যাপচার ও মেরামতের উদাহরণ, রানটাইম প্রবেশাধিকার প্রত্যাহার এবং সমস্যা সমাধান ব্যাখ্যা করে। `ott diagnostics --help` এই আলাদা কমান্ড পরিবার দেখায়; সাধারণ প্লেয়ার উপনাম ও প্রশাসক পরিচয়পত্র ডায়াগনস্টিক প্রবেশাধিকার দেয় না।

## স্থাপনের প্যাকেজ

- [Android ট্যাবলেট: কঠোর Plex কিয়স্ক ও চালু করা](docs/android-kiosk-setup.md)
- [যৌথ Android USB ব্যবস্থাপনা ও বুট পুনরুদ্ধার](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS ও Windows](docs/deployment.md)
- [কনফিগারেশন, পৌঁছানোর অর্থ ও সীমা](docs/api.md)
- [রিলিজ ও যাচাই প্রক্রিয়া](docs/development.md)

সারি সীমিত, ডিফল্টে 60 সেকেন্ড পর মেয়াদ শেষ হয় এবং মেমোরিতে থাকে। সার্ভার পুনরায় চালু করলে অপেক্ষমাণ কমান্ড মুছে যায়। একটিমাত্র প্রতিলিপি চালান; স্বাধীন প্রতিলিপি সারি ভাগ করে না। প্লেয়ার পাঠানোর চেষ্টার স্বীকৃতি দেয়, নিশ্চিত প্লেব্যাক বা হার্ডওয়্যার ভলিউমের নয়। নেটওয়ার্কের পুনঃচেষ্টা থেকে পুনরাবৃত্তি বাদ দেওয়া সক্রিয় প্লেয়ার সেশনের মধ্যে হয়, অ্যাপ রিস্টার্ট পেরিয়ে নয়।

এই সার্ভার [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss)-এর ঐচ্ছিক `local_proxy.py` উদাহরণের কমান্ড API মেনে চলে, আলাদা প্রশাসক/ডিভাইস পরিচয়পত্র ও স্বীকৃত ডেলিভারিসহ। পুরোনো প্রক্সি আলাদা উপাদান থাকে। প্রকল্প মিডিয়া প্রক্সি করে না বা প্লেয়ারের HLS, EPG কিংবা প্রদানকারীর নেটওয়ার্কিং বদলায় না।

## লাইসেন্স

MIT। অন্তর্ভুক্ত রিলিজ টুলের কৃতিত্ব `NOTICE` ও `LICENSES/`-এ নথিবদ্ধ।

## প্রকল্প রক্ষণাবেক্ষণ

[অবদান ও পরীক্ষার শর্ত](CONTRIBUTING.md), [নিরাপত্তা রিপোর্ট নীতি](SECURITY.md), [নিরাপত্তা নকশা](docs/security-design.md) এবং [OpenSSF প্রমাণ ও বাকি মানদণ্ড](docs/openssf-evidence.md) দেখুন।

বর্তমান প্লেয়ারে Plex সারি 500 ID পর্যন্ত নেয়। একবার এলোমেলো করতে `ott f10 plex play --shuffle ID...`, তারপর Plex প্রদানকারী নির্বাচিত থাকা অবস্থায় সারি পুনরাবৃত্তি ও লক করতে `ott f10 kiosk on --strict` ব্যবহার করুন। `ott f10 kiosk off` লক খোলে। পুরোনো প্লেয়ার নিজেদের ঘোষিত সারি সীমা রাখে।

## রিলিজ ক্যান্ডিডেট ফাজিং

পাবলিক রিলিজ ক্যান্ডিডেট [ফাজিং নির্দেশিকা](docs/fuzzing.md)-য় বর্ণিত ইনপুট সীমায় সীমিত নেটিভ ফাজিং চালায়। মিউটেশন অভিযান PR, বিটা বা রাতের বিল্ডে চলে না। নির্দেশিকায় আওতা, বাজেট, প্রমাণ ও রিগ্রেশন পুনরুৎপাদন আছে।

## কমিউনিটি ও নিরাপত্তার প্রমাণ

[অবদান](CONTRIBUTING.md), [পরিচালনা](GOVERNANCE.md), [আচরণবিধি](CODE_OF_CONDUCT.md), [রোডম্যাপ](ROADMAP.md) এবং [OpenSSF প্রমাণ](docs/openssf-evidence.md) দেখুন। প্রমাণসূচি সমর্থিত নিরাপত্তা প্রোফাইল, প্রকৃত পরীক্ষার ফল ও বাকি Silver শর্ত আলাদা করে।
