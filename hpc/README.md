# Execução no Santos Dumont (LNCC)

As 16.000 respostas em [`results/answers/`](../results/answers) foram geradas aqui: quatro modelos
× 4.000 casos, servidos por Ollama dentro de um container Singularity, um job Slurm por modelo.

## O runner

Todos os jobs executam [`run_benchmark.py`](run_benchmark.py) dentro do container. Ele lê o
benchmark de `$DATASET_DIR/$TASK_NAME.jsonl`, consulta o servidor Ollama local e grava
`answers_<modelo>.jsonl` em `$OUTPUT_DIR`, **salvando a `raw_response` íntegra**. A extração da
resposta é pós-processamento, nunca acontece na GPU.

Configuração por variável de ambiente: `OLLAMA_URL`, `LLM_MODEL`, `LLM_THINK`, `DATASET_DIR`,
`OUTPUT_DIR`, `TASK_NAME`, `MAX_SAMPLES`.

Hiperparâmetros fixos no código:

| Parâmetro | Valor |
|---|---|
| `temperature` | 0.1 |
| `num_predict` | 4096 |
| `num_ctx` | 8192 |
| `top_p` | 0.9 |
| `top_k` | default do Ollama (40) |
| `max_retries` | 5 |

**Retomada:** o script relê o arquivo de saída e pula os PMIDs já respondidos, então um job
interrompido pelo limite de tempo é retomado com o mesmo `sbatch`.

### `LLM_THINK=false`: a decisão que viabilizou a rodada

Gemma 4 e Qwen 3.6 têm modo de raciocínio explícito. Com ele ligado, o piloto deu **48,9 s/caso**:
o modelo gastava ~900 tokens "pensando" para responder duas palavras, o que projetava ~54 h para os
4.000 casos contra as 24 h de teto do job. Todos os jobs de produção rodaram com `LLM_THINK=false`.

O Llama 3.3 não é modelo de raciocínio e **rejeita** o parâmetro `think` com erro HTTP. O runner
detecta a recusa, marca `_think_nao_suportado` e repete a requisição sem o parâmetro, por isso o
mesmo job serve aos dois tipos de modelo.

> Consequência metodológica: os números de abstenção e de acurácia são de modelos operando **sem**
> cadeia de raciocínio, por decisão de orçamento computacional. É uma condição do experimento, não
> uma configuração acidental.

## Jobs de produção

Todos com `TASK_NAME="benchmark_v5"` (4.000 casos), partição `sequana_gpu`, 2 GPUs.

| Job | Modelo | Tempo alocado | Imagem | Saída |
|---|---|---|---|---|
| [`jobs/run_gemma4_31b_dense.slurm`](jobs/run_gemma4_31b_dense.slurm) | `gemma4:31b` (denso) | 24 h | `ollama_python_v2.sif` | `results/answers/answers_gemma4_31b.jsonl` |
| [`jobs/run_gemma4_26b_moe.slurm`](jobs/run_gemma4_26b_moe.slurm) | `gemma4:26b` (MoE) | 24 h | `ollama_python_v2.sif` | `results/answers/answers_gemma4_26b.jsonl` |
| [`jobs/run_qwen36_35b.slurm`](jobs/run_qwen36_35b.slurm) | `qwen3.6:35b` | 24 h | `ollama_python_v2.sif` | `results/answers/answers_qwen3.6_35b.jsonl` |
| [`jobs/run_llama33_70b.slurm`](jobs/run_llama33_70b.slurm) | `llama3.3:70b` | 20 h | `inspect_full.sif` | `results/answers/answers_llama3.3_70b.jsonl` |

O job do Llama 70B usa `inspect_full.sif` apenas porque era a imagem disponível quando foi montado;
o comando executado dentro do container é o mesmo.

## Piloto

[`jobs/pilot_v5.slurm`](jobs/pilot_v5.slurm): 20 casos, um modelo, partição `sequana_gpu_dev`,
20 min. Serve para medir o `elapsed_sec` médio antes de comprometer 24 h de fila:

```bash
PILOTO_MODELO=llama3.3:70b sbatch hpc/jobs/pilot_v5.slurm
```

Inspecione a saída com [`inspect_pilot.py`](inspect_pilot.py).

## Container

A definição Singularity está em [`container/ollama_python.def`](container/ollama_python.def)
(Ollama + Python). A imagem `.sif` compilada não é versionada (3,3 GB).

## Antes de submeter

```bash
python3 scripts/build/preflight_check.py
```

Confere dataset, gabarito, cobertura do NEBB e os próprios `.slurm`. Cada item corresponde a um
problema real encontrado durante o desenvolvimento do pipeline.
Se algum falhar, não suba para o cluster.
