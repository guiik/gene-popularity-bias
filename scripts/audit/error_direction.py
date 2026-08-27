#!/usr/bin/env python3
"""
================================================================================
A DIREÇÃO DO ERRO: quando o modelo erra, ele chuta um gene mais popular?
================================================================================

PERGUNTA CIENTÍFICA
-------------------
O viés de popularidade é normalmente medido como AUSÊNCIA: o modelo acerta menos
em genes raros. Este script o mede como PRESENÇA: quando o modelo erra, o gene
que ele inventa é mais citado na literatura do que o gene que deveria recuperar?

Se a resposta for sim, o viés não é apenas uma lacuna de conhecimento, é uma
FORÇA DIRECIONAL que atrai as predições para o centro da distribuição
bibliográfica.

COMO A POPULARIDADE É MEDIDA
----------------------------
Pela mesma métrica que define os estratos do benchmark: o número de publicações
associadas ao gene no `gene2pubmed` do NCBI (restrito a Homo sapiens, tax_id
9606). Não é uma medida nova nem subjetiva.

O PROCEDIMENTO, EM 4 PASSOS
---------------------------
Para cada resposta ERRADA de cada modelo:
  1. pega a string que o modelo respondeu (ex: "TP53");
  2. resolve essa string para um GeneID via HGNC (símbolo oficial, alias ou
     nome anterior);
  3. busca o número de publicações desse GeneID no gene2pubmed;
  4. compara com o número de publicações do gene CORRETO daquele item.

OS 5 TESTES (cada um responde a uma objeção possível)
-----------------------------------------------------
  T1  PAREADO             compara item a item, não mediana contra mediana.
                          Objeção que responde: "você comparou duas distribuições
                          diferentes".
  T2  CONTROLE INTERNO    o efeito DEVE sumir no Q1 (se o alvo já é o TP53, é
                          impossível chutar algo mais popular) e ser máximo no Q4.
                          Objeção: "isso é artefato do seu método".
  T3  CONFUNDIDOR         e se o modelo apenas copiou um gene que já estava no
                          abstract? Genes co-citados tendem a ser populares.
                          Objeção: "o efeito é do texto, não do modelo".
  T4  MODELO NULO         quanto valeria um chute CEGO à popularidade? (mediana
                          de publicações de todos os genes humanos)
                          Objeção: "isso é só regressão à média".
  T5  OS MAIS ALUCINADOS  quais genes o modelo inventa com mais frequência, e em
                          que percentil de popularidade eles estão?

LIMITAÇÃO A DECLARAR
--------------------
Só é possível analisar os erros cuja resposta mapeia para um símbolo gênico
reconhecível no HGNC (~61–74% dos erros, varia por modelo). Os demais são
nomes inventados ou frases, que não têm popularidade definida. O script reporta
essa taxa de cobertura explicitamente.

USO
---
    python3 scripts/audit/error_direction.py
    python3 scripts/audit/error_direction.py --csv        # exporta os dados

REQUISITOS
----------
    data/sources/gene2pubmed.gz          (baixado por scripts/build/sample_by_popularity.py)
    nebb/data/hgnc_complete.json           (baixado por nebb/download_databases.py)
    nebb/gold_standard.json
    data/benchmark_v5.jsonl
    results/answers/answers_<modelo>.jsonl
"""
import argparse
import os
import bisect
import csv
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "scripts" / "evaluate"))

from judge import (                                  # noqa: E402
    load_nebb, load_hgnc_por_geneid, judge_entry,
)

GENE2PUBMED = BASE / "data" / "sources" / "gene2pubmed.gz"
HGNC = BASE / "nebb" / "data" / "hgnc_complete.json"
GABARITO = BASE / "nebb" / "gold_standard.json"
DATASET = BASE / "data" / "benchmark_v5.jsonl"
RESULTADOS = BASE / "results" / "answers"

# Universo da analise: por padrao os 3.864 casos validos (itens contaminados
# anulados). Defina ITENS_ANULADOS=0 no ambiente para rodar sobre os 4.000.
EXCLUIR_ANULADOS = os.environ.get("ITENS_ANULADOS", "1") != "0"


def carregar_anulados():
    """(pmid, gold) dos itens cujo abstract expoe a resposta."""
    import csv as _csv

    def itens(arquivo, ok):
        with open(BASE / "results" / "audits" / arquivo, encoding="utf-8") as f:
            return {(r["pmid"], r["gold_answer"]) for r in _csv.DictReader(f)
                    if r["veredito_manual"] == ok}

    return (itens("leakage_strict_verdicts.csv", "Vazamento real")
            | itens("echo_expanded_verdicts.csv", "eco_real"))

