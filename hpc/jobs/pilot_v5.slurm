#!/bin/bash
# ==========================================================
# TESTE-PILOTO: benchmark_v5 (20 casos, 1 modelo)
#
# Roda ANTES da rodada cheia. Serve para conferir, com os proprios olhos:
#   - o prompt contem o ABSTRACT (nao so o titulo)
#   - o full_prompt termina com a instrucao answer-only
#   - a resposta e CURTA (um nome de gene), nao paragrafos de raciocinio
#   - completion_tokens na casa das dezenas -- se vier 4096, o chain-of-thought
#     NAO foi removido de verdade e a versao velha do script esta no cluster
#   - o elapsed_sec medio, para calcular se o --time da rodada cheia basta
# ==========================================================
#SBATCH --job-name=GT_Piloto_v5
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=10
#SBATCH --partition=sequana_gpu_dev
#SBATCH --account=ppg-lncc
#SBATCH --gres=gpu:2
#SBATCH --mem=150G
#SBATCH --time=00:20:00
#SBATCH --output=/scratch/ppg-lncc/guilherme.bittencourt/logs/geneturing/PILOTO_V5_%j.out
#SBATCH --error=/scratch/ppg-lncc/guilherme.bittencourt/logs/geneturing/PILOTO_V5_%j.err

USER_SCRATCH="/scratch/ppg-lncc/guilherme.bittencourt"
SIF_IMAGE="$USER_SCRATCH/ollama_python_v2.sif"
MODEL_CACHE="$USER_SCRATCH/ollama_models"
SCRIPT_PATH="$USER_SCRATCH/scripts"
SCRIPT_PYTHON="run_benchmark.py"

REAL_DATA="$USER_SCRATCH/dataset_genomica/sampled_data"
FAKE_DATA="/app/data"
OUTPUT_DIR_REAL="$USER_SCRATCH/output_piloto"      # separado! nao mistura com a rodada cheia
rm -f "$OUTPUT_DIR_REAL"/*.jsonl 2>/dev/null   # limpa piloto anterior (senao o resume pula tudo)
OUTPUT_DIR_CONTAINER="/app/output"

TASK_NAME="benchmark_v5"
MODEL_NAME="${PILOTO_MODELO:-gemma4:31b}"          # sobrescreva: PILOTO_MODELO=llama3.3:70b sbatch ...
LOG_DIR="$USER_SCRATCH/logs/geneturing"
mkdir -p "$LOG_DIR" "$OUTPUT_DIR_REAL"

export SINGULARITYENV_OLLAMA_MODELS="/ollama_models"
export SINGULARITYENV_OLLAMA_NUM_CTX="8192"
export SINGULARITYENV_DATASET_DIR="$FAKE_DATA"
export SINGULARITYENV_OUTPUT_DIR="$OUTPUT_DIR_CONTAINER"
export SINGULARITYENV_TASK_NAME="$TASK_NAME"
export SINGULARITYENV_LLM_MODEL="$MODEL_NAME"
export SINGULARITYENV_MAX_SAMPLES="20"             # <<< so 20 casos
export SINGULARITYENV_LLM_THINK="false"            # <<< desliga o raciocinio interno
export SINGULARITYENV_PYTHONUNBUFFERED="1"

echo "===================================================="
echo "[TESTE-PILOTO] $MODEL_NAME | 20 casos | $TASK_NAME"
echo "===================================================="

# Guarda-chuva: o script no cluster e mesmo o novo?
if grep -q "Think step-by-step" "$SCRIPT_PATH/$SCRIPT_PYTHON"; then
    echo "ERRO: $SCRIPT_PYTHON no cluster ainda tem 'Think step-by-step'."
    echo "      Voce subiu a versao VELHA. Abortando."
    exit 1
fi
echo "[OK] script sem chain-of-thought."

# 1. Sobe o Ollama
singularity exec --nv -B "$MODEL_CACHE:/ollama_models" "$SIF_IMAGE" \
    ollama serve > "$LOG_DIR/ollama_piloto_$SLURM_JOB_ID.log" 2>&1 &
OLLAMA_PID=$!
sleep 30

# 2. Roda
singularity exec --nv -B /scratch:/scratch -B /home:/home \
    -B "$SCRIPT_PATH:/app" -B "$REAL_DATA:$FAKE_DATA" \
    -B "$OUTPUT_DIR_REAL:$OUTPUT_DIR_CONTAINER" \
    --pwd /app "$SIF_IMAGE" python3 "$SCRIPT_PYTHON"

kill $OLLAMA_PID 2>/dev/null
wait $OLLAMA_PID 2>/dev/null

echo "===================================================="
echo "[FINALIZADO] Confira agora:"
echo "  python3 $USER_SCRATCH/scripts/inspecionar_piloto.py"
echo "===================================================="
