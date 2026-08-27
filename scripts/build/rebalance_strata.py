#!/usr/bin/env python3
"""
Rebalanceia os estratos do benchmark, complementando Q2/Q3/Q4 ate o mesmo N do Q1 (768).

Motivo: a amostragem original era balanceada (1000/estrato), mas o filtro de anotacao do
PubTator derrubou os estratos de forma desigual -- perda de 23% no Q1 contra 64% no Q4,
porque o NER do PubTator falha mais em genes obscuros. Resultado: Q4 ficou com 362 casos
contra 768 do Q1, justamente no estrato mais importante para a pergunta de pesquisa.

Este script:
  1. Le o gene2pubmed do NCBI (humano, tax_id 9606) e reconstroi os estratos.
  2. Monta um pool de PMIDs candidatos NUNCA usados (exclui os 4000 da amostra original,
     inclusive os que ja falharam no filtro -- falhariam de novo).
  3. Respeita os tetos por gene da metodologia (Q1=10 pubs/gene, demais=5), contando o que
     ja foi usado no dataset atual.
  4. Consulta o PubTator, mascara com a logica corrigida (todas as ocorrencias, match exato
     de GeneID) e vai acumulando ate atingir o alvo por estrato.
  5. Escreve o dataset balanceado = casos do v3 + os novos.

Nao remove o vies de sobrevivencia (os casos que entram continuam sendo os que o PubTator
reconhece), mas devolve poder estatistico ao Q4.
"""
import gzip
import json
import random
import re
import time
import argparse
from pathlib import Path
from collections import defaultdict, Counter

import requests

BASE = Path(__file__).resolve().parents[2]
GENE2PUBMED = BASE / "data" / "sources" / "gene2pubmed.gz"
AMOSTRA_ORIG = BASE / "data" / "benchmark_pmids.csv"
DATASET_V3 = BASE / "data" / "intermediate" / "benchmark_v3_remasked.jsonl"

PROMPT_TEMPLATE = (
    "Based on your knowledge of genomic literature, fill in the [MASK] ONLY "
    "with the correct exact gene symbol or biological entity name in the "
    "following abstract:\n\n{abstract}"
)
PUBTATOR_URL = ("https://www.ncbi.nlm.nih.gov/research/pubtator-api/"
                "publications/export/biocjson?pmids=")

TETO_POR_GENE = {"Q1_Super_Populares": 10}   # demais estratos: 5
TETO_PADRAO = 5
SEED = 42


def estrato_de(n_pubs):
    if n_pubs >= 1000:
        return "Q1_Super_Populares"
    if n_pubs >= 100:
        return "Q2_Medios"
    if n_pubs >= 10:
        return "Q3_Baixa_Popularidade"
    return "Q4_Cauda_Longa"


