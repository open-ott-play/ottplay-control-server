# Android USB পরিবহনের ইমেজ

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

এই ডিরেক্টরিতে শেয়ার করা ম্যানেজারের পরিবহন কন্টেইনারের ডিপ্লয়মেন্ট টেমপ্লেট এবং [`Dockerfile`](Dockerfile) রয়েছে। Python ম্যানেজার USB হোস্টে চলে। ইমেজটি ADB, Python এবং পারমাণবিক রক্ষণাবেক্ষণ অ্যাডাপ্টারের সরঞ্জাম দেয়; এতে দ্বিতীয় ডিভাইস রেজিস্ট্রি বা পুনরুদ্ধার ডেমন নেই। প্রস্তুতি, কমান্ড, স্থানান্তর, পুনরুদ্ধারের সীমা ও রোলব্যাকের জন্য [অপারেটর নির্দেশিকা](../../docs/android-usb-manager.md) দেখুন।

Git checkout না থাকলে একই [রিলিজ](https://github.com/open-ott-play/ottplay-control-server/releases) থেকে `ottplay-control-server-deployment.tar.gz` ও `release-manifest.json` নামান। আর্কাইভের ডাইজেস্ট যাচাই করে [ডিপ্লয়মেন্ট প্যাকেজ সংগ্রহ](../../docs/android-usb-manager.md#get-the-deployment-package) অনুযায়ী নতুন ডিরেক্টরিতে খুলুন। প্যাকেজে এই রেসিপি ও `tools/`-এর ছয়টি হোস্ট/রিমোট স্ক্রিপ্ট রয়েছে; ডিরেক্টরির গঠন অক্ষুণ্ণ রাখুন। `v0.1.5-beta.6`-সহ পুরোনো প্যাকেজে স্ক্রিপ্টগুলি নেই; তার বদলে সংশ্লিষ্ট সোর্স আর্কাইভ দরকার। প্যাকেজে তৈরি USB পরিবহন ইমেজ নেই। কন্ট্রোল সার্ভারের OCI রিলিজ আর্টিফ্যাক্টের উদ্দেশ্য আলাদা।

## সোর্স ও আর্টিফ্যাক্টের উৎস

ভিত্তি হলো `python:3.12.14-slim-trixie`, যা `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`-এ পিন করা। ভিত্তি, APT নির্দেশ এবং হ্যাশ-লক করা `websocket-client==1.9.0` ইনস্টলেশন [PR 128-এর b4181bf সংস্করণে](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager) পর্যালোচিত হয়েছে। উপলব্ধ বিল্ড ক্যাশ পুনর্ব্যবহারের জন্য নির্দেশের পাঠ অপরিবর্তিত রাখা হয়েছে। ঐচ্ছিক WebSocket প্যাকেজ বিদ্যমান সাধারণ রক্ষণাবেক্ষণ ক্লায়েন্ট সমর্থন করে; স্থানান্তরিত CDP অ্যাডাপ্টার শুধু Python-এর স্ট্যান্ডার্ড লাইব্রেরিও ব্যবহার করতে পারে।

এই রেসিপি লক করা wheel ইনস্টলের পরে ইনস্টল করা `pip` প্যাকেজ সরায়, PR 128-এর ম্যানেজার ও পুনরুদ্ধার কোড বাদ দেয়, ADB ফোরগ্রাউন্ডে চালায় এবং উত্তরাধিকারসূত্রে পাওয়া হেলথ চেক বন্ধ করে। রানটাইম ইনস্টলার সরানো আক্রমণের উপলব্ধ ক্ষেত্র কমায়; এটি ত্রুটিমুক্ত ভলনারেবিলিটি স্ক্যানের প্রমাণ নয়।

PR 128-এর পর্যালোচিত আর্টিফ্যাক্ট ছিল `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`। এতে ভিন্ন এন্ট্রিপয়েন্ট ও অসামঞ্জস্যপূর্ণ heartbeat হেলথ চেক আছে। এটি রেফারেন্স আর্টিফ্যাক্ট, এই Dockerfile-এর বিল্ড নয়। এর ইমেজ ID, সোর্সের উৎস বা স্ক্যানের ফল এই রেসিপির বলে দাবি করবেন না।

APT রিপোজিটরি snapshot-এ পিন করা নেই। তাই একই ভিত্তির ডাইজেস্ট ও Python wheel হ্যাশ থাকলেও ভবিষ্যতের পরিষ্কার পুনর্নির্মাণ নতুন OS প্যাকেজ বেছে নিতে পারে। বিল্ড অনুমোদনের সময় সঠিক সোর্স রিভিশন, বিল্ডার/প্ল্যাটফর্ম, তৈরি ইমেজ ID, প্যাকেজ সংস্করণ ও স্ক্যান নথিভুক্ত করুন। প্রোডাকশন কনফিগারেশনে সেই অনুমোদিত অপরিবর্তনীয় ইমেজ ID বা ডাইজেস্ট ব্যবহার করতে হবে।

## ব্যস্ত USB হোস্টে বিঘ্ন না ঘটিয়ে বিল্ড

আলাদা বিল্ডারে তৈরি ও স্ক্যান করাই শ্রেয়। লক্ষ্য হোস্টে শুধু অনুমোদিত ইমেজ দরকার; প্যাকেজ ডাউনলোড বা বিল্ড টুলচেইন দরকার নেই।

প্রস্তুত স্থানীয় বিল্ডার পুনর্ব্যবহারের আগে নিশ্চিত করুন সঠিক ভিত্তি ইমেজ আছে এবং APT ও wheel ইনস্টলেশন লেয়ার ক্যাশে রয়েছে। শুধু PR 128-এর চূড়ান্ত ইমেজ লোড করলে কোনো শর্তই প্রমাণিত হয় না। রিপোজিটরির মূল বা খোলা ডিপ্লয়মেন্ট ডিরেক্টরি থেকে ইচ্ছাকৃত অফলাইন প্রচেষ্টা হলো:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` বিল্ড ধাপে প্রযোজ্য; ভিত্তি নির্ধারণের সময় বিল্ডার কখনো রেজিস্ট্রিতে যোগাযোগ করবে না, এমন সাধারণ নিশ্চয়তা নয়। সীমিত হোস্টে পদ্ধতিটি ব্যবহারের আগে বিল্ডারের স্থানীয় ভিত্তি নির্ধারণ নিশ্চিত করুন। APT বা wheel লেয়ার অনুপস্থিত হলে USB হোস্টে চুপিসারে নেটওয়ার্ক পুনর্নির্মাণ অনুমোদন না দিয়ে প্রচেষ্টা থামান।

BuildKit বিল্ড ধাপের নেটওয়ার্ক মোড ক্যাশ কীতে রাখে। তাই ডিফল্ট নেটওয়ার্ক মোডে তৈরি ক্যাশ উপরোক্ত অফলাইন প্রচেষ্টায় নাও মিলতে পারে। সঠিক ভিত্তি ও নির্দেশের ক্রম যাচাইয়ের পরই তা পুনর্ব্যবহার করুন; কোনো প্যাকেজ ইনস্টল ধাপ `CACHED` দেখানোর বদলে চললে থামুন। শেষের `pip` অপসারণ স্পষ্টভাবে `RUN --network=none` ব্যবহার করে; আগের ক্যাশ করা ধাপ তাদের মূল মোড ব্যবহার করলেও এতে নেটওয়ার্ক লাগে না।

প্রোডাকশন কনফিগারেশন বদলানোর আগে Python ও ADB সংস্করণ দেখুন, তৈরি হওয়া সঠিক ইমেজ স্ক্যান করুন, বাইনারি ফাইল স্থানান্তর ও APK প্রস্তুতি যাচাই করুন, তারপর নির্দেশিকার ডিভাইস পরিচয়, কী অনুমোদন, স্লিপ ও বুট গ্রহণযোগ্যতার পরীক্ষা করুন। পুরোনো Android-এর সঙ্গে ADB 34-এর যোগাযোগ পরীক্ষা করতেই হবে; শুধু সংস্করণ নম্বর KitKat বা Fire সামঞ্জস্য প্রমাণ করে না। রোলব্যাকের প্রয়োজন শেষ না হওয়া পর্যন্ত বর্তমান ইমেজ ও ব্যক্তিগত ডিপ্লয়মেন্ট ব্যাকআপ রাখুন।

## রানটাইম সম্পদের বাজেট

এই Dockerfile নয়, হোস্ট ম্যানেজার 256 MiB মেমরি, মেমরি ও swap মিলিয়ে 256 MiB এবং CPU shares 128 প্রয়োগ করে; CPU বা PID-এর কঠোর কোটা নেই। এটি `--no-healthcheck`, `--network none`, শুধু-পঠনযোগ্য রুট ফাইলসিস্টেম, capabilities অপসারণ ও সুনির্দিষ্ট USB নোড ম্যাপিং নিশ্চিত করে। `/tmp` সীমাবদ্ধ tmpfs, যা একই মেমরি বাজেট খরচ করে। প্রস্তুতি অ্যাডাপ্টার 1 MiB করে লেখে এবং একটি payload 128 MiB-তে সীমিত করে; অপারেশনগুলি ম্যানেজারের লক ভাগ করে।

Docker [মেমরি, swap ও CPU-share নিয়ন্ত্রণ](https://docs.docker.com/engine/containers/resource_constraints/) এবং [`--no-healthcheck` বিকল্পের](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck) পার্থক্য নথিভুক্ত করেছে।