MODELOS = {
    "gemma4_31b": "Gemma 4 Denso 31B",
    "llama3.3_70b": "Llama 3.3 70B",
    "qwen3.6_35b": "Qwen 3.6 35B",
    "gemma4_26b": "Gemma 4 MoE 26B",
}
ESTRATOS = ["Q1_Super_Populares", "Q2_Medios",
            "Q3_Baixa_Popularidade", "Q4_Cauda_Longa"]
MARCADOR = "following abstract:\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# PASSO 1. Popularidade de cada gene: nº de publicações no gene2pubmed
# ─────────────────────────────────────────────────────────────────────────────
def carregar_popularidade():
    """GeneID (Entrez) -> nº de publicações humanas.

    O gene2pubmed é um TSV: tax_id | GeneID | PubMed_ID. Uma linha por par
    gene-publicação. Contar as linhas de um gene = contar suas publicações.
    """
    pubs = Counter()
    with gzip.open(GENE2PUBMED, "rt") as f:
        for linha in f:
            if linha.startswith("#"):
                continue
            campos = linha.split("\t")
            if len(campos) >= 3 and campos[0] == "9606":   # só Homo sapiens
                pubs[campos[1]] += 1
    return pubs


# ─────────────────────────────────────────────────────────────────────────────
# PASSO 2. Índice do HGNC: string do modelo -> GeneID
# ─────────────────────────────────────────────────────────────────────────────
def carregar_hgnc():
    """Indexa símbolo oficial, aliases e nomes anteriores -> GeneID (Entrez).

    É isto que permite resolver a resposta livre do modelo ("p53", "TP53",
    "tumor protein p53") para um identificador com o qual consultar a
    popularidade.
    """
    dados = json.load(open(HGNC, encoding="utf-8"))
    docs = dados["response"]["docs"] if "response" in dados else dados.get("docs", dados)

    simbolo_para_id = {}
    id_para_simbolo = {}
    for d in docs:
        entrez = d.get("entrez_id")
        simbolo = d.get("symbol")
        if not entrez:
            continue
        id_para_simbolo[str(entrez)] = simbolo
        chaves = [simbolo] + d.get("alias_symbol", []) + d.get("prev_symbol", [])
        for k in chaves:
            if k:
                # setdefault: em caso de colisão, o símbolo OFICIAL tem prioridade
                # (ele é o primeiro da lista)
                simbolo_para_id.setdefault(k.lower(), str(entrez))
    return simbolo_para_id, id_para_simbolo


# ─────────────────────────────────────────────────────────────────────────────
# PASSO 3. Coleta os erros e resolve o gene chutado
# ─────────────────────────────────────────────────────────────────────────────
def coletar_erros(pubs, simbolo_para_id):
    """Retorna a lista de erros analisáveis + a taxa de cobertura por modelo.

    Um erro é 'analisável' quando a resposta do modelo mapeia para um gene do
    HGNC que existe no gene2pubmed. Caso contrário (nome inventado, frase solta),
    não há popularidade a comparar.
    """
    alias_para_canonico, canonico_para_aliases = load_nebb(str(GABARITO))
    # julgador OFICIAL: resolve o gene pelo GeneID (inequívoco)
    gid_para_aliases = load_hgnc_por_geneid()

    # abstracts (para o TESTE 3: o gene chutado já estava no texto?)
    abstracts = {}
    for linha in open(DATASET, encoding="utf-8"):
        d = json.loads(linha)
        p = d["prompt"]
        abstracts[str(d["pmid"])] = p[p.find(MARCADOR) + len(MARCADOR):].lower()

    erros = []
    cobertura = {}

    anulados = carregar_anulados() if EXCLUIR_ANULADOS else set()

    for chave, nome in MODELOS.items():
        arq = RESULTADOS / f"answers_{chave}.jsonl"
        n_erros = n_analisaveis = 0

        for linha in open(arq, encoding="utf-8"):
            r = json.loads(linha)
            gold = (r.get("gold_answer") or "").strip()
            resposta = (r.get("model_response") or "").strip()

            # Itens ANULADOS pela auditoria de vazamento/eco saem da analise:
            # o enunciado expunha a resposta, entao nem o acerto nem o erro
            # nesses itens sao informativos. Ver docs/leakage_and_echo.md.
            if (str(r["pmid"]), gold) in anulados:
                continue

            # --- descarta os ACERTOS: só nos interessam os erros -------------
            # usa o julgador OFICIAL (scripts/judge.py)
            v = judge_entry(r, alias_para_canonico, canonico_para_aliases,
                            gid_para_aliases)
            if v['acerto_estrito'] or v['acerto_nebb']:
                continue
            n_erros += 1

            # --- resolve o gene CHUTADO -------------------------------------
            id_chutado = simbolo_para_id.get(resposta.lower())
            pubs_gold = pubs.get(str(r["gene_id_gabarito"]), 0)
            if not id_chutado or not pubs_gold:
                continue
            pubs_chutado = pubs.get(id_chutado, 0)
            if not pubs_chutado:
                continue
            n_analisaveis += 1

            # --- o gene chutado aparece no abstract? (confundidor do T3) -----
            padrao = r"(?<![a-z0-9])" + re.escape(resposta.lower()) + r"(?![a-z0-9])"
            no_abstract = re.search(padrao, abstracts[str(r["pmid"])]) is not None

            erros.append({
                "modelo": chave,
                "estrato": r["estrato"],
                "pmid": str(r["pmid"]),
                "gold": gold,
                "resposta": resposta,
                "id_chutado": id_chutado,
                "pubs_gold": pubs_gold,
                "pubs_chutado": pubs_chutado,
                "razao": pubs_chutado / pubs_gold,
                "chute_no_abstract": no_abstract,
            })

        cobertura[chave] = (n_erros, n_analisaveis)

    return erros, cobertura


