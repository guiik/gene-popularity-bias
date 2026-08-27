# Viés de popularidade na recuperação de genes por LLMs

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Benchmark, pipeline de avaliação e dados brutos da dissertação **"Avaliação Centrada em Dados
do Conhecimento Biomolecular em Modelos de Linguagem de Grande Porte"**, do Programa de
Pós-Graduação em Modelagem Computacional do Laboratório Nacional de Computação Científica (LNCC).

A pergunta: quando um gene é raramente citado na literatura, o que acontece com a capacidade de um
LLM de recuperá-lo? E quando o modelo erra, **para onde** ele erra?

---

## O experimento em uma tela

**4.000 tarefas de preenchimento de lacuna** (*cloze tasks*) construídas a partir de *abstracts* do
PubMed: a menção ao gene é substituída por `[MASK]` e o modelo precisa recuperá-la.

Os genes são estratificados em **quatro quartis de popularidade bibliográfica**, pelo número de PMIDs
distintos associados ao GeneID no `gene2pubmed` do NCBI (humano, `tax_id 9606`), com 1.000 casos
por estrato, de Q1 (super populares, tipo *TP53*) a Q4 (cauda longa).

**Quatro modelos** avaliados em *zero-shot* no supercomputador Santos Dumont, com o modo de
raciocínio explícito desligado para isolar a recuperação factual da memória paramétrica.

As respostas passam pelo **NEBB** (*Normalization Engine for Biological Benchmarking*), um pipeline
de três camadas que mapeia sinônimos, nomes anteriores e designações proteicas ao símbolo canônico
do HGNC. Sem ele, responder `p53` a um gabarito `TP53` contaria como erro.

## Resultados

Acurácia **auditada** sobre **3.864 casos**: os 136 itens cujo *abstract* expunha a resposta foram
anulados para todos os modelos. Fonte única: [`results/OFFICIAL_NUMBERS.md`](results/OFFICIAL_NUMBERS.md).

| Modelo | Q1 | Q2 | Q3 | Q4 (cauda longa) | **Global** |
|---|---:|---:|---:|---:|---:|
| **Llama 3.3 70B** (denso) | 61,2% | 46,1% | **30,2%** | 11,8% | **37,3%** |
| Gemma 4 31B (denso) | **62,1%** | 43,4% | 21,6% | **12,6%** | 34,9% |
| Qwen 3.6 35B | 59,0% | 39,6% | 23,5% | 12,2% | 33,6% |
| Gemma 4 26B (MoE) | 53,2% | 32,6% | 17,3% | 10,2% | 28,3% |

**A degradação na cauda longa é transversal:** queda relativa de 78% a 81% de Q1 para Q4, em todas
as arquiteturas. A variante MoE ficou em último em todos os estratos, e o roteamento por
especialistas não mitiga o viés.

**O viés é uma força direcional, não só uma lacuna.** Em **77,6%** dos erros, o gene que o modelo
inventa é mais citado que o gene correto, com razão mediana de **3,7×**, chegando a **16,3×** na cauda
longa. Ao falhar, o modelo desloca ativamente a predição para o centro da distribuição
bibliográfica. O perigo para aplicações biomédicas não é a ausência de resposta: é a produção
fluente do gene errado e mais popular.

**A limitação está no corpus, não na engenharia.** 51,2% dos casos não foram recuperados por
nenhum dos quatro modelos, 2,6× mais do que se os erros fossem independentes.

**A normalização semântica é indispensável:** o NEBB resgata ~30% dos acertos que a comparação
literal de strings descartaria.

---

## Reproduzir

```bash
pip install -r requirements.txt

# 1. julga as 16.000 respostas e imprime as métricas por modelo
bash scripts/evaluate/judge_all.sh

# 2. audita os acertos: o abstract entregou a resposta?
python3 scripts/audit/leakage_strict.py    # gabarito literal sobrevivente no texto
python3 scripts/audit/echo_expanded.py     # sinônimo creditado que ecoa o texto

# 3. acurácia bruta e auditada, por modelo e por estrato
python3 scripts/evaluate/audited_accuracy.py

# 4. análises da direção e da dose do viés
python3 scripts/audit/dose_response.py
python3 scripts/audit/error_direction.py
```

Os passos 1–3 rodam offline com o que está versionado. Os do passo 4 precisam do `gene2pubmed.gz`
do NCBI (260 MB, fora do git):

```bash
python3 scripts/build/sample_by_popularity.py   # baixa para data/sources/
```

## Estrutura

```
data/
  benchmark_v5.jsonl          os 4.000 casos: abstract mascarado, gabarito, GeneID, estrato
  benchmark_pmids.csv         a amostra de candidatos (PMID, GeneID, estrato)
  gold_symbols_v5.txt         símbolos-gabarito, entrada do NEBB

nebb/                         Normalization Engine for Biological Benchmarking
  pipeline.py                 cascata de resolução: HGNC offline → UniProt → heurística → APIs
  gold_standard.json          o dicionário: alias → gene canônico, com proveniência
  data/                       HGNC + UniProt na versão exata usada

scripts/
  build/                      construção do benchmark (amostragem, mascaramento, rebalanceamento)
  evaluate/                   julgamento e métricas
  audit/                      auditorias de vazamento, eco e as análises do viés

hpc/                          execução no Santos Dumont (Slurm + Singularity + Ollama)

results/
  OFFICIAL_NUMBERS.md         fonte única da verdade dos números
  answers/                    as 16.000 respostas brutas dos modelos
  judgments/                  o veredito por resposta
  audits/                     CSVs das auditorias, com os vereditos manuais

docs/                         metodologia, auditorias e revisões
```

## Como ler o julgamento

Cada resposta cai em uma de três classes:

- **Acerto estrito**: o modelo produziu o símbolo do gabarito.
- **Acerto expandido (NEBB)**: produziu um alias, nome anterior ou designação proteica que o NEBB
  resolve para o mesmo gene canônico.
- **Erro ou abstenção**: respondeu outro gene, ou nada.

O julgador ([`scripts/evaluate/judge.py`](scripts/evaluate/judge.py)) resolve o gene do gabarito
**pelo GeneID**, não pela string. A diferença não é cosmética: resolver por string fazia `FAS` cair
em *FASN*, `Rac1` em *RNASE1* e `brain natriuretic peptide` em *NPPA* (que é o peptídeo **atrial**).
Eram 133 de 4.000 gabaritos resolvendo para o gene errado. O histórico completo dessa correção está
em [`results/OFFICIAL_NUMBERS.md`](results/OFFICIAL_NUMBERS.md).

## Licença

[MIT](LICENSE). O texto da dissertação não faz parte deste repositório.
