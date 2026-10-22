# Android टैबलेट: Plex सख्त कियोस्क और स्टार्टअप

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB गाइड](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB प्रबंधक](android-usb-manager.md)

समर्पित टैबलेट को तीन अलग-अलग सत्यापित सेटिंग चाहिए: सामग्री कतार, OTT की सख्त कियोस्क नीति और Android स्टार्टअप। APK स्थापित करना या स्क्रीन बंद/फिर शुरू करना जाँचना OS रीबूट के बाद स्टार्टअप सिद्ध नहीं करता।

## पहचान और स्थापना

1. भौतिक टैबलेट, OTT alias/UUID, USB प्रबंधक alias, सीरियल, मॉडल, Android ID और SDK दर्ज करें। OTT और USB नाम अलग हो सकते हैं; alias से पहचान न मानें और दूसरे टैबलेट का टोकन न लें।
2. SDK-संगत आधिकारिक APK वैरिएंट लें, रिलीज़ SHA-256 और पैकेज/हस्ताक्षर पहचान सत्यापित करके `install -r` से स्थापित करें। प्लेयर डेटा बचाएँ। USB यदि [साझा प्रबंधक](android-usb-manager.md) के नियंत्रण में है तो उसी का उपयोग करें; प्रतिस्पर्धी ADB सर्वर या उपकरण निर्दिष्ट किए बिना `adb` कमांड न चलाएँ।
3. प्लेयर को उसके अपने नियंत्रक क्रेडेंशियल से जोड़ें और `ott TABLET` जाँचें। इच्छित नाम वाला प्रीसेट लोड करके `ott TABLET load PRESET verify` चलाएँ। घरेलू नेटवर्क प्रीसेट और विकास मशीन का localhost प्रीसेट अदल-बदल योग्य नहीं हैं।

## टैबलेट से Plex सत्यापन

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

लाइब्रेरी ID, शीर्षक और आइटम संख्या जाँचें। व्यवस्थापक कंप्यूटर का सफल अनुरोध टैबलेट की कनेक्टिविटी साबित नहीं करता। विफलता पर टैबलेट में सहेजा पता, रूटिंग, TLS समर्थन और क्रेडेंशियल जाँचें। सर्वर का वैध HTTPS endpoint चुनें। पुराने फर्मवेयर को निजी LAN HTTP चाहिए तो इसे उसी स्थापना तक सीमित रखें, टोकन और मीडिया का बिना एन्क्रिप्शन LAN परिवहन दर्ज करें, साझा प्रीसेट न बदलें और प्रमाणपत्र जाँच वैश्विक रूप से बंद न करें। निदान या दस्तावेज़ में टोकन या निजी मीडिया URL कभी प्रकाशित न करें।