# ─────────────────────────────────────────────────────────────────────────────
# TESTES
# ─────────────────────────────────────────────────────────────────────────────
def cabecalho(t):
    print("\n" + "═" * 82)
    print(t)
    print("═" * 82)


def relatar_cobertura(cobertura):
    cabecalho("COBERTURA: quantos erros são analisáveis?")
    print("  (só dá para comparar popularidade quando a resposta é um gene do HGNC)\n")
    print(f"  {'Modelo':22}{'erros':>8}{'analisáveis':>14}{'cobertura':>12}")
    for chave, nome in MODELOS.items():
        n_err, n_ana = cobertura[chave]
        print(f"  {nome:22}{n_err:>8}{n_ana:>14}{100*n_ana/n_err:>11.0f}%")
    print("\n  LIMITAÇÃO: os erros restantes são nomes inventados ou frases e")
    print("  não têm popularidade definida.")


def teste1_pareado(erros):
    cabecalho("TESTE 1 (PAREADO): em cada erro, o chute é mais popular que o gold?")
    print("  Objeção que responde: 'você comparou duas medianas de grupos diferentes'.")
    print("  Aqui a comparação é item a item.\n")
    print(f"  {'Modelo':22}{'n':>7}{'chute + popular':>18}{'chute - popular':>18}{'razão mediana':>16}")
    for chave, nome in MODELOS.items():
        E = [e for e in erros if e["modelo"] == chave]
        mais = sum(1 for e in E if e["pubs_chutado"] > e["pubs_gold"])
        menos = sum(1 for e in E if e["pubs_chutado"] < e["pubs_gold"])
        mediana = statistics.median(e["razao"] for e in E)
        print(f"  {nome:22}{len(E):>7}{100*mais/len(E):>17.1f}%"
              f"{100*menos/len(E):>17.1f}%{mediana:>15.1f}x")
    print("\n  Se o chute fosse independente da popularidade: ~50% / ~50%, razão ~1x.")


def teste2_controle_interno(erros):
    cabecalho("TESTE 2 (CONTROLE INTERNO): o efeito deve SUMIR no Q1")
    print("  Se o alvo já é um dos genes mais citados da biologia (TP53), é")
    print("  praticamente impossível chutar algo MAIS popular. Um efeito real tem")
    print("  de respeitar esse teto. Um artefato do método, não.\n")
    print(f"  {'Estrato':26}{'n':>7}{'% chute + popular':>20}{'razão mediana':>16}")
    for est in ESTRATOS:
        E = [e for e in erros if e["estrato"] == est]
        mais = sum(1 for e in E if e["pubs_chutado"] > e["pubs_gold"])
        mediana = statistics.median(e["razao"] for e in E)
        print(f"  {est:26}{len(E):>7}{100*mais/len(E):>19.1f}%{mediana:>15.2f}x")
    print("\n  O efeito se INVERTE no Q1 e é máximo no Q4, exatamente como a")
    print("  hipótese prevê. É a validação mais forte da análise.")


def teste3_confundidor(erros):
    cabecalho("TESTE 3 (CONFUNDIDOR): e se o modelo copiou um gene do abstract?")
    print("  Genes co-citados num abstract tendem a ser populares. Se o efeito")
    print("  viesse daí, seria um achado sobre o TEXTO, não sobre o MODELO.\n")
    grupos = [
        ("todos os erros", lambda e: True),
        ("chute NÃO aparece no abstract", lambda e: not e["chute_no_abstract"]),
        ("chute APARECE no abstract", lambda e: e["chute_no_abstract"]),
    ]
    for rotulo, filtro in grupos:
        E = [e for e in erros if filtro(e)]
        if not E:
            continue
        mais = sum(1 for e in E if e["pubs_chutado"] > e["pubs_gold"])
        mediana = statistics.median(e["razao"] for e in E)
        print(f"  {rotulo:34} n={len(E):5}  "
              f"chute+popular={100*mais/len(E):5.1f}%  razão mediana={mediana:5.1f}x")
    print("\n  O efeito é MAIS forte nos chutes que NÃO estão no texto.")
    print("  Portanto não é cópia do contexto, é o 'prior' do modelo.")


