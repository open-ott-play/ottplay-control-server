<div lang="he" dir="rtl">

# טאבלט Android: קיוסק Plex קשיח והפעלה

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[מדריך ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [מנהל USB](android-usb-manager.md)

טאבלט ייעודי דורש שלוש הגדרות שנבדקות בנפרד: תור התוכן, מדיניות הקיוסק הקשיחה של OTT והפעלת Android. התקנת APK או בדיקת כיבוי מסך וחזרה אינן מוכיחות הפעלה אחרי אתחול מערכת ההפעלה.

## זיהוי והתקנה

1. תעדו את הטאבלט הפיזי, כינוי/UUID ב־OTT, כינוי מנהל USB, מספר סידורי, דגם, Android ID ו־SDK. כינויי OTT ו־USB עשויים להיות שונים; אל תסיקו זהות מכינוי ואל תשתמשו בטוקן של טאבלט אחר.
2. השתמשו בגרסת APK רשמית התואמת ל־SDK, אמתו את SHA-256 של השחרור ואת זהות החבילה/החתימה, והתקינו עם `install -r`. שמרו את נתוני הנגן. השתמשו ב[מנהל המשותף](android-usb-manager.md) כשהוא מחזיק ב־USB; אל תפעילו שרת ADB מתחרה או פקודות `adb` ללא מכשיר יעד מוגדר.
3. צמדו את הנגן עם פרטי הבקר הייחודיים שלו ובדקו `ott TABLET`. טענו את הקביעה המוכנה בעלת השם הרצוי והריצו `ott TABLET load PRESET verify`. קביעות הרשת הביתית ו־localhost של מחשב פיתוח אינן חלופיות.

## בדיקת Plex מתוך הטאבלט

<div dir="ltr">

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

</div>

בדקו מזהה ספרייה, כותרת ומספר פריטים. בקשה מוצלחת ממחשב מנהל המערכת אינה מוכיחה קישוריות של הטאבלט. בכשל בדקו בטאבלט כתובת שמורה, ניתוב, תמיכת TLS ופרטי הזדהות. העדיפו נקודת HTTPS תקפה של השרת. אם קושחה ישנה זקוקה ל־HTTP ב־LAN פרטי, הגבילו זאת להתקנה זו, תעדו שהטוקנים והמדיה עוברים ללא הצפנה ואל תשנו קביעה משותפת או תשביתו אימות תעודות באופן גלובלי. לעולם אל תפרסמו טוקנים או כתובות מדיה פרטיות באבחון או בתיעוד.

בנגנים חדשים יותר המציגים `categories` ב־`caps`, Plex פותח את כל הסרטונים ללא חובה לבחור תיקיות. השתמשו ב־`ott TABLET categories` וב־`ott TABLET category all`; ראו [קטגוריות מדיה](cli.md#media-categories-and-stable-video-selection). שלבי בחירת התיקיות להלן מתייחסים לגרסת הנגן הישנה המצוינת במפורש.

שינוי כתובת שרת Plex מוחק שורשי ספריות שנבחרו. בחרו ספק **Plex**, פתחו **Plex settings → Folders**, סמנו את שורת הספרייה הרצויה (לא רק **Browse folders**), בחרו **Ok** ואחר כך **Save and open library**. בנגן `1.1.58-beta.19` פרטי השרת לבדם יכולים לפתוח בורר תיקיות כאשר `ready` עדיין false; השלימו בחירה לפני פקודות התור. אל תחליפו ל־M3U רק לקבלת `ready: true`: תור Plex יכול לנגן שם, אבל `kiosk on --strict` עשוי לשמור מדיניות M3U ממתינה ללא מדיה קשורה. זה לא נועל או משחזר את תור Plex.

## הפעלה ונעילה של התור

<div dir="ltr">

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

</div>

לתור מסודר השתמשו ב־`plex library 'LIBRARY NAME'` במקום `plex shuffle`. Shuffle בוחר סדר פעם אחת והקיוסק חוזר עליו. אשרו את הפריטים הרצויים, `enabled: true`, `strict: true` ומדיניות הקשורה לתור המדיה הנכון של Plex. מדיניות `waiting` ללא ערוץ או מדיה אינה קיוסק שעבר קבלה. צפו לפחות בשתי דגימות עם מיקום/מונה פריימים מתקדם ובדקו את המסך. אישור או תווית `playing` ללא התקדמות אינם מספיקים. בדקו מעבר טבעי בסוף פריט; אם בדיקת לולאה מלאה אינה מעשית, תעדו את המגבלה במפורש.

קיוסק קשיח שולט בממשק הנגן. הוא אינו רישום כבעל מכשיר Android או הוכחה לחסימת Home, Recents, התראות ו־Settings. שמרו את משגר המערכת ונתיב תחזוקה בנוכחות מפעיל.

## הפעלה נפרדת של התחלה באתחול

בנו ואמתו [OTT Home 0.2.0](../native/android-home/README.md), תוך שמירת מפתח החתימה הקיים לעדכונים. התקינו `play.ott.home` לצד `play.ott.foss`. פתחו `play.ott.home/.StartupActivity` והפעילו **Start OTT after reboot**. ברירת המחדל כבויה. ההגדרה אינה משנה את הקובץ הרשמי, חתימת OTA, נתוני הפרופילים או שיוך לחצן Home.

בקושחה עם בורר Home אפשר לבחור גם **OTT Home → Always**. כשאין בורר (כולל קושחת Fire הישנה שנבדקה), אישור ההפעלה באתחול פועל עצמאית. התקנות שכבר הוגדרו כ־Home ברירת מחדל שומרות את התנהגותן. כבו את שני המנגנונים כדי לחזור לחלוטין למצב הקודם.

`recover_on_boot` של מנהל USB המשותף הוא מנגנון שלישי ועצמאי: הוא דורש NAS וכבל USB, מטפל רק באתחול חדש ולא מפעיל מחדש ברציפות יישום שקרס. הוא אינו תחליף להפעלה אוטונומית. בבדיקתה השאירו את מדיניות השחזור כבויה כדי שלא תסתיר כשל הפעלה של היישום הנלווה. כבדו מסכי נעילה מאובטחים ופתחו כרגיל אם נדרש.

## קבלה ומסירה

שמרו ראיות בפרטיות, ללא פרטי הזדהות באישורים ציבוריים:

- גרסאות APK, גיבובים, מיפוי זהויות פיזית/OTT/USB והקביעה שנבחרה.
- ספריית Plex, מספר/סדר פריטים, מדיניות קשיחה וניגון התחלתי מתקדם.
- מזהה אתחול לפני ואחרי אתחול אמיתי של מערכת ההפעלה; לאחריו `sys.boot_completed=1`.
- ללא הפעלה ידנית של הנגן או שחזור USB בין האתחול לתצפית.
- פעילות OTT בחזית, חיבור מחדש לבקר, שחזור תור/מדיניות קשיחה והתקדמות בניגון. תוצאת `requested` ממקלט האתחול פירושה רק שיגור הבקשה.
- בדיקת לחצן הפעלה נפרדת: מסך כבוי, שמע נעצר, פתיחת נעילה רגילה וחזרה.
- מגבלות מדויקות: צורך בפתיחת נעילה מאובטחת, שחזור בסיוע USB, יציאות פיזיות שלא נבדקו, שגיאות זרם או מחסור בזיכרון. אל תגדירו מקרים אלה כקבלה מלאה.

לפני מסירת הטאבלט הבא השתמשו באותה רשימת בדיקה במקום להעתיק טענת הצלחה של מכשיר קודם. טאבלטי Fire ישנים עשויים לדרוש `hls.js` ל־M3U משום ש־HLS המובנה של היצרן עלול לקרוס; אין בכך הוכחה ליציבות Plex.

## תחזוקה וחזרה לאחור

<div dir="ltr">

```sh
ott TABLET kiosk off
```

</div>

פתחו **OTT Startup**, כבו **Start OTT after reboot** והחזירו את בחירת Home המקורית אם שונתה. לפי הצורך הסירו רק `play.ott.home` דרך תעבורת USB שאומתה; שמרו את `play.ott.foss` ונתוניו. כיבוי ההתחלה אינו משנה את התור ואינו מבטל אוטומטית את הקיוסק.

</div>
