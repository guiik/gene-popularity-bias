#!/usr/bin/env python3
"""
Mascaramento corrigido (Golden Matcher v3).

Bug do pipeline original: para cada PMID, pegava-se só a PRIMEIRA anotação do
PubTator cujo GeneID batesse com o gabarito, extraía o texto literal daquela
UNICA ocorrencia e fazia um replace dessa string. Qualquer outra forma de
escrever a mesma entidade no resto do abstract (sigla entre parenteses, nome
completo repetido, variante de maiuscula/minuscula, ortologo com GeneID
diferente mas mesmo texto) ficava visivel sem mascara -- vazamento.

Correcao:
  1. Colige TODAS as anotacoes Gene do documento (title + abstract) cujo
     GeneID bate com o alvo -- nao para na primeira.
  2. Fecha tambem o caso "mesma palavra, GeneID diferente" (ortologos,
     isoformas): qualquer anotacao Gene cujo TEXTO (case-insensitive) seja
     igual a um dos textos-alvo tambem é mascarada.
  3. Mascara TODAS essas ocorrencias de uma vez (nao só a primeira).

Mantém o gold_answer original (resposta_esperada) do dataset já em uso, para
não quebrar a infraestrutura de julgamento (NEBB, judge.py etc.) --
só fecha os buracos de mascaramento.

Fonte da amostra (pmid -> geneID -> estrato): data/intermediate/benchmark_v2.jsonl
(reaproveita a amostra já usada, sem re-amostrar do zero).
"""
import json
import re
import sys
import time
import argparse
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parents[2]
SRC_DATASET = BASE / "data" / "intermediate" / "benchmark_v2.jsonl"

PROMPT_TEMPLATE = (
    "Based on your knowledge of genomic literature, fill in the [MASK] ONLY "
    "with the correct exact gene symbol or biological entity name in the "
    "following abstract:\n\n{abstract}"
)

PUBTATOR_URL = "https://www.ncbi.nlm.nih.gov/research/pubtator-api/publications/export/biocjson?pmids="


def load_targets(pmid_filter=None):
    targets = {}
    with open(SRC_DATASET, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            pmid = str(d["pmid"])
            if pmid_filter and pmid not in pmid_filter:
                continue
            targets[pmid] = {
                "gene_id_gabarito": str(d["gene_id_gabarito"]),
                "estrato": d.get("estrato", ""),
                "resposta_esperada": d.get("resposta_esperada", ""),
                "prompt_original": d.get("prompt", ""),
            }
    return targets


def fetch_chunk(pmids, max_retries=3):
    url = PUBTATOR_URL + ",".join(pmids)
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                return r.json().get("PubTator3", [])
            print(f"  [HTTP {r.status_code}] tentativa {attempt+1}")
        except Exception as e:
            print(f"  [erro] tentativa {attempt+1}: {e}")
        time.sleep(3)
    return []


def mask_doc(doc, gene_alvo):
    """Retorna dict com texto mascarado e diagnostico, ou None se o gene alvo
    nao foi encontrado em nenhuma anotacao do documento."""
    texto_completo = ""
    all_gene_annos = []  # (texto, ids_str)

    for passage in doc.get("passages", []):
        texto_completo += passage.get("text", "") + " "
        for anno in passage.get("annotations", []):
            if anno.get("infons", {}).get("type") != "Gene":
                continue
            ids_str = anno.get("infons", {}).get("identifier", "") or ""
            text = anno.get("text", "")
            if text:
                all_gene_annos.append((text, ids_str))

    # Passo 1: todas as anotacoes cujo GeneID bate com o alvo
    ids_alvo_set = {gene_alvo}
    target_texts = {
        text for text, ids_str in all_gene_annos
        if ids_alvo_set & set(ids_str.split(";"))
    }
    if not target_texts:
        return None

    # Passo 2: reincidencia textual sob GeneID diferente (ortologo/isoforma) --
    # mesma palavra, tag diferente, ainda assim vaza a resposta.
    target_texts_lower = {t.lower() for t in target_texts}
    extra_texts = {
        text for text, _ in all_gene_annos
        if text.lower() in target_texts_lower
    }
    spans = target_texts | extra_texts

    spans_sorted = sorted(spans, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(s) for s in spans_sorted))
    n_hits = len(pattern.findall(texto_completo))
    texto_mascarado = pattern.sub("[MASK]", texto_completo)

    return {
        "texto_mascarado": texto_mascarado.strip(),
        "spans_mascarados": sorted(spans, key=str.lower),
        "n_ocorrencias_mascaradas": n_hits,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pmids", help="arquivo texto com um PMID por linha (subset). Se omitido, roda tudo.")
    ap.add_argument("--out", default=str(BASE / "data" / "intermediate" / "benchmark_v3_remasked.jsonl"))
    ap.add_argument("--report", default=str(BASE / "results" / "audits" / "masking_v3_diagnostics.jsonl"))
    ap.add_argument("--chunk-size", type=int, default=20)
    args = ap.parse_args()

    pmid_filter = None
    if args.pmids:
        pmid_filter = {l.strip() for l in open(args.pmids) if l.strip()}

    targets = load_targets(pmid_filter)
    print(f"{len(targets)} PMIDs alvo carregados de {SRC_DATASET.name}")

    pmids = list(targets.keys())
    out_rows = []
    report_rows = []
    n_ok = 0
    n_not_found = 0
    n_leak_closed = 0  # casos onde mascaramos mais de 1 ocorrencia (vazamento fechado)

    for i in range(0, len(pmids), args.chunk_size):
        chunk = pmids[i:i + args.chunk_size]
        print(f"Consultando PubTator: {i+1}-{i+len(chunk)} de {len(pmids)}...")
        docs = fetch_chunk(chunk)
        docs_by_pmid = {str(d.get("pmid")): d for d in docs}

        for pmid in chunk:
            info = targets[pmid]
            doc = docs_by_pmid.get(pmid)
            if not doc:
                n_not_found += 1
                continue
            result = mask_doc(doc, info["gene_id_gabarito"])
            if not result:
                n_not_found += 1
                continue

            n_ok += 1
            if result["n_ocorrencias_mascaradas"] > 1:
                n_leak_closed += 1

            prompt = PROMPT_TEMPLATE.format(abstract=result["texto_mascarado"])
            out_rows.append({
                "pmid": pmid,
                "gene_id_gabarito": info["gene_id_gabarito"],
                "estrato": info["estrato"],
                "resposta_esperada": info["resposta_esperada"],
                "prompt": prompt,
            })
            report_rows.append({
                "pmid": pmid,
                "estrato": info["estrato"],
                "resposta_esperada": info["resposta_esperada"],
                "spans_mascarados": result["spans_mascarados"],
                "n_ocorrencias_mascaradas": result["n_ocorrencias_mascaradas"],
            })

        time.sleep(1)

    with open(args.out, "w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(args.report, "w", encoding="utf-8") as f:
        for row in report_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nOK: {n_ok}  |  Nao encontrados no PubTator: {n_not_found}")
    print(f"Casos com >1 ocorrencia mascarada (vazamento fechado pela correcao): {n_leak_closed} de {n_ok}")
    print(f"\nDataset corrigido: {args.out}")
    print(f"Relatorio de diagnostico: {args.report}")


if __name__ == "__main__":
    main()