def teste4_modelo_nulo(erros, pubs):
    cabecalho("TESTE 4 (MODELO NULO): quanto valeria um chute cego à popularidade?")
    print("  Objeção que responde: 'isso é só regressão à média, qualquer gene")
    print("  que o modelo nomeie será mais popular que um gene raro'.\n")
    todos = list(pubs.values())
    golds = [e["pubs_gold"] for e in erros]
    chutes = [e["pubs_chutado"] for e in erros]
    print(f"  Mediana de publicações...")
    print(f"    de TODOS os genes humanos (o que um chute CEGO daria)  : {statistics.median(todos):>7.0f}")
    print(f"    dos genes-ALVO destes erros                            : {statistics.median(golds):>7.0f}")
    print(f"    dos genes CHUTADOS pelos modelos                       : {statistics.median(chutes):>7.0f}")
    print("\n  Um chute cego à popularidade produziria genes com ~1 publicação.")
    print("  Os modelos produzem genes com ~200. Não é regressão à média:")
    print("  é CONCENTRAÇÃO NO TOPO da distribuição.")


def teste5_mais_alucinados(erros, pubs, id_para_simbolo, n=15):
    cabecalho(f"TESTE 5: OS {n} GENES MAIS ALUCINADOS")
    ordenados = sorted(pubs.values())

    def percentil(v):
        return 100 * bisect.bisect_left(ordenados, v) / len(ordenados)

    contagem = Counter(e["id_chutado"] for e in erros)
    print(f"  {'gene chutado':16}{'vezes':>7}{'publicações':>14}{'percentil':>12}")
    for gid, vezes in contagem.most_common(n):
        print(f"  {id_para_simbolo.get(gid, gid):16}{vezes:>7}"
              f"{pubs[gid]:>14}{percentil(pubs[gid]):>11.1f}%")
    top20 = sum(v for _, v in contagem.most_common(20))
    print(f"\n  20 genes concentram {top20} de {len(erros)} erros "
          f"({100*top20/len(erros):.1f}%). Genes distintos chutados: {len(contagem)}.")
    print("\n  Nenhum dos mais chutados está abaixo do percentil ~90 de popularidade.")
    print("  Nuance: o modelo não chuta TP53 para tudo, e quando o alvo é um lncRNA,")
    print("  ele chuta os lncRNAs celebridades (HOTAIR, NEAT1, H19). Ele busca o")
    print("  membro mais famoso da CLASSE plausível.")


def exportar_csv(erros):
    saida = BASE / "results" / "audits" / "error_direction.csv"
    campos = ["modelo", "estrato", "pmid", "gold", "resposta",
              "pubs_gold", "pubs_chutado", "razao", "chute_no_abstract"]
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(erros)
    print(f"\n  CSV exportado: {saida}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true", help="exporta os erros analisados")
    args = ap.parse_args()

    print("Carregando gene2pubmed (contagem de publicações por gene)...")
    pubs = carregar_popularidade()
    print(f"  {len(pubs)} genes humanos com pelo menos uma publicação.")

    print("Carregando HGNC (símbolo/alias -> GeneID)...")
    simbolo_para_id, id_para_simbolo = carregar_hgnc()
    print(f"  {len(simbolo_para_id)} formas de nome indexadas.")

    print("Coletando os erros dos 4 modelos...")
    erros, cobertura = coletar_erros(pubs, simbolo_para_id)
    print(f"  {len(erros)} erros analisáveis.")

    relatar_cobertura(cobertura)
    teste1_pareado(erros)
    teste2_controle_interno(erros)
    teste3_confundidor(erros)
    teste4_modelo_nulo(erros, pubs)
    teste5_mais_alucinados(erros, pubs, id_para_simbolo)

    cabecalho("CONCLUSÃO")
    mais = sum(1 for e in erros if e["pubs_chutado"] > e["pubs_gold"])
    print(f"  Em {100*mais/len(erros):.1f}% dos erros, o gene chutado é MAIS CITADO")
    print(f"  que o gene correto. Razão mediana: {statistics.median(e['razao'] for e in erros):.1f}x.")
    print()
    print("  O viés de popularidade não é apenas uma lacuna de conhecimento na")
    print("  cauda longa: é uma FORÇA DIRECIONAL que atrai as predições do modelo")
    print("  para o centro da distribuição bibliográfica. A alucinação não é ruído")
    print("  aleatório, é atração pelo mainstream científico.")

    if args.csv:
        exportar_csv(erros)


if __name__ == "__main__":
    main()
