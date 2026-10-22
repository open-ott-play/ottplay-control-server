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

<div dir="rtl" lang="he">

[תיעוד / Wiki](docs/wiki/Home.he.md)

שרת קטן לאירוח עצמי שמעביר פקודות מרחוק לנגני OTT-play. הנגנים שולחים בקשות HTTP יוצאות, ולכן הטלוויזיה אינה צריכה לחשוף פורט מאזין. לכל נגן תור וטוקן גישה משלו; טוקן מנהל נפרד משמש לשליחת פקודות.

האפשרות של [שליטה אמינה](docs/reliable-control.md) מוסיפה פרטי גישה לאינטגרציות בהיקף מוגבל, שמירה עמידה ומוגבלת של תוצאות פקודות, החלה ניתנת להמשך של הגדרות Local/Home עם אימות מדויק, והעברה מפורשת בין ניגון חי לניגון מהארכיון. היכולות דורשות גרסאות בקר ו־CLI המכילות מדריך זה. אימות מדויק והעברת ניגון דורשים גם נגן מעודכן. גרסת ההתקנה הישנה המוצמדת בהמשך אינה כוללת יכולות אלה. [תרשים הארכיטקטורה](docs/assets/reliable-control.svg) מראה היכן נשמרים ההגדרות, פרטי הגישה ומצב הפעולות.

השרת פועל כקובץ הרצה Go יחיד ונייד, עם גרסה מוצמדת של ספריית DNS לגילוי שירותים אופציונלי. חבילות הגרסה תומכות ב־Linux amd64, arm64 ו־ARMv7; ב־macOS Intel ו־Apple Silicon; וב־Windows amd64 ו־arm64. תמונות הקונטיינר תומכות ב־Linux amd64 ו־arm64. Kubernetes ו־k3s משתמשים ב־Helm chart המצורף.

## הפעלת שרת

