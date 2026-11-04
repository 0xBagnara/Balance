# Balance

Consulta saldos públicos de endereços Bitcoin mainnet usando uma API compatível com Esplora. O programa utiliza apenas a biblioteca padrão do Python e trabalha com endereços públicos.

## Preparação

Requer Python 3.10 ou superior e Linux, com suporte a `fcntl.flock` e `os.O_DIRECTORY`. Não há dependências externas de Python. A validação registrada nesta versão foi feita com Python 3.14 em Linux.

As listas reais e os resultados ficam fora do Git. Em uma instalação nova, prepare sua lista:

```bash
mkdir -p list
cp examples/lista-modelo.txt list/minha-lista.txt
```

Edite `list/minha-lista.txt`, substituindo os marcadores comentados por endereços públicos Bitcoin mainnet, um por linha e sem `#` no início. O modelo contém apenas comentários e não executa consultas por si só.

```bash
python3 consultar_btc.py --arquivo minha-lista.txt
```

## Uso atual

Na pasta `Balance`:

```bash
python3 consultar_btc.py --help
python3 consultar_btc.py --arquivo puzzles_pendentes.txt
python3 consultar_btc.py --arquivo puzzles_resolvidos.txt --intervalo 3
```

Os comandos de consulta acessam a rede e gravam resultados. Sem `--arquivo`, o programa lê todos os TXT e JSON de `list/`, incluindo subpastas. Endereços repetidos são consultados uma vez por execução e mantêm suas origens.

Os nomes `puzzles_pendentes.txt` e `puzzles_resolvidos.txt` acima são exemplos de listas locais; esses arquivos não são distribuídos no repositório.

Os valores apresentados são confirmados; movimentações da mempool não entram nos totais. Uma lista chamada `puzzles_resolvidos` ou `puzzles_pendentes` representa a classificação de entrada, sem verificar automaticamente se o puzzle foi resolvido.

## Arquivos

| Caminho | Função |
| --- | --- |
| `consultar_btc.py` | Entrada de linha de comando e implementação atual |
| `list/` | Listas de endereços em TXT ou JSON |
| `examples/lista-modelo.txt` | Modelo com marcadores fictícios para montar uma lista local |
| `resultados/todos.csv` | Resultados da consulta geral |
| `resultados/com_saldo.txt` | Endereços com saldo confirmado positivo |
| `resultados/erros.txt` | Falhas de consulta |
| `resultados/avisos.txt` | Avisos de leitura e reconhecimento das listas |
| `resultados/estado.json` | Checkpoint com identificação, situação e registros confirmados pela gravação |
| `resultados/historico/` | Rodadas gerais anteriores, com manifesto SHA-256 |
| `resultados/por_arquivo/` | Resultados das consultas com `--arquivo` |
| `docs/revisao-2026-11-04.md` | Diagnóstico e plano de evolução |

## Retomada e atualização

Uma execução interrompida ou com pendências reutiliza consultas bem-sucedidas quando a assinatura das listas e a API coincidem com as registradas no estado. O mesmo comando retoma o trabalho nessas condições. Erros de consulta são tentados novamente; avisos das listas ficam separados em `avisos.txt` e não criam pendências de consulta. Se nenhuma entrada for reconhecida, o programa termina com erro sem substituir relatórios anteriores.

Uma execução após conclusão, uma mudança na assinatura das listas/API ou o uso de `--atualizar` inicia uma nova rodada. Antes de substituir os arquivos atuais, o programa copia os anteriores para `historico/` dentro do respectivo destino e confere seus hashes SHA-256. Se o arquivamento falhar, a nova rodada não é iniciada.

O checkpoint `estado.json` é gravado atomicamente após cada resultado, com sincronização do arquivo e da pasta. O CSV é uma exportação regenerável: se uma falha ocorrer entre a gravação do checkpoint e do relatório, a retomada usa o checkpoint. Os relatórios TXT são atualizados no encerramento normal ou por Ctrl-C. Arquivos de estado inválidos e históricos CSV legados inválidos interrompem a execução para preservar os dados. Isso não constitui validação de resistência a desligamento físico abrupto.

Um bloqueio de processo impede duas execuções desta versão de gravarem simultaneamente no projeto. O bloqueio é liberado pelo sistema ao encerrar o processo; não é necessário apagar o arquivo `.balance.lock`.

Consultas individuais agora usam a extensão no destino, como `resultados/por_arquivo/enderecos.txt/`, evitando colisão com `enderecos.json`. Na primeira execução, resultados do destino antigo sem extensão são copiados quando sua origem corresponde ao arquivo selecionado. Os originais são mantidos. Estados antigos não registravam a API: a compatibilidade de retomada assume a fonte padrão original, Blockstream; outra API inicia uma nova rodada arquivada.

## Verificação local

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Os testes usam pastas temporárias e consultas simuladas, sem acessar a rede ou os resultados reais.

## Próximas etapas

A etapa de preservação e retomada foi implementada; veja [o registro da etapa](docs/preservacao-2026-11-04.md). Permanecem propostas a extração de módulos, consulta estruturada ao histórico por endereço, atualização seletiva e monitoramento. O [diagnóstico inicial](docs/revisao-2026-11-04.md) registra a situação anterior às correções.
