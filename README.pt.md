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

[Documentação / Wiki](docs/wiki/Home.pt.md)

Um pequeno servidor para hospedar por conta própria que entrega comandos remotos a reprodutores OTT-play. Os reprodutores fazem solicitações HTTP de saída, portanto a TV não precisa expor uma porta de escuta. Cada reprodutor tem sua própria fila e token de acesso; um token de administrador separado envia os comandos.

O [controle confiável](docs/reliable-control.md) opcional acrescenta credenciais de integração com escopo limitado, resultados de comandos persistentes com armazenamento limitado, aplicação retomável de configurações Local/Home com verificação exata das definições e transferência explícita entre reprodução ao vivo e de arquivo. Esses recursos exigem revisões do controlador e da CLI que contenham este guia. A verificação exata e a transferência também exigem um reprodutor atualizado. A revisão de instalação antiga fixada abaixo não inclui esses recursos. O [diagrama de arquitetura](docs/assets/reliable-control.svg) mostra onde permanecem configurações, credenciais e estado das operações.

O servidor funciona como um único executável Go portátil, com uma biblioteca DNS de versão fixa para descoberta opcional de serviços. Os pacotes abrangem Linux amd64, arm64 e ARMv7; macOS Intel e Apple Silicon; Windows amd64 e arm64. As imagens de contêiner suportam Linux amd64 e arm64. Kubernetes e k3s usam o chart Helm incluído.

## Iniciar um servidor

