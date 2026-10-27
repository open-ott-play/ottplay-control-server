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

[문서 / Wiki](docs/wiki/Home.ko.md)

OTT-play 플레이어에 원격 명령을 전달하는 작은 자체 호스팅 서버입니다. 플레이어가 외부로 HTTP 요청을 보내므로 TV가 수신 포트를 노출할 필요가 없습니다. 각 플레이어는 자체 큐와 액세스 토큰을 가지며, 명령 제출에는 별도 관리자 토큰을 사용합니다.

선택 기능인 [신뢰성 있는 제어](docs/reliable-control.md)는 범위가 제한된 통합 자격 증명, 보관량이 제한된 영속 명령 결과, 설정을 정확히 검증하며 재개할 수 있는 Local/Home 적용, 실시간/다시보기 재생의 명시적 인계를 추가합니다. 이 기능에는 이 가이드를 포함한 컨트롤러와 CLI 리비전이 필요합니다. 정확한 설정 검증과 인계에는 플레이어 업데이트도 필요합니다. 아래에 고정된 이전 설치 리비전은 이 기능을 포함하지 않습니다. [아키텍처 다이어그램](docs/assets/reliable-control.svg)은 설정, 자격 증명 및 작업 상태가 어디에 남는지 보여 줍니다.

서버는 이동 가능한 단일 Go 실행 파일로 동작하며, 선택적 서비스 검색에 버전이 고정된 DNS 라이브러리를 사용합니다. 배포 패키지는 Linux amd64, arm64, ARMv7, macOS Intel과 Apple Silicon, Windows amd64와 arm64를 지원합니다. 컨테이너 이미지는 Linux amd64와 arm64를 지원합니다. Kubernetes와 k3s는 포함된 Helm chart를 사용합니다.

## 서버 시작

