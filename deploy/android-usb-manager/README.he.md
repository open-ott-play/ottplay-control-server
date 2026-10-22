<div lang="he" dir="rtl">

# תמונת תעבורת USB לאנדרואיד

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

תיקייה זו מכילה תבניות פריסה ואת [`Dockerfile`](Dockerfile) למכולת התעבורה של המנהל המשותף. מנהל Python פועל במארח ה־USB. התמונה מספקת ADB, Python וכלים למתאם התחזוקה האטומית; היא אינה מכילה מרשם מכשירים שני או שירות שחזור נוסף. ראו את [מדריך המפעיל](../../docs/android-usb-manager.md) להכנה, פקודות, הגירה, מגבלות שחזור וחזרה לגרסה קודמת.

ללא עותק עבודה של Git, הורידו את `ottplay-control-server-deployment.tar.gz` ואת `release-manifest.json` מאותה [גרסה](https://github.com/open-ott-play/ottplay-control-server/releases). אמתו את תקציר הארכיון וחלצו לתיקייה חדשה כמתואר ב[קבלת חבילת הפריסה](../../docs/android-usb-manager.md#get-the-deployment-package). החבילה כוללת מתכון זה ושישה סקריפטים למארח/לצד המרוחק תחת `tools/`; שמרו על מבנה התיקיות. חבילות ישנות, כולל `v0.1.5-beta.6`, חסרות סקריפטים אלה ודורשות במקומם את ארכיון המקור התואם. החבילה אינה כוללת תמונת תעבורת USB בנויה. נכס ה־OCI של שרת הבקרה מיועד למטרה אחרת.

## מקור הקוד והתוצרים

הבסיס הוא `python:3.12.14-slim-trixie`, מקובע ל־`sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. הבסיס, הוראת APT והתקנת `websocket-client==1.9.0` הנעולה בגיבוב נבדקו ב[PR 128 ברוויזיה b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). טקסט ההוראות נשמר כדי לאפשר שימוש חוזר במטמון בנייה קיים. חבילת WebSocket האופציונלית תומכת בלקוחות התחזוקה הכלליים הקיימים; מתאמי CDP שהועברו יכולים גם להשתמש רק בספרייה התקנית של Python.

המתכון מסיר את חבילת `pip` המותקנת לאחר התקנת ה־wheel הנעול, משמיט את קוד המנהל והשחזור של PR 128, מפעיל ADB בחזית ומשבית בדיקות בריאות שעברו בירושה. הסרת המתקין מזמן הריצה מצמצמת את שטח התקיפה הזמין; אינה ראיה לסריקת פגיעויות נקייה.

התוצר שנבדק ב־PR 128 היה `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. הוא מכיל נקודת כניסה שונה ובדיקת בריאות heartbeat שאינה תואמת. זהו תוצר ייחוס, לא בנייה של Dockerfile זה. אין לייחס למתכון זה את מזהה התמונה, מקור הקוד או תוצאות הסריקה שלו.

מאגרי APT אינם מקובעים לתצלומי מצב. לכן בנייה נקייה בעתיד עשויה לבחור חבילות מערכת חדשות יותר גם עם אותו תקציר בסיס ואותו גיבוב wheel של Python. בעת אישור בנייה תעדו רוויזיית מקור מדויקת, בונה/פלטפורמה, מזהה תמונה שנוצר, גרסאות חבילות וסריקה. תצורת הייצור חייבת להשתמש במזהה התמונה הבלתי משתנה שאושר או בתקציר שלו.

## בנייה בלי להפריע למארח USB עמוס

העדיפו לבנות ולסרוק בבונה נפרד. מארח היעד זקוק רק לתמונה שאושרה, לא להורדת חבילות או לשרשרת כלי בנייה.

בשימוש חוזר בבונה מקומי מוכן, ודאו קודם שתמונת הבסיס המדויקת זמינה וששכבות התקנת APT ו־wheel שמורות במטמון. טעינת התמונה הסופית של PR 128 בלבד אינה מוכיחה אף תנאי. משורש המאגר או מתיקיית הפריסה שחולצה, ניסיון מכוון ללא רשת הוא:

<div dir="ltr">

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

</div>

`--network=none` חל על שלבי הבנייה; זו אינה הבטחה כללית שהבונה לעולם לא יפנה למרשם בעת איתור הבסיס. אשרו איתור מקומי של הבסיס לפני שימוש בהליך במארח מוגבל. אם חסרה שכבת APT או wheel, עצרו במקום לאפשר בשקט בנייה מחדש דרך הרשת במארח ה־USB.

BuildKit כולל את מצב הרשת של שלב הבנייה במפתח המטמון. לכן מטמון שנוצר במצב הרשת המוגדר כברירת מחדל עשוי שלא להתאים לניסיון הלא מקוון לעיל. השתמשו בו שוב רק אחרי בדיקת הבסיס המדויק ושרשרת ההוראות, ועצרו אם שלב התקנת חבילות רץ במקום לדווח `CACHED`. ההסרה הסופית של `pip` משתמשת במפורש ב־`RUN --network=none`; היא אינה זקוקה לרשת גם אם שלבים קודמים מהמטמון משתמשים במצבם המקורי.

לפני שינוי תצורת הייצור, בדקו את גרסאות Python ו־ADB, סרקו את התמונה המדויקת שנוצרה, אמתו העברת קבצים בינריים והכנת APK, ואז בצעו את בדיקות הקבלה במדריך לזהות המכשיר, אישור המפתחות, שינה ואתחול. יש לבדוק תקשורת של ADB 34 עם Android ישן; מספר הגרסה לבדו אינו מוכיח תאימות ל־KitKat או Fire. שמרו את התמונה הנוכחית ואת גיבוי הפריסה הפרטי עד שלא יהיה עוד צורך בחזרה לאחור.

## תקציב משאבים בזמן ריצה

מנהל המארח, ולא Dockerfile זה, מחיל זיכרון של 256 MiB, זיכרון ועוד swap של 256 MiB ו־CPU shares בערך 128, ללא מכסה קשיחה של CPU או PID. הוא אוכף `--no-healthcheck`, `--network none`, מערכת קבצים שורשית לקריאה בלבד, הסרת capabilities ומיפוי מדויק של צומתי USB. `/tmp` הוא tmpfs מוגבל שצורך את אותו תקציב זיכרון. מתאם ההכנה כותב מקטעי 1 MiB ומגביל מטען יחיד ל־128 MiB; הפעולות חולקות את נעילת המנהל.

Docker מתעד את ההבחנה בין [בקרות זיכרון, swap ו־CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) לבין [האפשרות `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).

</div>
