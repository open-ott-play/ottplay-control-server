# Документация контроллера OTT-play

<!-- language-navigation:start -->
<p dir="ltr">
<a href="Home.md" lang="en" dir="ltr">English</a> · <a href="Home.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="Home.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="Home.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="Home.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="Home.es.md" lang="es" dir="ltr">Español</a> · <a href="Home.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="Home.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="Home.he.md" lang="he" dir="rtl">עברית</a> · <a href="Home.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="Home.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="Home.it.md" lang="it" dir="ltr">Italiano</a> · <a href="Home.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="Home.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="Home.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="Home.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="Home.pt.md" lang="pt" dir="ltr">Português</a> · <a href="Home.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="Home.th.md" lang="th" dir="ltr">ไทย</a> · <a href="Home.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="Home.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="Home.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="Home.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="Home.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Этот версионируемый указатель документации служит источником навигации в стиле вики; его редактирование не публикует страницы GitHub Wiki автоматически.

- [Запуск и подключение плеера](../../README.ru.md)
- [Терминальный CLI: установка и справочник команд](../cli.md)
- [Home Assistant: баннеры, токен, установка и восстановление](../home-assistant.ru.md)
- [Надёжное управление: журнал, продолжение Local/Home, точная проверка и передача воспроизведения](../reliable-control.ru.md)
- [Контракт протокола обмена/API](../api.md)
- [Аутентификация, области полномочий операторов и защита транспорта](../security-design.md)
- [Диагностика во время работы](../remote-diagnostics.md)
- [Общий USB-менеджер Android: установка, восстановление и миграция](../android-usb-manager.ru.md)
- [Рабочий стенд и сохранённые доказательства](../workbench.md)
- [Развёртывание и постоянное хранилище](../deployment.md)
- [Разработка и тесты](../development.md)
- [Процесс выпуска](../release-workflow.md)

Контроллер доставляет команды с установленными ограничениями. Учётные данные провайдера, идентичность каталога и политика воспроизведения принадлежат плееру. Квитанция об успешном запросе не заменяет проверку изображения и звука на физическом устройстве.
