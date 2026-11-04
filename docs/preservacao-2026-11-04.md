# Preservação e retomada — etapa implementada

## Backup anterior às alterações

Cópia integral em diretório local externo ao projeto, identificado como `Balance-20261104-131843-480793`, com 21 arquivos e 113.939 bytes. O arquivo `manifesto-sha256.json` lista os hashes relativos à raiz do backup. Foram comparados os hashes da origem antes da cópia, do backup e da origem depois da cópia. O backup e seu manifesto permanecem locais.

No encerramento da validação, os hashes do backup foram novamente conferidos. Os 17 arquivos originais de listas, resultados e backup `.bak` permaneciam idênticos ao manifesto. Nenhum saldo real foi consultado.

## Alterações

- Arquivamento das rodadas anteriores em `historico/<data-id>/`, com manifesto SHA-256, antes de iniciar uma nova rodada.
- Checkpoint versão 2 contendo os registros e a fonte API, gravado atomicamente com `fsync` e renomeação. CSVs passam a ser exportações regeneráveis.
- Retomada baseada no checkpoint; resultados já gravados com sucesso são reutilizados, falhas e endereços ainda ausentes são consultados.
- Exclusão mútua com `flock`, abrangendo todas as consultas desta versão no mesmo projeto.
- Rejeição de estado inválido e de CSV legado inválido, sem reinicialização silenciosa.
- Avisos das entradas em `avisos.txt`, separados de erros de consulta e da decisão de retomar pendências.
- Destinos individuais mantêm a extensão. Dados antigos compatíveis são copiados, preservando os originais.
- Correção da conversão negativa de BTC para satoshis e preservação do horário inicial da rodada.

## Evidências

18 testes automatizados cobrem interrupção e retomada, repetição de falhas, avisos, troca de listas/API, atualização explícita, nova rodada após conclusão, arquivo histórico e hashes, checkpoints e CSVs inválidos, entrada vazia, destinos distintos, importação legada, bloqueio real entre processos, falha de arquivamento e falhas durante gravação de checkpoint/CSV.

Também foram executados três cenários em cópias temporárias dos dados reais:

| Cenário | Consultas simuladas necessárias | Registros finais |
| --- | ---: | ---: |
| Retomada geral | 143 | 213 |
| Importação e retomada de `enderecos.txt` | 210 | 213 |
| Nova rodada de `puzzles_resolvidos.txt` após conclusão | 83 | 83 |

As consultas reutilizadas conservaram seus valores locais; somente as consultas simuladas novas receberam valores fictícios nas cópias temporárias.

## Limites e trabalho posterior

Não houve teste de desligamento físico abrupto nem de disponibilidade real da API. O checkpoint é a fonte de retomada; CSV e TXT são exportações separadas e podem ficar atrasados após uma falha. Na retomada, CSV é regenerado e os TXT são atualizados no encerramento. A escrita regrava o checkpoint e o CSV completos a cada resultado, adequada ao tamanho atual, sem avaliação de desempenho para listas muito maiores.

Os arquivos históricos são preservados, sem limpeza automática. A consulta estruturada ao histórico, o armazenamento em SQLite e a reorganização em módulos continuam fora desta etapa.
