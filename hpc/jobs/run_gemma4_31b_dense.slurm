#!/bin/bash
# ==========================================================
# BENCHMARK COMPLETO - GEMMA 4 31B (Arquitetura Densa)
# ==========================================================
#SBATCH --job-name=GT_FullDenso
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=10
#SBATCH --partition=sequana_gpu
#SBATCH --account=ppg-lncc
#SBATCH --gres=gpu:2
#SBATCH --mem=150G
#SBATCH --time=24:00:00          
#SBATCH --output=/scratch/ppg-lncc/guilherme.bittencourt/logs/geneturing/FULL_GEMMA4_DENSO_%j.out
#SBATCH --error=/scratch/ppg-lncc/guilherme.bittencourt/logs/geneturing/FULL_GEMMA4_DENSO_%j.err

USER_SCRATCH="/scratch/ppg-lncc/guilherme.bittencourt"
SIF_IMAGE="$USER_SCRATCH/ollama_python_v2.sif"
MODEL_CACHE="$USER_SCRATCH/ollama_models"
SCRIPT_PATH="$USER_SCRATCH/scripts"
SCRIPT_PYTHON="run_benchmark.py"

# Apontando para os dados reais
REAL_DATA="$USER_SCRATCH/dataset_genomica/sampled_data"
FAKE_DATA="/app/data"
OUTPUT_DIR_REAL="$USER_SCRATCH/output"
OUTPUT_DIR_CONTAINER="/app/output"

# ===> ATENCAO: COLOQUE O NOME DO SEU ARQUIVO DE BENCHMARK AQUI (SEM O .jsonl) <===
TASK_NAME="benchmark_v5"

LOG_DIR="$USER_SCRATCH/logs/geneturing"
mkdir -p "$LOG_DIR" "$OUTPUT_DIR_REAL"

export SINGULARITYENV_OLLAMA_MODELS="/ollama_models"
export SINGULARITYENV_OLLAMA_NUM_CTX="8192"
export SINGULARITYENV_LLM_THINK="false"   # desliga raciocinio interno (Llama ignora via fallback)
export SINGULARITYENV_DATASET_DIR="$FAKE_DATA"
export SINGULARITYENV_OUTPUT_DIR="$OUTPUT_DIR_CONTAINER"
export SINGULARITYENV_TASK_NAME="$TASK_NAME"
export SINGULARITYENV_PYTHONUNBUFFERED="1"
export SINGULARITYENV_LLM_MODEL="gemma4:31b"

echo "===================================================="
echo "[INICIANDO BENCHMARK COMPLETO - Gemma 4 31B Denso]"
echo "ARQUIVO ALVO: $TASK_NAME.jsonl"
echo "===================================================="

# 1. Sobe o servidor Ollama no fundo
singularity exec --nv -B "$MODEL_CACHE:/ollama_models" "$SIF_IMAGE" \
    ollama serve > "$LOG_DIR/ollama_full_denso_$SLURM_JOB_ID.log" 2>&1 &
OLLAMA_PID=$!
sleep 30

# 2. Roda o script de avaliacao Python
singularity exec --nv -B /scratch:/scratch -B /home:/home -B "$SCRIPT_PATH:/app" -B "$REAL_DATA:$FAKE_DATA" -B "$OUTPUT_DIR_REAL:$OUTPUT_DIR_CONTAINER" --pwd /app "$SIF_IMAGE" python3 "$SCRIPT_PYTHON"

# 3. Desliga o Ollama com seguranca
kill $OLLAMA_PID 2>/dev/null
wait $OLLAMA_PID 2>/dev/null
echo "[FINALIZADO]"
