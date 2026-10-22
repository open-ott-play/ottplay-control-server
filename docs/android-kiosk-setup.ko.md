# Android 태블릿: 엄격한 Plex 키오스크와 시작

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[ADB 가이드](android-adb.md) · [OTT Home](../native/android-home/README.md) · [USB 관리자](android-usb-manager.md)

전용 태블릿에는 콘텐츠 대기열, OTT의 엄격한 키오스크 정책, Android 시작이라는 세 가지 설정을 따로 검증해야 합니다. APK 설치나 화면 끄기/재개 검증만으로 OS 재부팅 후 시작을 입증할 수 없습니다.

## 식별 및 설치

1. 실제 태블릿, OTT 별칭/UUID, USB 관리자 별칭, 일련번호, 모델, Android ID와 SDK를 기록하세요. OTT와 USB 별칭은 다를 수 있습니다. 별칭만으로 신원을 판단하거나 다른 태블릿의 토큰을 재사용하지 마세요.
2. SDK와 호환되는 공식 APK 변형을 사용하고 릴리스 SHA-256과 패키지/서명 신원을 확인한 뒤 `install -r`로 설치하세요. 플레이어 데이터를 보존하세요. USB를 [공유 관리자](android-usb-manager.md)가 소유하면 이를 사용하고, 경쟁 ADB 서버나 대상을 명시하지 않은 `adb` 명령을 실행하지 마세요.
3. 플레이어 자신의 컨트롤러 자격 증명으로 페어링하고 `ott TABLET`를 확인하세요. 원하는 이름 있는 프리셋을 불러와 `ott TABLET load PRESET verify`를 실행하세요. 홈 네트워크 프리셋과 개발 컴퓨터의 localhost 프리셋은 서로 대체할 수 없습니다.

## 태블릿에서 Plex 검증

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

라이브러리 ID, 제목과 항목 수를 확인하세요. 관리자 컴퓨터의 요청 성공이 태블릿 연결을 입증하지는 않습니다. 실패하면 태블릿의 저장 주소, 라우팅, TLS 지원과 자격 증명을 확인하세요. 서버의 유효한 HTTPS 엔드포인트를 권장합니다. 구형 펌웨어에 개인 LAN의 HTTP가 필요하다면 해당 설치에만 한정하고, 토큰과 미디어가 암호화되지 않은 LAN으로 전송됨을 기록하세요. 공유 프리셋을 수정하거나 인증서 검증을 전역으로 끄지 마세요. 진단이나 문서에 토큰 또는 비공개 미디어 URL을 공개하지 마세요.

Plex 서버 주소를 바꾸면 선택된 라이브러리 루트가 지워집니다. **Plex** 공급자를 고르고 **Plex settings → Folders**를 열어 원하는 라이브러리 행을 체크하세요(**Browse folders**만으로는 부족합니다). **Ok**, 이어서 **Save and open library**를 선택하세요. 플레이어 `1.1.58-beta.19`에서는 서버 자격 증명만으로 폴더 선택기가 열려도 `ready`가 false일 수 있습니다. 대기열 명령 전에 폴더 선택을 완료하세요. `ready: true`를 얻기 위해 M3U로 바꾸지 마세요. Plex 대기열이 거기서 재생돼도 `kiosk on --strict`가 미디어 없는 대기 중 M3U 정책을 저장할 수 있습니다. 이는 Plex 대기열을 잠그거나 복원하지 않습니다.

## 대기열 시작 및 잠금

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

순서 있는 대기열에는 `plex shuffle` 대신 `plex library 'LIBRARY NAME'`을 사용하세요. Shuffle은 순서를 한 번 정하고 키오스크는 이를 반복합니다. 원하는 항목, `enabled: true`, `strict: true`, 올바른 Plex 미디어 대기열에 연결된 정책을 확인하세요. 채널이나 미디어 없는 `waiting` 정책은 승인된 키오스크가 아닙니다. 위치/프레임 수가 증가하는 샘플을 두 번 이상 관찰하고 화면을 확인하세요. 진행 없는 응답 확인이나 `playing` 표시는 부족합니다. 항목 끝의 자연스러운 전환을 시험하고, 전체 반복 검사가 어렵다면 그 한계를 명시하세요.

