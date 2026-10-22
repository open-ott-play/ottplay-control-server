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

[दस्तावेज़ / Wiki](docs/wiki/Home.hi.md)

खुद होस्ट किया जा सकने वाला एक छोटा सर्वर, जो OTT-play प्लेयर तक रिमोट कमांड पहुँचाता है। प्लेयर बाहर की ओर HTTP अनुरोध भेजते हैं, इसलिए टीवी को कोई सुनने वाला पोर्ट खोलने की ज़रूरत नहीं होती। हर प्लेयर की अपनी कतार और ऐक्सेस टोकन है; कमांड भेजने के लिए अलग एडमिनिस्ट्रेटर टोकन इस्तेमाल होता है।

वैकल्पिक [विश्वसनीय नियंत्रण](docs/reliable-control.md) सीमित अधिकार वाले इंटीग्रेशन क्रेडेंशियल, सीमित मात्रा में टिकाऊ कमांड परिणाम, सटीक सेटिंग सत्यापन के साथ फिर शुरू किया जा सकने वाला Local/Home लागू करना, और लाइव/आर्काइव प्लेबैक का स्पष्ट हस्तांतरण जोड़ता है। इसके लिए इस गाइड वाले नियंत्रक और CLI रिविज़न चाहिए। सटीक सत्यापन और हस्तांतरण के लिए अपडेट किया गया प्लेयर भी चाहिए। नीचे पिन किया गया पुराना इंस्टॉलेशन रिविज़न ये सुविधाएँ नहीं रखता। [आर्किटेक्चर चित्र](docs/assets/reliable-control.svg) दिखाता है कि सेटिंग्स, क्रेडेंशियल और ऑपरेशन की स्थिति कहाँ रहती है।

सर्वर एक पोर्टेबल Go एक्ज़ीक्यूटेबल के रूप में चलता है। वैकल्पिक सर्विस खोज के लिए DNS लाइब्रेरी का संस्करण पिन है। रिलीज़ पैकेज Linux amd64, arm64 और ARMv7; macOS Intel और Apple Silicon; तथा Windows amd64 और arm64 के लिए हैं। कंटेनर इमेज Linux amd64 और arm64 का समर्थन करती हैं। Kubernetes और k3s में शामिल Helm chart इस्तेमाल होता है।

## सर्वर शुरू करें

