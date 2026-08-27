#!/usr/bin/env python3
"""
Curva dose-resposta: acuracia por faixa de popularidade bibliografica.

A estratificacao em quartis e, por construcao, uma discretizacao arbitraria.
Este script trata a contagem de publicacoes como variavel CONTINUA e verifica
se o gradiente sobrevive ao corte -- ou seja, se o vies de popularidade e um
achado real ou artefato da escolha dos quartis.

POPULARIDADE: numero de PMIDs distintos associados ao GeneID do gabarito no
gene2pubmed do NCBI (restrito a Homo sapiens, tax_id 9606). Mesma fonte e
mesmo criterio de scripts/audit/error_direction.py.

UNIVERSO -- os 3.864 casos VALIDOS, nao os 4.000 aplicados.
Os 136 itens contaminados (abstract expoe a resposta) sao ANULADOS, conforme
a convencao adotada em docs/leakage_and_echo.md secao 9. Isso importa
particularmente aqui: a contaminacao concentra-se na cauda longa, que e
exatamente a regiao onde este script mede o "piso do conhecimento". Calcular a
curva sobre os 4.000 inflaria o piso com acertos que o proprio texto entregou.

Saidas:
  results/audits/dose_response.csv  -- uma linha por faixa, com acuracia por modelo
  stdout                        -- as duas tabelas de dose-resposta

Uso:
  python3 scripts/audit/dose_response.py [--incluir-contaminados]
"""
import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DATASET = BASE / "data" / "benchmark_v5.jsonl"
GENE2PUBMED = BASE / "data" / "sources" / "gene2pubmed.gz"
JULG = BASE / "results" / "judgments"
OUT_CSV = BASE / "results" / "audits" / "dose_response.csv"

MODELOS = {
    "llama3.3_70b": "Llama 70B",
    "gemma4_31b": "Gemma Denso",
    "qwen3.6_35b": "Qwen",
    "gemma4_26b": "Gemma MoE",
}

# Faixas da tabela dose_resposta. Limite superior exclusivo.
FAIXAS = [
    ("1--4", 1, 5), ("5--9", 5, 10), ("10--29", 10, 30), ("30--99", 30, 100),
    ("100--299", 100, 300), ("300--999", 300, 1000),
    ("1.000--2.999", 1000, 3000), (">= 3.000", 3000, float("inf")),
]
# Agrupamento mais grosso (Tabela piso), para medir a amplitude entre modelos.
FAIXAS_PISO = [
    ("1--9", 1, 10), ("10--29", 10, 30), ("30--99", 30, 100),
    ("100--999", 100, 1000), (">= 1.000", 1000, float("inf")),
]


def carregar_popularidade():
    """GeneID -> numero de PMIDs distintos (Homo sapiens)."""
    pubs = defaultdict(set)
    with gzip.open(GENE2PUBMED, "rt", encoding="utf-8") as f:
        next(f, None)
        for linha in f:
            campos = linha.split("\t")
            if len(campos) >= 3 and campos[0] == "9606":
                pubs[campos[1].strip()].add(campos[2].strip())
    return {g: len(p) for g, p in pubs.items()}


def carregar_contaminados():
    """(pmid, gold) dos itens anulados -- vazamento estrito + eco confirmados."""
    def itens(arquivo, ok):
        with open(BASE / "results" / "audits" / arquivo, encoding="utf-8") as f:
            return {(r["pmid"], r["gold_answer"]) for r in csv.DictReader(f)
                    if r["veredito_manual"] == ok}
    return (itens("leakage_strict_verdicts.csv", "Vazamento real")
            | itens("echo_expanded_verdicts.csv", "eco_real"))


