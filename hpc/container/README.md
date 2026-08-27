# Container: Ollama + Python3 para Sdumont

## Build (máquina local, requer sudo)

```bash
sudo singularity build ollama_python_v2.sif ollama_python.def
```

## Envio para o Sdumont

```bash
scp ollama_python_v2.sif guilherme.bittencourt@login.sdumont.lncc.br:/scratch/ppg-lncc/guilherme.bittencourt/
```

## Uso no .slurm

Substitua `inspect_full.sif` por `ollama_python_v2.sif` no script SLURM.

O bind `-B "$MODEL_CACHE:/ollama_models"` já faz o Ollama usar o `/scratch` para
armazenar modelos (evita estourar a quota do home), pois a variável `OLLAMA_MODELS`
já está exportada no `%environment` do container.
