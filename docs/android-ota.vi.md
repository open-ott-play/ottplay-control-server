<div lang="vi" dir="ltr">

# Android OTA: trình phát thông thường và tác nhân root riêng

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Với **k8a/fire8 và các trình phát Capacitor khác**, dùng bộ cập nhật Wi-Fi của trình phát thông thường, không dùng lệnh tác nhân root:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Bỏ `--version` sẽ chọn bản beta đã phát hành mới nhất. Dùng `full` thay cho `api22` trên Android 7+. CLI tìm bản phát hành chính thức, kiểm tra manifest, chuẩn bị APK và thăm dò trạng thái trước khi yêu cầu cài đặt đúng một lần. Android có thể yêu cầu xác nhận trên máy tính bảng. Hãy chủ động tắt kiosk và mở khóa cài đặt được bảo vệ trước bảo trì; lệnh không bao giờ đổi các chính sách đó. Xem [quy trình CLI đầy đủ](cli.md#capacitor-apk-updates-over-wi-fi).

Cần APK đã cài có hỗ trợ `AppUpdate`, Wi-Fi và trình phát đang chạy kết nối với bộ điều khiển. Những lần cập nhật thông thường tiếp theo không cần USB, nhưng không truy cập được máy tính bảng ngoại tuyến hoặc WebView đã dừng. Cài lần đầu, bộ cập nhật không có, ứng dụng hỏng hoặc mất mạng vẫn có thể cần khôi phục tại chỗ. Không ngụ ý có lịch tự động phát hành hay trình cài đặt âm thầm dùng chung.

Phần còn lại mô tả **một tác nhân bảo trì a1 có root khác**, giới hạn ở bản cài Matricom/API19 đã chuẩn bị `play.ott.kitkat`. Nó chưa được cài và không dùng được trên thiết bị Fire/Capacitor thông thường.

Sau bước khởi tạo USB có root một lần, tác nhân Android độc lập nhận cập nhật đã ký qua kết nối HTTPS đi ra của mình. Nó không cần WebView hoạt động để tự cập nhật. Tác nhân 0.1.2 còn hỗ trợ gói phối hợp APK và trình trợ giúp cảm ứng hệ thống, có tệp khôi phục được lưu và xác minh trước khi hoàn tất.

Hướng dẫn này trình bày toàn bộ đường đi. Với trường manifest chính xác, giới hạn và yêu cầu phần cứng, xem [tài liệu tác nhân gốc](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Những thành phần được cập nhật

![Kiến trúc OTA: byte bản phát hành đã duyệt được ký, công bố qua HTTPS trực tiếp, tác nhân độc lập xác minh và kiểm tra sau cài đặt. Triển khai web tách riêng.](images/ota-architecture.svg)

- **Tác nhân gốc:** `kind: agent` thay tệp nhị phân ARMv7 của chính nó. Cách này cũng hoạt động khi OTT bị vô hiệu hóa và ứng dụng HOME khác đang được dùng.
- **Chỉ APK:** `kind: apk` thay lớp bọc nhưng giữ dữ liệu. OTT phải được bật và ở tiền cảnh. Trình trợ giúp nhúng trong bản ứng viên phải khớp chính xác trình trợ giúp hệ thống đã cài.
- **APK và trợ giúp cảm ứng:** `kind: bundle` cập nhật cả hai thành một cặp. Tác nhân cũ phải nhận trước bản cập nhật chỉ tác nhân có hỗ trợ gói.
- **Trình phát được lưu trữ:** triển khai hiện vật web đã xác minh tại origin hiện có, rồi tải lại và kiểm tra trình phát hoạt động. Cập nhật APK không triển khai JavaScript được lưu trữ. Xem [quy trình phát hành](release-workflow.md).
- **Bộ điều khiển và CLI:** triển khai bộ điều khiển và cài riêng đầy đủ các mô-đun CLI. Cập nhật một trong hai không thay mã trên máy tính bảng.

Firmware, nhân, WebView hệ thống, môi giới root và tập lệnh khởi động nằm ngoài định dạng OTA này. [Tài liệu gốc](../native/android-agent/README.md#bootstrap) mô tả giới hạn khởi tạo và khôi phục. Các thao tác OTA được hỗ trợ không cần ADB qua mạng hay shell từ xa tùy ý.

## Từ bản phát hành đã duyệt đến cài đặt được xác nhận

1. **Chọn byte đã xác minh.** Hợp nhất thay đổi đã duyệt sau khi CI đạt. Tải hiện vật mong muốn và kiểm tra phiên bản nguồn, bằng chứng phát hành bất biến và checksum. Trích tệp nhị phân `agent` từ kho nén gốc; bản thân kho nén không phải nội dung tác nhân có thể cài.
2. **Chuẩn bị và ký.** Dùng khóa Ed25519 riêng hiện có mà khóa công khai đã được ghim khi khởi tạo. Giữ kín khóa và thông tin xác thực thiết bị.
3. **Công bố và kiểm tra truyền tải.** Chỉ công bố nội dung và `signed.json` tại đường dẫn HTTPS bất biến. Cả hai phải trả byte trực tiếp với HTTP 200; bộ cập nhật từ chối chuyển hướng. Chạy lệnh `verify` của công cụ đóng gói.
4. **Gửi một cập nhật.** Ghi ID yêu cầu và kiểm tra trạng thái sau xác nhận. Phản hồi mất hoặc chưa rõ không phải lý do lặp thao tác mù quáng.
5. **Xác nhận hiệu quả.** So sánh hash và phiên bản đã cài vừa đọc với bản phát hành đã chuẩn bị. Với trình phát hoạt động, kiểm tra cả mục tiêu kiosk, hàng đợi và tiến trình phát. Hình và tiếng vật lý cần quan sát.

Ví dụ bản phát hành tác nhân; thay phiên bản đã chọn và đường dẫn thực tế:

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

`ENVELOPE_SHA256` là `envelope_sha256` của công cụ đóng gói, không phải hash nội dung. `accepted` nghĩa là được nhận, không có nghĩa cài đặt thành công. Tự thay thế có thể để biên nhận thao tác cũ ở `unknown`; môi trường chạy mới, phiên bản tác nhân dự kiến và hash đã cài khớp cung cấp bằng chứng cài đặt. [Tài liệu CLI](cli.md#independent-android-agent) mô tả định tuyến gốc độc lập và xử lý biên nhận.

## Cách giao dịch APK/trợ giúp khôi phục

![Giao dịch APK/trợ giúp: kiểm tra trước, lưu cặp cũ, áp dụng, xác minh, hoàn tất; lỗi phục hồi cặp cũ và khôi phục chưa xong vẫn chặn thay đổi ứng dụng.](images/ota-transaction.svg)

Gói chứa chính xác `app.apk` và `ott-touch-guard`. Công cụ có thể tạo trực tiếp từ APK lớp bọc đã ký bằng `--kind bundle --apk`. Nó trích trợ giúp nhúng nên hai bản sao được giao khớp nhau. Xem [lệnh gói và yêu cầu](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Trước khi đổi tệp, tác nhân yêu cầu cặp đã cài khớp, đủ dung lượng trống, OTT phản hồi ở tiền cảnh, phân vùng hệ thống chỉ đọc và hỗ trợ hạ phiên bản APK được công bố. Với kiosk khóa, nó xác minh cùng ID kênh TV hoặc ID VPortal theo thứ tự; chỉ bằng độ dài hàng đợi là chưa đủ. Hàng đợi khóa không hỗ trợ bị từ chối. OTT cố ý bị tắt cũng bị từ chối, bảo toàn ứng dụng HOME hiện tại của người dùng.

Tệp khôi phục và giao dịch đang chờ được lưu bền vững trước khi dừng ứng dụng. Tác nhân đợi ứng dụng và trợ giúp cũ dừng, thay trợ giúp nguyên tử, đưa `/system` về chỉ đọc, cài APK và kiểm tra kết quả. Áp dụng hoặc xác minh lỗi sẽ khôi phục cặp trước. Khôi phục lúc khởi động trả lại tệp mà không mở OTT hay chiếm HOME.

Nếu khôi phục không hoàn tất, `updates.recovery_failed` là đúng. Chẩn đoán và sửa chữa đã ký chỉ cho tác nhân vẫn dùng được; thay đổi ứng dụng và watchdog phát bị chặn. Không được xóa giao dịch đã lưu để ép cài đặt. Khôi phục nhị phân không đảo ngược di chuyển dữ liệu ứng dụng, và firmware vẫn có thể từ chối hạ phiên bản. Xem [giới hạn khôi phục](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Kết quả và hồ sơ nghiệm thu

![Biểu đồ xác thực: 488 kiểm thử CLI, 576 kiểm thử công cụ phát hành và 6 kiểm thử đóng gói OTA; máy tính bảng tham chiếu chạy tác nhân mới đã xác minh, nhưng nghiệm thu vật lý APK/trợ giúp vẫn chờ.](images/ota-validation.svg)

Đợt kiểm toán ngày 14 tháng 10 năm 2026 đạt 488 kiểm thử CLI (bỏ qua một), 576 kiểm thử công cụ phát hành, sáu kiểm thử đóng gói OTA, kiểm tra Go gốc về tranh chấp/tĩnh và bản dựng ARMv7. Số đếm mô tả kích thước bộ kiểm thử, không phải độ bao phủ hay hiệu năng. Kiểm thử chèn lỗi thử kho nén không hợp lệ, giao dịch gián đoạn, khôi phục một phần, đổi mục tiêu và hàng rào khôi phục. Đánh giá độc lập cũng tìm và sửa kiểm tra định danh hàng đợi chưa đủ cùng xác thực APK/trợ giúp không khớp giữa máy chủ và thiết bị.

Theo kiểm tra ngày 15 tháng 10 năm 2026:

- Triển khai OTA đã hợp nhất trong [PR bộ điều khiển #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) và có trong [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Gói tác nhân đã ký hiện tại và trước đó được công bố tại [máy chủ phân phối OTA](https://android-updates.ottplay.here.now/). Byte nội dung và phong bì đã được tải, so sánh thành công qua HTTPS trực tiếp.
- Máy tính bảng tham chiếu cập nhật từ `0.1.1-beta.21` lên `0.1.2-beta.4` qua kênh OTA độc lập. Phiên bản tác nhân đang chạy và SHA-256 đã cài khớp bản phát hành chọn; môi trường chạy thay đổi còn ID khởi động OS giữ nguyên. OTT vẫn không có tiến trình ứng dụng đang chạy và không có thao tác mở ứng dụng hoặc chọn HOME được gửi.
- Trạng thái gốc báo hash trợ giúp cảm ứng hệ thống, không có gói chờ và không lỗi khôi phục. Siêu dữ liệu APK không có, vì vậy quan sát này không xác định phiên bản hay hash APK đã cài.
- Giao dịch APK/trợ giúp phối hợp đã xác thực phía máy chủ; cài đặt vật lý, phục hồi hạ phiên bản, hình và tiếng chưa được nghiệm thu trên máy tính bảng tham chiếu.

Mỗi lần triển khai sau, giữ nguồn/checksum bản phát hành, hash phong bì đã chuẩn bị, ID yêu cầu, trạng thái gốc trước/sau và kết quả nghiệm thu vật lý riêng. Tải xuống thành công hay biên nhận lệnh không thay thế các quan sát này.

</div>
