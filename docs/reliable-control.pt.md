<div lang="pt" dir="ltr">

# Controlo fiável e transferência de reprodução

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Índice da documentação](wiki/Home.md)

Estas funções requerem o controlador e a CLI de uma revisão que inclua este guia. A comparação exata de definições e a transferência também exigem um leitor atualizado. Os arquivos de versões antigas e a revisão histórica da CLI no guia de instalação não recebem estas funções automaticamente. Instale **todos os ficheiros `cli/*.py` em conjunto**, incluindo `preset_session.py`; preserve a configuração privada.

![Fluxo de controlo, ponto de controlo e transferência](assets/reliable-control.svg)

## Ativar resultados persistentes com limites

Adicione este bloco opcional à configuração privada do servidor, valide-a e reinicie o servidor. Escolha um caminho absoluto num sistema de ficheiros local persistente onde a conta do serviço possa escrever. Em Unix, o diretório deve ser privado (0700); os ficheiros existentes do diário e de bloqueio devem ser ficheiros regulares privados (0600). Em Windows, restrinja a ACL do diretório à conta do serviço. Um único processo é proprietário de cada diário.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

O servidor cria diretórios em falta com modo 0700. Os limites são 1–4096 registos, 60–86400 segundos e 4 MiB no disco. A retenção deve abranger `command_ttl_seconds`. Um diário cheio rejeita novas operações com chave em vez de remover registos ainda válidos. Ficheiros corrompidos, um diretório Unix inseguro, outro processo de escrita ou uma falha na escrita persistente causam uma recusa segura. O caminho de comandos existente em memória continua a ser o predefinido.

A unidade systemd incluída cria `/var/lib/ottplay-control` com modo 0700. Para Compose, prepare um diretório no anfitrião pertencente ao UID 65532, defina `CONTROL_SERVER_STATE` com o caminho absoluto e combine `deploy/compose.yml` com `deploy/compose.journal.yml`. Este último acrescenta apenas a montagem de estado com escrita; o sistema de ficheiros raiz permanece só de leitura. Para Helm, prepare separadamente uma reivindicação de volume persistente e defina `journalExistingClaim=CLAIM_NAME`. A montagem fica em `/var/lib/ottplay-control`; utilize o subdiretório privado indicado acima. O chart mantém uma réplica, `Recreate` e `fsGroupChangePolicy: OnRootMismatch`, para que uma nova montagem não alargue recursivamente as permissões dos diretórios privados já criados. Confirme que o controlador de armazenamento escolhido suporta esta semântica de propriedade. O chart não cria nem elimina a reivindicação, e ativar uma montagem não ativa o diário: isso continua controlado pela configuração privada do servidor.

Para `POST /api/requests`, adicione `idempotency_key` junto de `action` e `params`. Os POST de comandos antigos aceitam antes `Idempotency-Key`. As chaves contêm 16–80 letras ASCII, algarismos, sublinhados ou hífenes; gere uma chave aleatória nova para cada intenção. O mesmo proprietário/chave com o mesmo dispositivo, ação e conteúdo JSON normalizado devolve o ID original sem voltar a colocar na fila. Alterar o conteúdo devolve 409. O âmbito das chaves é o ID do operador (ou o administrador), não o dispositivo. Depois da retenção expirar, a chave pode ser aceite como nova intenção: os clientes não devem repetir uma intenção antiga fora desse intervalo.

`GET /api/operations?device_id=DEVICE` informa se o diário está ativo, os seus limites e o TTL dos comandos. Adicione `&key=KEY` para ler uma operação:

- `pending`: aceite; ainda não existe resposta conservada.
- `responded`: chegou uma resposta validada do leitor; `result_status` é `ok`, `rejected` ou `unsupported`. O corpo do resultado original continua a expirar normalmente.
- `acknowledged`: um consumidor antigo confirmou o comando. Isto não prova reprodução visível nem que uma alteração de definições foi guardada.
- `unknown`: o prazo terminou ou o servidor reiniciou antes de conservar uma resposta. O servidor **nunca repete** este comando após reiniciar.

Um POST repetido com chave devolve `status: retained`, o ID original e o estado. Os novos POST normais mantêm `status: queued`. Os registos contêm apenas proprietário, dispositivo, ação, ID aleatórios, resumo criptográfico do conteúdo, estado e prazos. Não contêm conteúdos de comandos, URL de fornecedores, tokens, capturas de ecrã ou corpos de respostas. Os comandos pendentes não são persistidos. Isto é deduplicação e acompanhamento de resultados com limites, não execução exatamente uma vez, uma fila de trabalho persistente ou alta disponibilidade entre vários servidores.

## Planear, aplicar, retomar e verificar Local/Home