def carregar_gene2pubmed():
    pares = []
    counts = Counter()
    with gzip.open(GENE2PUBMED, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 3 or p[0] != "9606":
                continue
            pares.append((p[1], p[2]))     # (GeneID, PMID)
            counts[p[1]] += 1
    return pares, counts


def mask_doc(doc, gene_alvo):
    """Mascara TODAS as formas do gene alvo. Retorna (gold, texto_mascarado) ou None.

    gold = texto da primeira anotacao (em ordem de documento) com GeneID exato.
    """
    texto = ""
    annos = []          # (texto, ids) na ordem do documento
    for passage in doc.get("passages", []):
        texto += passage.get("text", "") + " "
        for a in passage.get("annotations", []):
            if a.get("infons", {}).get("type") != "Gene":
                continue
            t = a.get("text", "")
            ids = (a.get("infons", {}).get("identifier") or "").split(";")
            if t:
                annos.append((t, ids))

    alvo = [t for t, ids in annos if gene_alvo in ids]   # match EXATO de GeneID
    if not alvo:
        return None
    gold = alvo[0]

    spans = set(alvo)
    baixo = {s.lower() for s in spans}
    spans |= {t for t, _ in annos if t.lower() in baixo}   # ortologo/isoforma mesmo texto

    pat = re.compile("|".join(re.escape(s) for s in sorted(spans, key=len, reverse=True)))
    return gold, pat.sub("[MASK]", texto).strip()


def fetch(pmids, max_retries=3):
    for _ in range(max_retries):
        try:
            r = requests.get(PUBTATOR_URL + ",".join(pmids), timeout=40)
            if r.status_code == 200:
                return {str(d.get("pmid")): d for d in r.json().get("PubTator3", [])}
        except Exception:
            pass
        time.sleep(3)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alvo", type=int, default=1000, help="casos por estrato")
    ap.add_argument("--base", default=str(DATASET_V3),
                    help="dataset de partida a ser complementado")
    ap.add_argument("--out", default=str(BASE / "data" / "intermediate" / "benchmark_v4_balanced.jsonl"))
    args = ap.parse_args()

    # ── estado atual ─────────────────────────────────────────────────────────
    atuais = [json.loads(l) for l in open(args.base, encoding="utf-8") if l.strip()]
    por_estrato = Counter(r["estrato"] for r in atuais)
    uso_por_gene = Counter(str(r["gene_id_gabarito"]) for r in atuais)

    # PMIDs a nunca reutilizar: os 4000 da amostra original (inclusive os que ja falharam
    # no filtro -- falhariam de novo) MAIS os que ja estao na base. Um PMID pode estar
    # ligado a dois genes no gene2pubmed (ex: artigo que descreve dois pseudogenes), e o
    # pipeline deduplica por PMID -- entao cada PMID entra no maximo uma vez.
    usados = {str(r["pmid"]) for r in atuais}
    with open(AMOSTRA_ORIG, encoding="utf-8") as f:
        next(f)
        for line in f:
            usados.add(line.split(",")[0].strip())

    print("Situacao atual:", dict(por_estrato))
    faltam = {e: max(0, args.alvo - por_estrato.get(e, 0))
              for e in ["Q1_Super_Populares", "Q2_Medios",
                        "Q3_Baixa_Popularidade", "Q4_Cauda_Longa"]}
    print("Faltam para o alvo de", args.alvo, ":", faltam)
    if not any(faltam.values()):
        print("Nada a fazer.")
        return

    # ── pool de candidatos ───────────────────────────────────────────────────
    print(f"\nLendo {GENE2PUBMED.name}...")
    pares, counts = carregar_gene2pubmed()
    g_est = {g: estrato_de(n) for g, n in counts.items()}

    pool = defaultdict(list)
    for gene, pmid in pares:
        if pmid in usados:
            continue
        e = g_est[gene]
        if faltam.get(e, 0) == 0:
            continue
        pool[e].append((gene, pmid))

    rnd = random.Random(SEED)
    for e in pool:
        rnd.shuffle(pool[e])
    print("Pool de candidatos inedito:", {e: len(v) for e, v in pool.items()})

    # ── coleta ate atingir o alvo, respeitando teto por gene ─────────────────
    novos = []
    for estrato, precisa in faltam.items():
        if precisa == 0:
            continue
        teto = TETO_POR_GENE.get(estrato, TETO_PADRAO)
        cands = pool.get(estrato, [])
        print(f"\n=== {estrato}: precisa de {precisa} (teto {teto} pubs/gene) ===")

        aceitos = 0
        buf = []
        i = 0
        while aceitos < precisa and i < len(cands):
            # monta um lote de 20 respeitando o teto
            # RESERVA o slot do gene ja na montagem do lote. Sem isso, varios PMIDs do
            # mesmo gene entram no mesmo lote antes de uso_por_gene subir, e o teto
            # estoura (ex: TP53 com 12 publicacoes, teto 10).
            lote = []
            reservado = Counter()
            while len(lote) < 20 and i < len(cands):
                gene, pmid = cands[i]
                i += 1
                if pmid in usados:
                    continue
                if uso_por_gene[gene] + reservado[gene] >= teto:
                    continue
                usados.add(pmid)          # trava contra PMID ligado a dois genes
                reservado[gene] += 1
                lote.append((gene, pmid))
            if not lote:
                break

            docs = fetch([p for _, p in lote])
            for gene, pmid in lote:
                if aceitos >= precisa:
                    break
                d = docs.get(pmid)
                if not d:
                    continue
                res = mask_doc(d, gene)
                if not res:
                    continue
                gold, texto = res
                if "[MASK]" not in texto:
                    continue
                buf.append({
                    "pmid": pmid,
                    "gene_id_gabarito": gene,
                    "estrato": estrato,
                    "resposta_esperada": gold,
                    "prompt": PROMPT_TEMPLATE.format(abstract=texto),
                })
                uso_por_gene[gene] += 1
                aceitos += 1
            print(f"  {aceitos}/{precisa}  (testados {i} candidatos)", end="\r")
            time.sleep(1)

        print(f"  {aceitos}/{precisa}  (testados {i} candidatos) | "
              f"taxa de aproveitamento: {100*aceitos/max(i,1):.1f}%")
        novos.extend(buf)

    # ── escreve ──────────────────────────────────────────────────────────────
    final = atuais + novos
    with open(args.out, "w", encoding="utf-8") as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nNovos casos: {len(novos)}")
    print("Distribuicao final:", dict(Counter(r["estrato"] for r in final)))
    print(f"Total: {len(final)}")
    print(f"Salvo em: {args.out}")


if __name__ == "__main__":
    main()