Baixe o arquivo correspondente em [Releases](https://github.com/open-ott-play/ottplay-control-server/releases), verifique-o com o manifesto da versão e extraia-o. No Windows, use `ottplay-control-server.exe` nos comandos abaixo.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` cria credenciais aleatórias e distintas em um novo arquivo com acesso exclusivo do proprietário no Unix. Recusa-se a sobrescrever um arquivo ou link simbólico existente e nunca imprime as credenciais. Mantenha o arquivo privado; no Windows, restrinja sua ACL à conta que executa o servidor. `config.example.json` documenta os campos e contém intencionalmente tokens de exemplo inválidos.

O endereço padrão é `0.0.0.0:8081` (todas as interfaces IPv4, incluindo a LAN). Conecte as TVs usando o endereço LAN e a porta do servidor. Configurações existentes mantêm o valor explícito de `listen`; altere-o para `0.0.0.0:8081` ou use `--listen 0.0.0.0:8081` para expor uma instalação antes restrita ao loopback. Adicione a **origem exata da página** do reprodutor a `allowed_origins`, por exemplo `http://192.168.1.20:8443`. Esquema, nome do host e porta devem corresponder; não acrescente um caminho. Uma lista de origens vazia permite apenas clientes fora do navegador. Alterações de configuração entram em vigor após reiniciar. As credenciais do dispositivo e do administrador continuam obrigatórias.

HTTP é suportado para navegadores de TVs antigas em uma LAN confiável. Use HTTPS quando a conexão atravessar uma rede não confiável: `serve --config config.json --tls-cert server.crt --tls-key server.key`, ou termine TLS em um proxy reverso. Páginas do reprodutor carregadas via HTTPS não podem se conectar, no navegador, a um servidor HTTP sem criptografia. Aplicativos nativos também mantêm a política de transporte de seu sistema operacional; essa configuração não a flexibiliza.

HTTPS direto valida a cadeia de certificados fornecida antes de abrir a escuta: chaves RSA devem ter pelo menos 2048 bits e ECDSA pelo menos 224 bits; Ed25519 também é aceito. Substitua certificados com chaves pequenas demais antes da atualização. Veja a [política de certificados TLS e seu escopo](docs/security-design.md#direct-https-certificate-policy).

## Conectar o reprodutor

Use uma versão do reprodutor que inclua a conexão ao servidor de comandos. Abra **Configurações → Controle remoto** e informe:

1. **Endereço do servidor:** IP ou nome do host, opcionalmente com porta, ou um endereço completo `http://` / `https://`. Um host sem esquema usa HTTP na porta 8081 por padrão.
2. **Código de acesso:** o `token` da entrada desse reprodutor em `devices`, nunca `admin_token`.
3. **Conectar:** ative a consulta periódica e confira o estado exibido no mesmo diálogo.

O reprodutor armazena essa conexão apenas na instalação atual e a exclui dos backups de configurações e da transferência pela nuvem. Não exige o servidor HTTP local separado. Navegadores web precisam de uma origem CORS permitida; páginas de TV empacotadas com `Origin: null` podem usar a definição explícita `allow_null_origin` nas rotas designadas de dispositivo/bootstrap. Isso não habilita rotas de administrador ou operador de diagnóstico.

## Controle pelo terminal

O [guia ilustrado de Android OTA](docs/android-ota.md) distingue atualizações normais do Capacitor via Wi-Fi (`ott DEVICE update --variant api22|full`, última beta por padrão; `--version` seleciona uma versão) do agente separado com acesso root. Explica as atualizações assinadas do agente e de APK/helper, recuperação e verificação do resultado instalado.

O [guia de Android ADB](docs/android-adb.md) cobre acesso USB por uma estação de trabalho ou Synology, controle de tela e teclado, capturas de tela, logs, instalação de APK e reinicialização normal, incluindo limitações de tablets antigos.

A [descoberta e o pareamento DNS-SD](docs/discovery.md) opcionais permitem aos reprodutores encontrar o controlador pelo domínio de rede e receber um token individual após `ott approve NAME CODE`. DNS contém apenas metadados do serviço. Conexões manuais existentes continuam funcionando sem ativar a descoberta.

Use a CLI Python para aliases de reprodutores, volume em tempo real, busca de canais, EPG atual, seleção de provedor e configuração de provedores compatíveis: [guia da CLI](docs/cli.md). Servidor e reprodutor devem incluir a extensão de solicitação/resposta.

A CLI Python é instalada a partir de uma cópia dos fontes, separadamente do arquivo do servidor nativo. As [etapas de instalação e configuração](docs/cli.md#installation-and-connection) cobrem macOS/Linux e Windows, configuração de Python/PATH e os módulos necessários. Fixam um commit revisado do desenvolvimento 0.1.1 com workbench e verificação offline; a CLI estável v0.1.0 e os arquivos do servidor nativo não incluem esses comandos. [Atualize ou reverta a CLI](docs/cli.md#update-select-or-roll-back-the-cli) sem substituir uma cópia de trabalho, um inicializador ou uma configuração privada existentes. Inicializadores locais podem usar o [resolvedor de playlists](docs/cli.md#local-playlist-resolver) somente leitura para pesquisar canais, programas atuais e arquivos reproduzíveis sem controlador. Depois de instalar e configurar:

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

`a1` é um alias de exemplo para um reprodutor registrado, não um nome de computador. Veja [registro e pareamento](docs/cli.md#register-and-connect-a-player), [todos os comandos com exemplos](docs/cli.md#commands), [desconexão e revogação de acesso](docs/cli.md#disconnect-deregister-or-revoke-a-player) e [solução de problemas](docs/cli.md#troubleshooting).

Para um reprodutor sempre ligado, use [standby e go](docs/cli.md#stop-playback-while-keeping-the-player-online) para parar a reprodução e retomá-la depois sem sair do aplicativo. `stop` continua sendo alias de `standby`; `start` e `wake` continuam sendo aliases de `go`. O controle remoto precisa permanecer conectado, portanto a espera não promete ausência de tráfego de rede. `ott a1` mostra as operações anunciadas pelo reprodutor. Um comando aceito não confirma que o desligamento ou o início da reprodução terminou.

Os [comandos de proporção da imagem](docs/cli.md#aspect-ratio-fit-and-fill) exigem CLI e controlador atualizados e um reprodutor que anuncie a capacidade `aspect`. A mudança é aplicada após confirmação; leia `aspect` novamente para confirmar o modo e se foi salvo. Fill recorta proporcionalmente em vez de esticar a imagem.

As [capturas de tela](docs/cli.md#remote-screenshots) remotas exigem CLI e controlador atualizados e um reprodutor com adaptador de captura compatível. Ativar a conexão Controle remoto do reprodutor autoriza capturas e diagnósticos para esse controlador; capturas nativas não precisam de uma permissão separada. Navegadores ainda exigem seu seletor local de fonte. Imagens podem conter configurações, telas de PIN e credenciais visíveis, portanto conecte-se apenas a controladores confiáveis. `ott a1` informa disponibilidade e eventual necessidade de selecionar uma fonte. Capturas são separadas da coleta de telemetria estruturada.

Para embaralhar todos os vídeos VPortal correspondentes a um título e reproduzi-los em repetição:

```sh
ott l vpr "wedding"
```

`vpr` embaralha toda a seleção uma vez e depois repete essa fila. Execute novamente para outro embaralhamento. `vp` reproduz os resultados na ordem do catálogo; `vp --list` os lista sem alterar a reprodução. Veja os [comandos VPortal](docs/cli.md#commands).

Para uma sequência Plex explícita usando a configuração Plex salva do reprodutor:

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

A CLI espera até 45 segundos por padrão. Escrever `ott -t 45 l plex shuffle "Tom and Jerry"` torna explícito o mesmo tempo limite de resposta; não limita a duração da reprodução dos vídeos. Coletar bibliotecas Plex grandes pode levar mais que um tempo curto de 10–15 segundos. Coloque `-t SECONDS` (ou `--timeout SECONDS`) antes do alias do reprodutor. Veja [tempos limite das bibliotecas Plex](docs/cli.md#plex-libraries-by-name) para exemplos de prévia e o que fazer se uma solicitação de reprodução expirar.

Filas Plex exigem CLI, controlador e reprodutor com `plex_queue`. `plex play --shuffle ID...` embaralha uma vez os IDs fornecidos. As filas param após o último item, salvo se a política de quiosque existente as repetir. Enquanto uma fila existir, os comandos comuns `next` e `prev` atuam nela; `plex stop` a limpa. Uma prévia nunca troca o provedor nem substitui a reprodução atual. Veja [comandos de fila Plex](docs/cli.md#ordered-plex-queues) para limites e disponibilidade. A busca de bibliotecas exige ainda reprodutor e controlador atualizados que anunciem `plex_library`; usa a conexão Plex salva do reprodutor e rejeita seleções ambíguas ou incompletas. Veja [bibliotecas Plex por nome](docs/cli.md#plex-libraries-by-name).

### Salvar perfis de vídeos locais

Com reprodutor e controlador atuais, arquivos/pastas locais usam o provedor `offline`:

```sh
ott d1 provider offline
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Conecte primeiro os arquivos/pastas no reprodutor. Salvar registra a configuração e o vídeo selecionado sem iniciar a reprodução. A exportação transfere apenas descrições; mídia e permissões do sistema/navegador permanecem no dispositivo. Outra instalação exige vinculação local, e carregar não significa reprodução confirmada. Veja [perfis de vídeos locais](docs/cli.md#local-videos-profiles) para configuração, capacidades, limites do formato portátil e solução de problemas.

### Salvar o item atual entre perfis

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Favoritos persistem no reprodutor de destino durante reinicializações normais. Selecionar um carrega seu provedor/perfil salvo e resolve um link novo; uma fonte alterada ou item ausente gera falha sem substituição por outro canal ou filme. CLI, controlador e reprodutor devem suportar `favorites`; `ott l` mostra as operações disponíveis. Veja [favoritos entre perfis](docs/cli.md#favorites-across-profiles) para aliases, limites, semântica de confirmação e solução de problemas.

### Carregar uma configuração salva

Armazene configurações nomeadas na seção privada `presets` de `~/.config/ottplay-control/cli.json`. Cada uma contém perfis M3U numerados (playlist e horas de arquivo), credenciais Plex e o perfil M3U a selecionar quando o carregamento terminar. Veja o [exemplo de configuração](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

A CLI valida primeiro toda a configuração e aguarda a confirmação do reprodutor antes de enviar cada próxima alteração. Trata o carregamento do provedor, salva Plex e os slots M3U e seleciona o perfil M3U configurado. Um erro interrompe a sequência e informa as etapas confirmadas; uma escrita incerta nunca é repetida automaticamente. Não é necessário um serviço adicional de perfis nem reiniciar o controlador. Mantenha essa configuração privada: ela contém credenciais dos provedores.

## Enviar um comando

Este exemplo lê o token de administrador da configuração local privada sem colocá-lo na URL ou nos argumentos da linha de comando:

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

Outros comandos selecionam canal por número ou nome, escolhem um canal aleatório, trocam provedores, mostram uma mensagem, alteram uma playlist M3U quando suportado ou encerram o reprodutor. A disponibilidade depende da plataforma e do provedor atual. Veja o [contrato da API](docs/api.md).

Com um reprodutor que suporte o provedor VPortal independente, `ott a1 provider
vportal` seleciona seus próprios 15 perfis. Use `profile N vportal LINK`, `profile N
name NAME` ou um arquivo privado `profile-config N FILE` contendo `name` e `vportal`, e depois selecione o slot configurado com `profile N`. Perfis M3U existentes permanecem separados. `profiles` informa metadados sem expor links da conta ou chaves.

Após iniciar um filme ou fila de episódios VPortal, `ott a1 kiosk on` bloqueia essa seleção. O reprodutor preserva a fila e sua posição entre recarregamentos e resolve novamente as URLs da mídia para recuperação. Use `kiosk off` para escolher outro título ou mudar perfis, depois `kiosk on` para bloquear. O recibo do quiosque mostra o título e a quantidade de episódios; solicitações ao provedor e URLs de fluxos ficam no reprodutor.

Use `ott a1 kiosk on --strict` para reprodução infantil: um toque local mostra o rodapé do vídeo. Reprodutores core atualizados permitem tocar ou arrastar a linha do tempo para avançar ou retroceder no episódio VPortal/Plex atual; os demais controles locais permanecem bloqueados. Reprodutores antigos, quiosques de canais e wrappers que interceptam gestos na linha do tempo mantêm um rodapé somente leitura. Um bloqueio existente mantém seu alvo. Libere-o pelo controlador com `ott a1 kiosk off`. Em sites Android, a proteção contra saída pelo sistema exige fixação separada do aplicativo com PIN; veja [modo quiosque](docs/cli.md#kiosk-mode).

## Diagnósticos remotos

Comece pelo [início rápido do workbench somente leitura](docs/workbench.md#quick-start) para `doctor`, `inspect` de interface/mídia, consulta de recibos de operações, pacotes de evidências, `health`, `media-progress` web/nativo e `report verify` offline. Ele usa a configuração CLI de administrador e os aliases existentes. A via nativa é para o agente de manutenção Android provisionado separadamente; reprodutores Tauri usam inspeção web. Os relatórios distinguem progresso do decodificador de verificação física da tela/som. Veja [solução de problemas do workbench](docs/workbench.md#troubleshooting) para capacidades ausentes, vias indisponíveis e evidências incompletas.

Os [diagnósticos do protocolo 2](docs/remote-diagnostics.md) opcionais fornecem sessões explicitamente habilitadas por ambiente de execução, com credenciais de operador separadas e limitadas e telemetria estruturada com limites. A conexão habilitada do reprodutor fornece autorização local sem outra solicitação de confiança; cada captura ainda tem uma concessão de sessão limitada. A consulta do controle de diagnóstico é independente da fila de comandos existente.

O [guia da CLI de diagnóstico e MCP](docs/diagnostics-cli.md) cobre instalação, credenciais do operador, conexão, cada comando/ferramenta de diagnóstico, exemplos de captura e reparo, revogação de ambientes de execução e solução de problemas. `ott diagnostics --help` lista essa família separada de comandos; aliases comuns de reprodutores e credenciais de administrador não concedem acesso de diagnóstico.

## Pacotes de implantação

- [Tablet Android: quiosque Plex estrito e inicialização](docs/android-kiosk-setup.md)
- [Gerenciamento USB Android compartilhado e recuperação de inicialização](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS e Windows](docs/deployment.md)
- [Configuração, semântica de entrega e limites](docs/api.md)
- [Processo de publicação e validação](docs/development.md)

As filas têm limites, expiram após 60 segundos por padrão e residem na memória. Reiniciar o servidor descarta os comandos pendentes. Execute uma réplica; réplicas independentes não compartilham filas. Um reprodutor confirma uma tentativa de entrega, não a reprodução ou o volume do hardware. Repetições de rede são deduplicadas na sessão ativa do reprodutor, não entre reinicializações do aplicativo.

Este servidor segue a API de comandos do exemplo opcional `local_proxy.py` em [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), com credenciais separadas de administrador/dispositivo e entrega com confirmação. O proxy antigo permanece um componente separado. Este projeto não atua como proxy de mídia nem altera a rede HLS, EPG ou dos provedores do reprodutor.

## Licença

MIT. A atribuição das ferramentas de publicação incorporadas está em `NOTICE` e `LICENSES/`.

## Manutenção do projeto

Veja [requisitos de contribuição e testes](CONTRIBUTING.md), a [política de relatos de segurança](SECURITY.md), o [projeto de segurança](docs/security-design.md) e as [evidências OpenSSF e critérios restantes](docs/openssf-evidence.md).

Filas Plex aceitam até 500 IDs nos reprodutores atuais. Use `ott f10 plex play --shuffle ID...` para embaralhar uma vez, depois `ott f10 kiosk on --strict` com o provedor Plex selecionado para repetir e bloquear essa fila. `ott f10 kiosk off` libera o bloqueio. Reprodutores antigos mantêm o limite de fila que anunciam.

## Fuzzing de versões candidatas

Versões candidatas públicas executam fuzzing nativo limitado das fronteiras de entrada descritas no [guia de fuzzing](docs/fuzzing.md). Campanhas de mutação não são executadas em PRs, betas ou compilações noturnas. O guia cobre escopo, orçamentos, evidências e reprodução de regressões.

## Comunidade e evidências de segurança

Veja [contribuições](CONTRIBUTING.md), [governança](GOVERNANCE.md), o [Código de Conduta](CODE_OF_CONDUCT.md), o [roteiro](ROADMAP.md) e as [evidências OpenSSF](docs/openssf-evidence.md). O índice distingue perfis de segurança suportados, resultados reais de testes e requisitos Silver restantes.
