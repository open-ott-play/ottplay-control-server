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

[เอกสาร / Wiki](docs/wiki/Home.th.md)

เซิร์ฟเวอร์ขนาดเล็กสำหรับโฮสต์เองเพื่อส่งคำสั่งระยะไกลไปยังเครื่องเล่น OTT-play เครื่องเล่นเป็นฝ่ายส่งคำขอ HTTP ออกไป ทีวีจึงไม่ต้องเปิดพอร์ตรอรับการเชื่อมต่อ เครื่องเล่นแต่ละเครื่องมีคิวและโทเคนเข้าถึงของตนเอง ส่วนการส่งคำสั่งใช้โทเคนผู้ดูแลระบบแยกต่างหาก

คุณสมบัติ[การควบคุมที่เชื่อถือได้](docs/reliable-control.md)ซึ่งเป็นทางเลือก เพิ่มข้อมูลรับรองอินทิเกรชันที่จำกัดสิทธิ์ การเก็บผลคำสั่งแบบถาวรแต่มีขอบเขต การใช้ชุดตั้งค่า Local/Home ต่อจากเดิมพร้อมตรวจสอบค่าอย่างแม่นยำ และการส่งต่อการเล่นระหว่างรายการสดกับย้อนหลังอย่างชัดเจน คุณสมบัติเหล่านี้ต้องใช้รีวิชันของตัวควบคุมและ CLI ที่มีคู่มือนี้ การตรวจสอบค่าและส่งต่อยังต้องอัปเดตเครื่องเล่นด้วย รีวิชันติดตั้งเก่าที่ตรึงไว้ด้านล่างไม่มีคุณสมบัติเหล่านี้ [แผนภาพสถาปัตยกรรม](docs/assets/reliable-control.svg)แสดงว่าค่าตั้ง ข้อมูลรับรอง และสถานะการทำงานเก็บไว้ที่ใด

เซิร์ฟเวอร์ทำงานเป็นไฟล์เรียกใช้ Go แบบพกพาเพียงไฟล์เดียว พร้อมไลบรารี DNS ที่ตรึงเวอร์ชันไว้สำหรับการค้นหาบริการแบบเลือกใช้ แพ็กเกจรุ่นเผยแพร่รองรับ Linux amd64, arm64 และ ARMv7; macOS Intel และ Apple Silicon; Windows amd64 และ arm64 อิมเมจคอนเทนเนอร์รองรับ Linux amd64 และ arm64 ส่วน Kubernetes และ k3s ใช้ Helm chart ที่ให้มา

## เริ่มเซิร์ฟเวอร์