नए प्लेयर में `caps` के अंतर्गत `categories` उपलब्ध होने पर Plex फ़ोल्डर चुने बिना सभी वीडियो खोलता है। `ott TABLET categories` और `ott TABLET category all` का उपयोग करें; [मीडिया श्रेणियाँ](cli.md#media-categories-and-stable-video-selection) देखें। नीचे दिए गए फ़ोल्डर चयन के चरण स्पष्ट रूप से बताई गई पुरानी प्लेयर संस्करण के लिए हैं।

Plex सर्वर पता बदलने से चुने लाइब्रेरी रूट साफ़ हो जाते हैं। **Plex** प्रदाता चुनें, **Plex settings → Folders** खोलें, इच्छित लाइब्रेरी की पंक्ति चुनें (सिर्फ़ **Browse folders** नहीं), **Ok** और फिर **Save and open library** चुनें। प्लेयर `1.1.58-beta.19` में सिर्फ़ सर्वर क्रेडेंशियल फ़ोल्डर चयन खोल सकते हैं जबकि `ready` false रहता है; कतार कमांड से पहले चयन पूरा करें। केवल `ready: true` पाने के लिए M3U न चुनें: Plex कतार वहाँ चल सकती है, पर `kiosk on --strict` बिना जुड़े मीडिया की प्रतीक्षारत M3U नीति सहेज सकता है। वह Plex कतार लॉक या पुनर्स्थापित नहीं करती।

## कतार शुरू और लॉक करें

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

क्रमबद्ध कतार के लिए `plex shuffle` की जगह `plex library 'LIBRARY NAME'` उपयोग करें। Shuffle एक बार क्रम चुनता है; कियोस्क उसे दोहराता है। इच्छित आइटम, `enabled: true`, `strict: true` और सही Plex मीडिया कतार से जुड़ी नीति जाँचें। चैनल या मीडिया के बिना `waiting` नीति स्वीकृत कियोस्क नहीं है। बढ़ती स्थिति/फ़्रेम संख्या वाले कम-से-कम दो नमूने देखें और स्क्रीन जाँचें। प्रगति बिना रसीद या `playing` लेबल पर्याप्त नहीं है। आइटम समाप्ति का प्राकृतिक संक्रमण परखें; पूरा लूप जाँचना अव्यावहारिक हो तो सीमा स्पष्ट लिखें।

सख्त कियोस्क प्लेयर UI नियंत्रित करता है। यह Android device-owner पंजीकरण नहीं, न ही Home, Recents, सूचनाएँ और Settings बंद होने का प्रमाण है। सिस्टम लॉन्चर और ऑपरेटर की उपस्थिति वाला रखरखाव मार्ग बनाए रखें।

## स्टार्टअप अलग से सक्षम करें

अपडेट के लिए मौजूदा हस्ताक्षर कुंजी रखकर [OTT Home 0.2.0](../native/android-home/README.md) बनाएँ और जाँचें। `play.ott.foss` के साथ `play.ott.home` स्थापित करें। `play.ott.home/.StartupActivity` खोलकर **Start OTT after reboot** चालू करें। डिफ़ॉल्ट में यह बंद है। यह आधिकारिक प्लेयर बाइनरी, OTA हस्ताक्षर, प्रोफ़ाइल डेटा या Home बटन असाइनमेंट नहीं बदलता।

Home चयन वाले फर्मवेयर में चाहें तो **OTT Home → Always** भी चुनें। चयन उपलब्ध न हो (परीक्षित पुराने Fire सहित), तब बूट की अनुमति स्वतंत्र स्टार्टअप देती है। पहले default Home बनाई गई स्थापनाएँ अपना व्यवहार रखती हैं। पूर्ण रोलबैक के लिए दोनों तंत्र बंद करें।

साझा USB प्रबंधक का `recover_on_boot` तीसरा स्वतंत्र तंत्र है: इसे NAS और USB केबल चाहिए, सिर्फ़ नया बूट संभालता है और क्रैश ऐप को लगातार पुनः शुरू नहीं करता। यह स्वायत्त स्टार्टअप का विकल्प नहीं है। उसके परीक्षण में रिकवरी नीति बंद रखें ताकि साथी ऐप का न खुलना न छिपे। सुरक्षित लॉकस्क्रीन का सम्मान करें; माँगने पर सामान्य तरीके से खोलें।

## स्वीकृति और हस्तांतरण

प्रमाण निजी रखें; सार्वजनिक रसीदों में क्रेडेंशियल न हों:

- APK संस्करण, हैश, भौतिक/OTT/USB पहचान का मानचित्र और चुना प्रीसेट।
- Plex लाइब्रेरी, आइटम संख्या/क्रम, सख्त नीति और प्रारंभिक बढ़ता प्लेबैक।
- वास्तविक OS रीबूट से पहले/बाद बूट ID; बाद में `sys.boot_completed=1`।
- रीबूट और अवलोकन के बीच हाथ से प्लेयर चलाना या USB रिकवरी न होना।
- OTT अग्रभूमि गतिविधि, नियंत्रक पुनःकनेक्शन, कतार/सख्त नीति की बहाली और बढ़ता प्लेबैक। बूट रिसीवर का `requested` परिणाम केवल अनुरोध भेजना है।
- अलग पावर-बटन परीक्षण: स्क्रीन बंद, आवाज़ रुकी, सामान्य अनलॉक और पुनरारंभ।
- सटीक सीमाएँ: सुरक्षित अनलॉक आवश्यक, USB-सहायता वाली रिकवरी, अपरखे भौतिक निकास, स्ट्रीम त्रुटियाँ या मेमोरी खत्म होना। इन्हें पूर्ण स्वीकृति न कहें।

अगला टैबलेट सौंपने से पहले पिछले उपकरण की सफलता का दावा कॉपी न करके यही सूची अपनाएँ। पुराने Fire को M3U के लिए `hls.js` चाहिए हो सकता है क्योंकि निर्माता का नेटिव HLS क्रैश कर सकता है; इससे Plex प्लेबैक की स्थिरता साबित नहीं होती।

## रखरखाव और रोलबैक

```sh
ott TABLET kiosk off
```

**OTT Startup** खोलें, **Start OTT after reboot** बंद करें और बदला हो तो मूल Home चयन वापस रखें। ज़रूरत पर सत्यापित USB से सिर्फ़ `play.ott.home` हटाएँ; `play.ott.foss` और उसका डेटा बनाए रखें। स्टार्टअप बंद करने से कतार नहीं बदलती और कियोस्क अपने-आप बंद नहीं होता।
