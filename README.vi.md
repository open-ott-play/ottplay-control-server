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

[Tài liệu / Wiki](docs/wiki/Home.vi.md)

Một máy chủ nhỏ có thể tự lưu trữ, dùng để chuyển lệnh điều khiển từ xa đến các trình phát OTT-play. Trình phát chủ động gửi yêu cầu HTTP ra ngoài, nên TV không phải mở cổng lắng nghe. Mỗi trình phát có hàng đợi và token truy cập riêng; token quản trị viên riêng biệt dùng để gửi lệnh.

Tính năng [điều khiển tin cậy](docs/reliable-control.md) tùy chọn bổ sung thông tin xác thực tích hợp có phạm vi hạn chế, lưu bền vững kết quả lệnh trong giới hạn, áp dụng cấu hình Local/Home có thể tiếp tục với xác minh chính xác các thiết lập, và chuyển giao rõ ràng giữa phát trực tiếp và phát từ kho lưu trữ. Các tính năng này đòi hỏi phiên bản bộ điều khiển và CLI có chứa hướng dẫn này. Xác minh chính xác và chuyển giao cũng cần trình phát được cập nhật. Phiên bản cài đặt cũ được ghim bên dưới không có các tính năng này. [Sơ đồ kiến trúc](docs/assets/reliable-control.svg) cho biết nơi giữ thiết lập, thông tin xác thực và trạng thái thao tác.

Máy chủ chạy dưới dạng một tệp thực thi Go di động duy nhất, với thư viện DNS được ghim phiên bản để khám phá dịch vụ tùy chọn. Các gói phát hành dành cho Linux amd64, arm64 và ARMv7; macOS Intel và Apple Silicon; Windows amd64 và arm64. Ảnh container hỗ trợ Linux amd64 và arm64. Kubernetes và k3s sử dụng chart Helm đi kèm.

## Khởi động máy chủ

