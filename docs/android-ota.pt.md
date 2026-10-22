<div lang="pt" dir="ltr">

# OTA Android: leitores comuns e agente root separado

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Para **k8a/fire8 e outros leitores Capacitor**, utilize o atualizador Wi-Fi do leitor normal, não o comando do agente root:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Omitir `--version` seleciona a beta publicada mais recente. Use `full` em vez de `api22` em Android 7+. A CLI encontra a versão oficial, verifica o manifesto, prepara o APK e consulta o seu estado antes de pedir a instalação uma única vez. O Android pode exigir confirmação no tablet. Desative explicitamente o quiosque e desbloqueie as definições protegidas antes da manutenção; o comando nunca altera essas políticas. Consulte o [procedimento completo da CLI](cli.md#capacitor-apk-updates-over-wi-fi).

São necessários um APK já instalado com suporte a `AppUpdate`, Wi-Fi e um leitor em execução ligado ao controlador. As atualizações normais seguintes não precisam de USB, mas não alcançam um tablet offline nem uma WebView parada. A primeira instalação, um atualizador indisponível, uma aplicação avariada ou perda de rede ainda podem exigir recuperação local. Não está implícito um agendador automático de versões nem um instalador silencioso geral.

O resto deste guia descreve um **agente de manutenção a1 diferente, com root**, limitado à instalação Matricom/API19 preparada `play.ott.kitkat`. Não está já instalado nem é utilizável em dispositivos Fire/Capacitor comuns.

Após a preparação inicial única por USB com root, o agente Android independente pode receber atualizações assinadas pela sua ligação HTTPS de saída. Não precisa de uma WebView funcional para se atualizar. O agente 0.1.2 também suporta um pacote coordenado de APK e auxiliar tátil do sistema, com ficheiros de recuperação guardados e verificação antes da confirmação final.

Este guia explica todo o percurso. Para campos exatos do manifesto, limites e requisitos de hardware, consulte a [referência do agente nativo](../native/android-agent/README.md#signed-updates-over-wi-fi).

## O que é atualizado

![Arquitetura OTA: os bytes revistos são assinados, publicados por HTTPS direto, verificados pelo agente independente e conferidos após instalar. O deployment web é separado.](images/ota-architecture.svg)

- **Agente nativo:** `kind: agent` substitui o próprio binário ARMv7. Também funciona quando OTT está desativado e outra aplicação HOME está em uso.
- **Apenas APK:** `kind: apk` substitui o invólucro mantendo os dados. OTT deve estar ativo e em primeiro plano. O auxiliar incorporado no candidato deve coincidir exatamente com o auxiliar instalado no sistema.
- **APK e auxiliar tátil:** `kind: bundle` atualiza ambos como par. Um agente antigo deve primeiro receber uma atualização apenas do agente que suporte pacotes.
- **Leitor alojado:** publique o artefacto web verificado na origem existente, depois recarregue e examine um leitor ativo. Atualizar o APK não publica o JavaScript alojado. Consulte o [fluxo de publicação](release-workflow.md).
- **Controlador e CLI:** publique o controlador e instale separadamente todo o conjunto de módulos CLI. Atualizar qualquer um não substitui código no tablet.

Firmware, kernel, WebView do sistema, intermediário root e scripts de arranque estão fora deste formato OTA. A [referência nativa](../native/android-agent/README.md#bootstrap) descreve os limites de preparação e recuperação. Operações OTA suportadas não exigem ADB de rede nem shell remota arbitrária.

## Da versão revista à instalação confirmada

1. **Escolher bytes verificados.** Integre alterações revistas após a CI passar. Descarregue o artefacto pretendido e verifique revisão de origem, provas imutáveis da versão e soma de verificação. Extraia o binário `agent` do arquivo nativo; o arquivo não é por si uma carga de agente instalável.
2. **Preparar e assinar.** Use a chave Ed25519 privada existente cuja chave pública ficou fixada na preparação inicial. Mantenha privados a chave e os dados de acesso do dispositivo.
3. **Publicar e verificar o transporte.** Publique apenas a carga e `signed.json` em caminhos HTTPS imutáveis. Ambos devem devolver diretamente os bytes com HTTP 200; o atualizador recusa redirecionamentos. Execute `verify` na ferramenta de empacotamento.
4. **Enviar uma atualização.** Registe o ID do pedido e examine o estado após a confirmação. Uma resposta perdida ou incerta não justifica repetir cegamente uma operação.
5. **Confirmar o efeito.** Compare hashes e versões instalados acabados de consultar com a versão preparada. Num leitor ativo, verifique também alvo do quiosque, fila e progressão da reprodução. Imagem e som físicos exigem observação.

Exemplo de uma versão do agente; substitua a versão escolhida e os caminhos reais:

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

`ENVELOPE_SHA256` é o `envelope_sha256` da ferramenta de empacotamento, não o hash da carga. `accepted` significa admissão, não instalação bem-sucedida. A substituição do próprio agente pode deixar um recibo antigo como `unknown`; o novo runtime, a versão esperada e o hash instalado correspondente comprovam a instalação. A [referência CLI](cli.md#independent-android-agent) descreve encaminhamento nativo independente e tratamento dos recibos.

## Como a transação APK/auxiliar recupera

![Transação APK/auxiliar: pré-verificação, guardar par anterior, aplicar, verificar e confirmar; falhas restauram o par anterior e recuperação incompleta mantém alterações à aplicação bloqueadas.](images/ota-transaction.svg)

O pacote contém exatamente `app.apk` e `ott-touch-guard`. A ferramenta pode criá-lo diretamente do APK invólucro assinado com `--kind bundle --apk`. Extrai o auxiliar incorporado para que as duas cópias entregues coincidam. Consulte o [comando e requisitos do pacote](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Antes de alterar ficheiros, o agente exige um par instalado coincidente, espaço livre suficiente, um leitor OTT responsivo em primeiro plano, partição do sistema só de leitura e suporte anunciado à descida de versão APK. Num quiosque bloqueado, verifica o mesmo ID de canal TV ou os ID VPortal ordenados; comprimentos de fila iguais não bastam. Filas bloqueadas não suportadas são recusadas. Uma aplicação OTT intencionalmente desativada também é recusada, preservando a aplicação HOME atual do utilizador.

Os ficheiros de recuperação e a transação pendente são persistidos antes de parar a aplicação. O agente espera a paragem da aplicação e do auxiliar antigo, substitui o auxiliar atomicamente, repõe `/system` só de leitura, instala o APK e verifica o resultado. Uma falha de aplicação ou verificação desencadeia a restauração do par anterior. A recuperação no arranque repõe ficheiros sem lançar OTT nem assumir HOME.

Se a recuperação não terminar, `updates.recovery_failed` é verdadeiro. Diagnósticos e reparação assinada apenas do agente continuam disponíveis; alterações à aplicação e watchdog de reprodução ficam bloqueados. A transação guardada não pode ser eliminada para forçar uma instalação. Recuperar binários não desfaz migrações de dados da aplicação e o firmware ainda pode recusar uma descida de versão. Consulte as [limitações de recuperação](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Resultados e registo de aceitação

![Gráfico de validação: 488 testes CLI, 576 de ferramentas de publicação e 6 de empacotamento OTA; o tablet de referência executa o novo agente verificado, mas a aceitação física APK/auxiliar está pendente.](images/ota-validation.svg)

A auditoria de 14 de outubro de 2026 passou 488 testes CLI (um ignorado), 576 testes de ferramentas de publicação, seis testes de empacotamento OTA, verificações nativas Go de concorrência/estáticas e uma compilação ARMv7. Estes números indicam dimensão das suites, não cobertura ou desempenho. Testes com injeção de falhas exercitam arquivos inválidos, transações interrompidas, restauração parcial, alterações de alvo e bloqueios de recuperação. A revisão independente também detetou e corrigiu uma verificação insuficiente da identidade da fila e validação APK/auxiliar diferente entre anfitrião e dispositivo.

Conforme verificado em 15 de outubro de 2026:

- A implementação OTA está integrada no [PR do controlador #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) e incluída em [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Os pacotes assinados atual e anterior do agente estão publicados no [anfitrião de distribuição OTA](https://android-updates.ottplay.here.now/). Os bytes da carga e do envelope foram descarregados e comparados com sucesso por HTTPS direto.
- O tablet de referência passou de `0.1.1-beta.21` para `0.1.2-beta.4` pelo canal OTA independente. A versão do agente em execução e o SHA-256 instalado correspondiam à versão escolhida; o runtime mudou e o ID de arranque do sistema manteve-se. OTT continuava sem processo de aplicação ativo e não foi enviada qualquer operação de lançamento da aplicação ou escolha HOME.
- O estado nativo indicava o hash do auxiliar tátil do sistema, nenhum pacote pendente e nenhuma falha de recuperação. Os metadados APK estavam indisponíveis; a observação não estabelece versão nem hash do APK instalado.
- A transação coordenada APK/auxiliar tem validação no anfitrião; instalação física, recuperação de descida de versão, imagem e áudio ainda não foram aceites no tablet de referência.

Em cada distribuição posterior, conserve origem/somas da versão, hash do envelope preparado, ID do pedido, estado nativo antes/depois e resultado separado da aceitação física. Um descarregamento bem-sucedido ou recibo de comando não substitui essas observações.

</div>
