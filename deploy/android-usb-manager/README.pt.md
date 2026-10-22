# Imagem de transporte USB Android

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Este diretório contém os modelos de implantação e o [`Dockerfile`](Dockerfile) do contentor de transporte do gestor partilhado. O gestor Python executa-se no anfitrião USB. A imagem fornece ADB, Python e ferramentas do adaptador de manutenção atómica; não contém um segundo registo de dispositivos nem um serviço de recuperação. Consulte o [guia do operador](../../docs/android-usb-manager.md) para provisionamento, comandos, migração, limites de recuperação e reversão.

Sem uma cópia Git, descarregue `ottplay-control-server-deployment.tar.gz` e `release-manifest.json` da mesma [versão](https://github.com/open-ott-play/ottplay-control-server/releases). Verifique o resumo criptográfico do arquivo e extraia-o para um diretório novo conforme [Obter o pacote de implantação](../../docs/android-usb-manager.md#get-the-deployment-package). O pacote inclui esta receita e os seis scripts locais/remotos em `tools/`; mantenha a estrutura de diretórios. Pacotes antigos, incluindo `v0.1.5-beta.6`, não têm esses scripts e exigem o arquivo de código-fonte correspondente. O pacote não inclui uma imagem de transporte USB construída. O artefacto OCI do servidor de controlo serve outra finalidade.

## Proveniência do código e dos artefactos

A base é `python:3.12.14-slim-trixie`, fixada em `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. A base, a instrução APT e a instalação de `websocket-client==1.9.0` fixada por hash foram revistas no [PR 128, revisão b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). O texto das instruções foi mantido para reutilizar uma cache de compilação disponível. O pacote WebSocket opcional suporta clientes genéricos de manutenção existentes; os adaptadores CDP migrados também podem usar apenas a biblioteca padrão do Python.

Esta receita remove o pacote `pip` instalado após instalar o wheel fixado, exclui o código do gestor e de recuperação do PR 128, inicia ADB em primeiro plano e desativa verificações de saúde herdadas. Remover o instalador em execução reduz a superfície de ataque disponível; não prova uma análise sem vulnerabilidades.

O artefacto revisto do PR 128 era `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Contém um ponto de entrada diferente e uma verificação de saúde por heartbeat incompatível. É um artefacto de referência, não uma compilação deste Dockerfile. Não atribua a esta receita o seu ID de imagem, proveniência do código ou resultados de análise.

Os repositórios APT não estão fixados a instantâneos. Uma futura recompilação limpa pode portanto selecionar pacotes de sistema mais recentes, mesmo com o mesmo resumo da base e hash do wheel Python. Ao qualificar uma compilação, registe a revisão exata, construtor/plataforma, ID da imagem resultante, versões dos pacotes e análise. A configuração de produção deve usar esse ID imutável qualificado ou o resumo criptográfico.

## Compilar sem perturbar um anfitrião USB ocupado

Prefira compilar e analisar num construtor separado. O anfitrião de destino só precisa da imagem qualificada, não de descarregar pacotes ou ferramentas de compilação.

Ao reutilizar um construtor local preparado, confirme primeiro que a imagem base exata está disponível e que as camadas de instalação APT e wheel estão em cache. Carregar só a imagem final do PR 128 não demonstra nenhuma das condições. A partir da raiz do repositório ou do diretório de implantação extraído, uma tentativa deliberadamente offline é:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` aplica-se aos passos de compilação; não garante em geral que o construtor nunca contacte um registo ao resolver a base. Confirme a resolução local da base antes de usar o procedimento num anfitrião limitado. Se faltar uma camada APT ou wheel, pare a tentativa em vez de permitir silenciosamente uma recompilação pela rede no anfitrião USB.

BuildKit inclui o modo de rede do passo na chave da cache. Assim, uma cache criada com o modo de rede padrão pode falhar na tentativa offline acima. Reutilize-a só depois de verificar a base exata e a cadeia de instruções; pare se algum passo de instalação de pacotes executar em vez de indicar `CACHED`. A remoção final de `pip` usa explicitamente `RUN --network=none`; não precisa de rede mesmo quando passos anteriores em cache usam o modo original.

Antes de alterar a configuração de produção, confira as versões de Python e ADB, analise a imagem exata resultante, verifique transferência de ficheiros binários e preparação de APK e depois realize as verificações de aceitação do guia: identidade do dispositivo, autorização de chaves, suspensão e arranque. É necessário testar ADB 34 com Android antigo; o número da versão não prova compatibilidade com KitKat ou Fire. Guarde a imagem atual e a cópia privada da implantação até já não precisar de reversão.

## Orçamento de execução

O gestor do anfitrião, não este Dockerfile, aplica 256 MiB de memória, 256 MiB de memória mais swap e CPU shares 128, sem quota rígida de CPU ou PID. Impõe `--no-healthcheck`, `--network none`, sistema de ficheiros raiz só de leitura, capacidades removidas e mapeamentos exatos de nós USB. `/tmp` é um tmpfs limitado que consome o mesmo orçamento de memória. O adaptador de preparação escreve blocos de 1 MiB e limita cada carga a 128 MiB; as operações partilham o bloqueio do gestor.

Docker documenta a diferença entre os [controlos de memória, swap e CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) e a [opção `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