Tải kho nén phù hợp từ [các bản phát hành](https://github.com/open-ott-play/ottplay-control-server/releases), xác minh theo bản kê phát hành rồi giải nén. Trên Windows, dùng `ottplay-control-server.exe` trong các lệnh dưới đây.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` tạo thông tin xác thực ngẫu nhiên, khác nhau trong một tệp mới mà chỉ chủ sở hữu có quyền truy cập trên Unix. Lệnh từ chối ghi đè tệp hoặc liên kết tượng trưng hiện có và không bao giờ in thông tin xác thực. Giữ tệp ở chế độ riêng tư; trên Windows, giới hạn ACL cho tài khoản chạy máy chủ. `config.example.json` mô tả các trường và cố ý chứa token giữ chỗ không hợp lệ.

Địa chỉ mặc định là `0.0.0.0:8081` (mọi giao diện IPv4, kể cả LAN). Kết nối TV bằng địa chỉ LAN và cổng của máy chủ. Cấu hình hiện có giữ nguyên giá trị `listen` được đặt rõ ràng; đổi thành `0.0.0.0:8081` hoặc dùng `--listen 0.0.0.0:8081` để mở truy cập cho bản cài trước đây chỉ dùng loopback. Thêm **origin chính xác của trang** trình phát vào `allowed_origins`, chẳng hạn `http://192.168.1.20:8443`. Giao thức, tên máy và cổng phải khớp; không thêm đường dẫn. Danh sách origin trống chỉ cho phép ứng dụng khách không phải trình duyệt. Thay đổi cấu hình có hiệu lực sau khi khởi động lại. Vẫn bắt buộc có thông tin xác thực thiết bị và quản trị viên.

HTTP được hỗ trợ cho trình duyệt TV cũ trong LAN đáng tin cậy. Dùng HTTPS khi kết nối đi qua mạng không đáng tin: `serve --config config.json --tls-cert server.crt --tls-key server.key`, hoặc kết thúc TLS tại proxy ngược. Trang trình phát HTTPS không thể kết nối đến máy chủ HTTP thuần trong trình duyệt. Ứng dụng native vẫn tuân thủ chính sách truyền tải của hệ điều hành; thiết lập này không nới lỏng chính sách đó.

HTTPS trực tiếp xác minh chuỗi chứng chỉ được cung cấp trước khi mở cổng lắng nghe: khóa RSA phải ít nhất 2048 bit, ECDSA ít nhất 224 bit; cũng hỗ trợ Ed25519. Thay chứng chỉ có khóa quá nhỏ trước khi nâng cấp. Xem [chính sách chứng chỉ TLS và phạm vi áp dụng](docs/security-design.md#direct-https-certificate-policy).

## Kết nối trình phát

Dùng bản phát hành trình phát có tính năng kết nối máy chủ lệnh. Mở **Cài đặt → Điều khiển từ xa** và nhập:

1. **Địa chỉ máy chủ:** IP hoặc tên máy, tùy chọn kèm cổng, hoặc địa chỉ đầy đủ `http://` / `https://`. Tên máy không có giao thức mặc định dùng HTTP cổng 8081.
2. **Mã truy cập:** `token` trong mục của trình phát đó ở `devices`, không bao giờ dùng `admin_token`.
3. **Kết nối:** bật thăm dò và kiểm tra trạng thái trong cùng hộp thoại.

Trình phát chỉ lưu kết nối này trong bản cài hiện tại và loại khỏi bản sao lưu thiết lập/truyền qua đám mây. Không cần máy chủ HTTP cục bộ riêng. Trình duyệt web cần origin CORS được phép; trang TV đóng gói với `Origin: null` có thể dùng thiết lập rõ ràng `allow_null_origin` cho các tuyến thiết bị/khởi tạo được chỉ định. Điều này không mở các tuyến quản trị viên hay người vận hành chẩn đoán.

## Điều khiển qua terminal

[Hướng dẫn Android OTA có minh họa](docs/android-ota.md) phân biệt cập nhật Capacitor thông thường qua Wi-Fi (`ott DEVICE update --variant api22|full`, mặc định beta mới nhất; `--version` chọn bản phát hành) với tác nhân riêng có quyền root. Hướng dẫn giải thích cập nhật tác nhân và APK/helper có chữ ký, khôi phục và xác minh kết quả đã cài.

[Hướng dẫn Android ADB](docs/android-adb.md) bao quát truy cập USB qua máy trạm hoặc Synology, điều khiển màn hình và bàn phím, ảnh chụp màn hình, nhật ký, cài APK và khởi động lại thông thường, kể cả hạn chế của máy tính bảng cũ.

[Khám phá và ghép nối DNS-SD](docs/discovery.md) tùy chọn cho phép trình phát tìm bộ điều khiển qua miền mạng và nhận token thiết bị riêng sau `ott approve NAME CODE`. DNS chỉ chứa siêu dữ liệu dịch vụ. Kết nối thủ công hiện có tiếp tục hoạt động mà không cần bật khám phá.

Dùng CLI Python cho bí danh trình phát, âm lượng trực tiếp, tìm kênh, EPG hiện tại, chọn nhà cung cấp và cấu hình nhà cung cấp được hỗ trợ: [hướng dẫn CLI](docs/cli.md). Máy chủ và trình phát đều phải có phần mở rộng yêu cầu/phản hồi.

CLI Python được cài từ bản sao mã nguồn, tách khỏi kho nén máy chủ native. [Các bước cài đặt và cấu hình](docs/cli.md#installation-and-connection) bao quát macOS/Linux và Windows, thiết lập Python/PATH và các mô-đun CLI cần thiết. Chúng ghim một commit mã nguồn phát triển 0.1.1 đã được rà soát, có workbench và xác minh ngoại tuyến; CLI ổn định v0.1.0 và kho nén máy chủ native không có các lệnh đó. [Cập nhật hoặc quay lui CLI](docs/cli.md#update-select-or-roll-back-the-cli) mà không thay thế bản sao làm việc, trình khởi chạy hay cấu hình riêng tư hiện có. Trình khởi chạy cục bộ có thể dùng [bộ phân giải danh sách phát](docs/cli.md#local-playlist-resolver) chỉ đọc để tìm kênh, chương trình hiện tại và kho lưu trữ có thể phát mà không cần bộ điều khiển. Sau khi cài đặt và cấu hình:

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

`a1` là bí danh ví dụ của một trình phát đã đăng ký, không phải tên máy tính. Xem [đăng ký và ghép nối](docs/cli.md#register-and-connect-a-player), [mọi lệnh kèm ví dụ](docs/cli.md#commands), [ngắt kết nối và thu hồi truy cập](docs/cli.md#disconnect-deregister-or-revoke-a-player) và [khắc phục sự cố](docs/cli.md#troubleshooting).

Với trình phát luôn bật, dùng [standby và go](docs/cli.md#stop-playback-while-keeping-the-player-online) để dừng phát và đánh thức sau đó mà không thoát ứng dụng. `stop` vẫn là bí danh của `standby`; `start` và `wake` vẫn là bí danh của `go`. Điều khiển từ xa phải giữ kết nối, nên chế độ chờ không cam kết thiết bị không còn lưu lượng mạng. `ott a1` hiển thị các thao tác trình phát công bố. Lệnh được chấp nhận không xác nhận việc tắt hay khởi động phát đã hoàn tất.

[Các lệnh tỷ lệ khung hình](docs/cli.md#aspect-ratio-fit-and-fill) cần CLI và bộ điều khiển mới, cùng trình phát công bố khả năng `aspect`. Thay đổi được áp dụng sau xác nhận; đọc lại `aspect` để kiểm tra chế độ và việc lưu. Fill cắt theo tỷ lệ thay vì kéo giãn ảnh.

[Ảnh chụp màn hình](docs/cli.md#remote-screenshots) từ xa cần CLI và bộ điều khiển mới, cùng trình phát có bộ chuyển đổi chụp được hỗ trợ. Bật kết nối Điều khiển từ xa của trình phát cấp quyền chụp và chẩn đoán cho bộ điều khiển đó; ảnh chụp native không cần công tắc quyền riêng. Trình duyệt vẫn cần bộ chọn nguồn cục bộ. Ảnh có thể chứa thiết lập, màn hình PIN và thông tin xác thực đang hiển thị, nên chỉ kết nối với bộ điều khiển đáng tin. `ott a1` báo khả năng sử dụng và yêu cầu chọn nguồn. Ảnh chụp tách biệt với thu thập dữ liệu đo có cấu trúc.

Để xáo trộn mọi video VPortal khớp với một tựa đề và phát lặp lại:

```sh
ott l vpr "wedding"
```

`vpr` xáo trộn toàn bộ lựa chọn một lần rồi lặp lại hàng đợi đó. Chạy lại để xáo trộn mới. `vp` phát kết quả theo thứ tự danh mục; `vp --list` liệt kê mà không đổi việc phát. Xem [lệnh VPortal](docs/cli.md#commands).

Với chuỗi Plex được chỉ định rõ, dùng cấu hình Plex đã lưu của trình phát:

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

CLI mặc định chờ tối đa 45 giây. Viết `ott -t 45 l plex shuffle "Tom and Jerry"` chỉ định rõ cùng thời gian chờ phản hồi đó; không giới hạn thời lượng phát video. Thu thập thư viện Plex lớn có thể lâu hơn thời gian chờ ngắn 10–15 giây. Đặt `-t SECONDS` (hoặc `--timeout SECONDS`) trước bí danh trình phát. Xem [thời gian chờ thư viện Plex](docs/cli.md#plex-libraries-by-name) để biết ví dụ xem trước và cách xử lý khi yêu cầu phát hết thời gian chờ.

Hàng đợi Plex cần CLI, bộ điều khiển và trình phát triển khai `plex_queue`. `plex play --shuffle ID...` xáo trộn các ID đã cho một lần. Hàng đợi dừng sau mục cuối trừ khi chính sách kiosk hiện có lặp lại nó. Khi hàng đợi còn tồn tại, `next` và `prev` thông thường tác động lên nó; `plex stop` xóa hàng đợi. Xem trước không bao giờ đổi nhà cung cấp hay thay thế việc phát hiện tại. Xem [lệnh hàng đợi Plex](docs/cli.md#ordered-plex-queues) về giới hạn và mức sẵn sàng. Tìm thư viện còn cần trình phát và bộ điều khiển mới công bố `plex_library`; dùng kết nối Plex đã lưu của trình phát và từ chối lựa chọn không rõ ràng hoặc thiếu. Xem [thư viện Plex theo tên](docs/cli.md#plex-libraries-by-name).

### Lưu hồ sơ video cục bộ

Với trình phát và bộ điều khiển hiện hành, tệp/thư mục cục bộ dùng nhà cung cấp `offline`:

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Trước hết kết nối tệp/thư mục trên trình phát. Lưu ghi lại cấu hình và video được chọn mà không bắt đầu phát. Xuất chỉ chuyển phần mô tả; nội dung đa phương tiện và quyền hệ điều hành/trình duyệt vẫn ở thiết bị. Bản cài khác cần liên kết cục bộ, và tải không có nghĩa là việc phát được xác nhận. Xem [hồ sơ video cục bộ](docs/cli.md#local-videos-profiles) về thiết lập, khả năng, giới hạn định dạng di động và khắc phục sự cố.

### Lưu mục hiện tại giữa các hồ sơ

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Mục yêu thích tồn tại trên trình phát đích qua các lần khởi động lại thông thường. Chọn một mục sẽ tải nhà cung cấp/hồ sơ đã lưu và phân giải liên kết mới; nguồn thay đổi hoặc mục bị thiếu sẽ báo lỗi, không thay bằng kênh/phim khác. CLI, bộ điều khiển và trình phát phải hỗ trợ `favorites`; `ott l` hiển thị thao tác khả dụng. Xem [yêu thích giữa các hồ sơ](docs/cli.md#favorites-across-profiles) về bí danh, giới hạn, ý nghĩa xác nhận và khắc phục sự cố.

### Tải cấu hình đã lưu

Lưu cấu hình có tên trong phần riêng tư `presets` của `~/.config/ottplay-control/cli.json`. Mỗi cấu hình chứa hồ sơ M3U đánh số (danh sách phát và số giờ lưu trữ), thông tin xác thực Plex và hồ sơ M3U được chọn khi tải xong. Xem [ví dụ cấu hình](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI xác thực toàn bộ cấu hình trước rồi chờ trình phát xác nhận trước khi gửi thay đổi tiếp theo. Nó xử lý việc tải nhà cung cấp, lưu Plex và các vị trí M3U rồi chọn hồ sơ M3U đã cấu hình. Lỗi dừng chuỗi thao tác và báo các bước đã xác nhận; thao tác ghi không chắc kết quả không bao giờ tự động lặp lại. Không cần dịch vụ hồ sơ bổ sung hay khởi động lại bộ điều khiển. Giữ cấu hình này riêng tư: nó chứa thông tin xác thực nhà cung cấp.

## Gửi lệnh

Ví dụ này đọc token quản trị viên từ cấu hình cục bộ riêng tư mà không đưa vào URL hoặc đối số dòng lệnh:

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

Các lệnh khác chọn kênh theo số hoặc tên, chọn ngẫu nhiên, đổi nhà cung cấp, hiển thị thông báo, đổi danh sách M3U khi được hỗ trợ hoặc thoát trình phát. Khả năng sử dụng tùy thuộc nền tảng trình phát và nhà cung cấp hiện tại. Xem [hợp đồng API](docs/api.md).

Với trình phát hỗ trợ nhà cung cấp VPortal độc lập, `ott a1 provider
vportal` chọn 15 hồ sơ riêng của nó. Dùng `profile N vportal LINK`, `profile N
name NAME` hoặc tệp riêng tư `profile-config N FILE` chứa `name` và `vportal`, rồi chọn vị trí đã cấu hình bằng `profile N`. Các hồ sơ M3U hiện có vẫn riêng biệt. `profiles` báo siêu dữ liệu mà không lộ liên kết tài khoản hoặc khóa.

Sau khi bắt đầu phim hoặc hàng đợi tập VPortal, `ott a1 kiosk on` khóa lựa chọn đó. Trình phát giữ hàng đợi và vị trí qua các lần tải lại, đồng thời phân giải lại URL nội dung để khôi phục. Dùng `kiosk off` để chọn tựa đề khác hoặc đổi hồ sơ, rồi `kiosk on` để khóa. Biên nhận kiosk hiển thị tựa đề và số tập; yêu cầu nhà cung cấp và URL luồng vẫn ở trình phát.

Dùng `ott a1 kiosk on --strict` để phát cho trẻ em: chạm cục bộ sẽ hiện thanh dưới video. Trình phát core mới cho phép chạm hoặc kéo dòng thời gian để tua trong tập VPortal/Plex hiện tại; các điều khiển cục bộ khác vẫn bị khóa. Trình phát cũ, kiosk kênh và lớp bọc chặn cử chỉ dòng thời gian vẫn có thanh chỉ đọc. Khóa hiện có giữ nguyên đích. Mở khóa từ bộ điều khiển bằng `ott a1 kiosk off`. Trên trang web Android, bảo vệ khỏi thoát qua hệ thống đòi hỏi ghim ứng dụng riêng bằng PIN; xem [chế độ kiosk](docs/cli.md#kiosk-mode).

## Chẩn đoán từ xa

Bắt đầu bằng [hướng dẫn nhanh workbench chỉ đọc](docs/workbench.md#quick-start) cho `doctor`, `inspect` giao diện/nội dung, tra biên nhận thao tác, gói bằng chứng, `health`, `media-progress` web/native và `report verify` ngoại tuyến. Hướng dẫn dùng cấu hình CLI quản trị viên cùng bí danh trình phát hiện có. Nhánh native dành cho tác nhân bảo trì Android được cấp riêng; trình phát Tauri dùng kiểm tra web. Báo cáo phân biệt tiến độ bộ giải mã với xác minh màn hình/âm thanh vật lý. Xem [khắc phục sự cố workbench](docs/workbench.md#troubleshooting) về khả năng bị thiếu, nhánh không khả dụng và bằng chứng chưa đầy đủ.

[Chẩn đoán giao thức 2](docs/remote-diagnostics.md) tùy chọn cung cấp phiên được bật rõ ràng cho từng môi trường chạy, với thông tin xác thực người vận hành riêng có phạm vi giới hạn và dữ liệu đo có cấu trúc trong giới hạn. Kết nối trình phát đã bật cấp quyền cục bộ mà không hỏi tin cậy lần nữa; mỗi lần thu thập vẫn có thời hạn phiên giới hạn. Thăm dò điều khiển chẩn đoán độc lập với hàng đợi lệnh hiện có.

[Hướng dẫn CLI chẩn đoán và MCP](docs/diagnostics-cli.md) bao quát cài đặt, thông tin xác thực người vận hành, kết nối, mọi lệnh/công cụ chẩn đoán, ví dụ thu thập và sửa chữa, thu hồi quyền môi trường chạy và khắc phục sự cố. `ott diagnostics --help` liệt kê nhóm lệnh riêng này; bí danh trình phát thông thường và thông tin xác thực quản trị viên không cấp quyền chẩn đoán.

## Gói triển khai

- [Máy tính bảng Android: kiosk Plex nghiêm ngặt và khởi động](docs/android-kiosk-setup.md)
- [Quản lý USB Android dùng chung và khôi phục khởi động](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS và Windows](docs/deployment.md)
- [Cấu hình, ngữ nghĩa chuyển lệnh và giới hạn](docs/api.md)
- [Quy trình phát hành và xác thực](docs/development.md)

Hàng đợi có giới hạn, mặc định hết hạn sau 60 giây và nằm trong bộ nhớ. Khởi động lại máy chủ loại bỏ lệnh đang chờ. Chạy một bản sao; các bản sao độc lập không chia sẻ hàng đợi. Trình phát xác nhận lần thử chuyển lệnh, không xác nhận việc phát hay âm lượng phần cứng. Yêu cầu mạng thử lại được khử trùng lặp trong phiên trình phát đang chạy, không xuyên qua lần khởi động lại ứng dụng.

Máy chủ này tuân theo API lệnh của ví dụ tùy chọn `local_proxy.py` trong [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), với thông tin xác thực quản trị viên/thiết bị tách biệt và chuyển lệnh có xác nhận. Proxy cũ vẫn là thành phần riêng. Dự án này không làm proxy nội dung hay thay đổi mạng HLS, EPG hoặc nhà cung cấp của trình phát.

## Giấy phép

MIT. Ghi công cho công cụ phát hành được đưa kèm nằm trong `NOTICE` và `LICENSES/`.

## Bảo trì dự án

Xem [yêu cầu đóng góp và kiểm thử](CONTRIBUTING.md), [chính sách báo cáo bảo mật](SECURITY.md), [thiết kế bảo mật](docs/security-design.md) và [bằng chứng OpenSSF cùng tiêu chí còn lại](docs/openssf-evidence.md).

Hàng đợi Plex nhận tối đa 500 ID trên trình phát hiện hành. Dùng `ott f10 plex play --shuffle ID...` để xáo trộn một lần, rồi `ott f10 kiosk on --strict` khi đã chọn nhà cung cấp Plex để lặp và khóa hàng đợi đó. `ott f10 kiosk off` mở khóa. Trình phát cũ giữ giới hạn hàng đợi mà chúng công bố.

## Fuzzing bản ứng viên phát hành

Các bản ứng viên phát hành công khai chạy fuzzing native có giới hạn trên biên đầu vào mô tả trong [hướng dẫn fuzzing](docs/fuzzing.md). Chiến dịch đột biến không chạy trên PR, beta hay bản dựng hằng đêm. Hướng dẫn bao quát phạm vi, ngân sách, bằng chứng và tái hiện hồi quy.

## Cộng đồng và bằng chứng bảo mật

Xem [đóng góp](CONTRIBUTING.md), [quản trị](GOVERNANCE.md), [quy tắc ứng xử](CODE_OF_CONDUCT.md), [lộ trình](ROADMAP.md) và [bằng chứng OpenSSF](docs/openssf-evidence.md). Chỉ mục bằng chứng phân biệt cấu hình bảo mật được hỗ trợ, kết quả kiểm thử thực tế và yêu cầu Silver còn lại.