[रिलीज़](https://github.com/open-ott-play/ottplay-control-server/releases) से उपयुक्त आर्काइव डाउनलोड करें, रिलीज़ मैनिफ़ेस्ट से सत्यापित करें और निकालें। Windows पर नीचे की कमांड में `ottplay-control-server.exe` इस्तेमाल करें।

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` एक नई फ़ाइल में रैंडम, अलग-अलग क्रेडेंशियल बनाता है। Unix पर फ़ाइल केवल मालिक के लिए उपलब्ध होती है। यह मौजूदा फ़ाइल या सिम्बॉलिक लिंक पर लिखने से मना करता है और क्रेडेंशियल कभी प्रिंट नहीं करता। फ़ाइल निजी रखें; Windows में उसकी ACL सर्वर चलाने वाले खाते तक सीमित करें। `config.example.json` फ़ील्ड समझाता है और जानबूझकर अमान्य प्लेसहोल्डर टोकन रखता है।

डिफ़ॉल्ट पता `0.0.0.0:8081` है, यानी LAN समेत सभी IPv4 इंटरफ़ेस। टीवी को सर्वर के LAN पते और पोर्ट से जोड़ें। मौजूदा कॉन्फ़िगरेशन अपना स्पष्ट `listen` मान बनाए रखते हैं; पहले केवल loopback वाले इंस्टॉलेशन को उपलब्ध कराने के लिए इसे `0.0.0.0:8081` करें या `--listen 0.0.0.0:8081` इस्तेमाल करें। प्लेयर के **पेज का सटीक origin** `allowed_origins` में जोड़ें, जैसे `http://192.168.1.20:8443`। स्कीम, होस्टनाम और पोर्ट मिलना चाहिए; पाथ न जोड़ें। खाली origin सूची केवल गैर-ब्राउज़र क्लाइंट को अनुमति देती है। कॉन्फ़िगरेशन बदलाव रीस्टार्ट के बाद लागू होते हैं। डिवाइस और एडमिनिस्ट्रेटर क्रेडेंशियल फिर भी ज़रूरी हैं।

विश्वसनीय LAN के पुराने टीवी ब्राउज़र के लिए HTTP समर्थित है। कनेक्शन अविश्वसनीय नेटवर्क से गुज़रे तो HTTPS इस्तेमाल करें: `serve --config config.json --tls-cert server.crt --tls-key server.key`, या रिवर्स प्रॉक्सी पर TLS समाप्त करें। HTTPS प्लेयर पेज ब्राउज़र में साधारण HTTP सर्वर से नहीं जुड़ सकते। नेटिव ऐप भी ऑपरेटिंग सिस्टम की ट्रांसपोर्ट नीति बनाए रखते हैं; यह सेटिंग उसे ढीला नहीं करती।

सीधा HTTPS लिसनर खोलने से पहले दी गई सर्टिफ़िकेट चेन सत्यापित करता है: RSA कुंजी कम-से-कम 2048 बिट और ECDSA कम-से-कम 224 बिट होनी चाहिए; Ed25519 भी समर्थित है। अपग्रेड से पहले छोटी कुंजी वाला सर्टिफ़िकेट बदलें। [TLS सर्टिफ़िकेट नीति और उसका दायरा](docs/security-design.md#direct-https-certificate-policy) देखें।

## प्लेयर जोड़ें

कमांड-सर्वर कनेक्शन सुविधा वाली प्लेयर रिलीज़ इस्तेमाल करें। **सेटिंग्स → रिमोट कंट्रोल** खोलकर भरें:

1. **सर्वर पता:** सर्वर का IP या होस्टनाम, वैकल्पिक पोर्ट सहित, या पूरा `http://` / `https://` पता। केवल होस्ट देने पर डिफ़ॉल्ट HTTP पोर्ट 8081 है।
2. **ऐक्सेस कोड:** `devices` में उस प्लेयर की एंट्री का `token`; कभी `admin_token` नहीं।
3. **कनेक्ट:** पोलिंग चालू करें और उसी डायलॉग में स्थिति देखें।

प्लेयर यह कनेक्शन केवल वर्तमान इंस्टॉलेशन में रखता है और सेटिंग बैकअप/क्लाउड ट्रांसफ़र से बाहर रखता है। अलग लोकल HTTP लिसनर आवश्यक नहीं है। वेब ब्राउज़र को अनुमत CORS origin चाहिए; `Origin: null` वाले पैकेज्ड टीवी पेज निर्धारित डिवाइस/bootstrap रूट के लिए स्पष्ट `allow_null_origin` सेटिंग इस्तेमाल कर सकते हैं। इससे एडमिनिस्ट्रेटर या डायग्नोस्टिक ऑपरेटर रूट नहीं खुलते।

## टर्मिनल रिमोट

[चित्रों वाली Android OTA गाइड](docs/android-ota.md) सामान्य Capacitor Wi-Fi अपडेट (`ott DEVICE update --variant api22|full`, डिफ़ॉल्ट नवीनतम बीटा; `--version` से रिलीज़ चुनें) और अलग root वाले एजेंट में अंतर बताती है। इसमें हस्ताक्षरित एजेंट तथा APK/helper अपडेट, रिकवरी और इंस्टॉल हुए परिणाम का सत्यापन बताया गया है।

[Android ADB गाइड](docs/android-adb.md) वर्कस्टेशन या Synology से USB ऐक्सेस, स्क्रीन और कीबोर्ड नियंत्रण, स्क्रीनशॉट, लॉग, APK इंस्टॉलेशन और सामान्य रीबूट समझाती है, पुराने टैबलेट की सीमाओं सहित।

वैकल्पिक [DNS-SD खोज और पेयरिंग](docs/discovery.md) से प्लेयर अपने नेटवर्क डोमेन द्वारा नियंत्रक खोज सकते हैं और `ott approve NAME CODE` के बाद अलग डिवाइस टोकन पा सकते हैं। DNS में केवल सर्विस मेटाडेटा रहता है। मौजूदा मैन्युअल कनेक्शन खोज चालू किए बिना चलते रहते हैं।

प्लेयर उपनाम, लाइव वॉल्यूम, चैनल खोज, वर्तमान EPG, प्रदाता चयन और समर्थित प्रदाता कॉन्फ़िगरेशन के लिए Python CLI इस्तेमाल करें: [CLI गाइड](docs/cli.md)। सर्वर और प्लेयर दोनों में अनुरोध/प्रतिक्रिया एक्सटेंशन होना चाहिए।

Python CLI स्रोत की वर्किंग कॉपी से इंस्टॉल होता है, नेटिव सर्वर आर्काइव से अलग। [इंस्टॉलेशन और कॉन्फ़िगरेशन चरण](docs/cli.md#installation-and-connection) macOS/Linux और Windows, Python/PATH सेटअप तथा आवश्यक CLI मॉड्यूल बताते हैं। वे workbench और ऑफ़लाइन सत्यापन वाले समीक्षा किए गए 0.1.1 विकास कमिट पर पिन हैं; स्थिर CLI v0.1.0 और नेटिव सर्वर आर्काइव में ये कमांड नहीं हैं। मौजूदा वर्किंग कॉपी, लॉन्चर या निजी कॉन्फ़िगरेशन बदले बिना [CLI अपडेट या रोलबैक करें](docs/cli.md#update-select-or-roll-back-the-cli)। लोकल लॉन्चर रीड-ओनली [प्लेलिस्ट रिज़ॉल्वर](docs/cli.md#local-playlist-resolver) से नियंत्रक के बिना चैनल, वर्तमान कार्यक्रम और चलने योग्य आर्काइव खोज सकते हैं। इंस्टॉलेशन और कॉन्फ़िगरेशन के बाद:

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

`a1` पंजीकृत प्लेयर का उदाहरण उपनाम है, कंप्यूटर का होस्टनाम नहीं। [पंजीकरण और पेयरिंग](docs/cli.md#register-and-connect-a-player), [उदाहरण सहित सभी कमांड](docs/cli.md#commands), [डिस्कनेक्ट और ऐक्सेस निरस्त करना](docs/cli.md#disconnect-deregister-or-revoke-a-player) तथा [समस्या निवारण](docs/cli.md#troubleshooting) देखें।

हमेशा चालू प्लेयर पर ऐप छोड़े बिना प्लेबैक रोकने और बाद में जगाने के लिए [standby और go](docs/cli.md#stop-playback-while-keeping-the-player-online) इस्तेमाल करें। `stop`, `standby` का उपनाम रहता है; `start` और `wake`, `go` के उपनाम हैं। रिमोट कंट्रोल जुड़ा रहना चाहिए, इसलिए स्टैंडबाय का अर्थ शून्य नेटवर्क ट्रैफ़िक नहीं है। `ott a1` प्लेयर द्वारा घोषित ऑपरेशन दिखाता है। स्वीकार की गई कमांड यह पुष्टि नहीं करती कि बंद होना या प्लेबैक शुरू होना पूरा हो गया है।

[आस्पेक्ट कमांड](docs/cli.md#aspect-ratio-fit-and-fill) के लिए अपडेटेड CLI, नियंत्रक और `aspect` क्षमता बताने वाला प्लेयर चाहिए। पुष्टि के बाद बदलाव लागू होता है; मोड और उसके सेव होने की जाँच के लिए `aspect` फिर पढ़ें। Fill तस्वीर खींचने के बजाय अनुपात बनाए रखकर किनारे काटता है।

रिमोट [स्क्रीनशॉट](docs/cli.md#remote-screenshots) के लिए अपडेटेड CLI, नियंत्रक और समर्थित कैप्चर अडैप्टर वाला प्लेयर चाहिए। प्लेयर का रिमोट कंट्रोल कनेक्शन चालू करना उस नियंत्रक को स्क्रीनशॉट और डायग्नोस्टिक्स की अनुमति देता है; नेटिव स्क्रीनशॉट के लिए अलग अनुमति स्विच नहीं चाहिए। ब्राउज़र में लोकल स्रोत चयन अब भी आवश्यक है। तस्वीरों में प्लेयर सेटिंग, PIN स्क्रीन और दिख रहे क्रेडेंशियल आ सकते हैं, इसलिए केवल विश्वसनीय नियंत्रक से जुड़ें। `ott a1` उपलब्धता और आवश्यक स्रोत चयन बताता है। स्क्रीनशॉट संरचित टेलीमेट्री संग्रह से अलग हैं।

किसी शीर्षक से मेल खाते सभी VPortal वीडियो को शफ़ल करके बार-बार चलाने के लिए:

```sh
ott l vpr "wedding"
```

`vpr` पूरे चयन को एक बार शफ़ल करता है, फिर उसी कतार को दोहराता है। नए शफ़ल के लिए फिर चलाएँ। `vp` कैटलॉग क्रम में परिणाम चलाता है; `vp --list` प्लेबैक बदले बिना सूची दिखाता है। [VPortal कमांड](docs/cli.md#commands) देखें।

प्लेयर की सेव की हुई Plex कॉन्फ़िगरेशन से स्पष्ट Plex क्रम चलाने के लिए:

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

CLI डिफ़ॉल्ट में अधिकतम 45 सेकंड प्रतीक्षा करता है। `ott -t 45 l plex shuffle "Tom and Jerry"` लिखने से वही प्रतिक्रिया टाइमआउट स्पष्ट होता है; वीडियो कितनी देर चलेंगे यह सीमित नहीं होता। बड़ी Plex लाइब्रेरी जुटाने में 10–15 सेकंड के छोटे टाइमआउट से अधिक लग सकता है। `-t SECONDS` या `--timeout SECONDS` प्लेयर उपनाम से पहले रखें। प्रीव्यू उदाहरण और प्लेबैक अनुरोध टाइमआउट होने पर उपाय के लिए [Plex लाइब्रेरी टाइमआउट](docs/cli.md#plex-libraries-by-name) देखें।

Plex कतारों के लिए `plex_queue` लागू करने वाले CLI, नियंत्रक और प्लेयर चाहिए। `plex play --shuffle ID...` दिए गए ID एक बार शफ़ल करता है। मौजूदा कियोस्क नीति उन्हें दोहराती न हो तो कतार अंतिम आइटम के बाद रुकती है। कतार रहते सामान्य `next` और `prev` उसी पर काम करते हैं; `plex stop` उसे साफ़ करता है। प्रीव्यू कभी प्रदाता नहीं बदलता और न वर्तमान प्लेबैक बदलता है। सीमाएँ और तैयार होने की शर्तें [Plex कतार कमांड](docs/cli.md#ordered-plex-queues) में हैं। लाइब्रेरी खोज के लिए `plex_library` बताने वाले अपडेटेड प्लेयर और नियंत्रक भी चाहिए; यह प्लेयर का सेव किया Plex कनेक्शन इस्तेमाल करती है और अस्पष्ट या अधूरा चयन अस्वीकार करती है। [नाम से Plex लाइब्रेरी](docs/cli.md#plex-libraries-by-name) देखें।

### लोकल वीडियो प्रोफ़ाइल सेव करें

वर्तमान प्लेयर और नियंत्रक में लोकल फ़ाइल/फ़ोल्डर `offline` प्रदाता इस्तेमाल करते हैं:

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

पहले प्लेयर पर फ़ाइल/फ़ोल्डर जोड़ें। सेव करने से उनकी कॉन्फ़िगरेशन और चयनित वीडियो दर्ज होता है, प्लेबैक शुरू नहीं होता। एक्सपोर्ट केवल विवरण भेजता है; मीडिया और OS/ब्राउज़र अनुमति डिवाइस पर रहती हैं। दूसरे इंस्टॉलेशन में लोकल बाइंडिंग चाहिए और लोड करना प्लेबैक की पुष्टि नहीं है। सेटअप, क्षमताएँ, पोर्टेबल फ़ॉर्मैट की सीमाएँ और समस्या निवारण के लिए [लोकल वीडियो प्रोफ़ाइल](docs/cli.md#local-videos-profiles) देखें।

### वर्तमान आइटम को प्रोफ़ाइलों के बीच सेव करें

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

फ़ेवरिट सामान्य रीस्टार्ट के बाद भी लक्ष्य प्लेयर पर रहते हैं। एक चुनने पर उसका सेव किया प्रदाता/प्रोफ़ाइल लोड होता है और नया लिंक खोजा जाता है; स्रोत बदलने या आइटम गायब होने पर त्रुटि होती है, दूसरा चैनल/फ़िल्म नहीं लगाया जाता। CLI, नियंत्रक और प्लेयर में `favorites` समर्थन चाहिए; `ott l` उपलब्ध ऑपरेशन दिखाता है। उपनाम, सीमाएँ, पुष्टि का अर्थ और समस्या निवारण [प्रोफ़ाइलों के बीच फ़ेवरिट](docs/cli.md#favorites-across-profiles) में हैं।

### सेव किया सेटअप लोड करें

नाम वाले सेटअप `~/.config/ottplay-control/cli.json` के निजी `presets` भाग में रखें। हर सेटअप में नंबर वाली M3U प्रोफ़ाइलें (प्लेलिस्ट और आर्काइव घंटे), Plex क्रेडेंशियल और लोड के बाद चुनी जाने वाली M3U प्रोफ़ाइल रहती है। [कॉन्फ़िगरेशन उदाहरण](docs/cli.md#named-setups) देखें।

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI पहले पूरा सेटअप जाँचता है, फिर अगला बदलाव भेजने से पहले प्लेयर की पुष्टि की प्रतीक्षा करता है। यह प्रदाता लोड करना संभालता है, Plex और M3U स्लॉट सेव करता है और निर्धारित M3U प्रोफ़ाइल चुनता है। त्रुटि क्रम रोकती है और पुष्ट चरण बताती है; अनिश्चित परिणाम वाली लिखाई अपने-आप दोहराई नहीं जाती। अलग प्रोफ़ाइल सेवा या नियंत्रक रीस्टार्ट नहीं चाहिए। यह कॉन्फ़िगरेशन निजी रखें: इसमें प्रदाता क्रेडेंशियल हैं।

## कमांड भेजें

यह उदाहरण एडमिनिस्ट्रेटर टोकन लोकल निजी कॉन्फ़िगरेशन से पढ़ता है और उसे URL या कमांड-लाइन तर्कों में नहीं रखता:

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

दूसरी कमांड नंबर/नाम से चैनल चुनती हैं, रैंडम चैनल चुनती हैं, प्रदाता बदलती हैं, संदेश दिखाती हैं, समर्थन होने पर M3U प्लेलिस्ट बदलती हैं या प्लेयर बंद करती हैं। उपलब्धता प्लेयर प्लेटफ़ॉर्म और वर्तमान प्रदाता पर निर्भर है। [API कॉन्ट्रैक्ट](docs/api.md) देखें।

अलग VPortal प्रदाता समर्थित हो तो `ott a1 provider
vportal` उसकी अपनी 15 प्रोफ़ाइलें चुनता है। `profile N vportal LINK`, `profile N
name NAME` या `name` और `vportal` वाली निजी `profile-config N FILE` इस्तेमाल करें, फिर `profile N` से कॉन्फ़िगर स्लॉट चुनें। मौजूदा M3U प्रोफ़ाइलें अलग रहती हैं। `profiles` खाते के लिंक या कुंजियाँ दिखाए बिना मेटाडेटा बताता है।

VPortal फ़िल्म या एपिसोड कतार शुरू करने के बाद `ott a1 kiosk on` चयन लॉक करता है। प्लेयर रीलोड के बाद कतार और स्थिति बचाता है तथा रिकवरी के लिए मीडिया URL फिर खोजता है। दूसरा शीर्षक या प्रोफ़ाइल चुनने को `kiosk off`, फिर लॉक करने को `kiosk on` इस्तेमाल करें। कियोस्क रसीद शीर्षक और एपिसोड गिनती दिखाती है; प्रदाता अनुरोध और स्ट्रीम URL प्लेयर पर रहते हैं।

बच्चों के प्लेबैक के लिए `ott a1 kiosk on --strict` इस्तेमाल करें: लोकल टैप वीडियो फ़ुटर दिखाता है। अपडेटेड core प्लेयर उसकी टाइमलाइन पर टैप या ड्रैग करके वर्तमान VPortal/Plex एपिसोड में आगे-पीछे जाने देते हैं; अन्य लोकल नियंत्रण लॉक रहते हैं। पुराने प्लेयर, चैनल कियोस्क और टाइमलाइन जेस्चर रोकने वाले रैपर रीड-ओनली फ़ुटर रखते हैं। मौजूदा लॉक अपना लक्ष्य बनाए रखता है। नियंत्रक से `ott a1 kiosk off` से खोलें। Android वेबसाइटों पर सिस्टम से बाहर निकलने को रोकने के लिए PIN के साथ अलग ऐप पिनिंग चाहिए; [कियोस्क मोड](docs/cli.md#kiosk-mode) देखें।

## रिमोट डायग्नोस्टिक्स

`doctor`, UI/मीडिया `inspect`, ऑपरेशन रसीद खोज, साक्ष्य बंडल, `health`, वेब/नेटिव `media-progress` और ऑफ़लाइन `report verify` के लिए [रीड-ओनली workbench क्विक स्टार्ट](docs/workbench.md#quick-start) से शुरू करें। यह मौजूदा एडमिनिस्ट्रेटर CLI कॉन्फ़िगरेशन और प्लेयर उपनाम इस्तेमाल करता है। नेटिव मार्ग अलग से तैयार Android रखरखाव एजेंट के लिए है; Tauri प्लेयर वेब निरीक्षण इस्तेमाल करते हैं। रिपोर्ट डिकोडर की प्रगति और वास्तविक स्क्रीन/आवाज़ के सत्यापन में अंतर बताती हैं। गायब क्षमताएँ, अनुपलब्ध मार्ग या अधूरे साक्ष्य के लिए [workbench समस्या निवारण](docs/workbench.md#troubleshooting) देखें।

वैकल्पिक [प्रोटोकॉल 2 डायग्नोस्टिक्स](docs/remote-diagnostics.md) हर रनटाइम के लिए स्पष्ट रूप से चालू सत्र, अलग सीमित ऑपरेटर क्रेडेंशियल और सीमित संरचित टेलीमेट्री देते हैं। चालू प्लेयर कनेक्शन एक और विश्वास सवाल के बिना लोकल अनुमति देता है; हर कैप्चर के सत्र की वैध अवधि फिर भी सीमित रहती है। डायग्नोस्टिक नियंत्रण पोलिंग मौजूदा कमांड कतार से स्वतंत्र है।

[डायग्नोस्टिक CLI और MCP गाइड](docs/diagnostics-cli.md) इंस्टॉलेशन, ऑपरेटर क्रेडेंशियल, कनेक्शन, हर डायग्नोस्टिक कमांड/टूल, कैप्चर और सुधार उदाहरण, रनटाइम ऐक्सेस निरस्तीकरण और समस्या निवारण बताती है। `ott diagnostics --help` यह अलग कमांड समूह दिखाता है; सामान्य प्लेयर उपनाम और एडमिनिस्ट्रेटर क्रेडेंशियल डायग्नोस्टिक ऐक्सेस नहीं देते।

## डिप्लॉयमेंट पैकेज

- [Android टैबलेट: सख्त Plex कियोस्क और स्टार्टअप](docs/android-kiosk-setup.md)
- [साझा Android USB प्रबंधन और बूट रिकवरी](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS और Windows](docs/deployment.md)
- [कॉन्फ़िगरेशन, डिलीवरी अर्थ और सीमाएँ](docs/api.md)
- [रिलीज़ और सत्यापन प्रक्रिया](docs/development.md)

कतारें सीमित हैं, डिफ़ॉल्ट में 60 सेकंड बाद समाप्त होती हैं और मेमोरी में रहती हैं। सर्वर रीस्टार्ट लंबित कमांड हटा देता है। एक रेप्लिका चलाएँ; स्वतंत्र रेप्लिकाएँ कतार साझा नहीं करतीं। प्लेयर डिलीवरी के प्रयास की पुष्टि करता है, वास्तविक प्लेबैक या हार्डवेयर वॉल्यूम की नहीं। नेटवर्क रीट्राइ का डुप्लिकेट हटाना चालू प्लेयर सत्र के भीतर होता है, ऐप रीस्टार्ट के पार नहीं।

यह सर्वर [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) के वैकल्पिक `local_proxy.py` उदाहरण की कमांड API का पालन करता है, अलग एडमिनिस्ट्रेटर/डिवाइस क्रेडेंशियल और पुष्टि वाली डिलीवरी के साथ। पुराना प्रॉक्सी अलग घटक रहता है। यह प्रोजेक्ट मीडिया प्रॉक्सी नहीं करता और प्लेयर की HLS, EPG या प्रदाता नेटवर्किंग नहीं बदलता।

## लाइसेंस

MIT। साथ में शामिल रिलीज़ टूल का श्रेय `NOTICE` और `LICENSES/` में दर्ज है।

## प्रोजेक्ट रखरखाव

[योगदान और परीक्षण आवश्यकताएँ](CONTRIBUTING.md), [सुरक्षा रिपोर्ट नीति](SECURITY.md), [सुरक्षा डिज़ाइन](docs/security-design.md) और [OpenSSF साक्ष्य व शेष मानदंड](docs/openssf-evidence.md) देखें।

वर्तमान प्लेयर में Plex कतार 500 ID तक स्वीकार करती है। एक बार शफ़ल को `ott f10 plex play --shuffle ID...` चलाएँ, फिर Plex प्रदाता चुना हो तो कतार दोहराने और लॉक करने को `ott f10 kiosk on --strict` इस्तेमाल करें। `ott f10 kiosk off` लॉक खोलता है। पुराने प्लेयर अपनी घोषित कतार सीमा बनाए रखते हैं।

## रिलीज़ कैंडिडेट फ़ज़िंग

सार्वजनिक रिलीज़ कैंडिडेट [फ़ज़िंग गाइड](docs/fuzzing.md) में वर्णित इनपुट सीमाओं पर सीमित नेटिव फ़ज़िंग चलाते हैं। म्यूटेशन अभियान PR, बीटा या नाइटली बिल्ड पर नहीं चलते। गाइड दायरा, बजट, साक्ष्य और रिग्रेशन दोहराने के तरीके बताती है।

## समुदाय और सुरक्षा साक्ष्य

[योगदान](CONTRIBUTING.md), [गवर्नेंस](GOVERNANCE.md), [आचार संहिता](CODE_OF_CONDUCT.md), [रोडमैप](ROADMAP.md) और [OpenSSF साक्ष्य](docs/openssf-evidence.md) देखें। साक्ष्य सूची समर्थित सुरक्षा प्रोफ़ाइल, वास्तविक परीक्षण परिणाम और शेष Silver आवश्यकताओं में अंतर करती है।
