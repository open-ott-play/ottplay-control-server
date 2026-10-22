# แท็บเล็ต Android: คีออสก์ Plex แบบเข้มงวดและการเริ่มระบบ

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[คู่มือ ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [ตัวจัดการ USB](android-usb-manager.md)

แท็บเล็ตเฉพาะงานต้องมีการตั้งค่าสามส่วนที่ตรวจแยกกัน ได้แก่ คิวเนื้อหา นโยบายคีออสก์เข้มงวดของ OTT และการเริ่ม Android การติดตั้ง APK หรือทดสอบปิดหน้าจอแล้วกลับมาทำงานไม่ได้พิสูจน์ว่าจะเริ่มหลังรีบูตระบบปฏิบัติการ

## ระบุตัวตนและติดตั้ง

1. บันทึกแท็บเล็ตจริง alias/UUID ของ OTT alias ตัวจัดการ USB หมายเลขซีเรียล รุ่น Android ID และ SDK ชื่อ OTT กับ USB อาจต่างกัน อย่าอนุมานตัวตนจาก alias หรือใช้โทเคนของแท็บเล็ตอื่น
2. ใช้ APK ทางการรุ่นที่เข้ากับ SDK ตรวจ SHA-256 ของรุ่นและตัวตนแพ็กเกจ/ลายเซ็น แล้วติดตั้งด้วย `install -r` เก็บข้อมูลเครื่องเล่นไว้ ถ้า USB อยู่กับ[ตัวจัดการร่วม](android-usb-manager.md) ให้ใช้ตัวจัดการนั้น อย่าเปิดเซิร์ฟเวอร์ ADB แข่งขันหรือใช้คำสั่ง `adb` โดยไม่ระบุอุปกรณ์
3. จับคู่เครื่องเล่นด้วยข้อมูลรับรองตัวควบคุมของมันเองและตรวจ `ott TABLET` โหลดพรีเซ็ตชื่อที่ต้องการแล้วรัน `ott TABLET load PRESET verify` พรีเซ็ตเครือข่ายบ้านกับ localhost ของเครื่องพัฒนาใช้แทนกันไม่ได้

## ตรวจ Plex จากแท็บเล็ต

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

ตรวจ ID ชื่อและจำนวนรายการในไลบรารี คำขอสำเร็จจากคอมพิวเตอร์ผู้ดูแลไม่ได้พิสูจน์การเชื่อมต่อแท็บเล็ต เมื่อผิดพลาดให้ตรวจที่อยู่ที่บันทึก เส้นทาง เงื่อนไข TLS และข้อมูลรับรองบนแท็บเล็ต เลือก endpoint HTTPS ที่ถูกต้องของเซิร์ฟเวอร์ หากเฟิร์มแวร์เก่าต้องใช้ HTTP ใน LAN ส่วนตัว ให้จำกัดเฉพาะการติดตั้งนี้ บันทึกว่าโทเคนและสื่อส่งแบบไม่เข้ารหัส และอย่าแก้พรีเซ็ตที่แชร์หรือปิดการตรวจใบรับรองทั้งระบบ ห้ามเผยแพร่โทเคนหรือ URL สื่อส่วนตัวในข้อมูลวินิจฉัยหรือเอกสาร

ในเพลเยอร์รุ่นใหม่ที่แสดง `categories` ใน `caps` Plex จะเปิดวิดีโอทั้งหมดโดยไม่ต้องเลือกโฟลเดอร์ก่อน ใช้ `ott TABLET categories` และ `ott TABLET category all` ดู [หมวดหมู่สื่อ](cli.md#media-categories-and-stable-video-selection) ขั้นตอนเลือกโฟลเดอร์ด้านล่างใช้กับเพลเยอร์รุ่นเก่าที่ระบุไว้อย่างชัดเจน

การเปลี่ยนที่อยู่ Plex ล้างรากไลบรารีที่เลือกไว้ เลือกผู้ให้บริการ **Plex** เปิด **Plex settings → Folders** ติ๊กแถวไลบรารีที่ต้องการ (ไม่ใช่แค่ **Browse folders**) เลือก **Ok** แล้ว **Save and open library** ในเครื่องเล่น `1.1.58-beta.19` ข้อมูลเซิร์ฟเวอร์อย่างเดียวอาจเปิดตัวเลือกโฟลเดอร์ได้แต่ `ready` ยังเป็น false ต้องเลือกโฟลเดอร์ให้จบก่อนคำสั่งคิว อย่าเปลี่ยนเป็น M3U เพียงเพื่อได้ `ready: true` คิว Plex อาจเล่นได้ที่นั่น แต่ `kiosk on --strict` อาจบันทึกนโยบาย M3U รออยู่โดยไม่มีสื่อผูกไว้ ซึ่งไม่ล็อกหรือกู้คืนคิว Plex

## เริ่มและล็อกคิว

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

คิวตามลำดับให้ใช้ `plex library 'LIBRARY NAME'` แทน `plex shuffle` Shuffle เลือกลำดับครั้งเดียว คีออสก์เล่นลำดับนั้นซ้ำ ตรวจรายการที่ต้องการ `enabled: true`, `strict: true` และนโยบายที่ผูกคิว Plex ถูกต้อง นโยบาย `waiting` ที่ไม่มีช่องหรือสื่อยังไม่ผ่านการรับรองคีออสก์ สังเกตตัวอย่างอย่างน้อยสองครั้งว่าตำแหน่ง/จำนวนเฟรมเพิ่มขึ้นและตรวจหน้าจอ การตอบรับหรือป้าย `playing` โดยไม่มีความคืบหน้าไม่พอ ทดสอบเปลี่ยนรายการตามธรรมชาติเมื่อจบ หากทดสอบทั้งรอบไม่สะดวกให้บันทึกข้อจำกัดอย่างชัดเจน

คีออสก์เข้มงวดควบคุม UI เครื่องเล่น ไม่ใช่การลงทะเบียนเจ้าของอุปกรณ์ Android หรือหลักฐานว่า Home, Recents, การแจ้งเตือนและ Settings ถูกบล็อก เก็บตัวเปิดแอประบบและช่องทางบำรุงรักษาที่มีผู้ดูแลอยู่ไว้

## เปิดการเริ่มระบบแยกต่างหาก

สร้างและตรวจ [OTT Home 0.2.0](../native/android-home/README.md) โดยเก็บกุญแจลงนามเดิมสำหรับอัปเดต ติดตั้ง `play.ott.home` ควบคู่ `play.ott.foss` เปิด `play.ott.home/.StartupActivity` แล้วเปิด **Start OTT after reboot** ค่าเริ่มต้นคือปิด ไม่เปลี่ยนไบนารีเครื่องเล่นทางการ ลายเซ็น OTA ข้อมูลโปรไฟล์หรือการกำหนดปุ่ม Home

ถ้าเฟิร์มแวร์มีตัวเลือก Home จะเลือก **OTT Home → Always** เพิ่มก็ได้ ถ้าไม่มี (รวมถึง Fire รุ่นเก่าที่ทดสอบแล้ว) การอนุญาตตอนบูตจะเริ่มแอปได้เอง การติดตั้งที่เคยตั้ง default Home ยังคงพฤติกรรมเดิม ปิดทั้งสองกลไกเพื่อย้อนกลับครบถ้วน

`recover_on_boot` ของตัวจัดการ USB ร่วมเป็นกลไกอิสระที่สาม ต้องใช้ NAS กับสาย USB จัดการเฉพาะการบูตใหม่ และไม่เริ่มแอปที่ล้มซ้ำตลอดเวลา ไม่ใช่สิ่งแทนการเริ่มเอง เมื่อทดสอบการเริ่มอิสระ ให้ปิดนโยบายกู้คืนเพื่อไม่ให้ซ่อนปัญหาที่แอปคู่กันไม่เปิด เคารพหน้าจอล็อกที่ปลอดภัยและปลดล็อกตามปกติเมื่อร้องขอ

## ตรวจรับและส่งมอบ

เก็บหลักฐานแบบส่วนตัว โดยไม่มีข้อมูลรับรองในใบตอบรับสาธารณะ:

- รุ่น APK แฮช การจับคู่ตัวตนจริง/OTT/USB และพรีเซ็ตที่เลือก
- ไลบรารี Plex จำนวน/ลำดับรายการ นโยบายเข้มงวดและการเล่นเริ่มต้นที่คืบหน้า
- Boot ID ก่อนและหลังรีบูตระบบจริง จากนั้น `sys.boot_completed=1`
- ไม่มีการเปิดเครื่องเล่นด้วยมือหรือกู้คืน USB ระหว่างรีบูตกับสังเกตผล
- OTT อยู่เบื้องหน้า เชื่อมตัวควบคุมใหม่ คิว/นโยบายกลับคืนและเล่นต่อ ผล `requested` ของตัวรับบูตหมายถึงส่งคำขอเท่านั้น
- ทดสอบปุ่มพลังงานแยก: หน้าจอดับ เสียงหยุด ปลดล็อกปกติแล้วกลับมาทำงาน
- ข้อจำกัดที่ชัดเจน: ต้องปลดล็อกปลอดภัย กู้คืนช่วยด้วย USB ทางออกทางกายภาพที่ยังไม่ทดสอบ สตรีมผิดพลาดหรือหน่วยความจำหมด อย่าเรียกกรณีเหล่านี้ว่าผ่านตรวจรับทั้งหมด

ก่อนส่งมอบแท็บเล็ตถัดไป ให้ใช้รายการตรวจเดิมแทนคัดลอกความสำเร็จของเครื่องก่อน Fire เก่าอาจต้องใช้ `hls.js` กับ M3U เพราะ HLS ของผู้ผลิตอาจล้ม ซึ่งไม่ได้พิสูจน์ว่า Plex เล่นได้เสถียร

## บำรุงรักษาและย้อนกลับ

```sh
ott TABLET kiosk off
```

เปิด **OTT Startup** ปิด **Start OTT after reboot** และคืนตัวเลือก Home เดิมถ้าเคยเปลี่ยน หากจำเป็นถอนแค่ `play.ott.home` ผ่าน USB ที่ตรวจแล้ว เก็บ `play.ott.foss` กับข้อมูลไว้ การปิดเริ่มระบบไม่เปลี่ยนคิวหรือปิดคีออสก์อัตโนมัติ
