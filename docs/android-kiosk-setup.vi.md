# Máy tính bảng Android: kiosk Plex nghiêm ngặt và khởi động

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Hướng dẫn ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Trình quản lý USB](android-usb-manager.md)

Máy tính bảng chuyên dụng cần ba thiết lập được kiểm tra riêng: hàng đợi nội dung, chính sách kiosk nghiêm ngặt của OTT và khởi động Android. Cài APK hay kiểm tra tắt màn hình/tiếp tục không chứng minh ứng dụng khởi động sau khi khởi động lại hệ điều hành.

## Nhận diện và cài đặt

1. Ghi lại máy tính bảng vật lý, bí danh/UUID OTT, bí danh trình quản lý USB, số sê-ri, mẫu máy, Android ID và SDK. Bí danh OTT và USB có thể khác nhau; không suy ra danh tính từ bí danh hoặc dùng token của máy khác.
2. Dùng biến thể APK chính thức tương thích SDK, kiểm tra SHA-256 bản phát hành và danh tính gói/chữ ký, rồi cài bằng `install -r`. Giữ dữ liệu trình phát. Dùng [trình quản lý chung](android-usb-manager.md) nếu nó sở hữu USB; không chạy máy chủ ADB cạnh tranh hay lệnh `adb` không chỉ rõ thiết bị.
3. Ghép trình phát bằng thông tin xác thực bộ điều khiển riêng và kiểm tra `ott TABLET`. Nạp preset có tên mong muốn, chạy `ott TABLET load PRESET verify`. Preset mạng gia đình và localhost của máy phát triển không thay thế cho nhau.

## Kiểm tra Plex từ máy tính bảng

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Kiểm tra ID, tiêu đề và số mục thư viện. Yêu cầu thành công từ máy quản trị không chứng minh kết nối máy tính bảng. Khi lỗi, kiểm tra địa chỉ đã lưu, định tuyến, TLS và thông tin xác thực trên máy tính bảng. Ưu tiên endpoint HTTPS hợp lệ của máy chủ. Nếu firmware cũ cần HTTP trong LAN riêng, giới hạn ở cài đặt này, ghi nhận token và media truyền không mã hóa, không sửa preset chung hay tắt xác minh chứng chỉ toàn cục. Không công bố token hoặc URL media riêng trong chẩn đoán hay tài liệu.

Đổi địa chỉ máy chủ Plex xóa gốc thư viện đã chọn. Chọn nhà cung cấp **Plex**, mở **Plex settings → Folders**, đánh dấu hàng thư viện cần dùng (không chỉ **Browse folders**), chọn **Ok**, rồi **Save and open library**. Với trình phát `1.1.58-beta.19`, chỉ thông tin máy chủ có thể mở bộ chọn thư mục trong khi `ready` vẫn false; hoàn tất chọn trước lệnh hàng đợi. Không đổi sang M3U chỉ để có `ready: true`: hàng đợi Plex có thể phát ở đó, nhưng `kiosk on --strict` có thể lưu chính sách M3U chờ không gắn media. Điều đó không khóa hay khôi phục hàng đợi Plex.

## Khởi chạy và khóa hàng đợi

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

Với hàng đợi có thứ tự, dùng `plex library 'LIBRARY NAME'` thay `plex shuffle`. Shuffle chọn thứ tự một lần; kiosk lặp lại. Xác nhận mục mong muốn, `enabled: true`, `strict: true` và chính sách gắn đúng hàng đợi media Plex. Chính sách `waiting` không có kênh hay media không phải kiosk đã nghiệm thu. Quan sát ít nhất hai mẫu có vị trí/số khung hình tăng và nhìn màn hình. Biên nhận hoặc nhãn `playing` không có tiến triển là chưa đủ. Thử chuyển tự nhiên khi hết mục; nếu thử trọn vòng không thực tế, ghi rõ giới hạn đó.

Kiosk nghiêm ngặt điều khiển giao diện trình phát. Không phải đăng ký chủ sở hữu thiết bị Android hay bằng chứng Home, Recents, thông báo và Settings bị chặn. Giữ trình khởi chạy hệ thống và đường bảo trì có người giám sát.

## Bật khởi động riêng biệt

Dựng và xác minh [OTT Home 0.2.0](../native/android-home/README.md), giữ khóa ký hiện có cho cập nhật. Cài `play.ott.home` cạnh `play.ott.foss`. Mở `play.ott.home/.StartupActivity`, bật **Start OTT after reboot**. Mặc định tùy chọn tắt. Nó không thay đổi tệp nhị phân chính thức, chữ ký OTA, dữ liệu hồ sơ hay gán nút Home.

Nếu firmware có bộ chọn Home, có thể chọn thêm **OTT Home → Always**. Khi thiếu bộ chọn (kể cả firmware Fire cũ đã thử), việc cho phép khởi động cung cấp khả năng chạy độc lập. Cài đặt Home mặc định trước đó giữ hành vi. Tắt cả hai cơ chế để hoàn tác đầy đủ.

`recover_on_boot` của trình quản lý USB chung là cơ chế độc lập thứ ba: cần NAS và cáp USB, chỉ xử lý lần khởi động mới, không liên tục khởi chạy lại ứng dụng hỏng. Nó không thay thế khởi động tự chủ. Khi thử tự chủ, tắt chính sách phục hồi để không che việc ứng dụng đồng hành không khởi chạy. Tôn trọng khóa màn hình an toàn; mở khóa bình thường khi được yêu cầu.

## Nghiệm thu và bàn giao

Lưu bằng chứng riêng tư, không để thông tin xác thực trong biên nhận công khai:

- Phiên bản APK, mã băm, ánh xạ danh tính vật lý/OTT/USB và preset đã chọn.
- Thư viện Plex, số lượng/thứ tự mục, chính sách nghiêm ngặt và phát ban đầu đang tiến triển.
- ID boot trước/sau lần khởi động lại hệ điều hành thực; sau đó `sys.boot_completed=1`.
- Không chạy trình phát thủ công hay phục hồi USB giữa khởi động lại và quan sát.
- Hoạt động OTT ở tiền cảnh, kết nối lại bộ điều khiển, hàng đợi/chính sách khôi phục và phát tiến triển. Kết quả `requested` của bộ nhận boot chỉ là gửi yêu cầu.
- Thử nút nguồn riêng: màn hình tắt, âm thanh dừng, mở khóa thường và tiếp tục.
- Giới hạn chính xác: cần mở khóa an toàn, phục hồi nhờ USB, đường thoát vật lý chưa thử, lỗi luồng hoặc hết bộ nhớ. Không gọi các trường hợp này là đã nghiệm thu hoàn toàn.

Trước bàn giao máy tiếp theo, dùng cùng danh sách kiểm tra thay vì chép kết quả thành công máy trước. Fire cũ có thể cần `hls.js` cho M3U vì HLS gốc của nhà sản xuất có thể hỏng; điều đó không chứng minh Plex phát ổn định.

## Bảo trì và hoàn tác

```sh
ott TABLET kiosk off
```

Mở **OTT Startup**, tắt **Start OTT after reboot**, khôi phục lựa chọn Home gốc nếu đã đổi. Nếu cần, chỉ gỡ `play.ott.home` qua USB đã xác minh; giữ `play.ott.foss` cùng dữ liệu. Tắt khởi động không đổi hàng đợi hay tự động tắt kiosk.
