# NEBB: Normalization Engine for Biological Benchmarking

NEBB é um pipeline robusto de normalização de entidades biológicas, desenvolvido para servir como gabarito de avaliação em benchmarks de modelos de linguagem (LLMs) aplicados à biologia.

## Objetivo

Mapear termos biológicos ruidosos e heterogêneos (como nomes coloquiais, símbolos de genes, aliases e IDs de proteínas) para seus **símbolos canônicos oficiais**, utilizando o HGNC como base de verdade primária, e construir um **Hub Biológico Universal** de 3 camadas para cada entidade resolvida.

## Arquitetura

O pipeline implementa uma cascata de resolução com prioridade **Offline-First**:

```
Entrada (string bruta)
    │
    ▼
1. Busca offline HGNC (symbol → alias → prev_symbol)
    │
    ▼
2. Busca offline UniProt (gene primário)
    │
    ▼
3. Heurística (Regex)
    │
    ▼
4. APIs externas paralelas (HGNC API → UniProt API → MyGene.info)
    │
    ▼
Saída: SemanticEntity com Hub de 3 Camadas + Data Provenance
```

## Hub Biológico de 3 Camadas

Para cada entidade resolvida, o sistema expande o símbolo canônico em:

| Camada    | Conteúdo                                                  | Fonte          |
|-----------|-----------------------------------------------------------|----------------|
| `gene`    | Símbolos oficiais, aliases e nomes anteriores             | HGNC           |
| `rnam`    | IDs de transcritos (RefSeq `NM_...`, Ensembl `ENSG...`)   | HGNC / UniProt |
| `proteina`| IDs de acesso UniProt (ex: `P04637`)                      | HGNC / UniProt |

Cada termo dentro do Hub carrega sua **lista de proveniência** (ex: `["HGNC", "UniProt API"]`), garantindo rastreabilidade completa.

## Estrutura de Saída (`gold_standard.json`)

```json
"BCL2": {
    "canonical_gene": "BCL2",
    "identidade_biologica": {},
    "termos_originais": ["BCL2", "Bcl-2", "Bcl2", "bcl-2"],
    "gene": {
        "BCL2": ["HGNC"],
        "Bcl-2": ["HGNC"]
    },
    "rnam": {
        "NM_000633": ["HGNC"],
        "ENSG00000171791": ["HGNC"]
    },
    "proteina": {
        "P10415": ["HGNC"]
    },
    "classificacao_composta": ["Gene (symbol)"]
}
```

## Bases de Dados

| Fonte     | Tipo    | Estratégia       |
|-----------|---------|------------------|
| HGNC      | JSON    | Offline (memória) |
| UniProt   | TSV     | Offline (memória) |
| HGNC API  | REST    | Fallback online  |
| UniProt API | REST  | Fallback online  |
| MyGene.info | REST  | Fallback online  |

## Limitação conhecida: miRBase indisponível

O `data/aliases.txt` da miRBase contém apenas o fallback de emergência gerado pelo
`download_databases.py` quando o download falha; o servidor da miRBase retorna HTTP 500 ou 404
em todos os espelhos testados (`/download/aliases.txt`, `/ftp/CURRENT/`, `/ftp/22.1/`).

**Impacto:** o HGNC cobre 2.005 símbolos de gene de microRNA (`MIR21`, `MIR137HG`, ...), então os
*genes* de miRNA estão cobertos. O que se perde são os nomes de **miRNA maduro**
(`hsa-miR-21-3p`, `-5p`) e os aliases históricos da miRBase. A normalização de miRNA se apoia,
portanto, em HGNC mais heurística: o `judge.py` reconhece variantes de grafia
(`mir-21` ≡ `mir21` ≡ `microRNA-21`), mas sem base de referência dedicada.

## Execução

```bash
# Ativar ambiente
. venv/bin/activate

# Processar lote completo
python batch_processor.py

# Testes e tipagem
pytest && mypy pipeline.py cache.py batch_processor.py
```

## Estrutura do Projeto

```
nebb/
├── pipeline.py           # Orquestrador e lógica de normalização
├── batch_processor.py    # Processamento em lote → gold_standard.json
├── cache.py              # Persistência thread-safe do cache
├── config_sources.json   # Paths dos bancos e endpoints de API
├── download_databases.py # ETL das bases offline
├── gold_standard.json    # Dicionário final de normalização
├── data/
│   ├── hgnc_complete.json
│   └── uniprot_mapping.tsv
├── tests/
│   ├── test_pipeline.py
│   └── test_live_apis.py
└── docs/
    ├── spec.md                    # Especificação do pipeline
    └── gold_standard_creation.md  # Como o gold_standard.json foi gerado
```

## Tecnologias

- **Python 3.10+** · `dataclasses` · `typing` · `concurrent.futures`
- **Testes**: `pytest` · `mypy`
- **Bases**: HGNC · UniProt · MyGene.info
