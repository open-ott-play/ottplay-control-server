# Tablet Android: quiosque Plex estrito e arranque

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Guia ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Gestor USB](android-usb-manager.md)

Um tablet dedicado precisa de três definições verificadas separadamente: fila de conteúdos, política de quiosque estrito OTT e arranque Android. Instalar um APK ou verificar desligar/retomar o ecrã não prova o arranque após reiniciar o sistema.

## Identificar e instalar

1. Registe o tablet físico, alias/UUID OTT, alias do gestor USB, número de série, modelo, ID Android e SDK. Os aliases OTT e USB podem diferir; não deduza a identidade de um alias nem reutilize o token de outro tablet.
2. Use a variante oficial APK compatível com esse SDK, verifique SHA-256 da versão e identidade do pacote/assinatura e instale com `install -r`. Preserve os dados. Use o [gestor partilhado](android-usb-manager.md) quando controlar USB; não inicie outro servidor ADB nem comandos `adb` sem dispositivo explícito.
3. Emparelhe o reprodutor com as próprias credenciais do controlador e verifique `ott TABLET`. Carregue a predefinição nomeada desejada e execute `ott TABLET load PRESET verify`. As predefinições da rede doméstica e localhost de uma máquina de desenvolvimento não são intercambiáveis.

## Verificar Plex a partir do tablet

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Confira ID, título e número de itens da biblioteca. Um pedido bem-sucedido do computador do administrador não prova a conectividade do tablet. Perante falhas, verifique nele endereço guardado, encaminhamento, TLS e credenciais. Prefira o endpoint HTTPS válido do servidor. Se firmware antigo exigir HTTP na LAN privada, limite-o a esta instalação, registe que tokens e conteúdos circulam sem encriptação e não altere predefinições partilhadas nem desative globalmente a verificação de certificados. Nunca publique tokens ou URL de conteúdos privados em diagnósticos ou documentação.

Nos leitores mais recentes que apresentam `categories` em `caps`, o Plex abre todos os vídeos sem exigir a seleção de pastas. Utilize `ott TABLET categories` e `ott TABLET category all`; consulte [categorias de multimédia](cli.md#media-categories-and-stable-video-selection). Os passos de seleção de pastas abaixo referem-se à versão anterior explicitamente indicada.

Mudar o endereço do servidor Plex limpa as raízes de biblioteca escolhidas. Selecione o fornecedor **Plex**, abra **Plex settings → Folders**, marque a linha da biblioteca desejada (não apenas **Browse folders**), escolha **Ok** e depois **Save and open library**. No reprodutor `1.1.58-beta.19`, só as credenciais podem abrir o seletor enquanto `ready` continua false; termine a seleção antes dos comandos da fila. Não mude para M3U apenas para obter `ready: true`: uma fila Plex pode reproduzir ali, mas `kiosk on --strict` pode guardar uma política M3U em espera sem conteúdo associado. Isso não bloqueia nem restaura a fila Plex.

## Iniciar e bloquear a fila

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

Para uma fila ordenada use `plex library 'LIBRARY NAME'` em vez de `plex shuffle`. Shuffle escolhe uma ordem uma vez; o quiosque repete-a. Confirme os itens desejados, `enabled: true`, `strict: true` e uma política ligada à fila Plex correta. Uma política `waiting` sem canal ou conteúdo não é um quiosque aceite. Observe pelo menos duas amostras com posição/contador de fotogramas a avançar e inspecione o ecrã. Um recibo ou indicação `playing` sem progresso não basta. Teste uma transição natural no fim de um item; se um ciclo completo for impraticável, registe explicitamente essa limitação.

O quiosque estrito controla a interface do reprodutor. Não é inscrição como proprietário do dispositivo Android nem prova que Home, Recents, notificações e Settings estejam bloqueados. Mantenha o lançador do sistema e um acesso de manutenção supervisionado.

## Ativar o arranque separadamente

Compile e verifique [OTT Home 0.2.0](../native/android-home/README.md), mantendo a chave de assinatura existente para atualizações. Instale `play.ott.home` ao lado de `play.ott.foss`. Abra `play.ott.home/.StartupActivity` e ative **Start OTT after reboot**. A opção vem desligada. Não altera o binário oficial, a assinatura OTA, os perfis ou a atribuição do botão Home.

No firmware com seletor Home, pode também escolher **OTT Home → Always**. Onde o seletor não existe (incluindo o firmware Fire antigo testado), a opção de arranque funciona independentemente. Instalações antes configuradas como Home predefinido mantêm o comportamento. Desative os dois mecanismos para reversão completa.

`recover_on_boot` do gestor USB partilhado é um terceiro mecanismo independente: exige NAS e cabo USB, trata apenas um novo arranque e não reinicia continuamente uma aplicação que falhou. Não substitui arranque autónomo. Ao testá-lo, deixe a política de recuperação desligada para não esconder a falta de lançamento da aplicação acompanhante. Respeite os bloqueios seguros; desbloqueie normalmente quando solicitado.

## Aceitação e entrega

Guarde evidências em privado, sem credenciais em recibos públicos:

- Versões e hashes APK, correspondência de identidades física/OTT/USB e predefinição escolhida.
- Biblioteca Plex, quantidade/ordem de itens, política estrita e reprodução inicial a avançar.
- ID de arranque antes e depois de reiniciar realmente o sistema; depois, `sys.boot_completed=1`.
- Nenhum início manual do reprodutor nem recuperação USB entre reinício e observação.
- Atividade OTT em primeiro plano, reconexão ao controlador, fila/política estrita restaurada e reprodução a avançar. O resultado `requested` do recetor de arranque significa apenas envio.
- Teste separado do botão de alimentação: ecrã apagado, áudio parado, desbloqueio normal e retoma.
- Limites exatos: desbloqueio seguro obrigatório, recuperação assistida por USB, saídas físicas não testadas, falhas de fluxo ou falta de memória. Não declare esses casos totalmente aceites.

Antes de entregar outro tablet, repita a lista em vez de copiar o sucesso do anterior. Tablets Fire antigos podem precisar de `hls.js` para M3U porque o HLS nativo do fabricante pode falhar; isso não prova estabilidade Plex.

## Manutenção e reversão

```sh
ott TABLET kiosk off
```

Abra **OTT Startup**, desative **Start OTT after reboot** e restaure a seleção Home original se foi alterada. Se necessário, desinstale apenas `play.ott.home` pelo transporte USB verificado; preserve `play.ott.foss` e os dados. Desativar o arranque não altera a fila nem desliga automaticamente o quiosque.
