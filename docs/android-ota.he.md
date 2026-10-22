<div lang="he" dir="rtl">

# Android OTA: נגנים רגילים וסוכן נפרד עם root

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

עבור **k8a/fire8 ונגני Capacitor אחרים**, השתמשו במעדכן ה־Wi-Fi של הנגן הרגיל, ולא בפקודת הסוכן בעל הרשאות root:

<div dir="ltr">

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

</div>

השמטת `--version` בוחרת את הבטא האחרונה שפורסמה. ב־Android 7+ השתמשו ב־`full` במקום `api22`. ה־CLI מאתר את המהדורה הרשמית, בודק את המניפסט שלה, מכין את ה־APK ומתשאל את מצבו לפני בקשת התקנה אחת. Android עשוי לדרוש אישור בטאבלט. לפני התחזוקה השביתו במפורש את מצב הקיוסק ופתחו את נעילת ההגדרות המוגנות; הפקודה לעולם אינה משנה מדיניות זו. ראו את [נוהל ה־CLI המלא](cli.md#capacitor-apk-updates-over-wi-fi).

נדרשים APK שכבר מותקן ותומך ב־`AppUpdate`, חיבור Wi-Fi ונגן פועל המחובר לבקר. עדכונים רגילים בהמשך אינם דורשים USB, אך אי אפשר להגיע כך לטאבלט לא מקוון או ל־WebView שנעצר. התקנה ראשונה, מעדכן לא זמין, יישום פגום או אובדן רשת עדיין עשויים לדרוש שחזור מקומי. אין בכך הבטחה למתזמן מהדורות אוטומטי או למתקין שקט כללי.

שאר המדריך מתאר **סוכן תחזוקה אחר של a1 עם root**, המוגבל להתקנת Matricom/API19 שהוכנה מראש, `play.ott.kitkat`. הוא אינו כבר מותקן או ניתן לשימוש במכשירי Fire/Capacitor רגילים.

לאחר אתחול התצורה החד־פעמי דרך USB עם root, סוכן Android העצמאי יכול לקבל עדכונים חתומים דרך חיבור ה־HTTPS היוצא שלו. הוא אינו זקוק ל־WebView תקין כדי לעדכן את עצמו. סוכן 0.1.2 תומך גם בחבילה מתואמת של APK ועזר מגע מערכתי, עם קובצי שחזור שמורים ואימות לפני אישור סופי.

המדריך מסביר את התהליך המלא. לשדות המניפסט המדויקים, למגבלות ולדרישות החומרה, השתמשו ב[תיעוד הסוכן המקורי](../native/android-agent/README.md#signed-updates-over-wi-fi).

## מה מתעדכן

![ארכיטקטורת OTA: בתי המהדורה שעברו סקירה נחתמים, מפורסמים ב־HTTPS ישיר, מאומתים בידי הסוכן העצמאי ונבדקים לאחר ההתקנה. פריסת הווב נפרדת.](images/ota-architecture.svg)

- **הסוכן המקורי:** `kind: agent` מחליף את הקובץ הבינרי ARMv7 שלו עצמו. מסלול זה פועל גם כאשר OTT מושבת ויישום HOME אחר נמצא בשימוש.
- **APK בלבד:** `kind: apk` מחליף את המעטפת תוך שמירת נתוניה. OTT חייב להיות מופעל ובחזית. העזר המוטמע במועמד חייב להתאים בדיוק לעזר המערכת המותקן.
- **APK ועזר המגע:** `kind: bundle` מעדכן את שני הרכיבים כזוג. סוכן ישן חייב לקבל קודם עדכון של הסוכן בלבד התומך בחבילות.
- **נגן מתארח:** פרסו את תוצר הווב המאומת ב־origin הקיים, ואז טענו מחדש ובדקו נגן פעיל. עדכון APK אינו פורס את קוד JavaScript המתארח. ראו את [תהליך הפרסום](release-workflow.md).
- **הבקר וה־CLI:** פרסו את הבקר והתקינו בנפרד את ערכת מודולי ה־CLI המלאה. עדכון של אחד מהם אינו מחליף קוד בטאבלט.

קושחה, ליבה, WebView מערכתי, מתווך root וסקריפטי אתחול אינם חלק מפורמט OTA זה. [התיעוד המקורי](../native/android-agent/README.md#bootstrap) מתאר את גבולות האתחול והשחזור. פעולות OTA נתמכות אינן דורשות ADB ברשת או מעטפת מרוחקת שרירותית.

## ממהדורה שנבדקה להתקנה מאושרת

1. **בחרו בתים מאומתים.** מזגו שינויים שעברו סקירה לאחר שה־CI עבר. הורידו את תוצר המהדורה המבוקש ובדקו את גרסת המקור, ראיות הפרסום הבלתי משתנות וסכום הביקורת. חלצו את הבינרי `agent` מהארכיון המקורי; הארכיון עצמו אינו מטען סוכן שניתן להתקנה.
2. **הכינו וחתמו.** השתמשו במפתח Ed25519 הפרטי הקיים, שהמפתח הציבורי שלו קובע בזמן האתחול. שמרו את המפתח ואת פרטי הגישה של המכשיר פרטיים.
3. **פרסמו ובדקו את התעבורה.** פרסמו רק את המטען ואת `signed.json` בנתיבי HTTPS בלתי משתנים. שניהם חייבים להחזיר את הבתים ישירות עם HTTP 200; המעדכן מסרב להפניות. הפעילו את פקודת `verify` של כלי האריזה.
4. **שלחו עדכון אחד.** רשמו את מזהה הבקשה ובדקו מצב לאחר האישור. תגובה שאבדה או שאינה ודאית אינה סיבה לחזור בעיוורון על פעולה.
5. **אשרו את ההשפעה.** השוו גיבובים וגרסאות מותקנים שנקראו כעת למהדורה שהוכנה. בנגן פעיל בדקו גם את יעד הקיוסק, התור והתקדמות הניגון. תמונה וקול פיזיים דורשים תצפית.

דוגמה למהדורת סוכן; החליפו לגרסה שנבחרה ולנתיבים האמיתיים:

<div dir="ltr">

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.4 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.4/agent \
  --manifest-url https://updates.example/0.1.2-beta.4/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.4

# Publish the prepared agent and signed.json, then verify the published bytes.
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.4

ott --receipt --timeout 40 a1 android update \
  https://updates.example/0.1.2-beta.4/signed.json ENVELOPE_SHA256
ott --timeout 30 a1 android status
ott a1 android operation REQUEST_ID
```

</div>

`ENVELOPE_SHA256` הוא `envelope_sha256` של כלי האריזה, ולא גיבוב המטען. `accepted` פירושו קבלה, לא התקנה מוצלחת. החלפה עצמית עשויה להשאיר קבלה ישנה של פעולה במצב `unknown`; סביבת הריצה החדשה, גרסת הסוכן הצפויה והגיבוב המותקן התואם מספקים ראיות להתקנה. [תיעוד ה־CLI](cli.md#independent-android-agent) מתאר ניתוב מקורי עצמאי וטיפול בקבלות.

## כיצד עסקת APK/עזר משתחזרת

![עסקת APK/עזר: בדיקה מוקדמת, שמירת הזוג הקודם, החלה, אימות ואישור; כשלים משחזרים את הזוג הקודם ושחזור לא מלא משאיר שינויי יישום חסומים.](images/ota-transaction.svg)

החבילה מכילה בדיוק `app.apk` ו־`ott-touch-guard`. כלי האריזה יכול ליצור אותה ישירות מ־APK המעטפת החתום באמצעות `--kind bundle --apk`. הוא מחלץ את העזר המוטמע, כך ששני העותקים הנמסרים תואמים. ראו את [פקודת החבילה והדרישות](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

לפני שינוי קבצים, הסוכן דורש זוג מותקן תואם, די מקום פנוי, נגן OTT מגיב בחזית, מחיצת מערכת לקריאה בלבד ותמיכה מוצהרת בשנמוך APK. בקיוסק נעול הוא מאמת את אותו מזהה ערוץ טלוויזיה או מזהי VPortal מסודרים; אורכי תור שווים בלבד אינם מספיקים. תורים נעולים שאינם נתמכים נדחים. גם יישום OTT שהושבת בכוונה נדחה, תוך שמירה על יישום HOME הנוכחי של המשתמש.

קובצי השחזור והעסקה הממתינה נשמרים באחסון עמיד לפני עצירת היישום. הסוכן ממתין לעצירת היישום והעזר הישן, מחליף את העזר אטומית, מחזיר את `/system` לקריאה בלבד, מתקין את ה־APK ובודק את התוצאה. כשל בהחלה או באימות מפעיל שחזור של הזוג הקודם. שחזור בעת אתחול מחזיר קבצים בלי להפעיל OTT או להשתלט על HOME.

אם השחזור אינו יכול להסתיים, `updates.recovery_failed` הוא אמת. אבחון ותיקון חתום של הסוכן בלבד נשארים זמינים; שינויי היישום וכלב השמירה של הניגון חסומים. אין למחוק את העסקה השמורה כדי לכפות התקנה. שחזור קבצים בינריים אינו מבטל העברות נתונים של היישום, והקושחה עדיין יכולה לסרב לשנמוך. ראו את [מגבלות השחזור](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## תוצאות ורישום קבלה

![תרשים אימות: 488 בדיקות CLI, 576 בדיקות כלי פרסום ו־6 בדיקות אורז OTA; טאבלט הייחוס מריץ את הסוכן החדש המאומת, אך הקבלה הפיזית של APK/עזר עדיין ממתינה.](images/ota-validation.svg)

ביקורת המימוש ב־14 באוקטובר 2026 עברה 488 בדיקות CLI (אחת דולגה), 576 בדיקות כלי פרסום, שש בדיקות אורז OTA, בדיקות Go מקוריות למרוצי נתונים ובדיקות סטטיות ובניית ARMv7. המספרים מתארים את גודל חבילות הבדיקות, לא כיסוי או ביצועים. בדיקות הזרקת כשלים מפעילות ארכיונים לא תקינים, עסקאות שנקטעו, שחזור חלקי, שינויי יעד וחסימה בזמן שחזור. סקירה עצמאית גם גילתה ותיקנה בדיקת זהות תור לא מספקת ואימות APK/עזר לא תואם בין המארח למכשיר.

לפי הבדיקה ב־15 באוקטובר 2026:

- מימוש OTA מוזג ב[בקשת המיזוג של הבקר #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) ונכלל ב־[v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- חבילות הסוכן החתומות הנוכחית והקודמת מפורסמות ב[מארח הפצת OTA](https://android-updates.ottplay.here.now/). בתי המטען והמעטפת שלהן הורדו והושוו בהצלחה דרך HTTPS ישיר.
- טאבלט הייחוס עודכן מ־`0.1.1-beta.21` ל־`0.1.2-beta.4` בערוץ OTA העצמאי. גרסת הסוכן הרץ ו־SHA-256 המותקן התאימו למהדורה שנבחרה; סביבת הריצה השתנתה ומזהה אתחול מערכת ההפעלה נשאר זהה. ל־OTT עדיין לא היה תהליך יישום פעיל, ולא נשלחה פעולת הפעלת יישום או בחירת HOME.
- המצב המקורי דיווח על גיבוב עזר המגע המערכתי, ללא חבילה ממתינה וללא כשל שחזור. מטא־נתוני APK לא היו זמינים, ולכן תצפית זו אינה קובעת את גרסת ה־APK המותקן או את הגיבוב שלו.
- לעסקת APK/עזר המתואמת יש אימות בצד המארח; התקנה פיזית, שחזור שנמוך, תמונה ושמע עדיין לא התקבלו בטאבלט הייחוס.

בכל הפצה בהמשך שמרו את מקור המהדורה וסכומי הביקורת, גיבוב המעטפת המוכנה, מזהה הבקשה, המצב המקורי לפני ואחרי ותוצאת הקבלה הפיזית הנפרדת. הורדה מוצלחת או קבלת פקודה אינן תחליף לתצפיות האלה.

</div>
