# Estatisticas privadas

Abra `/estatisticas` e informe a senha configurada em `ESTATISTICAS_SENHA`.
Nao existe senha padrao. Sem essa variavel, pagina, login e CSV retornam 503.
Use uma senha longa e aleatoria. Nao coloque senhas em arquivos versionados.

## Configuracao local (PowerShell)

```powershell
$senhaEstatisticas = Read-Host 'Senha das estatisticas' -AsSecureString
$env:ESTATISTICAS_SENHA = [System.Net.NetworkCredential]::new('', $senhaEstatisticas).Password
$env:ESTATISTICAS_DB_PATH = Join-Path $PWD 'data\estatisticas.sqlite3'
.\.venv\Scripts\python.exe dashboard_associacoes.py
```

Dashboard: http://127.0.0.1:8050/ . Consulta: http://127.0.0.1:8050/estatisticas .
`PORT` permite selecionar outra porta. A sessao dura oito horas; Sair remove
o cookie. Alterar a senha invalida todas as sessoes anteriores.

## Dados e persistencia

- `ESTATISTICAS_DB_PATH`: caminho do SQLite. Padrao:
  `data/estatisticas.sqlite3`, relativo ao diretorio do codigo, nao ao terminal.
- Para persistir entre reinicios, use sempre o mesmo arquivo. Em hospedagem com
  disco efemero, configure um volume persistente e aponte a variavel para ele.
  Workers na mesma maquina podem compartilhar o arquivo (WAL e timeout de escrita).
  Instancias em maquinas distintas nao compartilham automaticamente o SQLite.
- O banco guarda somente dia (Brasilia, UTC-3), tipo de evento, chave da
  associacao nos cliques e hash do identificador aleatorio nas visitas.
  Nao guarda IP, nome de visitante, user-agent, referer ou URL de navegacao.
  Cookies sao pseudonimos, nao uma identificacao de pessoas. O identificador
  aleatorio expira em um ano e nao e vinculado aos cliques.
- Uma visita e um GET bem-sucedido da pagina inicial. Recargas contam novamente;
  robos tambem podem contar. Assets, callbacks, HEAD, login, CSV e estatisticas
  nao contam. A contagem comeca quando esta versao passa a registrar eventos.
- Navegadores distintos sao hashes distintos entre as visitas do periodo.
  Cookies apagados/bloqueados, navegacao privada ou outro navegador afetam a estimativa.
- Datas inicial e final sao inclusivas; padrao: ultimos 30 dias. O CSV usa o
  mesmo filtro e inclui resumo, dias sem visitas e cliques por associacao.
- Os links externos continuam em nova aba. A rota recebe somente chave da
  associacao e tipo (`site` ou `instagram`), busca a URL na base carregada e aceita
  apenas HTTP/HTTPS. Parametros adicionais sao rejeitados.
- A chave usa o ID da fonte quando preenchido e unico; na ausencia dele, usa
  nome, UF e municipio. Alterar esses campos no fallback inicia outra serie.
- Linhas com a mesma chave, nome e destinos efetivos iguais compartilham a
  contagem. Destinos sao validados e espacos nas extremidades sao removidos.
  Se o grupo tiver Site/Instagram ou nomes diferentes, cada variante recebe
  um sufixo deterministico; a ordem das linhas nao altera os destinos.
  Grupos sem conflito mantem as chaves anteriores. Eventos antigos de uma chave
  ambigua nao sao redistribuidos: o banco nao guardava qual destino foi usado.
  Adicionar ou remover um conflito pode mudar as chaves desse grupo; corrija os
  IDs da fonte para manter identidades estaveis. Nao ha migracao automatica.
- Falha durante a coleta nao bloqueia o dashboard nem os redirecionamentos.
  Falha de consulta retorna 503, sem apresentar contagens falsas como zero.
  Falha ao inicializar o arquivo interrompe a inicializacao com erro explicito.

## HTTPS em producao com Nginx

O cookie usa o esquema WSGI da requisicao (`request.is_secure`). Quando o Nginx
termina TLS e encaminha HTTP ao app, o esquema original precisa ser informado
por um proxy confiavel. Nao ha configuracao Nginx versionada neste repositorio.

Configure no ambiente do processo Python e reinicie os workers:

```text
ESTATISTICAS_PROXY_HOPS=1
ESTATISTICAS_COOKIE_SECURE=true
```

`ESTATISTICAS_PROXY_HOPS` (padrao `0`) habilita ProxyFix para confiar apenas no
numero indicado de valores de `X-Forwarded-Proto`, contados da direita.
Nao habilita confianca em For, Host, Port ou Prefix. Com `0`, o app nao interpreta
esses cabecalhos; o servidor WSGI ainda pode ter sua propria configuracao de proxy.
`ESTATISTICAS_COOKIE_SECURE=true` exige Secure nos cookies de autenticacao, CSRF
e navegador, inclusive na exclusao, mesmo se o cabecalho de HTTPS estiver ausente.
O padrao `false` permite HTTP local, mas continua usando Secure em HTTPS reconhecido.
Valores diferentes de true/false e contagens invalidas impedem a inicializacao.

No bloco HTTPS do Nginx, encaminhe o esquema definido pelo proprio Nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8050;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

O listener HTTP publico deve redirecionar para HTTPS. O backend deve aceitar
conexoes somente do Nginx (por exemplo, Gunicorn com `--bind 127.0.0.1:8050`
na mesma maquina, ou rede privada com firewall). ProxyFix conta saltos; nao
valida IPs de origem. Nao exponha a porta do backend nem repasse o cabecalho
fornecido pelo cliente. Neste exemplo, Nginx sobrescreve Proto com um unico
valor, portanto use `1`, mesmo se existir outro proxy antes dele.
Outras topologias exigem ajustar a configuracao ao cabecalho realmente enviado.
Logs do proxy/servidor sao independentes do SQLite: configure-os separadamente
para nao reter IPs ou outros dados pessoais.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os testes usam senhas aleatorias, SQLite temporario e uma fonte de associacoes
simulada para exercitar o dashboard real sem depender do Google Sheets.

Teste opcional de navegador (Playwright instalado somente no ambiente de teste):
`python tests/browser_check.py`. Ele testa login, CSV, novas abas, filtros e
layout desktop/mobile; usa outro servidor local para simular os destinos
externos e grava capturas em `test-results/`, ignorado pelo Git.
