# Especificação: pipeline de normalização e construção de hub biológico

## 1. Objetivo
Mapear entidades biológicas para um "Espaço Comum" (Hub), onde o símbolo canônico do HGNC serve como âncora para agrupar todas as variações aceitáveis de Gene, RNAm e Proteína.

## 2. Lógica de Classificação de Termos
O sistema deve identificar a natureza do termo original do benchmark baseando-se no campo onde o "match" ocorreu:
* **Gene (Símbolo Oficial):** Match no campo `symbol` do HGNC.
* **Gene (Nome Completo):** Match no campo `name` do HGNC.
* **Gene (Alias/Sinônimo):** Match nos campos `alias_symbol` ou `prev_symbol`.
* **Proteína (Nome Recomendado):** Match no campo `recommendedName` do UniProt.
* **Proteína (Alias/Sinônimo):** Match no campo `alternativeName` do UniProt.
* **RNAm (Acesso):** Match em padrões RefSeq (`NM_`) ou Ensembl (`ENST`).
* **Ambíguo:** Caso o termo conste em mais de uma categoria (ex: Alias de Gene e Nome de Proteína), o sistema deve listar ambas na classificação.

## 3. Estrutura do Hub (3 Camadas)
Cada gene canônico deve expandir-se para:
1. **Camada Gene:** Símbolos e sinônimos.
2. **Camada RNAm:** Acessos de transcritos (RefSeq/Ensembl).
3. **Camada Proteína:** Nomes e IDs de acesso (P04637, etc).

## 4. Requisitos Não Funcionais (NFRs)
* Prioridade de Aquisição de Dados Offline: O sistema deve evitar o uso de APIs quando possível. É altamente preferencial baixar arquivos dos bancos de dados que comporten-se num tamanho adequado para armazenamento e operá-los em memória.
* Controle Fidedigno de Fontes: Criação de um registro rastreável com os caminhos (paths) até os repositórios offline de dados ou informações/URLs configurados das APIs empregadas.
* Escalabilidade e Performance: Em casos ondem a adoção da cópia offline baseie-se inviável, o sistema passa a atuar em lotes otimizados utilizando concorrência e programação multitarefas (asyncio/threads) nos passos voltados à internet, mitigando assim os gargalos associados à conectividade web.
* Resiliência a Falhas: Erros intrínsecos de rede, HTTP 500 ou Timeouts de API isolam a falha e avançam a pesquisa passivamente ao passo inferir do funil sem encerrar a execução para as demais strings.

## 5. Processamento em Lote (Gabarito)
* Como avaliador, quero processar arquivos `.txt` contendo listas de entidades biológicas geradas por modelos.
* O sistema deve processar esses arquivos em lote e compilar um "Dicionário de Gabarito" final (formato JSON), mapeando a string original diretamente para seu símbolo canônico e a fonte da normalização, permitindo consultas ultra-rápidas (O(1)) no momento da avaliação do benchmark.
