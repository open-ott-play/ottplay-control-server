# เอกสารตัวควบคุม OTT-play

<!-- language-navigation:start -->
<p dir="ltr">
<a href="Home.md" lang="en" dir="ltr">English</a> · <a href="Home.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="Home.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="Home.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="Home.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="Home.es.md" lang="es" dir="ltr">Español</a> · <a href="Home.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="Home.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="Home.he.md" lang="he" dir="rtl">עברית</a> · <a href="Home.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="Home.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="Home.it.md" lang="it" dir="ltr">Italiano</a> · <a href="Home.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="Home.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="Home.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="Home.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="Home.pt.md" lang="pt" dir="ltr">Português</a> · <a href="Home.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="Home.th.md" lang="th" dir="ltr">ไทย</a> · <a href="Home.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="Home.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="Home.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="Home.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="Home.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

ดัชนีเอกสารที่มีการควบคุมเวอร์ชันนี้เป็นต้นทางของการนำทางแบบวิกิ การแก้ไขดัชนีจะไม่เผยแพร่หน้า GitHub Wiki โดยอัตโนมัติ

- [เริ่มและเชื่อมต่อเครื่องเล่น](../../README.th.md)
- [CLI ในเทอร์มินัล: การติดตั้งและเอกสารอ้างอิงคำสั่ง](../cli.md)
- [การควบคุมที่เชื่อถือได้: บันทึกเหตุการณ์ การทำ Local/Home ต่อ การตรวจสอบอย่างแม่นยำ และการส่งต่อ](../reliable-control.th.md)
- [ข้อกำหนดโปรโตคอลสื่อสาร/API](../api.md)
- [การยืนยันตัวตน ขอบเขตสิทธิ์ผู้ปฏิบัติงาน และความปลอดภัยในการรับส่ง](../security-design.md)
- [การวินิจฉัยขณะทำงาน](../remote-diagnostics.md)
- [ตัวจัดการ Android USB ที่ใช้ร่วมกัน: การติดตั้ง การกู้คืน และการย้ายระบบ](../android-usb-manager.th.md)
- [โต๊ะทำงานและหลักฐานที่บันทึกไว้](../workbench.md)
- [การปรับใช้และพื้นที่จัดเก็บถาวร](../deployment.md)
- [การพัฒนาและการทดสอบ](../development.md)
- [ขั้นตอนการออกรุ่น](../release-workflow.md)

ตัวควบคุมส่งคำสั่งภายในขีดจำกัดที่กำหนด ข้อมูลรับรองของผู้ให้บริการ ตัวตนของแค็ตตาล็อก และนโยบายการเล่นเป็นของเครื่องเล่น ใบตอบรับคำขอที่สำเร็จไม่สามารถแทนการดูภาพและฟังเสียงจากอุปกรณ์จริงได้
