# Ảnh truyền tải USB Android

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Thư mục này chứa mẫu triển khai và [`Dockerfile`](Dockerfile) cho container truyền tải của trình quản lý dùng chung. Trình quản lý Python chạy trên máy chủ USB. Ảnh cung cấp ADB, Python và công cụ cho bộ điều hợp bảo trì nguyên tử; không chứa sổ đăng ký thiết bị thứ hai hay tiến trình khôi phục nền. Xem [hướng dẫn vận hành](../../docs/android-usb-manager.md) về cấp phát, lệnh, chuyển đổi, giới hạn khôi phục và hoàn tác.

Nếu không có bản checkout Git, tải `ottplay-control-server-deployment.tar.gz` và `release-manifest.json` từ cùng một [bản phát hành](https://github.com/open-ott-play/ottplay-control-server/releases). Kiểm tra mã băm của tệp nén và giải nén vào thư mục mới theo [Lấy gói triển khai](../../docs/android-usb-manager.md#get-the-deployment-package). Gói chứa công thức này và sáu tập lệnh máy chủ/từ xa trong `tools/`; giữ nguyên cấu trúc thư mục. Các gói cũ, gồm `v0.1.5-beta.6`, thiếu các tập lệnh đó nên phải dùng tệp nén mã nguồn tương ứng. Gói không chứa ảnh truyền tải USB đã dựng. Tài sản OCI phát hành của máy chủ điều khiển có mục đích khác.

## Nguồn gốc mã nguồn và sản phẩm dựng

Ảnh nền là `python:3.12.14-slim-trixie`, cố định ở `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. Ảnh nền, lệnh APT và quá trình cài `websocket-client==1.9.0` khóa bằng mã băm đã được xem xét trong [PR 128 tại bản sửa đổi b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Giữ nguyên văn bản lệnh để tái sử dụng bộ đệm dựng sẵn có. Gói WebSocket tùy chọn hỗ trợ các máy khách bảo trì chung hiện có; bộ điều hợp CDP đã chuyển đổi cũng có thể chỉ dùng thư viện chuẩn Python.

Công thức này xóa gói `pip` đã cài sau khi cài wheel được khóa, loại trừ mã quản lý và khôi phục của PR 128, chạy ADB ở tiền cảnh và tắt các kiểm tra sức khỏe kế thừa. Xóa trình cài đặt lúc chạy làm giảm bề mặt tấn công sẵn có; không phải bằng chứng rằng quá trình quét lỗ hổng không tìm thấy vấn đề.

Sản phẩm PR 128 đã được xem xét là `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Nó có điểm vào khác và kiểm tra sức khỏe heartbeat không tương thích. Đây là sản phẩm tham chiếu, không phải bản dựng của Dockerfile này. Không gán ID ảnh, nguồn gốc mã hay kết quả quét của nó cho công thức này.

Kho APT không được cố định vào ảnh chụp. Vì vậy, lần dựng sạch sau này có thể chọn gói hệ điều hành mới hơn dù mã băm ảnh nền và wheel Python không đổi. Khi thẩm định bản dựng, ghi lại bản sửa đổi mã nguồn chính xác, bộ dựng/nền tảng, ID ảnh kết quả, phiên bản gói và kết quả quét. Cấu hình sản xuất phải dùng ID ảnh bất biến đã thẩm định đó hoặc mã băm của nó.

## Dựng mà không làm gián đoạn máy chủ USB bận

Ưu tiên dựng và quét trên bộ dựng riêng. Máy đích chỉ cần ảnh đã thẩm định, không cần tải gói hay bộ công cụ dựng.

Khi tái sử dụng bộ dựng cục bộ đã chuẩn bị, trước tiên xác minh có đúng ảnh nền và các lớp cài APT cùng wheel đã được lưu đệm. Chỉ nạp ảnh cuối của PR 128 không chứng minh được điều kiện nào. Từ thư mục gốc kho mã hoặc thư mục triển khai đã giải nén, lần thử có chủ đích ngoại tuyến như sau:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` áp dụng cho các bước dựng; không bảo đảm chung rằng bộ dựng sẽ không liên hệ kho đăng ký khi phân giải ảnh nền. Xác nhận phân giải ảnh nền cục bộ trước khi dùng quy trình này trên máy hạn chế tài nguyên. Nếu thiếu lớp APT hoặc wheel, dừng thử thay vì âm thầm cho phép dựng lại qua mạng trên máy chủ USB.

BuildKit đưa chế độ mạng của bước dựng vào khóa bộ đệm. Vì vậy, bộ đệm tạo bằng chế độ mạng mặc định có thể không khớp khi thử ngoại tuyến như trên. Chỉ tái sử dụng sau khi kiểm tra đúng ảnh nền và chuỗi lệnh; dừng nếu bước cài gói nào thực thi thay vì báo `CACHED`. Việc xóa `pip` cuối cùng dùng rõ ràng `RUN --network=none`; không cần mạng dù các bước đã lưu đệm trước đó dùng chế độ ban đầu.

Trước khi đổi cấu hình sản xuất, kiểm tra phiên bản Python và ADB, quét chính xác ảnh kết quả, xác minh truyền tệp nhị phân và chuẩn bị APK, rồi thực hiện kiểm tra nghiệm thu danh tính thiết bị, cấp quyền khóa, ngủ và khởi động trong hướng dẫn. Phải thử ADB 34 với thiết bị Android cũ; số phiên bản riêng lẻ không chứng minh tương thích KitKat hoặc Fire. Giữ ảnh hiện tại và bản sao lưu triển khai riêng tư đến khi không còn cần hoàn tác.

## Ngân sách tài nguyên lúc chạy

Trình quản lý máy chủ, không phải Dockerfile này, áp dụng bộ nhớ 256 MiB, bộ nhớ cộng swap 256 MiB và CPU shares 128, không có hạn ngạch cứng CPU hoặc PID. Nó bắt buộc `--no-healthcheck`, `--network none`, hệ tệp gốc chỉ đọc, loại bỏ capabilities và ánh xạ chính xác nút USB. `/tmp` là tmpfs có giới hạn, dùng cùng ngân sách bộ nhớ. Bộ điều hợp chuẩn bị ghi từng 1 MiB và giới hạn một tải trọng ở 128 MiB; các thao tác dùng chung khóa trình quản lý.

Docker giải thích sự khác nhau giữa [điều khiển bộ nhớ, swap và CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) và [tùy chọn `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
