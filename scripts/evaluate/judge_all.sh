#!/usr/bin/env bash
# Julga os 4 modelos da rodada v5 e imprime as metricas.
#
# Uso:
#   bash scripts/evaluate/judge_all.sh
#
# Espera as respostas dos modelos em results/answers/:
#   answers_gemma4_31b.jsonl
#   answers_gemma4_26b.jsonl
#   answers_qwen3.6_35b.jsonl
#   answers_llama3.3_70b.jsonl
set -euo pipefail

BASE="$(cd "$(dirname "$0")/../.." && pwd)"
IN="$BASE/results/answers"
OUT="$BASE/results/judgments"
GAB="$BASE/nebb/gold_standard.json"

mkdir -p "$OUT"

MODELOS=(gemma4_31b gemma4_26b qwen3.6_35b llama3.3_70b)

echo "════════ CONFERINDO OS ARQUIVOS ════════"
erro=0
for m in "${MODELOS[@]}"; do
    f="$IN/answers_${m}.jsonl"
    if [[ ! -f "$f" ]]; then
        echo "  [FALTA] $f"; erro=1; continue
    fi
    n=$(wc -l < "$f")
    if [[ "$n" -ne 4000 ]]; then
        echo "  [INCOMPLETO] $m: $n linhas (esperado 4000)"; erro=1
    else
        echo "  [OK] $m: $n linhas"
    fi
done
[[ "$erro" -eq 1 ]] && { echo; echo "Corrija antes de julgar."; exit 1; }

echo
echo "════════ JULGANDO ════════"
for m in "${MODELOS[@]}"; do
    echo "--- $m ---"
    # o julgamento e incremental: apaga para nao misturar com rodada anterior
    rm -f "$OUT/judgment_${m}.jsonl"
    python3 "$BASE/scripts/evaluate/judge.py" \
        "$IN/answers_${m}.jsonl" \
        "$OUT/judgment_${m}.jsonl" \
        --gabarito "$GAB"
done

echo
echo "════════ METRICAS ════════"
for m in "${MODELOS[@]}"; do
    echo
    echo "############ $m ############"
    python3 "$BASE/scripts/evaluate/metrics.py" \
        "$IN/answers_${m}.jsonl"
done

echo
echo "Julgamentos em: $OUT"
