# Processo de Criação do `gold_standard.json`

Este documento detalha o passo a passo técnico utilizado para gerar o arquivo de gabarito enriquecido, utilizado para validar a normalização de genes e proteínas no benchmark.

## 1. Arquitetura e Definição (Fase de Planejamento)
O objetivo principal foi criar um **Hub Biológico Universal** ancorado nos símbolos oficiais do HGNC. O planejamento seguiu as diretrizes:
- **Offline-First**: Priorizar bases locais para velocidade (O(1)) e consistência.
- **Cascata de Confiança**: Tentar resolver termos primeiro no HGNC, depois UniProt, e por fim buscar em APIs externas.

## 2. Ingestão e Preparação de Dados (ETL)
Utilizando o script `download_databases.py`, foram preparadas as bases offline:
- **HGNC (JSON)**: Contendo o mapeamento completo de símbolos oficiais, nomes, aliases e acessos externos (RefSeq, Ensembl).
- **UniProt (TSV)**: Mapeamento de IDs de acesso de proteínas para os símbolos de genes primários e informações de organismo.
- **Configuração**: Os caminhos foram registrados em `config_sources.json` para garantir rastreabilidade.

## 3. Desenvolvimento do Pipeline de Normalização
O core da lógica foi implementado no arquivo `pipeline.py`:
- **Orquestrador (`PipelineOrchestrator`)**: Gerencia o carregamento das bases em memória e a lógica de decisão.
- **Funções Puras**: Implementam busca exata por campos específicos (Symbol, Alias, accession, etc.).
- **Heurísticas**: Aplicação de Regex para extrair símbolos de strings ruidosas.
- **Fallback de API**: Implementação de chamadas multitarefa (concorrência) para as APIs do HGNC, UniProt e MyGene.info como última linha de defesa.

## 4. Construção do Hub de 3 Camadas
Para cada entidade resolvida, o sistema expandiu o símbolo canônico para preencher três níveis de informação:
1.  **Camada Gene**: Símbolos, sinônimos (aliases) e símbolos anteriores (`prev_symbol`).
2.  **Camada RNAm**: IDs de acesso de transcritos (RefSeq `NM_...` e Ensembl `ENSG...`).
3.  **Camada Proteína**: IDs de acesso UniProt (ex: `P04637`) e nomes recomendados.

## 5. Processamento em Lote e Consolidação
O script `batch_processor.py` foi o responsável final por:
- Ler os arquivos de saída gerados pelos modelos.
- Limpar e normalizar os termos originais através da cascata.
- **Detecção de Ambiguidade**: Identificar se um termo pertencia a mais de uma categoria (ex: um símbolo que também é alias de outro gene), gerando a `classificacao_composta`.
- **Agrupamento**: Organizar o dicionário final por "Gene Canônico" para facilitar consultas instantâneas.

## 6. Resultado Final
O arquivo `nebb/gold_standard.json` é o subproduto desse processamento, contendo milhares de entradas estruturadas que permitem ao avaliador comparar uma resposta de um modelo com todas as formas aceitáveis (aliases, proteínas, etc.) de um gene alvo.
