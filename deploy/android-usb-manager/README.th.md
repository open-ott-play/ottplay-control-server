# อิมเมจสำหรับการรับส่ง Android USB

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

ไดเรกทอรีนี้มีแม่แบบการปรับใช้และ [`Dockerfile`](Dockerfile) สำหรับคอนเทนเนอร์รับส่งของตัวจัดการที่ใช้ร่วมกัน ตัวจัดการ Python ทำงานบนโฮสต์ USB อิมเมจมี ADB, Python และเครื่องมือของอะแดปเตอร์บำรุงรักษาแบบอะตอมมิก แต่ไม่มีทะเบียนอุปกรณ์ชุดที่สองหรือดีมอนกู้คืน ดูการเตรียมระบบ คำสั่ง การย้าย ข้อจำกัดการกู้คืน และการย้อนกลับใน[คู่มือผู้ปฏิบัติงาน](../../docs/android-usb-manager.md)

หากไม่มีสำเนา Git ให้ดาวน์โหลด `ottplay-control-server-deployment.tar.gz` และ `release-manifest.json` จาก[รุ่นเดียวกัน](https://github.com/open-ott-play/ottplay-control-server/releases) ตรวจสอบไดเจสต์ของไฟล์เก็บถาวรแล้วแตกลงไดเรกทอรีใหม่ตาม[การรับแพ็กเกจปรับใช้](../../docs/android-usb-manager.md#get-the-deployment-package) แพ็กเกจมีสูตรนี้และสคริปต์ฝั่งโฮสต์/ระยะไกลหกไฟล์ใน `tools/` ต้องรักษาโครงสร้างไดเรกทอรี แพ็กเกจเก่า รวมถึง `v0.1.5-beta.6` ไม่มีสคริปต์เหล่านี้และต้องใช้ไฟล์ซอร์สที่ตรงกันแทน แพ็กเกจไม่มีอิมเมจรับส่ง USB ที่สร้างแล้ว ส่วนอาร์ติแฟกต์ OCI ของรุ่น control-server ใช้เพื่อวัตถุประสงค์อื่น

## ที่มาของซอร์สและอาร์ติแฟกต์

อิมเมจพื้นฐานคือ `python:3.12.14-slim-trixie` ซึ่งตรึงไว้ที่ `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b` อิมเมจพื้นฐาน คำสั่ง APT และการติดตั้ง `websocket-client==1.9.0` ที่ล็อกด้วยแฮชได้รับการตรวจใน [PR 128 รีวิชัน b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager) ข้อความคำสั่งคงเดิมเพื่อให้ใช้แคชบิลด์ที่มีอยู่ซ้ำได้ แพ็กเกจ WebSocket ที่เป็นทางเลือกสนับสนุนไคลเอนต์บำรุงรักษาทั่วไปที่มีอยู่ ส่วนอะแดปเตอร์ CDP ที่ย้ายมาแล้วสามารถใช้เพียงไลบรารีมาตรฐาน Python ได้เช่นกัน

สูตรนี้ลบแพ็กเกจ `pip` ที่ติดตั้งไว้หลังติดตั้ง wheel ที่ล็อกไว้ ไม่รวมโค้ดตัวจัดการและกู้คืนจาก PR 128 เริ่ม ADB ในเบื้องหน้า และปิดการตรวจสุขภาพที่สืบทอดมา การลบตัวติดตั้งขณะทำงานลดพื้นที่โจมตีที่ใช้ได้ แต่ไม่ได้พิสูจน์ว่าผลสแกนช่องโหว่สะอาด

อาร์ติแฟกต์ PR 128 ที่ผ่านการตรวจคือ `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a` ซึ่งมีจุดเริ่มต้นต่างกันและการตรวจสุขภาพ heartbeat ที่เข้ากันไม่ได้ นี่คืออาร์ติแฟกต์อ้างอิง ไม่ใช่ผลบิลด์จาก Dockerfile นี้ ห้ามนำ ID อิมเมจ ที่มาของซอร์ส หรือผลสแกนของมันมาอ้างว่าเป็นของสูตรนี้

คลัง APT ไม่ได้ตรึงกับสแนปช็อต ดังนั้นการสร้างใหม่แบบสะอาดในอนาคตอาจเลือกแพ็กเกจ OS ที่ใหม่กว่า แม้ใช้ไดเจสต์พื้นฐานและแฮช Python wheel เดิม เมื่อรับรองบิลด์ให้บันทึกรีวิชันซอร์สที่แน่นอน ตัวสร้าง/แพลตฟอร์ม ID อิมเมจที่ได้ รุ่นแพ็กเกจ และผลสแกน การตั้งค่าใช้งานจริงต้องใช้ ID อิมเมจที่เปลี่ยนไม่ได้และรับรองแล้วนั้นหรือไดเจสต์ของมัน

## สร้างโดยไม่รบกวนโฮสต์ USB ที่ยุ่งอยู่

ควรสร้างและสแกนบนตัวสร้างแยกต่างหาก โฮสต์เป้าหมายต้องการเพียงอิมเมจที่ผ่านการรับรอง ไม่ต้องดาวน์โหลดแพ็กเกจหรือมีชุดเครื่องมือบิลด์

เมื่อใช้ตัวสร้างในเครื่องที่เตรียมแล้วซ้ำ ให้ตรวจสอบก่อนว่ามีอิมเมจพื้นฐานที่ตรงกันและแคชเลเยอร์การติดตั้ง APT กับ wheel อยู่ การโหลดเพียงอิมเมจสุดท้ายจาก PR 128 ไม่ได้ยืนยันเงื่อนไขใดเลย จากรากของรีโพซิทอรีหรือไดเรกทอรีปรับใช้ที่แตกแล้ว การลองแบบออฟไลน์โดยตั้งใจทำได้ดังนี้:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` ใช้กับขั้นตอนบิลด์ ไม่ใช่คำรับรองทั่วไปว่าตัวสร้างจะไม่ติดต่อรีจิสทรีขณะค้นหาอิมเมจพื้นฐาน ก่อนใช้วิธีนี้บนโฮสต์ที่มีข้อจำกัด ให้ยืนยันว่าตัวสร้างหาอิมเมจพื้นฐานจากในเครื่องได้ หากขาดเลเยอร์ APT หรือ wheel ให้หยุดแทนการอนุญาตเงียบ ๆ ให้สร้างใหม่ผ่านเครือข่ายบนโฮสต์ USB

BuildKit รวมโหมดเครือข่ายของขั้นตอนบิลด์ไว้ในคีย์แคช ดังนั้นแคชที่สร้างในโหมดเครือข่ายเริ่มต้นอาจไม่ตรงกับการลองออฟไลน์ข้างต้น ใช้ซ้ำได้หลังตรวจสอบอิมเมจพื้นฐานและลำดับคำสั่งที่ตรงกันเท่านั้น และหยุดหากขั้นตอนติดตั้งแพ็กเกจทำงานจริงแทนที่จะรายงาน `CACHED` การลบ `pip` ขั้นสุดท้ายใช้ `RUN --network=none` อย่างชัดเจน จึงไม่ต้องใช้เครือข่ายแม้ขั้นตอนแคชก่อนหน้าใช้โหมดเดิม

ก่อนเปลี่ยนการตั้งค่าใช้งานจริง ให้ตรวจรุ่น Python และ ADB สแกนอิมเมจผลลัพธ์ที่ตรงกัน ตรวจการส่งไฟล์ไบนารีและการเตรียม APK แล้วทำการตรวจรับตามคู่มือเรื่องตัวตนอุปกรณ์ การอนุญาตกุญแจ การพัก และการบูต ต้องทดสอบ ADB 34 กับ Android รุ่นเก่า หมายเลขรุ่นอย่างเดียวไม่ยืนยันความเข้ากันได้กับ KitKat หรือ Fire เก็บอิมเมจปัจจุบันและข้อมูลสำรองการปรับใช้แบบส่วนตัวไว้จนไม่ต้องย้อนกลับแล้ว

## งบทรัพยากรขณะทำงาน

ตัวจัดการโฮสต์ ไม่ใช่ Dockerfile นี้ กำหนดหน่วยความจำ 256 MiB หน่วยความจำรวม swap 256 MiB และ CPU shares 128 โดยไม่มีโควตา CPU หรือ PID แบบตายตัว มันบังคับใช้ `--no-healthcheck`, `--network none`, ระบบไฟล์รากอ่านอย่างเดียว การตัด capabilities และการแมปโหนด USB ที่แน่นอน `/tmp` เป็น tmpfs ที่มีขีดจำกัดและใช้งบหน่วยความจำเดียวกัน อะแดปเตอร์เตรียมข้อมูลเขียนครั้งละ 1 MiB และจำกัดเพย์โหลดหนึ่งชุดไว้ที่ 128 MiB การดำเนินการใช้ล็อกของตัวจัดการร่วมกัน

Docker อธิบายความแตกต่างระหว่าง[การควบคุมหน่วยความจำ swap และ CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) กับ[ตัวเลือก `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck) ไว้ในเอกสาร