엄격한 키오스크는 플레이어 UI를 제어합니다. Android 기기 소유자 등록이나 Home, Recents, 알림과 Settings 차단의 증거가 아닙니다. 기존 시스템 런처와 사람이 지켜보는 유지보수 경로를 유지하세요.

## 시작을 별도로 활성화

업데이트용 기존 서명 키를 유지하면서 [OTT Home 0.2.0](../native/android-home/README.md)을 빌드하고 검증하세요. `play.ott.foss`와 함께 `play.ott.home`을 설치하세요. `play.ott.home/.StartupActivity`를 열고 **Start OTT after reboot**를 켜세요. 기본값은 꺼짐입니다. 공식 플레이어 바이너리, OTA 서명, 프로필 데이터나 Home 버튼 할당을 변경하지 않습니다.

Home 선택기가 있는 펌웨어에서는 필요하면 **OTT Home → Always**도 선택하세요. 선택기가 없으면(검증한 구형 Fire 포함) 부팅 허용이 독립적으로 시작을 제공합니다. 기존 기본 Home 설정은 동작을 유지합니다. 완전히 되돌리려면 두 메커니즘을 모두 끄세요.

공유 USB 관리자의 `recover_on_boot`는 세 번째 독립 메커니즘입니다. NAS와 USB 케이블이 필요하고 새 부팅만 처리하며, 중단된 앱을 계속 재시작하지 않습니다. 자율 시작의 대체물이 아닙니다. 자율 시작 시험에서는 복구를 꺼 두어 동반 앱의 실행 실패가 가려지지 않도록 하세요. 보안 잠금 화면을 존중하고 요청되면 정상적으로 잠금을 푸세요.

## 인수 및 전달

증거는 비공개로 보관하고 공개 기록에 자격 증명을 넣지 마세요.

- APK 버전, 해시, 실제 기기/OTT/USB 신원 매핑과 선택 프리셋.
- Plex 라이브러리, 항목 수/순서, 엄격한 정책과 초기 재생 진행.
- 실제 OS 재부팅 전후 부팅 ID와 이후의 `sys.boot_completed=1`.
- 재부팅과 관찰 사이 수동 플레이어 실행이나 USB 복구가 없음.
- OTT 포그라운드 활동, 컨트롤러 재연결, 대기열/엄격한 정책 복원과 재생 진행. 부팅 수신기의 `requested` 결과는 요청 발송만 의미합니다.
- 별도 전원 버튼 시험: 화면 꺼짐, 음성 정지, 정상 잠금 해제와 재개.
- 정확한 제한: 보안 잠금 해제 필요, USB 지원 복구, 시험하지 않은 물리적 이탈 경로, 스트림 오류나 메모리 부족. 이를 완전히 승인된 상태로 부르지 마세요.

다음 태블릿을 전달하기 전에도 이전 성공을 복사하지 말고 같은 목록을 확인하세요. 구형 Fire는 제조사 기본 HLS가 중단될 수 있어 M3U에 `hls.js`가 필요할 수 있습니다. 이것이 Plex 재생 안정성을 입증하지는 않습니다.

## 유지보수 및 롤백

```sh
ott TABLET kiosk off
```

**OTT Startup**을 열고 **Start OTT after reboot**를 끈 뒤, Home 선택을 바꿨다면 원래대로 복원하세요. 필요하면 검증된 USB 전송으로 `play.ott.home`만 제거하고 `play.ott.foss`와 데이터는 보존하세요. 시작 비활성화는 대기열을 바꾸거나 키오스크를 자동으로 끄지 않습니다.