[릴리스](https://github.com/open-ott-play/ottplay-control-server/releases)에서 맞는 압축 파일을 내려받고, 릴리스 매니페스트와 대조하여 검증한 후 압축을 풉니다. Windows에서는 아래 명령에 `ottplay-control-server.exe`를 사용하세요.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init`는 무작위의 서로 다른 자격 증명을 새 파일에 생성하며, Unix에서는 소유자만 접근할 수 있습니다. 기존 파일이나 심볼릭 링크 덮어쓰기를 거부하고, 자격 증명을 출력하지 않습니다. 파일을 비공개로 유지하세요. Windows에서는 ACL을 서버 실행 계정으로 제한하세요. `config.example.json`은 필드를 설명하며 의도적으로 유효하지 않은 자리 표시자 토큰을 포함합니다.

기본 주소는 `0.0.0.0:8081`로, LAN을 포함한 모든 IPv4 인터페이스입니다. 서버의 LAN 주소와 포트로 TV를 연결하세요. 기존 설정은 명시된 `listen` 값을 유지합니다. 이전에 루프백에서만 접근 가능했던 설치를 공개하려면 값을 `0.0.0.0:8081`로 바꾸거나 `--listen 0.0.0.0:8081`을 사용하세요. 플레이어의 **정확한 페이지 출처**를 `allowed_origins`에 추가하세요. 예: `http://192.168.1.20:8443`. 스킴, 호스트 이름 및 포트가 일치해야 하며 경로를 붙이지 마세요. 출처 목록이 비어 있으면 브라우저가 아닌 클라이언트만 허용됩니다. 설정 변경은 다시 시작한 후 적용됩니다. 기기 및 관리자 자격 증명은 여전히 필요합니다.

신뢰할 수 있는 LAN의 오래된 TV 브라우저를 위해 HTTP를 지원합니다. 연결이 신뢰할 수 없는 네트워크를 통과하면 HTTPS를 사용하세요. `serve --config config.json --tls-cert server.crt --tls-key server.key`를 실행하거나 역방향 프록시에서 TLS를 종료하세요. HTTPS 플레이어 페이지는 브라우저에서 일반 HTTP 서버에 연결할 수 없습니다. 네이티브 앱도 운영체제 전송 정책을 유지하며, 이 설정은 그 정책을 완화하지 않습니다.

직접 HTTPS를 제공할 때는 리스너를 열기 전에 제공된 인증서 체인을 검증합니다. RSA 키는 최소 2048비트, ECDSA 키는 최소 224비트여야 하며 Ed25519도 지원합니다. 키가 너무 짧은 인증서는 업그레이드 전에 교체하세요. [TLS 인증서 정책과 적용 범위](docs/security-design.md#direct-https-certificate-policy)를 참조하세요.

## 플레이어 연결

명령 서버 연결 기능이 포함된 플레이어 릴리스를 사용하세요. **설정 → 원격 제어**를 열고 다음을 입력합니다.

1. **서버 주소:** 서버 IP 또는 호스트 이름이며 포트를 붙일 수 있습니다. 완전한 `http://` / `https://` 주소도 가능합니다. 호스트만 입력하면 HTTP 포트 8081이 기본값입니다.
2. **접근 코드:** `devices`에서 해당 플레이어 항목의 `token`입니다. `admin_token`을 사용하지 마세요.
3. **연결:** 폴링을 활성화하고 같은 대화상자의 상태를 확인합니다.

플레이어는 이 연결을 현재 설치에만 저장하며 설정 백업/클라우드 전송에서 제외합니다. 별도의 로컬 HTTP 리스너는 필요하지 않습니다. 웹 브라우저에는 허용된 CORS 출처가 필요합니다. `Origin: null`인 패키지형 TV 페이지는 지정된 기기/초기 연결 경로에 명시적 `allow_null_origin` 설정을 사용할 수 있습니다. 관리자 또는 진단 운영자 경로까지 열리는 것은 아닙니다.

## 터미널 리모컨

[그림이 포함된 Android OTA 가이드](docs/android-ota.md)는 일반 Capacitor Wi-Fi 업데이트(`ott DEVICE update --variant api22|full`, 기본값은 최신 베타, `--version`으로 릴리스 선택)와 별도의 루트 권한 에이전트를 구분합니다. 서명된 에이전트 및 APK/helper 업데이트, 복구, 설치 결과 검증을 설명합니다.

[Android ADB 가이드](docs/android-adb.md)는 워크스테이션 또는 Synology를 통한 USB 접근, 화면과 키보드 제어, 스크린샷, 로그, APK 설치, 일반 재부팅을 다루며 오래된 태블릿의 제약도 포함합니다.

선택 기능인 [DNS-SD 검색과 페어링](docs/discovery.md)은 플레이어가 네트워크 도메인을 통해 컨트롤러를 찾고 `ott approve NAME CODE` 후 개별 기기 토큰을 받게 합니다. DNS에는 서비스 메타데이터만 포함됩니다. 검색을 켜지 않아도 기존 수동 연결은 계속 작동합니다.

플레이어 별칭, 실시간 음량, 채널 검색, 현재 EPG, 제공업체 선택 및 지원되는 제공업체 설정에는 Python CLI를 사용하세요. [CLI 가이드](docs/cli.md)를 참조하세요. 서버와 플레이어 모두 요청/응답 확장을 포함해야 합니다.

Python CLI는 소스 작업 복사본에서 설치하며 네이티브 서버 압축 파일과 별개입니다. [설치 및 설정 단계](docs/cli.md#installation-and-connection)는 macOS/Linux와 Windows, Python/PATH 설정 및 필요한 CLI 모듈을 다룹니다. Workbench와 오프라인 검증이 포함된, 검토된 0.1.1 개발 소스 커밋에 고정되어 있습니다. 안정판 CLI v0.1.0과 네이티브 서버 압축 파일에는 이 명령이 없습니다. 기존 작업 복사본, 실행기 또는 비공개 설정을 대체하지 않고 [CLI를 업데이트하거나 되돌릴 수 있습니다](docs/cli.md#update-select-or-roll-back-the-cli). 로컬 실행기는 읽기 전용 [재생목록 리졸버](docs/cli.md#local-playlist-resolver)를 이용해 컨트롤러 없이 채널, 현재 프로그램 및 재생 가능한 다시보기를 검색할 수 있습니다. 설치와 설정 후:

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

`a1`은 등록된 플레이어의 예시 별칭이며 컴퓨터 호스트 이름이 아닙니다. [등록 및 페어링](docs/cli.md#register-and-connect-a-player), [예제가 있는 모든 명령](docs/cli.md#commands), [연결 해제 및 접근 취소](docs/cli.md#disconnect-deregister-or-revoke-a-player), [문제 해결](docs/cli.md#troubleshooting)을 참조하세요.

항상 켜 두는 플레이어에서는 [standby와 go](docs/cli.md#stop-playback-while-keeping-the-player-online)로 앱을 종료하지 않고 재생을 멈추었다가 나중에 깨울 수 있습니다. `stop`은 계속 `standby`의 별칭이며, `start`와 `wake`는 `go`의 별칭입니다. 원격 제어는 연결된 상태여야 하므로 대기 상태가 네트워크 트래픽이 전혀 없음을 보장하지 않습니다. `ott a1`은 플레이어가 알리는 작업을 표시합니다. 명령 수락은 종료나 재생 시작이 완료되었다는 확인이 아닙니다.

[화면 비율 명령](docs/cli.md#aspect-ratio-fit-and-fill)에는 업데이트된 CLI와 컨트롤러 및 `aspect` 기능을 알리는 플레이어가 필요합니다. 변경은 확인 응답 후 적용됩니다. `aspect`를 다시 읽어 모드와 저장 여부를 확인하세요. Fill은 화면을 늘리는 대신 비율을 유지하며 잘라냅니다.

원격 [스크린샷](docs/cli.md#remote-screenshots)에는 업데이트된 CLI와 컨트롤러 및 지원되는 캡처 어댑터가 있는 플레이어가 필요합니다. 플레이어의 원격 제어 연결을 켜면 해당 컨트롤러에 스크린샷과 진단 권한을 부여합니다. 네이티브 스크린샷은 별도 권한 스위치가 필요하지 않습니다. 브라우저는 여전히 로컬 소스 선택기가 필요합니다. 이미지에는 플레이어 설정, PIN 화면, 보이는 자격 증명이 포함될 수 있으므로 신뢰하는 컨트롤러에만 연결하세요. `ott a1`은 사용 가능 여부와 필요한 소스 선택을 알립니다. 스크린샷은 구조화된 원격 측정 수집과 별개입니다.

제목과 일치하는 모든 VPortal 동영상을 섞어 반복 재생하려면:

```sh
ott l vpr "wedding"
```

`vpr`은 전체 선택을 한 번 섞은 다음 해당 큐를 반복합니다. 새 순서를 원하면 다시 실행하세요. `vp`는 카탈로그 순서로 일치 항목을 재생하고, `vp --list`는 재생을 바꾸지 않고 목록을 보여 줍니다. [VPortal 명령](docs/cli.md#commands)을 참조하세요.

플레이어에 저장된 Plex 설정으로 명시적 Plex 순서를 재생하려면:

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

CLI는 기본적으로 최대 45초 기다립니다. `ott -t 45 l plex shuffle "Tom and Jerry"`는 같은 응답 제한 시간을 명시할 뿐, 동영상 재생 시간을 제한하지 않습니다. 큰 Plex 라이브러리를 모으는 데는 10~15초의 짧은 제한 시간보다 오래 걸릴 수 있습니다. `-t SECONDS` 또는 `--timeout SECONDS`를 플레이어 별칭 앞에 두세요. 미리보기 예와 재생 요청 시간 초과 시 대응은 [Plex 라이브러리 제한 시간](docs/cli.md#plex-libraries-by-name)을 참조하세요.

Plex 큐에는 `plex_queue`를 구현한 CLI, 컨트롤러 및 플레이어가 필요합니다. `plex play --shuffle ID...`는 주어진 ID를 한 번 섞습니다. 기존 키오스크 정책이 반복하지 않는 한 마지막 항목 뒤에 큐가 멈춥니다. 큐가 있는 동안 일반 `next`와 `prev`는 큐에 작용하며, `plex stop`은 큐를 비웁니다. 미리보기는 제공업체를 바꾸거나 현재 재생을 대체하지 않습니다. 제한과 준비 상태는 [Plex 큐 명령](docs/cli.md#ordered-plex-queues)을 참조하세요. 라이브러리 검색에는 추가로 `plex_library`를 알리는 최신 플레이어와 컨트롤러가 필요합니다. 플레이어에 저장된 Plex 연결을 사용하고 모호하거나 불완전한 선택을 거부합니다. [이름으로 Plex 라이브러리 찾기](docs/cli.md#plex-libraries-by-name)를 참조하세요.

### 로컬 동영상 프로필 저장

최신 플레이어와 컨트롤러에서 로컬 파일/폴더는 `offline` 제공업체를 사용합니다.

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

먼저 플레이어에서 파일/폴더를 연결하세요. 저장은 재생을 시작하지 않고 설정과 선택한 동영상을 기록합니다. 내보내기는 설명만 옮기며 미디어와 운영체제/브라우저 접근 권한은 기기에 남습니다. 다른 설치에서는 로컬 연결이 필요하고, 불러오기가 재생 확인을 뜻하지는 않습니다. 설정, 기능, 이동 형식의 제한과 문제 해결은 [로컬 동영상 프로필](docs/cli.md#local-videos-profiles)을 참조하세요.

### 프로필을 넘나들며 현재 항목 저장

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

즐겨찾기는 정상 재시작 후에도 대상 플레이어에 유지됩니다. 선택하면 저장된 제공업체/프로필을 불러오고 새 링크를 확인합니다. 소스가 바뀌었거나 항목이 없으면 다른 채널/영화로 대체하지 않고 실패합니다. CLI, 컨트롤러 및 플레이어는 `favorites`를 지원해야 하며 `ott l`이 사용 가능한 작업을 표시합니다. 별칭, 제한, 확인 응답 의미 및 문제 해결은 [프로필 간 즐겨찾기](docs/cli.md#favorites-across-profiles)를 참조하세요.

### 저장한 설정 묶음 불러오기

이름 있는 설정 묶음은 `~/.config/ottplay-control/cli.json`의 비공개 `presets` 부분에 저장하세요. 각 묶음은 번호가 있는 M3U 프로필(재생목록과 다시보기 시간), Plex 자격 증명, 불러오기 완료 시 선택할 M3U 프로필을 포함합니다. [설정 예](docs/cli.md#named-setups)를 참조하세요.

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

CLI는 먼저 전체 설정을 검증한 뒤 다음 변경을 보내기 전에 플레이어의 확인 응답을 기다립니다. 제공업체 로딩을 처리하고 Plex와 M3U 슬롯을 저장하며 지정된 M3U 프로필을 선택합니다. 오류가 나면 순서를 중단하고 확인된 단계를 보고합니다. 결과가 불확실한 쓰기는 자동으로 반복하지 않습니다. 별도 프로필 서비스나 컨트롤러 재시작이 필요하지 않습니다. 제공업체 자격 증명이 들어 있으므로 설정을 비공개로 유지하세요.

## 명령 보내기

이 예는 관리자 토큰을 URL이나 명령줄 인수에 넣지 않고 로컬 비공개 설정에서 읽습니다.

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

다른 명령으로 채널 번호/이름 선택, 무작위 채널 선택, 제공업체 전환, 메시지 표시, 지원 시 M3U 재생목록 변경 또는 플레이어 종료가 가능합니다. 사용 가능 여부는 플레이어 플랫폼과 현재 제공업체에 따라 다릅니다. [API 계약](docs/api.md)을 참조하세요.

독립 VPortal 제공업체를 지원하는 플레이어에서 `ott a1 provider
vportal`은 그 제공업체의 전용 프로필 15개를 선택합니다. `profile N vportal LINK`, `profile N
name NAME` 또는 `name`과 `vportal`을 포함하는 비공개 `profile-config N FILE`을 사용하고, `profile N`으로 설정된 슬롯을 선택하세요. 기존 M3U 프로필은 별도로 유지됩니다. `profiles`는 계정 페이지 링크나 키를 노출하지 않고 메타데이터를 보고합니다.

VPortal 영화나 에피소드 큐를 시작한 뒤 `ott a1 kiosk on`으로 그 선택을 잠급니다. 플레이어는 새로고침 후에도 큐와 위치를 유지하고 복구를 위해 미디어 URL을 다시 확인합니다. 다른 제목 선택이나 프로필 변경에는 `kiosk off`를 사용하고, 이후 `kiosk on`으로 잠그세요. 키오스크 수신 확인에는 제목과 에피소드 수가 표시되며, 제공업체 요청과 스트림 URL은 플레이어에 남습니다.

어린이용 재생에는 `ott a1 kiosk on --strict`를 사용하세요. 로컬 탭은 동영상 하단 표시줄을 보여 줍니다. 업데이트된 core 플레이어는 타임라인을 탭하거나 끌어 현재 VPortal/Plex 에피소드 안에서 이동할 수 있게 하고, 다른 로컬 제어는 잠긴 상태로 둡니다. 이전 플레이어, 채널 키오스크 및 타임라인 제스처를 가로채는 래퍼는 읽기 전용 하단 표시줄을 유지합니다. 기존 잠금은 대상을 유지합니다. 컨트롤러에서 `ott a1 kiosk off`로 해제하세요. Android 웹사이트에서 시스템 종료 동작을 막으려면 PIN을 사용하는 별도 앱 고정이 필요합니다. [키오스크 모드](docs/cli.md#kiosk-mode)를 참조하세요.

## 원격 진단

`doctor`, UI/미디어 `inspect`, 작업 수신 확인 조회, 증거 묶음, `health`, 웹/네이티브 `media-progress` 및 오프라인 `report verify`는 [읽기 전용 workbench 빠른 시작](docs/workbench.md#quick-start)부터 확인하세요. 기존 관리자 CLI 설정과 플레이어 별칭을 사용합니다. 네이티브 경로는 별도로 준비된 Android 유지관리 에이전트용이며, Tauri 플레이어는 웹 검사를 사용합니다. 보고서는 디코더 진행과 물리적 화면/소리 확인을 구분합니다. 기능 누락, 사용할 수 없는 경로와 불완전한 증거는 [workbench 문제 해결](docs/workbench.md#troubleshooting)을 참조하세요.

선택 기능인 [프로토콜 2 진단](docs/remote-diagnostics.md)은 런타임별로 명시적으로 켠 세션을 제공하며, 별도의 범위 제한 운영자 자격 증명과 제한된 구조화 원격 측정을 사용합니다. 활성화된 플레이어 연결은 추가 신뢰 확인 없이 로컬 권한을 제공하지만, 각 캡처에는 여전히 제한된 세션 유효 기간이 있습니다. 진단 제어 폴링은 기존 명령 큐와 독립적입니다.

[진단 CLI 및 MCP 가이드](docs/diagnostics-cli.md)는 설치, 운영자 자격 증명, 연결 설정, 각 진단 명령/도구, 캡처 및 복구 예, 런타임 접근 취소와 문제 해결을 다룹니다. `ott diagnostics --help`는 별도 명령군을 나열합니다. 일반 플레이어 별칭과 관리자 자격 증명은 진단 접근 권한을 주지 않습니다.

## 배포 패키지

- [Android 태블릿: 엄격한 Plex 키오스크와 시작](docs/android-kiosk-setup.md)
- [공유 Android USB 관리와 부팅 복구](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS 및 Windows](docs/deployment.md)
- [설정, 전달 의미와 제한](docs/api.md)
- [릴리스 및 검증 절차](docs/development.md)

큐는 크기가 제한되고 기본적으로 60초 후 만료되며 메모리에 저장됩니다. 서버를 다시 시작하면 대기 중 명령은 버려집니다. 복제본 하나만 실행하세요. 독립 복제본은 큐를 공유하지 않습니다. 플레이어는 전달 시도를 확인할 뿐, 재생이나 하드웨어 음량을 확인하지 않습니다. 네트워크 재시도 중복 제거는 실행 중 플레이어 세션 안에서만 이루어지며 앱 재시작을 넘어서지 않습니다.

이 서버는 [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss)의 선택적 `local_proxy.py` 예제 명령 API를 따르며, 관리자/기기 자격 증명을 분리하고 확인 응답으로 전달합니다. 기존 프록시는 별도 구성요소로 남습니다. 이 프로젝트는 미디어를 프록시하거나 플레이어의 HLS, EPG, 제공업체 네트워크 통신을 바꾸지 않습니다.

## 라이선스

MIT. 포함된 릴리스 도구의 출처 표기는 `NOTICE`와 `LICENSES/`에 있습니다.

## 프로젝트 유지관리

[기여 및 테스트 요구사항](CONTRIBUTING.md), [보안 보고 정책](SECURITY.md), [보안 설계](docs/security-design.md), [OpenSSF 증거와 남은 기준](docs/openssf-evidence.md)을 참조하세요.

현재 플레이어의 Plex 큐는 최대 500개 ID를 받습니다. `ott f10 plex play --shuffle ID...`로 한 번 섞고, Plex 제공업체가 선택된 상태에서 `ott f10 kiosk on --strict`로 해당 큐를 반복하고 잠그세요. `ott f10 kiosk off`로 잠금을 해제합니다. 이전 플레이어는 자신이 알리는 큐 제한을 유지합니다.

## 릴리스 후보 퍼징

공개 릴리스 후보는 [퍼징 가이드](docs/fuzzing.md)에 설명된 입력 경계에 제한된 네이티브 퍼징을 실행합니다. 변이 캠페인은 PR, 베타 또는 야간 빌드에서는 실행하지 않습니다. 가이드는 범위, 예산, 증거와 회귀 문제 재현을 다룹니다.

## 커뮤니티 및 보안 증거

[기여](CONTRIBUTING.md), [거버넌스](GOVERNANCE.md), [행동 강령](CODE_OF_CONDUCT.md), [로드맵](ROADMAP.md), [OpenSSF 증거](docs/openssf-evidence.md)를 참조하세요. 증거 색인은 지원되는 보안 프로필, 실제 테스트 결과 및 남은 Silver 요구사항을 구분합니다.