O comportamento existente de `ott PLAYER load PRESET` é preservado. Para o novo fluxo:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` funciona offline: valida a predefinição local e lista as posições de definições e o perfil ativo pretendido sem mostrar os seus conteúdos. Não inspeciona o leitor. `verify` realiza comparações exatas só de leitura e verifica o perfil M3U selecionado. Devolve um código diferente de zero se houver diferenças ou a comparação não for suportada.

`apply` exige o diário do servidor. Cria um ponto de controlo privado e compara cada fornecedor configurado antes da primeira alteração. Cada gravação compara os bytes guardados com os valores esperados através de `settings_compare`; uma correspondência dispensa a escrita. A verificação final abrange Plex, todas as posições M3U, as posições Stalker opcionais e o perfil M3U selecionado. As comparações devolvem um booleano, nunca segredos guardados.

O ponto de controlo predefinido fica em `~/.config/ottplay-control/checkpoints/`. Utilize `--checkpoint /absolute/private/path/session.json` depois do modo para escolher outro ficheiro. Os pontos de controlo contêm dispositivo, nome da predefinição, hash da revisão do servidor/predefinição, nomes dos passos concluídos e uma chave aleatória de operação pendente com prazo. Não contêm valores de definições. Os ficheiros são substituídos atomicamente após fsync e bloqueados contra escritas simultâneas da CLI. Mantenha disponível a predefinição local correspondente. Em Unix, o diretório deve ser privado (0700), com ficheiros regulares em 0600. Em Windows, restrinja a ACL herdada do diretório ao utilizador que executa a CLI; os bits de modo Unix não impõem permissões de acesso Windows.

`resume` valida o mesmo servidor, dispositivo e revisão da predefinição, e depois lê o resultado pendente. Uma resposta conservada permite uma nova verificação imediata. Um resultado incerto bloqueia o envio de alterações até passarem o prazo do comando anterior, o tempo limite do pedido e uma pequena margem. Execute `resume` novamente após esse intervalo. Verifica de novo as definições guardadas mesmo para passos concluídos, para não confundir alterações do utilizador com escritas anteriores bem-sucedidas. Nunca repete cegamente um comando relativo. A seleção absoluta de fornecedor/perfil pode repetir-se após a proteção temporal.

Um novo `apply` nunca substitui um ponto de controlo existente. Reutilize-o com `resume`, ou escolha um novo ficheiro para uma predefinição alterada. Dispositivos diferentes têm pontos de controlo separados. Não existe transação nem reversão que abranja todos os leitores; as escritas anteriores bem-sucedidas permanecem quando um passo posterior falha. `verified` confirma os valores guardados no leitor no momento da comparação, não um carregamento de rede bem-sucedido nem a saída física de vídeo/áudio.

## Dar às integrações apenas a autoridade necessária

Utilize [operadores de comandos comuns](security-design.md#scoped-command-operators) para HA, uma ponte RF ou um agente, em vez de distribuir `admin_token`. Autorize ID exatos de dispositivos e operações como `input.channel_up` e `input.channel_down`. As operações de definições requerem permissões explícitas `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` e `provider_settings` para este fluxo de predefinições. A transferência exige `playback_handoff_export` na origem e `playback_handoff_import` no destino. Uma entrada de operador combina os dispositivos e ações listados; use identidades separadas quando a mesma operação não deva aplicar-se a todos os dispositivos listados. Os tokens e o consentimento para diagnóstico continuam a ser autoridades distintas.

Os operadores não podem consultar filas dos leitores, enviar respostas dos leitores, listar todos os dispositivos, ler saídas de depuração administrativa nem resultados de outros operadores. Restringir o âmbito de uma ação também impede ler a sua operação conservada. Remover um operador e reiniciar revoga a sua credencial. Não reutilize um ID de operador para outro interveniente enquanto os registos antigos permanecerem no diário.

## Continuar noutro leitor

```sh
ott handoff living-room bedroom
```

A origem exporta uma referência de direto/arquivo versão 1: fornecedor, identidade opaca da origem, impressão digital opaca do item `channel-v1:<8 hex>.<8 hex>` e, para arquivo, a hora atual do programa. Os ID originais dos fornecedores e os URL dos fluxos ficam locais. O destino deve ter **a mesma origem/conta configurada**, resolver um único item correspondente no seu catálogo e obter um URL novo com as suas próprias credenciais locais. O controlador não copia um URL reproduzível nem altera as credenciais do destino. A origem continua a reproduzir. Um resultado bem-sucedido da CLI significa que o destino enviou o pedido, não que o ecrã ou os altifalantes físicos tenham sido verificados.

A versão 1 suporta catálogos direto/arquivo `m3u`, `xtream` e `stalker` quando o leitor consegue fornecer as identidades necessárias. Itens desconhecidos ou ambíguos, origens incompatíveis e rejeições por políticas locais falham sem alternativa baseada no nome. VOD/séries, correspondência entre contas distintas, pausa da origem e sincronização contínua de histórico/favoritos não fazem parte deste primeiro contrato.

## Exercícios de validação e recuperação

Execute `go test -race ./...`, `go vet ./...` e `python3 -m unittest discover -s tests`. Os testes cobrem respostas perdidas após uma escrita bem-sucedida, comparação exata antes da retoma, desvio de configuração, prazos de operações desconhecidas, dois processos de escrita, diários corrompidos, leituras entre operadores e conteúdos de transferência malformados. Num leitor de pré-produção, interrompa também a CLI após guardar uma definição, reinicie o controlador, retome e verifique; teste uma transferência entre origens correspondentes e não correspondentes. Confirme vídeo/áudio separadamente.

Para validação local entre repositórios, com a cópia de trabalho FOSS atual e as dependências de desenvolvimento instaladas, execute:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

O teste de fumo compila este servidor Go e todo o projeto TypeScript FOSS, e executa o transporte real, os processadores de comandos, a identidade da origem e o codec de listas distribuído contra a CLI Python. Verifica definições exatas para os três fornecedores suportados, diferenças, transferência em direto com ID locais de linhas diferentes, deslocamentos de procura em arquivo, falhas de resolução de URL e validação rigorosa do formato transmitido. Apenas armazenamento, política do fornecedor e interfaces do anfitrião de reprodução são componentes simulados de teste; não verifica navegador, saída multimédia física nem acesso real a contas. Liga-se ao loopback, usa ficheiros temporários privados, não contacta URL de fluxos e remove os ficheiros/servidor ao terminar.

</div>
