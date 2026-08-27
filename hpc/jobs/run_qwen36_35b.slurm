#!/bin/bash
# ==========================================================
# PRODUCAO - Qwen 3.6 35B (Dataset Completo)
# ==========================================================
#SBATCH --job-name=GT_Prod_qwen
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=10
#SBATCH --partition=sequana_gpu
#SBATCH --account=ppg-lncc
#SBATCH --gres=gpu:2
#SBATCH --mem=150G
#SBATCH --time=24:00:00          
#SBATCH --output=/scratch/ppg-lncc/guilherme.bittencourt/logs/geneturing/PROD_QWEN_%j.out
#SBATCH --error=/scratch/ppg-lncc/guilherme.bittencourt/logs/geneturing/PROD_QWEN_%j.err

# ==========================================================
# CAMINHOS E CONFIGURACOES
# ==========================================================
USER_SCRATCH="/scratch/ppg-lncc/guilherme.bittencourt"
SIF_IMAGE="$USER_SCRATCH/ollama_python_v2.sif"
MODEL_CACHE="$USER_SCRATCH/ollama_models"
SCRIPT_PATH="$USER_SCRATCH/scripts"
SCRIPT_PYTHON="run_benchmark.py"

REAL_DATA="$USER_SCRATCH/dataset_genomica/sampled_data"
FAKE_DATA="/app/data"
OUTPUT_DIR_REAL="$USER_SCRATCH/output"
OUTPUT_DIR_CONTAINER="/app/output"

# dataset completo: 4.000 casos, 4 quartis
TASK_NAME="benchmark_v5"
MODEL_NAME="qwen3.6:35b"
LOG_DIR="$USER_SCRATCH/logs/geneturing"
mkdir -p "$LOG_DIR" "$OUTPUT_DIR_REAL"

echo "===================================================="
echo "[INICIANDO PRODUCAO - Qwen 3.6 35B]"
echo "ARQUIVO: $TASK_NAME.jsonl"
echo "MODELO: $MODEL_NAME"
echo "TEMPO MAX: 20:00:00"
echo "===================================================="

# Forca as variaveis de ambiente para dentro do container
export SINGULARITYENV_OLLAMA_MODELS="/ollama_models"
export SINGULARITYENV_OLLAMA_NUM_CTX="8192"
export SINGULARITYENV_LLM_THINK="false"   # desliga raciocinio interno (Llama ignora via fallback)

# 1. SUBIR OLLAMA
echo "[1/3] Subindo Ollama..."
singularity exec --nv -B "$MODEL_CACHE:/ollama_models" "$SIF_IMAGE" \
    ollama serve > "$LOG_DIR/ollama_prod_$SLURM_JOB_ID.log" 2>&1 &
OLLAMA_PID=$!
sleep 30

if ! kill -0 $OLLAMA_PID 2>/dev/null; then
    echo "[ERRO CRITICO]: Ollama nao subiu. Verifique: $LOG_DIR/ollama_prod_$SLURM_JOB_ID.log"
    exit 1
fi
echo "[OK] Ollama ativo (PID: $OLLAMA_PID)"

# 2. EXECUTAR PYTHON
echo "[2/3] Processando dataset completo via Python..."
singularity exec --nv \
    -B /scratch:/scratch \
    -B /home:/home \
    -B "$SCRIPT_PATH:/app" \
    -B "$REAL_DATA:$FAKE_DATA" \
    -B "$OUTPUT_DIR_REAL:$OUTPUT_DIR_CONTAINER" \
    --env DATASET_DIR="$FAKE_DATA" \
    --env TASK_NAME="$TASK_NAME" \
    --env LLM_MODEL="$MODEL_NAME" \
    --env OUTPUT_DIR="$OUTPUT_DIR_CONTAINER" \
    --env PYTHONUNBUFFERED=1 \
    --pwd /app \
    "$SIF_IMAGE" \
    python3 "$SCRIPT_PYTHON"

EXIT_CODE=$?

# 3. LIMPEZA E ENCERRAMENTO
echo "[3/3] Desligando Ollama e finalizando job..."
kill $OLLAMA_PID 2>/dev/null
wait $OLLAMA_PID 2>/dev/null

if [ $EXIT_CODE -eq 0 ]; then
    echo "[SUCESSO] Producao concluida!"
    echo "Resultados salvos em: $OUTPUT_DIR_REAL/answers_<modelo>.jsonl"
else
    echo "[FALHA] Producao falhou (Codigo $EXIT_CODE). Verifique logs em: $LOG_DIR"
fi