def faixa_de(n, faixas):
    for rotulo, lo, hi in faixas:
        if lo <= n < hi:
            return rotulo
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incluir-contaminados", action="store_true",
                    help="calcula sobre os 4.000 casos (curva NAO auditada); "
                         "so para comparacao -- o padrao e auditado, 3.864.")
    args = ap.parse_args()

    print("Carregando gene2pubmed...")
    pop = carregar_popularidade()
    contaminados = set() if args.incluir_contaminados else carregar_contaminados()

    # pmid -> gene_id, para resolver a popularidade de cada caso
    gene_do_caso = {}
    with open(DATASET, encoding="utf-8") as f:
        for linha in f:
            d = json.loads(linha)
            gene_do_caso[str(d["pmid"])] = str(d.get("gene_id_gabarito") or "")

    # acertos[faixa][modelo] = [acertos, casos]
    acertos = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    piso = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    itens_faixa, itens_piso = Counter(), Counter()
    anulados = sem_pop = 0

    for chave in MODELOS:
        with open(JULG / f"judgment_{chave}.jsonl", encoding="utf-8") as f:
            for linha in f:
                if not linha.strip():
                    continue
                r = json.loads(linha)
                item = (str(r["pmid"]), r["gold_answer"])
                if item in contaminados:
                    anulados += 1
                    continue

                gid = gene_do_caso.get(str(r["pmid"]), "")
                n = pop.get(gid)
                if not n:
                    sem_pop += 1
                    continue

                ok = bool(r.get("acerto_estrito") or r.get("acerto_nebb"))
                for faixas, alvo, cont in ((FAIXAS, acertos, itens_faixa),
                                           (FAIXAS_PISO, piso, itens_piso)):
                    rot = faixa_de(n, faixas)
                    if rot is None:
                        continue
                    alvo[rot][chave][0] += ok
                    alvo[rot][chave][1] += 1
                    if chave == "llama3.3_70b":
                        cont[rot] += 1

    universo = "4.000 (NAO auditado)" if args.incluir_contaminados else "3.864 (auditado)"
    print(f"\nUniverso: {universo}")
    if anulados:
        print(f"Casos anulados: {anulados} ({anulados // len(MODELOS)} por modelo)")
    if sem_pop:
        print(f"Casos sem popularidade no gene2pubmed: {sem_pop} "
              f"({sem_pop // len(MODELOS)} por modelo) -- excluidos")

    print("\n" + "=" * 78)
    print("CURVA DOSE-RESPOSTA")
    print("=" * 78)
    cab = f"{'Publicacoes':<16}{'Itens':>7}" + "".join(f"{n:>14}" for n in MODELOS.values())
    print(cab)
    print("-" * 78)
    linhas_csv = []
    for rot, _, _ in FAIXAS:
        if not itens_faixa[rot]:
            continue
        vals = []
        for chave in MODELOS:
            a, c = acertos[rot][chave]
            vals.append(100 * a / c if c else 0.0)
        print(f"{rot:<16}{itens_faixa[rot]:>7}" + "".join(f"{v:>13.1f}%" for v in vals))
        linhas_csv.append({"faixa": rot, "itens": itens_faixa[rot],
                           **{n: f"{v:.1f}" for n, v in zip(MODELOS.values(), vals)}})

    print("\n" + "=" * 78)
    print("O PISO DO CONHECIMENTO (amplitude entre modelos)")
    print("=" * 78)
    print(f"{'Publicacoes':<16}{'Itens':>7}{'Acuracia media':>18}{'Amplitude':>14}")
    print("-" * 78)
    for rot, _, _ in FAIXAS_PISO:
        if not itens_piso[rot]:
            continue
        vals = [100 * piso[rot][c][0] / piso[rot][c][1] for c in MODELOS if piso[rot][c][1]]
        media, amp = sum(vals) / len(vals), max(vals) - min(vals)
        print(f"{rot:<16}{itens_piso[rot]:>7}{media:>17.1f}%{amp:>12.1f}pp")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas_csv[0].keys()))
        w.writeheader()
        w.writerows(linhas_csv)
    print(f"\nCSV -> {OUT_CSV.relative_to(BASE)}")


if __name__ == "__main__":
    main()