ดาวน์โหลดไฟล์บีบอัดที่ตรงกับระบบจาก[รุ่นเผยแพร่](https://github.com/open-ott-play/ottplay-control-server/releases) ตรวจสอบกับรายการกำกับรุ่น แล้วแตกไฟล์ บน Windows ให้ใช้ `ottplay-control-server.exe` ในคำสั่งด้านล่าง

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` สร้างข้อมูลรับรองแบบสุ่มที่ไม่เหมือนกันในไฟล์ใหม่ ซึ่งบน Unix ให้เฉพาะเจ้าของเข้าถึงได้ คำสั่งปฏิเสธการเขียนทับไฟล์หรือลิงก์สัญลักษณ์เดิม และไม่พิมพ์ข้อมูลรับรองออกมา เก็บไฟล์เป็นส่วนตัว บน Windows ให้จำกัด ACL ไว้ที่บัญชีที่รันเซิร์ฟเวอร์ `config.example.json` อธิบายฟิลด์และจงใจใช้โทเคนตัวอย่างที่ไม่ถูกต้อง

ที่อยู่เริ่มต้นคือ `0.0.0.0:8081` ซึ่งหมายถึงทุกอินเทอร์เฟซ IPv4 รวม LAN เชื่อมต่อทีวีด้วยที่อยู่ LAN และพอร์ตของเซิร์ฟเวอร์ การตั้งค่าเดิมเก็บค่า `listen` ที่ระบุไว้อย่างชัดเจน หากต้องการเปิดการติดตั้งที่เดิมรับเฉพาะ loopback ให้เปลี่ยนเป็น `0.0.0.0:8081` หรือใช้ `--listen 0.0.0.0:8081` เพิ่ม **origin ของหน้าเครื่องเล่นที่ตรงทุกส่วน** ลงใน `allowed_origins` เช่น `http://192.168.1.20:8443` สคีม ชื่อโฮสต์ และพอร์ตต้องตรงกัน ไม่ต้องต่อท้ายพาธ หากรายการ origin ว่าง จะอนุญาตเฉพาะไคลเอนต์ที่ไม่ใช่เบราว์เซอร์ การเปลี่ยนค่ามีผลหลังเริ่มใหม่ ข้อมูลรับรองอุปกรณ์และผู้ดูแลยังจำเป็น

รองรับ HTTP สำหรับเบราว์เซอร์ทีวีเก่าใน LAN ที่เชื่อถือได้ ใช้ HTTPS เมื่อเชื่อมต่อผ่านเครือข่ายที่ไม่ไว้ใจ: `serve --config config.json --tls-cert server.crt --tls-key server.key` หรือสิ้นสุด TLS ที่ reverse proxy หน้าเครื่องเล่น HTTPS ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ HTTP ธรรมดาในเบราว์เซอร์ได้ แอปเนทีฟยังคงใช้นโยบายการส่งข้อมูลของระบบปฏิบัติการ การตั้งค่านี้ไม่ได้ผ่อนคลายนโยบายดังกล่าว

HTTPS โดยตรงตรวจสอบสายใบรับรองที่ให้มาก่อนเปิดรับการเชื่อมต่อ คีย์ RSA ต้องอย่างน้อย 2048 บิต และ ECDSA อย่างน้อย 224 บิต หรือใช้ Ed25519 เปลี่ยนใบรับรองที่มีคีย์สั้นเกินไปก่อนอัปเกรด ดู[นโยบายใบรับรอง TLS และขอบเขต](docs/security-design.md#direct-https-certificate-policy)

## เชื่อมต่อเครื่องเล่น

ใช้รุ่นเครื่องเล่นที่มีคุณสมบัติเชื่อมต่อเซิร์ฟเวอร์คำสั่ง เปิด **การตั้งค่า → การควบคุมระยะไกล** แล้วกรอก:

1. **ที่อยู่เซิร์ฟเวอร์:** IP หรือชื่อโฮสต์ จะระบุพอร์ตด้วยก็ได้ หรือใช้ที่อยู่ `http://` / `https://` แบบเต็ม หากมีเพียงชื่อโฮสต์ จะใช้ HTTP พอร์ต 8081 เป็นค่าเริ่มต้น
2. **รหัสเข้าถึง:** ค่า `token` ของเครื่องเล่นนั้นใน `devices` ห้ามใช้ `admin_token`
3. **เชื่อมต่อ:** เปิดการสอบถามเป็นระยะแล้วตรวจสอบสถานะในหน้าต่างเดียวกัน

เครื่องเล่นเก็บการเชื่อมต่อนี้เฉพาะในการติดตั้งปัจจุบัน ไม่รวมไว้ในข้อมูลสำรองการตั้งค่าหรือการย้ายผ่านคลาวด์ ไม่จำเป็นต้องใช้ตัวรับ HTTP ภายในแยกต่างหาก เว็บเบราว์เซอร์ต้องมี CORS origin ที่อนุญาต หน้าแอปทีวีที่บรรจุมาและมี `Origin: null` ใช้ค่า `allow_null_origin` อย่างชัดเจนได้สำหรับเส้นทางอุปกรณ์/เริ่มต้นที่กำหนดไว้ แต่ไม่เปิดเส้นทางผู้ดูแลหรือผู้ปฏิบัติงานวินิจฉัย

## รีโมตผ่านเทอร์มินัล

[คู่มือ Android OTA พร้อมภาพ](docs/android-ota.md)แยกการอัปเดต Capacitor ผ่าน Wi-Fi ตามปกติ (`ott DEVICE update --variant api22|full` ใช้เบตาล่าสุดเป็นค่าเริ่มต้น; `--version` เลือกรุ่น) ออกจากเอเจนต์ที่มีสิทธิ์ root ซึ่งติดตั้งต่างหาก คู่มืออธิบายการอัปเดตเอเจนต์และ APK/helper ที่ลงลายมือชื่อ การกู้คืน และการตรวจสอบผลติดตั้ง

[คู่มือ Android ADB](docs/android-adb.md)ครอบคลุม USB ผ่านเวิร์กสเตชันหรือ Synology การควบคุมหน้าจอและแป้นพิมพ์ ภาพหน้าจอ บันทึก การติดตั้ง APK และการเริ่มใหม่ตามปกติ รวมถึงข้อจำกัดของแท็บเล็ตเก่า

[การค้นหาและจับคู่ DNS-SD](docs/discovery.md)แบบเลือกใช้ช่วยให้เครื่องเล่นพบตัวควบคุมผ่านโดเมนเครือข่าย และรับโทเคนอุปกรณ์เฉพาะหลัง `ott approve NAME CODE` DNS มีเพียงเมทาดาทาของบริการ การเชื่อมต่อแบบระบุเองที่มีอยู่ยังใช้ได้โดยไม่เปิดการค้นหา

ใช้ Python CLI สำหรับชื่อแทนเครื่องเล่น ระดับเสียงปัจจุบัน การค้นหาช่อง EPG ปัจจุบัน การเลือกผู้ให้บริการ และการตั้งค่าผู้ให้บริการที่รองรับ ดู[คู่มือ CLI](docs/cli.md) ทั้งเซิร์ฟเวอร์และเครื่องเล่นต้องมีส่วนขยายคำขอ/การตอบกลับ

Python CLI ติดตั้งจากสำเนาซอร์สโค้ด แยกจากไฟล์บีบอัดเซิร์ฟเวอร์เนทีฟ [ขั้นตอนติดตั้งและตั้งค่า](docs/cli.md#installation-and-connection)ครอบคลุม macOS/Linux และ Windows การตั้ง Python/PATH และโมดูล CLI ที่จำเป็น โดยตรึง commit พัฒนา 0.1.1 ที่ผ่านการตรวจสอบและมี workbench กับการตรวจสอบออฟไลน์ CLI เสถียร v0.1.0 และไฟล์บีบอัดเซิร์ฟเวอร์เนทีฟไม่มีคำสั่งเหล่านี้ [อัปเดตหรือย้อนรุ่น CLI](docs/cli.md#update-select-or-roll-back-the-cli)ได้โดยไม่แทนที่สำเนางาน ตัวเรียกใช้ หรือการตั้งค่าส่วนตัวเดิม ตัวเรียกใช้ภายในใช้[ตัวแก้รายการเล่น](docs/cli.md#local-playlist-resolver)แบบอ่านอย่างเดียวเพื่อค้นหาช่อง รายการปัจจุบัน และรายการย้อนหลังที่เล่นได้โดยไม่มีตัวควบคุม หลังติดตั้งและตั้งค่า:

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

`a1` เป็นตัวอย่างชื่อแทนเครื่องเล่นที่ลงทะเบียน ไม่ใช่ชื่อโฮสต์คอมพิวเตอร์ ดู[การลงทะเบียนและจับคู่](docs/cli.md#register-and-connect-a-player) [ทุกคำสั่งพร้อมตัวอย่าง](docs/cli.md#commands) [การยกเลิกการเชื่อมต่อและสิทธิ์](docs/cli.md#disconnect-deregister-or-revoke-a-player) และ[การแก้ปัญหา](docs/cli.md#troubleshooting)

สำหรับเครื่องเล่นที่เปิดตลอด ให้ใช้ [standby และ go](docs/cli.md#stop-playback-while-keeping-the-player-online) เพื่อหยุดเล่นและปลุกภายหลังโดยไม่ออกจากแอป `stop` ยังคงเป็นชื่อแทน `standby`; `start` และ `wake` เป็นชื่อแทน `go` การควบคุมระยะไกลต้องเชื่อมต่ออยู่ สแตนด์บายจึงไม่ได้รับประกันว่าจะไม่มีทราฟฟิกเครือข่าย `ott a1` แสดงการทำงานที่เครื่องเล่นประกาศ การรับคำสั่งไม่ได้ยืนยันว่าปิดหรือเริ่มเล่นเสร็จแล้ว

[คำสั่งสัดส่วนภาพ](docs/cli.md#aspect-ratio-fit-and-fill)ต้องใช้ CLI และตัวควบคุมที่อัปเดต พร้อมเครื่องเล่นที่ประกาศความสามารถ `aspect` การเปลี่ยนแปลงใช้หลังตอบรับ ให้อ่าน `aspect` อีกครั้งเพื่อตรวจสอบโหมดและว่าบันทึกแล้วหรือไม่ Fill ครอบภาพตามสัดส่วนแทนการยืดภาพ

[ภาพหน้าจอ](docs/cli.md#remote-screenshots)ระยะไกลต้องใช้ CLI และตัวควบคุมที่อัปเดต พร้อมเครื่องเล่นที่มีอะแดปเตอร์จับภาพที่รองรับ การเปิดการเชื่อมต่อควบคุมระยะไกลอนุญาตให้ตัวควบคุมนั้นจับภาพและวินิจฉัย ภาพเนทีฟไม่ต้องมีสวิตช์สิทธิ์แยก เบราว์เซอร์ยังต้องเลือกแหล่งภาพภายใน ภาพอาจมีค่าตั้ง หน้าจอ PIN และข้อมูลรับรองที่มองเห็นได้ จึงควรเชื่อมต่อเฉพาะตัวควบคุมที่ไว้ใจ `ott a1` แจ้งความพร้อมและแหล่งภาพที่ต้องเลือก การจับภาพแยกจากการเก็บข้อมูลวัดแบบมีโครงสร้าง

เพื่อสุ่มวิดีโอ VPortal ทุกเรื่องที่ตรงกับชื่อและเล่นซ้ำ:

```sh
ott l vpr "wedding"
```

`vpr` สุ่มลำดับสิ่งที่เลือกทั้งหมดหนึ่งครั้ง แล้วเล่นคิวนั้นซ้ำ เรียกอีกครั้งเพื่อสุ่มใหม่ `vp` เล่นรายการที่ตรงตามลำดับแค็ตตาล็อก ส่วน `vp --list` แสดงรายการโดยไม่เปลี่ยนการเล่น ดู[คำสั่ง VPortal](docs/cli.md#commands)

สำหรับลำดับ Plex ที่ระบุชัดเจนโดยใช้การตั้งค่า Plex ที่บันทึกในเครื่องเล่น:

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

CLI รอสูงสุด 45 วินาทีเป็นค่าเริ่มต้น การเขียน `ott -t 45 l plex shuffle "Tom and Jerry"` เพียงระบุเวลารอคำตอบเดิมให้ชัด ไม่จำกัดระยะเวลาที่วิดีโอเล่น การรวบรวมไลบรารี Plex ขนาดใหญ่อาจนานกว่าเวลาสั้น ๆ 10–15 วินาที วาง `-t SECONDS` หรือ `--timeout SECONDS` ก่อนชื่อแทนเครื่องเล่น ดู[เวลารอไลบรารี Plex](docs/cli.md#plex-libraries-by-name)สำหรับตัวอย่างตรวจล่วงหน้าและวิธีจัดการคำขอเล่นที่หมดเวลา

คิว Plex ต้องใช้ CLI ตัวควบคุม และเครื่องเล่นที่มี `plex_queue` คำสั่ง `plex play --shuffle ID...` สุ่ม ID ที่ให้มาหนึ่งครั้ง คิวหยุดเมื่อจบรายการสุดท้าย เว้นแต่นโยบายคีออสก์เดิมให้เล่นซ้ำ ขณะที่มีคิว `next` และ `prev` ตามปกติจะทำงานกับคิวนั้น ส่วน `plex stop` ล้างคิว การตรวจล่วงหน้าไม่เปลี่ยนผู้ให้บริการหรือแทนที่การเล่นปัจจุบัน ดู[คำสั่งคิว Plex](docs/cli.md#ordered-plex-queues)สำหรับขีดจำกัดและความพร้อม การค้นหาไลบรารียังต้องใช้เครื่องเล่นและตัวควบคุมที่อัปเดตและประกาศ `plex_library` โดยใช้การเชื่อมต่อ Plex ที่บันทึกไว้และปฏิเสธตัวเลือกที่กำกวมหรือไม่ครบ ดู[ไลบรารี Plex ตามชื่อ](docs/cli.md#plex-libraries-by-name)

### บันทึกโปรไฟล์วิดีโอภายในเครื่อง

เมื่อใช้เครื่องเล่นและตัวควบคุมปัจจุบัน ไฟล์/โฟลเดอร์ภายในใช้ผู้ให้บริการ `offline`:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

เชื่อมต่อไฟล์/โฟลเดอร์ที่เครื่องเล่นก่อน การบันทึกเก็บค่าตั้งและวิดีโอที่เลือกโดยไม่เริ่มเล่น การส่งออกส่งเฉพาะคำอธิบาย สื่อและสิทธิ์ระบบปฏิบัติการ/เบราว์เซอร์ยังอยู่ในอุปกรณ์ การติดตั้งอื่นต้องผูกแหล่งไฟล์ภายในใหม่ และการโหลดไม่ได้หมายถึงยืนยันการเล่นแล้ว ดู[โปรไฟล์วิดีโอภายใน](docs/cli.md#local-videos-profiles)สำหรับการตั้งค่า ความสามารถ ข้อจำกัดรูปแบบพกพา และการแก้ปัญหา

### บันทึกรายการปัจจุบันข้ามโปรไฟล์

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

รายการโปรดยังคงอยู่ในเครื่องเล่นเป้าหมายหลังเริ่มใหม่ตามปกติ เมื่อเลือกจะโหลดผู้ให้บริการ/โปรไฟล์ที่บันทึกและหาลิงก์ใหม่ หากแหล่งเปลี่ยนหรือไม่มีรายการ จะล้มเหลวโดยไม่แทนด้วยช่องหรือภาพยนตร์อื่น CLI ตัวควบคุม และเครื่องเล่นต้องรองรับ `favorites`; `ott l` แสดงการทำงานที่มี ดู[รายการโปรดข้ามโปรไฟล์](docs/cli.md#favorites-across-profiles)สำหรับชื่อแทน ขีดจำกัด ความหมายการตอบรับ และการแก้ปัญหา

### โหลดชุดตั้งค่าที่บันทึกไว้

เก็บชุดตั้งค่าที่มีชื่อในส่วนส่วนตัว `presets` ของ `~/.config/ottplay-control/cli.json` แต่ละชุดมีโปรไฟล์ M3U แบบมีหมายเลข (รายการเล่นและชั่วโมงย้อนหลัง) ข้อมูลรับรอง Plex และโปรไฟล์ M3U ที่จะเลือกเมื่อโหลดเสร็จ ดู[ตัวอย่างการตั้งค่า](docs/cli.md#named-setups)

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI ตรวจสอบทั้งชุดก่อน แล้วรอเครื่องเล่นตอบรับก่อนส่งการเปลี่ยนแปลงถัดไป มันจัดการโหลดผู้ให้บริการ บันทึก Plex และช่อง M3U และเลือกโปรไฟล์ M3U ที่กำหนดไว้ ข้อผิดพลาดหยุดลำดับและรายงานขั้นตอนที่ยืนยันแล้ว การเขียนที่ผลไม่แน่นอนจะไม่ทำซ้ำอัตโนมัติ ไม่ต้องมีบริการโปรไฟล์เพิ่มหรือเริ่มตัวควบคุมใหม่ เก็บการตั้งค่านี้เป็นส่วนตัวเพราะมีข้อมูลรับรองผู้ให้บริการ

## ส่งคำสั่ง

ตัวอย่างนี้อ่านโทเคนผู้ดูแลจากการตั้งค่าส่วนตัวภายใน โดยไม่ใส่ใน URL หรืออาร์กิวเมนต์บรรทัดคำสั่ง:

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

คำสั่งอื่นเลือกช่องด้วยหมายเลขหรือชื่อ สุ่มช่อง เปลี่ยนผู้ให้บริการ แสดงข้อความ เปลี่ยนรายการ M3U เมื่อรองรับ หรือออกจากเครื่องเล่น ความพร้อมขึ้นกับแพลตฟอร์มเครื่องเล่นและผู้ให้บริการปัจจุบัน ดู[ข้อกำหนด API](docs/api.md)

บนเครื่องเล่นที่รองรับผู้ให้บริการ VPortal แยก คำสั่ง `ott a1 provider
vportal` เลือกโปรไฟล์เฉพาะของมัน 15 โปรไฟล์ ใช้ `profile N vportal LINK`, `profile N
name NAME` หรือไฟล์ส่วนตัว `profile-config N FILE` ที่มี `name` และ `vportal` แล้วเลือกช่องที่ตั้งด้วย `profile N` โปรไฟล์ M3U เดิมยังแยกต่างหาก `profiles` รายงานเมทาดาทาโดยไม่เปิดเผยลิงก์บัญชีหรือคีย์

หลังเริ่มภาพยนตร์หรือคิวตอน VPortal คำสั่ง `ott a1 kiosk on` ล็อกตัวเลือกนั้น เครื่องเล่นเก็บคิวและตำแหน่งไว้เมื่อโหลดใหม่ และหา URL สื่อใหม่เพื่อกู้คืน ใช้ `kiosk off` เพื่อเลือกชื่ออื่นหรือเปลี่ยนโปรไฟล์ จากนั้น `kiosk on` เพื่อล็อก ใบตอบรับคีออสก์แสดงชื่อและจำนวนตอน คำขอผู้ให้บริการและ URL สตรีมอยู่ที่เครื่องเล่น

ใช้ `ott a1 kiosk on --strict` สำหรับเด็กดู การแตะภายในจะแสดงแถบด้านล่างวิดีโอ เครื่องเล่น core ที่อัปเดตให้แตะหรือลากไทม์ไลน์เพื่อเลื่อนภายในตอน VPortal/Plex ปัจจุบันได้ แต่การควบคุมภายในอื่นยังล็อก เครื่องเล่นเก่า คีออสก์ช่อง และ wrapper ที่ดักท่าทางไทม์ไลน์ยังใช้แถบแบบอ่านอย่างเดียว ล็อกที่มีอยู่รักษาเป้าหมายเดิม ปลดจากตัวควบคุมด้วย `ott a1 kiosk off` บนเว็บไซต์ Android การป้องกันออกผ่านระบบต้องปักหมุดแอปแยกพร้อม PIN ดู[โหมดคีออสก์](docs/cli.md#kiosk-mode)

## การวินิจฉัยระยะไกล

เริ่มจาก[คู่มือเริ่มต้น workbench แบบอ่านอย่างเดียว](docs/workbench.md#quick-start) สำหรับ `doctor`, `inspect` ส่วนติดต่อ/สื่อ การค้นหาใบตอบรับงาน ชุดหลักฐาน `health`, `media-progress` แบบเว็บ/เนทีฟ และ `report verify` แบบออฟไลน์ ใช้การตั้งค่า CLI ผู้ดูแลและชื่อแทนเครื่องเล่นเดิม เส้นทางเนทีฟสำหรับเอเจนต์บำรุงรักษา Android ที่เตรียมแยก ส่วนเครื่องเล่น Tauri ใช้ตรวจสอบเว็บ รายงานแยกความคืบหน้าตัวถอดรหัสออกจากการตรวจภาพ/เสียงจริง ดู[การแก้ปัญหา workbench](docs/workbench.md#troubleshooting)เมื่อขาดความสามารถ เส้นทางใช้ไม่ได้ หรือหลักฐานไม่ครบ

[การวินิจฉัยโปรโตคอล 2](docs/remote-diagnostics.md)แบบเลือกใช้มีเซสชันที่เปิดอย่างชัดเจนแยกตามรันไทม์ พร้อมข้อมูลรับรองผู้ปฏิบัติงานแยกที่จำกัดสิทธิ์และข้อมูลวัดแบบมีโครงสร้างที่มีขอบเขต การเชื่อมต่อเครื่องเล่นที่เปิดอยู่ให้สิทธิ์ภายในโดยไม่ถามความเชื่อถือใหม่ แต่การเก็บข้อมูลแต่ละครั้งยังมีอายุเซสชันจำกัด การสอบถามควบคุมการวินิจฉัยเป็นอิสระจากคิวคำสั่งเดิม

[คู่มือ CLI วินิจฉัยและ MCP](docs/diagnostics-cli.md)ครอบคลุมการติดตั้ง ข้อมูลรับรองผู้ปฏิบัติงาน การเชื่อมต่อ ทุกคำสั่ง/เครื่องมือ ตัวอย่างเก็บข้อมูลและซ่อม การเพิกถอนรันไทม์ และการแก้ปัญหา `ott diagnostics --help` แสดงกลุ่มคำสั่งแยกนี้ ชื่อแทนเครื่องเล่นทั่วไปและข้อมูลรับรองผู้ดูแลไม่ได้ให้สิทธิ์วินิจฉัย

## แพ็กเกจติดตั้งใช้งาน

- [แท็บเล็ต Android: คีออสก์ Plex แบบเข้มงวดและเริ่มต้น](docs/android-kiosk-setup.md)
- [การจัดการ Android USB ร่วมและกู้คืนการบูต](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS และ Windows](docs/deployment.md)
- [การตั้งค่า ความหมายของการส่ง และขีดจำกัด](docs/api.md)
- [กระบวนการเผยแพร่และตรวจสอบ](docs/development.md)

คิวมีขอบเขต หมดอายุหลัง 60 วินาทีเป็นค่าเริ่มต้น และอยู่ในหน่วยความจำ การเริ่มเซิร์ฟเวอร์ใหม่ทิ้งคำสั่งที่รออยู่ ให้รันหนึ่ง replica เพราะ replica ที่แยกกันไม่แชร์คิว เครื่องเล่นตอบรับความพยายามส่ง ไม่ได้ยืนยันการเล่นหรือระดับเสียงฮาร์ดแวร์ การลองส่งเครือข่ายซ้ำถูกตัดรายการซ้ำภายในเซสชันเครื่องเล่นที่กำลังทำงาน ไม่ข้ามการเริ่มแอปใหม่

เซิร์ฟเวอร์นี้ทำตาม API คำสั่งของตัวอย่างทางเลือก `local_proxy.py` ใน [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss) โดยแยกข้อมูลรับรองผู้ดูแล/อุปกรณ์และมีการตอบรับส่ง พร็อกซีเก่ายังคงเป็นองค์ประกอบแยก โครงการนี้ไม่พร็อกซีสื่อหรือเปลี่ยนเครือข่าย HLS, EPG หรือผู้ให้บริการของเครื่องเล่น

## สัญญาอนุญาต

MIT การระบุที่มาของเครื่องมือเผยแพร่ที่นำมารวมอยู่ใน `NOTICE` และ `LICENSES/`

## การบำรุงรักษาโครงการ

ดู[ข้อกำหนดการร่วมพัฒนาและทดสอบ](CONTRIBUTING.md) [นโยบายรายงานความปลอดภัย](SECURITY.md) [การออกแบบความปลอดภัย](docs/security-design.md) และ[หลักฐาน OpenSSF กับเกณฑ์ที่เหลือ](docs/openssf-evidence.md)

คิว Plex รับได้ถึง 500 ID บนเครื่องเล่นปัจจุบัน ใช้ `ott f10 plex play --shuffle ID...` เพื่อสุ่มครั้งเดียว แล้ว `ott f10 kiosk on --strict` ขณะเลือกผู้ให้บริการ Plex เพื่อเล่นซ้ำและล็อกคิว `ott f10 kiosk off` ปลดล็อก เครื่องเล่นเก่ายังคงใช้ขีดจำกัดคิวที่ตัวเองประกาศ

## Fuzzing รุ่นผู้สมัครเผยแพร่

รุ่นผู้สมัครเผยแพร่สาธารณะรัน native fuzzing แบบจำกัดบนขอบเขตอินพุตที่อธิบายใน[คู่มือ fuzzing](docs/fuzzing.md) ไม่รันแคมเปญกลายพันธุ์บน PR เบตา หรือบิลด์กลางคืน คู่มือครอบคลุมขอบเขต งบประมาณ หลักฐาน และการทำปัญหาถดถอยซ้ำ

## ชุมชนและหลักฐานความปลอดภัย

ดู[การร่วมพัฒนา](CONTRIBUTING.md) [การกำกับดูแล](GOVERNANCE.md) [จรรยาบรรณ](CODE_OF_CONDUCT.md) [แผนงาน](ROADMAP.md) และ[หลักฐาน OpenSSF](docs/openssf-evidence.md) ดัชนีหลักฐานแยกโปรไฟล์ความปลอดภัยที่รองรับ ผลทดสอบจริง และข้อกำหนด Silver ที่เหลือ
