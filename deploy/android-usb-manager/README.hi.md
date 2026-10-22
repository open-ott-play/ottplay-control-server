# Android USB परिवहन इमेज

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

इस डायरेक्टरी में साझा प्रबंधक के परिवहन कंटेनर के डिप्लॉयमेंट टेम्पलेट और [`Dockerfile`](Dockerfile) हैं। Python प्रबंधक USB होस्ट पर चलता है। इमेज ADB, Python और परमाणु रखरखाव एडाप्टर के औज़ार देती है; इसमें दूसरी डिवाइस रजिस्ट्री या पुनर्प्राप्ति डेमन नहीं है। तैयारी, कमांड, माइग्रेशन, पुनर्प्राप्ति की सीमाओं और रोलबैक के लिए [ऑपरेटर गाइड](../../docs/android-usb-manager.md) देखें।

Git checkout के बिना, एक ही [रिलीज़](https://github.com/open-ott-play/ottplay-control-server/releases) से `ottplay-control-server-deployment.tar.gz` और `release-manifest.json` डाउनलोड करें। आर्काइव का डाइजेस्ट सत्यापित करके [डिप्लॉयमेंट पैकेज प्राप्त करना](../../docs/android-usb-manager.md#get-the-deployment-package) के अनुसार नई डायरेक्टरी में निकालें। पैकेज में यह रेसिपी और `tools/` के छह होस्ट/रिमोट स्क्रिप्ट हैं; डायरेक्टरी संरचना बनाए रखें। `v0.1.5-beta.6` सहित पुराने पैकेजों में ये स्क्रिप्ट नहीं हैं और उसके बदले संबंधित स्रोत आर्काइव चाहिए। पैकेज में बनी हुई USB परिवहन इमेज नहीं है। कंट्रोल सर्वर का OCI रिलीज़ आर्टिफैक्ट अलग उद्देश्य के लिए है।

## स्रोत और आर्टिफैक्ट की उत्पत्ति

आधार `python:3.12.14-slim-trixie` है, जिसे `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b` पर पिन किया गया है। आधार, APT निर्देश और हैश-लॉक किया गया `websocket-client==1.9.0` इंस्टॉलेशन [PR 128 के संशोधन b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager) में समीक्षित हैं। उपलब्ध बिल्ड कैश दोबारा उपयोग करने के लिए निर्देशों का पाठ रखा गया है। वैकल्पिक WebSocket पैकेज मौजूदा सामान्य रखरखाव क्लाइंटों का समर्थन करता है; माइग्रेट किए गए CDP एडाप्टर केवल Python मानक लाइब्रेरी भी इस्तेमाल कर सकते हैं।

यह रेसिपी लॉक किए wheel को स्थापित करने के बाद स्थापित `pip` पैकेज हटाती है, PR 128 के प्रबंधक और पुनर्प्राप्ति कोड को बाहर रखती है, ADB को अग्रभूमि में चलाती है और विरासत में मिले स्वास्थ्य परीक्षण बंद करती है। रनटाइम इंस्टॉलर हटाने से उपलब्ध आक्रमण क्षेत्र कम होता है; यह बिना दोष वाले भेद्यता स्कैन का प्रमाण नहीं है।

PR 128 का समीक्षित आर्टिफैक्ट `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a` था। इसमें अलग प्रवेश बिंदु और असंगत heartbeat स्वास्थ्य परीक्षण है। यह संदर्भ आर्टिफैक्ट है, इस Dockerfile का बिल्ड नहीं। इसकी इमेज ID, स्रोत की उत्पत्ति या स्कैन नतीजों को इस रेसिपी का न बताएं।

APT रिपॉज़िटरी snapshot पर पिन नहीं हैं। इसलिए समान आधार डाइजेस्ट और Python wheel हैश पर भी भविष्य का साफ़ पुनर्निर्माण नए OS पैकेज चुन सकता है। बिल्ड की योग्यता जाँचते समय सटीक स्रोत संशोधन, बिल्डर/प्लेटफ़ॉर्म, बनी इमेज ID, पैकेज संस्करण और स्कैन दर्ज करें। प्रोडक्शन कॉन्फ़िगरेशन को उसी योग्य अपरिवर्तनीय इमेज ID या डाइजेस्ट का उपयोग करना चाहिए।

## व्यस्त USB होस्ट को बाधित किए बिना बिल्ड

अलग बिल्डर पर निर्माण और स्कैन को प्राथमिकता दें। लक्ष्य होस्ट को केवल योग्य इमेज चाहिए, पैकेज डाउनलोड या बिल्ड टूलचेन नहीं।

तैयार स्थानीय बिल्डर का पुनः उपयोग करते समय पहले सुनिश्चित करें कि सटीक आधार इमेज उपलब्ध है और APT तथा wheel इंस्टॉलेशन परतें कैश में हैं। केवल PR 128 की अंतिम इमेज लोड करना इनमें से किसी शर्त को सिद्ध नहीं करता। रिपॉज़िटरी के मूल या निकाली गई डिप्लॉयमेंट डायरेक्टरी से जानबूझकर ऑफलाइन प्रयास यह है:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` बिल्ड चरणों पर लागू है; यह सामान्य गारंटी नहीं कि आधार ढूँढ़ते समय बिल्डर कभी रजिस्ट्री से संपर्क नहीं करेगा। सीमित होस्ट पर प्रक्रिया अपनाने से पहले आधार का स्थानीय समाधान सुनिश्चित करें। APT या wheel परत गायब हो तो USB होस्ट पर चुपचाप नेटवर्क पुनर्निर्माण की अनुमति देने के बजाय प्रयास रोकें।

BuildKit बिल्ड चरण के नेटवर्क मोड को कैश कुंजी में शामिल करता है। इसलिए डिफ़ॉल्ट नेटवर्क मोड में बना कैश ऊपर के ऑफलाइन प्रयास में नहीं मिल सकता। सटीक आधार और निर्देश श्रृंखला जाँचने के बाद ही कैश दोबारा उपयोग करें; कोई पैकेज इंस्टॉलेशन चरण `CACHED` दिखाने के बजाय चले तो रुकें। अंतिम `pip` हटाना स्पष्ट रूप से `RUN --network=none` उपयोग करता है; पिछले कैश किए चरण मूल मोड में हों तब भी इसे नेटवर्क नहीं चाहिए।

प्रोडक्शन कॉन्फ़िगरेशन बदलने से पहले Python और ADB संस्करण जाँचें, सटीक बनी इमेज स्कैन करें, बाइनरी फ़ाइल हस्तांतरण और APK तैयारी सत्यापित करें, फिर गाइड के डिवाइस पहचान, कुंजी प्राधिकरण, स्लीप और बूट स्वीकृति परीक्षण करें। पुराने Android डिवाइस से ADB 34 की बातचीत का परीक्षण ज़रूरी है; सिर्फ़ संस्करण नंबर KitKat या Fire संगतता सिद्ध नहीं करता। रोलबैक की आवश्यकता समाप्त होने तक वर्तमान इमेज और निजी डिप्लॉयमेंट बैकअप रखें।

## रनटाइम संसाधन बजट

यह Dockerfile नहीं, होस्ट प्रबंधक 256 MiB मेमोरी, मेमोरी और swap मिलाकर 256 MiB तथा CPU shares 128 लागू करता है; कोई कठोर CPU या PID कोटा नहीं है। यह `--no-healthcheck`, `--network none`, केवल-पढ़ने योग्य रूट फ़ाइल सिस्टम, capabilities हटाना और सटीक USB नोड मैपिंग लागू करता है। `/tmp` सीमित tmpfs है जो उसी मेमोरी बजट से खर्च करता है। स्टेजिंग एडाप्टर 1 MiB के लेखन करता है और एक payload को 128 MiB तक सीमित रखता है; ऑपरेशन प्रबंधक का लॉक साझा करते हैं।

Docker [मेमोरी, swap और CPU-share नियंत्रणों](https://docs.docker.com/engine/containers/resource_constraints/) और [`--no-healthcheck` विकल्प](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck) के बीच अंतर बताता है।