הורידו את הארכיון המתאים מתוך [הגרסאות](https://github.com/open-ott-play/ottplay-control-server/releases), אמתו אותו מול מניפסט הגרסה וחלצו אותו. ב־Windows השתמשו ב־`ottplay-control-server.exe` בפקודות הבאות.

</div>

<div dir="ltr">

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

</div>

<div dir="rtl" lang="he">

`init` יוצר פרטי גישה אקראיים ושונים בקובץ חדש, שב־Unix רק בעליו רשאי לגשת אליו. הוא מסרב לדרוס קובץ או קישור סמלי קיים ולעולם אינו מדפיס את פרטי הגישה. שמרו את הקובץ פרטי; ב־Windows הגבילו את ה־ACL שלו לחשבון שמריץ את השרת. `config.example.json` מתעד את השדות ומכיל במכוון טוקנים לדוגמה שאינם תקפים.

כתובת ברירת המחדל היא `0.0.0.0:8081`, כלומר כל ממשקי IPv4, לרבות LAN. חברו טלוויזיות באמצעות כתובת ה־LAN והפורט של השרת. תצורות קיימות שומרות על ערך `listen` המפורש שלהן; שנו אותו ל־`0.0.0.0:8081` או השתמשו ב־`--listen 0.0.0.0:8081` כדי לחשוף התקנה שהייתה מוגבלת ל־loopback. הוסיפו את **מקור הדף המדויק** של הנגן ל־`allowed_origins`, למשל `http://192.168.1.20:8443`. הסכמה, שם המארח והפורט חייבים להתאים; אין להוסיף נתיב. רשימת מקורות ריקה מאפשרת רק לקוחות שאינם דפדפנים. שינויים בתצורה נכנסים לתוקף לאחר הפעלה מחדש. פרטי הגישה למכשיר ולמנהל עדיין נדרשים.

HTTP נתמך לדפדפני טלוויזיה ישנים ברשת LAN מהימנה. כשהחיבור עובר ברשת לא מהימנה השתמשו ב־HTTPS: `serve --config config.json --tls-cert server.crt --tls-key server.key`, או סיימו TLS בפרוקסי הפוך. דפי נגן שנטענו ב־HTTPS אינם יכולים להתחבר לשרת HTTP רגיל בדפדפן. גם יישומים מקוריים שומרים על מדיניות התעבורה של מערכת ההפעלה; הגדרה זו אינה מקלה אותה.

HTTPS ישיר מאמת את שרשרת האישורים שסופקה לפני פתיחת ההאזנה: מפתחות RSA חייבים להיות באורך 2048 סיביות לפחות, ומפתחות ECDSA באורך 224 סיביות לפחות; גם Ed25519 נתמך. החליפו אישור בעל מפתח קצר מדי לפני השדרוג. ראו [מדיניות אישורי TLS והיקפה](docs/security-design.md#direct-https-certificate-policy).

## חיבור הנגן

השתמשו בגרסת נגן הכוללת חיבור לשרת הפקודות. פתחו **הגדרות → שליטה מרחוק** והזינו:

1. **כתובת השרת:** כתובת IP או שם מארח, עם פורט לפי הצורך, או כתובת מלאה `http://` / `https://`. מארח ללא סכמה משתמש כברירת מחדל ב־HTTP בפורט 8081.
2. **קוד גישה:** ה־`token` של רשומת הנגן ב־`devices`, ולעולם לא `admin_token`.
3. **התחברות:** הפעילו תשאול ובדקו את המצב המוצג באותה תיבת דו־שיח.

הנגן שומר את החיבור רק בהתקנה הנוכחית ואינו כולל אותו בגיבויי ההגדרות או בהעברה בענן. אין צורך במאזין HTTP המקומי הנפרד. דפדפני אינטרנט דורשים מקור CORS מורשה; דפי טלוויזיה ארוזים עם `Origin: null` יכולים להשתמש בהגדרה המפורשת `allow_null_origin` לנתיבי המכשיר והאתחול המיועדים לכך. הדבר אינו מאפשר נתיבי מנהל או מפעיל אבחון.

## שלט במסוף

[המדריך המאויר ל־Android OTA](docs/android-ota.md) מבדיל בין עדכוני Capacitor רגילים ב־Wi-Fi (`ott DEVICE update --variant api22|full`, הבטא האחרונה כברירת מחדל; `--version` בוחר גרסה) לבין סוכן נפרד עם הרשאות root. הוא מסביר עדכונים חתומים של הסוכן ושל APK/helper, התאוששות ואימות התוצאה המותקנת.

[מדריך Android ADB](docs/android-adb.md) מכסה גישת USB דרך תחנת עבודה או Synology, שליטה במסך ובמקלדת, צילומי מסך, יומנים, התקנת APK והפעלה מחדש רגילה, לרבות מגבלות טאבלטים ישנים.

[גילוי וצימוד DNS-SD](docs/discovery.md) האופציונליים מאפשרים לנגנים למצוא את הבקר דרך תחום הרשת שלהם ולקבל טוקן מכשיר אישי לאחר `ott approve NAME CODE`. DNS מכיל מטא־נתונים של השירות בלבד. חיבורים ידניים קיימים ממשיכים לפעול גם בלי להפעיל גילוי.

השתמשו ב־Python CLI לכינויים של נגנים, עוצמת קול בזמן אמת, חיפוש ערוצים, EPG נוכחי, בחירת ספק ותצורת ספקים נתמכים: [מדריך CLI](docs/cli.md). השרת והנגן חייבים שניהם לכלול את הרחבת הבקשה/תגובה.

Python CLI מותקן מעותק עבודה של המקור, בנפרד מארכיון השרת המקורי. [שלבי ההתקנה והתצורה](docs/cli.md#installation-and-connection) מכסים macOS/Linux ו־Windows, הגדרת Python/PATH ומודולי CLI נדרשים. הם מצמידים commit פיתוח 0.1.1 שנבדק, הכולל workbench ואימות לא מקוון; CLI יציב v0.1.0 וארכיוני השרת המקורי אינם כוללים פקודות אלה. אפשר [לעדכן את CLI או לחזור לגרסה קודמת](docs/cli.md#update-select-or-roll-back-the-cli) בלי להחליף עותק עבודה, משגר או תצורה פרטית קיימים. משגרים מקומיים יכולים להשתמש ב־[playlist resolver](docs/cli.md#local-playlist-resolver) לקריאה בלבד כדי לחפש ערוצים, תוכניות נוכחיות וארכיונים הניתנים לניגון ללא בקר. לאחר ההתקנה והתצורה:

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

<div dir="rtl" lang="he">

`a1` הוא כינוי לדוגמה של נגן רשום, לא שם מארח של מחשב. ראו [רישום וצימוד](docs/cli.md#register-and-connect-a-player), [כל הפקודות עם דוגמאות](docs/cli.md#commands), [ניתוק וביטול גישה](docs/cli.md#disconnect-deregister-or-revoke-a-player) ו־[פתרון בעיות](docs/cli.md#troubleshooting).

לנגן הפועל תמיד, השתמשו ב־[standby ו־go](docs/cli.md#stop-playback-while-keeping-the-player-online) כדי לעצור ניגון ולהעיר אותו מאוחר יותר בלי לצאת מהיישום. `stop` נשאר כינוי של `standby`;‏ `start` ו־`wake` נשארים כינויים של `go`. השליטה מרחוק חייבת להישאר מחוברת, ולכן מצב המתנה אינו מבטיח אפס תעבורת רשת. `ott a1` מציג את הפעולות שהנגן מפרסם. קבלת פקודה אינה אישור שהכיבוי או תחילת הניגון הושלמו.

[פקודות יחס התמונה](docs/cli.md#aspect-ratio-fit-and-fill) דורשות CLI ובקר מעודכנים ונגן שמפרסם יכולת `aspect`. השינוי מוחל לאחר אישור; קראו שוב את `aspect` כדי לוודא את המצב ואם נשמר. Fill חותך באופן יחסי במקום למתוח את התמונה.

[צילומי מסך](docs/cli.md#remote-screenshots) מרוחקים דורשים CLI ובקר מעודכנים ונגן עם מתאם לכידה נתמך. הפעלת חיבור השליטה מרחוק של הנגן מאשרת לבקר צילומי מסך ואבחון; צילומים מקוריים אינם דורשים מתג הרשאה נוסף. דפדפנים עדיין דורשים בחירת מקור מקומית. התמונות עשויות לכלול הגדרות נגן, מסכי PIN ופרטי גישה גלויים, לכן התחברו רק לבקר מהימן. `ott a1` מדווח על זמינות ועל בחירת מקור נדרשת. צילומי מסך נפרדים מאיסוף טלמטריה מובנית.

כדי לערבב את כל סרטוני VPortal התואמים לכותרת ולנגן אותם שוב ושוב:

</div>

<div dir="ltr">

```sh
ott l vpr "wedding"
```

</div>

<div dir="rtl" lang="he">

`vpr` מערבב את כל הבחירה פעם אחת ואז חוזר על התור. הפעילו אותו שוב לערבוב חדש. `vp` מנגן תוצאות בסדר הקטלוג; `vp --list` מציג אותן בלי לשנות ניגון. ראו [פקודות VPortal](docs/cli.md#commands).

לרצף Plex מפורש המשתמש בתצורת Plex השמורה בנגן:

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

<div dir="rtl" lang="he">

CLI ממתין עד 45 שניות כברירת מחדל. כתיבת `ott -t 45 l plex shuffle "Tom and Jerry"` מציינת במפורש את אותו זמן קצוב לתגובה; היא אינה מגבילה את משך ניגון הסרטונים. איסוף ספריות Plex גדולות עשוי להימשך יותר מזמן קצוב קצר של 10–15 שניות. הציבו `-t SECONDS` או `--timeout SECONDS` לפני כינוי הנגן. ראו [זמנים קצובים לספריות Plex](docs/cli.md#plex-libraries-by-name) לדוגמאות תצוגה מקדימה ולפעולות במקרה של חריגה בזמן בקשת ניגון.

תורי Plex דורשים CLI, בקר ונגן המממשים `plex_queue`.‏ `plex play --shuffle ID...` מערבב את המזהים שסופקו פעם אחת. התורים נעצרים לאחר הפריט האחרון, אלא אם מדיניות קיוסק קיימת חוזרת עליהם. כל עוד יש תור, `next` ו־`prev` הרגילים פועלים עליו; `plex stop` מנקה אותו. תצוגה מקדימה אינה מחליפה ספק או ניגון נוכחי. ראו [פקודות תור Plex](docs/cli.md#ordered-plex-queues) למגבלות ולמוכנות. חיפוש ספריות דורש גם נגן ובקר מעודכנים המפרסמים `plex_library`; הוא משתמש בחיבור Plex השמור בנגן ודוחה בחירות לא חד־משמעיות או חסרות. ראו [ספריות Plex לפי שם](docs/cli.md#plex-libraries-by-name).

### שמירת פרופילים לסרטונים מקומיים

בנגן ובקר עדכניים, קבצים/תיקיות מקומיים משתמשים בספק `offline`:

</div>

<div dir="ltr">

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

</div>

<div dir="rtl" lang="he">

חברו תחילה את הקבצים/תיקיות בנגן. השמירה מתעדת את תצורתם ואת הסרטון הנבחר בלי להתחיל ניגון. הייצוא מעביר תיאורים בלבד; המדיה והרשאות מערכת ההפעלה/הדפדפן נשארות במכשיר. התקנה אחרת דורשת שיוך מקומי, וטעינה אינה מעידה על ניגון מאומת. ראו [פרופילים לסרטונים מקומיים](docs/cli.md#local-videos-profiles) להגדרה, יכולות, מגבלות הפורמט הנייד ופתרון בעיות.

### שמירת הפריט הנוכחי בין פרופילים

</div>

<div dir="ltr">

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

</div>

<div dir="rtl" lang="he">

המועדפים נשמרים בנגן היעד גם לאחר הפעלות מחדש רגילות. בחירת מועדף טוענת את הספק/הפרופיל השמור ופותרת קישור חדש; מקור שהשתנה או פריט חסר גורמים לכשל בלי להחליף בערוץ או סרט אחר. CLI, הבקר והנגן חייבים לתמוך ב־`favorites`;‏ `ott l` מציג פעולות זמינות. ראו [מועדפים בין פרופילים](docs/cli.md#favorites-across-profiles) לכינויים, מגבלות, משמעות האישור ופתרון בעיות.

### טעינת תצורה שמורה

שמרו תצורות בעלות שם בסעיף הפרטי `presets` של `~/.config/ottplay-control/cli.json`. כל תצורה מכילה פרופילי M3U ממוספרים (רשימת ניגון ושעות ארכיון), פרטי גישה ל־Plex ופרופיל M3U שייבחר לאחר הטעינה. ראו [דוגמת התצורה](docs/cli.md#named-setups).

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

<div dir="rtl" lang="he">

CLI מאמת תחילה את התצורה כולה, ואז ממתין לאישור הנגן לפני שליחת כל שינוי נוסף. הוא מטפל בטעינת הספק, שומר את Plex ואת משבצות M3U ובוחר את פרופיל M3U שהוגדר. שגיאה עוצרת את הרצף ומדווחת אילו צעדים אושרו; כתיבה שתוצאתה אינה ודאית לעולם אינה חוזרת אוטומטית. אין צורך בשירות פרופילים נוסף או בהפעלה מחדש של הבקר. שמרו את התצורה פרטית: היא מכילה פרטי גישה לספקים.

## שליחת פקודה

דוגמה זו קוראת את טוקן המנהל מהתצורה המקומית הפרטית בלי להכניסו לכתובת URL או לארגומנטים של שורת הפקודה:

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

<div dir="rtl" lang="he">

פקודות אחרות בוחרות ערוץ לפי מספר או שם, בוחרות ערוץ אקראי, מחליפות ספקים, מציגות הודעה, משנות רשימת M3U כאשר נתמך או יוצאות מהנגן. הזמינות תלויה בפלטפורמת הנגן ובספק הנוכחי. ראו [חוזה ה־API](docs/api.md).

בנגן התומך בספק VPortal העצמאי, `ott a1 provider
vportal` בוחר את 15 הפרופילים שלו. השתמשו ב־`profile N vportal LINK`,‏ `profile N
name NAME` או בקובץ פרטי `profile-config N FILE` המכיל `name` ו־`vportal`, ואז בחרו את המשבצת שהוגדרה באמצעות `profile N`. פרופילי M3U קיימים נשארים נפרדים. `profiles` מדווח מטא־נתונים בלי לחשוף קישורי חשבון או מפתחות.

לאחר התחלת סרט או תור פרקים ב־VPortal,‏ `ott a1 kiosk on` נועל את הבחירה. הנגן שומר את התור והמיקום לאחר טעינות מחדש ופותר שוב כתובות מדיה לשחזור. השתמשו ב־`kiosk off` לבחירת כותרת אחרת או שינוי פרופילים, ואז ב־`kiosk on` לנעילה. אישור הקיוסק מציג כותרת ומספר פרקים; בקשות לספק וכתובות הזרמים נשארות בנגן.

השתמשו ב־`ott a1 kiosk on --strict` לניגון לילדים: הקשה מקומית מציגה את הסרגל התחתון של הסרטון. נגני core מעודכנים מאפשרים הקשה או גרירה בציר הזמן כדי לדלג בתוך פרק VPortal/Plex הנוכחי; שאר הפקדים המקומיים נשארים נעולים. נגנים ישנים, קיוסקים של ערוצים ומעטפות המיירטות מחוות ציר זמן משאירים סרגל לקריאה בלבד. נעילה קיימת שומרת על היעד שלה. שחררו אותה מהבקר באמצעות `ott a1 kiosk off`. באתרי Android, הגנה מפני יציאה דרך המערכת מחייבת הצמדת יישום נפרדת עם PIN; ראו [מצב קיוסק](docs/cli.md#kiosk-mode).

## אבחון מרחוק

התחילו ב־[מדריך המהיר ל־workbench לקריאה בלבד](docs/workbench.md#quick-start) עבור `doctor`,‏ `inspect` של ממשק/מדיה, חיפוש אישורי פעולות, חבילות ראיות, `health`,‏ `media-progress` בדפדפן/באופן מקורי ו־`report verify` לא מקוון. הוא משתמש בתצורת CLI המנהל ובכינויי הנגנים הקיימים. המסלול המקורי מיועד לסוכן תחזוקת Android שהוכן בנפרד; נגני Tauri משתמשים בבדיקת web. הדוחות מבחינים בין התקדמות מפענח לבין אימות פיזי של מסך/קול. ראו [פתרון בעיות workbench](docs/workbench.md#troubleshooting) לגבי יכולות חסרות, מסלולים לא זמינים וראיות לא שלמות.

[אבחון פרוטוקול 2](docs/remote-diagnostics.md) האופציונלי מספק הפעלות שמופעלות במפורש לכל סביבת ריצה, עם פרטי מפעיל נפרדים בהיקף מוגבל וטלמטריה מובנית ומוגבלת. חיבור הנגן הפעיל מספק הרשאה מקומית בלי בקשת אמון נוספת; לכל לכידה עדיין יש משך הפעלה מוגבל. תשאול בקרת האבחון עצמאי מתור הפקודות הקיים.

[מדריך CLI האבחון ו־MCP](docs/diagnostics-cli.md) מכסה התקנה, פרטי מפעיל, חיבור, כל פקודת/כלי אבחון, דוגמאות לכידה ותיקון, ביטול גישת סביבת ריצה ופתרון בעיות. `ott diagnostics --help` מציג משפחת פקודות נפרדת זו; כינויי נגנים רגילים ופרטי מנהל אינם מעניקים גישת אבחון.

## חבילות פריסה

- [טאבלט Android: קיוסק Plex מחמיר והפעלה](docs/android-kiosk-setup.md)
- [ניהול USB Android משותף ושחזור אתחול](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS ו־Windows](docs/deployment.md)
- [תצורה, משמעות המסירה ומגבלות](docs/api.md)
- [תהליך פרסום ואימות](docs/development.md)

התורים מוגבלים, פגים לאחר 60 שניות כברירת מחדל ונמצאים בזיכרון. הפעלת השרת מחדש משליכה פקודות ממתינות. הפעילו עותק שרת אחד; עותקים עצמאיים אינם משתפים תורים. נגן מאשר ניסיון מסירה, לא ניגון מאומת או עוצמת קול חומרתית. ניסיונות רשת חוזרים עוברים הסרת כפילויות בתוך הפעלת נגן פעילה, לא מעבר להפעלה מחדש של היישום.

שרת זה תואם ל־API הפקודות של הדוגמה האופציונלית `local_proxy.py` ב־[OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), עם פרטי מנהל/מכשיר נפרדים ומסירה מאושרת. הפרוקסי הישן נשאר רכיב נפרד. הפרויקט אינו מתווך מדיה ואינו משנה את תקשורת HLS, EPG או הספקים של הנגן.

## רישיון

MIT. ייחוס כלי הפרסום הכלולים מתועד ב־`NOTICE` וב־`LICENSES/`.

## תחזוקת הפרויקט

ראו [דרישות תרומה ובדיקות](CONTRIBUTING.md), [מדיניות דיווח אבטחה](SECURITY.md), [תכנון אבטחה](docs/security-design.md) ו־[ראיות OpenSSF והקריטריונים שנותרו](docs/openssf-evidence.md).

תורי Plex מקבלים עד 500 מזהים בנגנים עדכניים. השתמשו ב־`ott f10 plex play --shuffle ID...` לערבוב פעם אחת, ואז ב־`ott f10 kiosk on --strict` כשספק Plex נבחר כדי לחזור על התור ולנעול אותו. `ott f10 kiosk off` משחרר את הנעילה. נגנים ישנים שומרים על מגבלת התור שהם מפרסמים.

## Fuzzing לגרסאות מועמדות

גרסאות מועמדות ציבוריות מריצות fuzzing מקורי ומוגבל בגבולות הקלט המתוארים ב־[מדריך fuzzing](docs/fuzzing.md). קמפייני מוטציות אינם רצים ב־PR, בגרסאות בטא או בבניות לילה. המדריך מכסה היקף, תקציבים, ראיות ושחזור רגרסיות.

## קהילה וראיות אבטחה

ראו [תרומה](CONTRIBUTING.md), [ממשל](GOVERNANCE.md), [קוד התנהגות](CODE_OF_CONDUCT.md), [מפת דרכים](ROADMAP.md) ו־[ראיות OpenSSF](docs/openssf-evidence.md). אינדקס הראיות מבחין בין פרופילי אבטחה נתמכים, תוצאות בדיקה בפועל ודרישות Silver שנותרו.

</div>
