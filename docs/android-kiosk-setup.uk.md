# Планшет Android: суворий кіоск Plex і автозапуск

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Посібник ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB-менеджер](android-usb-manager.md)

Для виділеного планшета потрібні три окремо перевірені налаштування: черга вмісту, сувора політика кіоску OTT та автозапуск Android. Встановлення APK або перевірка вимкнення екрана/відновлення не доводить запуск після перезавантаження ОС.

## Ідентифікація та встановлення

1. Зафіксуйте фізичний планшет, alias/UUID OTT, alias USB-менеджера, серійний номер, модель, Android ID і SDK. Імена OTT та USB можуть відрізнятися; не визначайте ідентичність за ім’ям і не використовуйте токен іншого планшета.
2. Виберіть офіційний варіант APK, сумісний із SDK, перевірте SHA-256 випуску та ідентичність пакета/підпису, встановіть через `install -r`. Збережіть дані плеєра. Якщо USB належить [спільному менеджеру](android-usb-manager.md), використовуйте його; не запускайте конкурентний сервер ADB чи команди `adb` без визначення пристрою.
3. Сполучіть плеєр із власними реквізитами контролера й перевірте `ott TABLET`. Завантажте потрібний іменований пресет і виконайте `ott TABLET load PRESET verify`. Пресети домашньої мережі та localhost-пресет комп’ютера розробника не взаємозамінні.

## Перевірка Plex із планшета

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Перевірте ID бібліотеки, назву й кількість елементів. Успішний запит із комп’ютера адміністратора не доводить зв’язок планшета. При помилках перевірте на планшеті збережену адресу, маршрутизацію, TLS і реквізити. Надавайте перевагу дійсному HTTPS-адресу сервера. Якщо старій прошивці потрібен HTTP у приватній LAN, обмежте його цією інсталяцією, зафіксуйте незашифроване передавання токенів і медіа та не переписуйте спільний пресет чи глобальну перевірку сертифікатів. Ніколи не публікуйте токени або приватні URL медіа в діагностиці й документації.

У новіших плеєрах із можливістю `categories` у `caps` Plex відкриває всі відео без обов’язкового вибору папок. Використовуйте `ott TABLET categories` та `ott TABLET category all`; див. [категорії медіа](cli.md#media-categories-and-stable-video-selection). Наведені нижче кроки вибору папок стосуються явно зазначеної старої версії плеєра.

Зміна адреси Plex очищає вибрані корені бібліотек. Виберіть провайдера **Plex**, відкрийте **Plex settings → Folders**, позначте рядок потрібної бібліотеки (не лише **Browse folders**), виберіть **Ok**, потім **Save and open library**. У плеєрі `1.1.58-beta.19` самі реквізити можуть відкрити вибір папок, поки `ready` лишається false; завершіть вибір перед командами черги. Не змінюйте провайдера на M3U лише заради `ready: true`: черга Plex може грати там, але `kiosk on --strict` може зберегти політику M3U в очікуванні без прив’язаних медіа. Це не блокує й не відновлює чергу Plex.

## Запуск і блокування черги

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

Для впорядкованої черги використовуйте `plex library 'LIBRARY NAME'` замість `plex shuffle`. Shuffle один раз визначає порядок; кіоск повторює його. Підтвердьте потрібні елементи, `enabled: true`, `strict: true` і політику, прив’язану до потрібної черги Plex. Політика `waiting` без каналу чи медіа — не прийнятий кіоск. Зробіть щонайменше два виміри зі зростанням позиції/лічильника кадрів і огляньте екран. Підтвердження або напису `playing` без поступу недостатньо. Перевірте природний перехід після кінця елемента; якщо повний цикл перевірити непрактично, явно запишіть це обмеження.

Суворий кіоск керує інтерфейсом плеєра. Це не реєстрація Android device owner і не доказ блокування Home, Recents, сповіщень та Settings. Збережіть системний лаунчер і шлях обслуговування в присутності оператора.

## Окреме ввімкнення автозапуску

Зберіть і перевірте [OTT Home 0.2.0](../native/android-home/README.md), зберігши наявний ключ підпису для оновлень. Встановіть `play.ott.home` поруч із `play.ott.foss`. Відкрийте `play.ott.home/.StartupActivity` та ввімкніть **Start OTT after reboot**. Типово параметр вимкнений. Він не змінює офіційний бінарний файл, підпис OTA, профілі чи призначення кнопки Home.

На прошивці з вибором Home можна також вибрати **OTT Home → Always**. Якщо вибору немає (зокрема на перевіреній старій прошивці Fire), дозвіл запуску працює незалежно. Попередньо налаштовані інсталяції типового Home зберігають поведінку. Для повного відкату вимкніть обидва механізми.

`recover_on_boot` спільного USB-менеджера — третій незалежний механізм: потребує NAS і USB-кабелю, обробляє лише нове завантаження та не перезапускає постійно застосунок після збою. Це не заміна автономного запуску. Під час його перевірки залиште відновлення вимкненим, щоб воно не приховало відсутність запуску супровідним застосунком. Дотримуйтеся захисту екрана блокування; розблокуйте штатно на запит.

## Приймання й передавання

Зберігайте докази приватно, без реквізитів у публічних квитанціях:

- Версії APK, хеші, відповідність фізичної/OTT/USB ідентичності та вибраний пресет.
- Бібліотека Plex, кількість/порядок елементів, сувора політика й початкове відтворення з поступом.
- Boot ID до і після справжнього перезавантаження ОС; потім `sys.boot_completed=1`.
- Відсутність ручного запуску плеєра чи USB-відновлення між перезавантаженням і спостереженням.
- Активність OTT на передньому плані, повторне підключення контролера, відновлена черга/сувора політика та поступ відтворення. Результат `requested` завантажувального приймача означає лише надсилання.
- Окремий тест кнопки живлення: екран згас, звук зупинений, штатне розблокування й відновлення.
- Точні межі: потрібне захищене розблокування, USB-допомога, неперевірені фізичні виходи, помилки потоків або брак пам’яті. Не називайте такі випадки повністю прийнятими.

Перед передаванням наступного планшета повторіть цей список замість копіювання успіху попереднього. Старим Fire може знадобитися `hls.js` для M3U через збої вбудованого HLS виробника; це не доводить стабільність Plex.

## Обслуговування та відкат

```sh
ott TABLET kiosk off
```

Відкрийте **OTT Startup**, вимкніть **Start OTT after reboot** і відновіть початковий Home, якщо змінювали його. За потреби видаліть лише `play.ott.home` через перевірений USB-транспорт; збережіть `play.ott.foss` і дані. Вимкнення автозапуску не змінює чергу й не вимикає кіоск автоматично.
