#!/usr/bin/env python3
"""
Pipeline de avaliacao LLM para tarefas de Genomica.
Foco em SEGURANCA DE DADOS (salva o raw_response completo para pos-processamento).
"""
import os
import sys
import json
import time
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Configuracoes via variaveis de ambiente
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
MODEL = os.environ.get("LLM_MODEL", "qwen3.6:35b")
DATASET_DIR = Path(os.environ.get("DATASET_DIR", "/app/data"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
TASK_NAME = os.environ.get("TASK_NAME", "benchmark_v5")
MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES", "0"))

# Desliga o modo de raciocinio dos modelos que o tem (Gemma 4, Qwen 3.6).
# Sem isso o modelo gasta ~900 tokens "pensando" para responder duas palavras:
# no piloto deu 48,9 s/caso -> ~54h para os 4000 casos, contra 24h de job.
# O Llama 3.3 nao e modelo de raciocinio e rejeita esse parametro -- por isso o
# fallback automatico em query_ollama().
THINK = os.environ.get("LLM_THINK", "false").lower() == "true"

# Vira True se o servidor recusar o parametro 'think' (modelo sem esse modo).
_think_nao_suportado = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def load_jsonl_dataset(task):
    filepath = DATASET_DIR / f"{task}.jsonl"
    if not filepath.exists():
        raise FileNotFoundError(f"Dataset nao encontrado: {filepath}")

    samples = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line: continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as e:
                logging.warning(f"Linha {line_num} invalida em {filepath}: {e}")
    return samples

ANSWER_ONLY_SUFFIX = (
    "\n\nRespond with ONLY the name of the masked biological entity. "
    "Do not explain, do not reason, do not repeat the abstract. "
    "Output nothing but the entity name."
)


def build_prompt(prompt):
    """Prompt final enviado ao modelo. Resposta direta, sem chain-of-thought."""
    return prompt + ANSWER_ONLY_SUFFIX


def montar_payload(full_prompt, com_think):
    corpo = {
        "model": MODEL,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 4096,
            "num_ctx": 8192,
            "top_p": 0.9
        }
    }
    if com_think:
        corpo["think"] = THINK      # False = nao raciocina, responde direto
    return json.dumps(corpo).encode("utf-8")


def query_ollama(full_prompt, max_retries=5):
    """Consulta Ollama e retorna o DADO BRUTO intacto."""
    global _think_nao_suportado

    headers = {"Content-Type": "application/json"}

    for attempt in range(max_retries):
        usar_think = not THINK and not _think_nao_suportado
        payload = montar_payload(full_prompt, usar_think)
        try:
            req = urllib.request.Request(OLLAMA_URL, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8"))

                raw_response = data.get("response", "")
                prompt_tokens = data.get("prompt_eval_count", 0)
                completion_tokens = data.get("eval_count", 0)
                finish_reason = data.get("done_reason", "unknown")

                if raw_response.strip() == "":
                    logging.warning("[DIAGNOSTICO] A API retornou uma string 100% vazia.")
                    logging.warning(f"[DIAGNOSTICO] Finish Reason: {finish_reason} | Tokens: {completion_tokens}")

                metrics = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "finish_reason": finish_reason
                }

                return raw_response, metrics

        except urllib.error.HTTPError as e:
            corpo = ""
            try:
                corpo = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            # Modelo sem modo de raciocinio (ex: Llama 3.3) recusa 'think'.
            # Desliga o parametro e tenta de novo, sem gastar retry.
            if usar_think and "think" in corpo.lower():
                _think_nao_suportado = True
                logging.warning(
                    "[THINK] O modelo nao suporta 'think'. "
                    "Seguindo sem o parametro (esperado no Llama 3.3)."
                )
                continue
            logging.warning(f"Tentativa {attempt+1}/{max_retries} falhou: HTTP {e.code} {corpo[:200]}")
            time.sleep(15 * (attempt + 1))

        except Exception as e:
            logging.warning(f"Tentativa {attempt+1}/{max_retries} falhou: {e}")
            time.sleep(15 * (attempt + 1))

    return "[ERRO: FALHA NA CONSULTA]", {}

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Modelo no nome do arquivo: sem isso os 4 modelos escrevem no mesmo
    # {TASK_NAME}_results.jsonl e o resume de um contamina/sobrescreve o outro.
    safe_model = MODEL.replace(":", "_").replace("/", "_")
    output_file = OUTPUT_DIR / f"answers_{safe_model}.jsonl"

    logging.info(f"[START] Iniciando: {TASK_NAME} | Modelo: {MODEL}")
    samples = load_jsonl_dataset(TASK_NAME)

    if MAX_SAMPLES > 0:
        samples = samples[:MAX_SAMPLES]
        logging.info(f"[TESTE] Limitando a {MAX_SAMPLES} amostras.")

    processed_pmids = set()
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    processed_pmids.add(entry.get("pmid"))
                except json.JSONDecodeError:
                    continue
        logging.info(f"[RESUME] Retomando execucao: {len(processed_pmids)} ja processados.")

    with open(output_file, "a", encoding="utf-8") as out_f:
        for idx, sample in enumerate(samples):
            pmid = str(sample.get("pmid"))
            if pmid in processed_pmids:
                continue

            prompt = sample.get("prompt", "")
            full_prompt = build_prompt(prompt)
            gold_answer = sample.get("gold_answer", sample.get("resposta_esperada", ""))

            start_time = time.time()
            raw_ans, metrics = query_ollama(full_prompt)
            elapsed_sec = round(time.time() - start_time, 2)

            logging.info(f"[{idx+1}/{len(samples)}] PMID {pmid} | Tempo: {elapsed_sec}s | Tokens (In/Out): {metrics.get('prompt_tokens', 0)}/{metrics.get('completion_tokens', 0)}")

            result = {
                "pmid": pmid,
                "estrato": sample.get("estrato", "Desconhecido"),
                "gene_id_gabarito": sample.get("gene_id_gabarito"),
                "gold_answer": gold_answer,
                "prompt": prompt,
                "full_prompt": full_prompt,
                "model_response": raw_ans,
                "model": MODEL,
                "timestamp": datetime.now().isoformat(),
                "elapsed_sec": elapsed_sec,
                "prompt_tokens": metrics.get("prompt_tokens", 0),
                "completion_tokens": metrics.get("completion_tokens", 0),
                "status": "success" if raw_ans != "[ERRO: FALHA NA CONSULTA]" else "failed"
            }

            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_f.flush()
            processed_pmids.add(pmid)

    logging.info(f"[FINISH] {TASK_NAME} finalizado. Resultados salvos em {output_file}")

if __name__ == "__main__":
    main()
