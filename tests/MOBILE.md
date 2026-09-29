# Verificacao do dashboard publico

Execute na raiz, com Playwright e Chromium instalados no ambiente de teste:

```powershell
.\.venv\Scripts\python.exe tests/mobile_check.py
```

Larguras padrao: 360, 390, 768 e 1440 px (altura 900 px). E possivel passar
larguras individuais: `python tests/mobile_check.py 360 390`.

O teste usa o dashboard real com 32 associacoes e geometrias simuladas,
SQLite temporario, destinos locais para Site/Instagram e respostas simuladas
para os formularios Google. Confere os URLs originais e a abertura em nova aba;
nao verifica a disponibilidade dos servicos externos.

Em ambiente sem acesso ao CDN do Plotly, baixe `https://cdn.plot.ly/un/world_110m.json`
e informe seu caminho em `MOBILE_TOPOJSON`, ou salve em
`test-results/world_110m.json`. Fontes externas sao bloqueadas no teste.

Verifica painel, toque/teclado, preservacao de filtros, formularios, mapa
renderizado, 13 graficos preenchidos em uma coluna no mobile (duas no desktop),
ausencia de overflow da pagina, rolagem horizontal e paginacao das tabelas,
links rastreados e acesso a pagina privada sem incrementar visitas.

Capturas e resultados por largura ficam em `test-results/mobile/`, ignorado
pelo Git. As imagens `*-estatisticas.png` e `*-graficos.png` sao da aba publica;
`*-privada.png` documenta apenas a verificacao de regressao da pagina privada.
