#!/usr/bin/env python3
"""
Acuracia BRUTA e AUDITADA da rodada v5, por modelo e por estrato.

  bruta    = (acertos estritos + acertos expandidos) / casos
  auditada = bruta MENOS os acertos que o proprio texto entregou

Dois descontos, de auditorias independentes, ambas em CENSO (100% dos itens
sinalizados revisados a mao -- nao ha estimativa nem extrapolacao aqui):

  vazamento estrito  36 acertos  -- results/audits/leakage_strict_verdicts.csv
                                    (veredito "Vazamento real")
  eco expandido     306 acertos  -- results/audits/echo_expanded_verdicts.csv
                                    (veredito "eco_real")

Criterio conservador nos dois: itens marcados "Falso alarme"/"falso_alarme" ou
"Duvidoso" NAO sao descontados. Na duvida, nao se acusa vazamento -- o que
SUBESTIMA o desconto e portanto atenua o gradiente reportado.

DUAS CONVENCOES DE DESCONTO -- o script calcula as duas.

  ESTRITA (--convencao estrita)
      Desconta so os acertos cuja STRING casou no texto (os sinalizados).
      306 acertos de eco.

  SEMANTICA (--convencao semantica, PADRAO)
      Desconta TODO acerto expandido de uma questao cujo item foi confirmado
      como expositor, independentemente da grafia que o modelo emitiu.
      344 acertos de eco.

Por que a SEMANTICA e a correta: o casamento de string e um PROXY para "a
resposta estava disponivel no texto", e o proxy tem falso negativo quando o
modelo escreve a mesma entidade com outra grafia. Inspecao dos 37 acertos em
disputa (2026-07-20) nao encontrou UM SO caso de recuperacao independente --
todos sao traducao trivial do termo exposto:

    texto expoe VGLUT2        -> modelo responde SLC17A6  (simbolo oficial)
    texto expoe cyclophilin A -> modelo responde PPIA     (simbolo oficial)
    texto expoe BHD           -> modelo responde FLCN     (simbolo oficial)
    texto expoe REST          -> modelo responde "RE1-silencing transcription
                                 factor"                  (sigla expandida)
    texto expoe miRNA-575     -> modelo responde miR-575  (convencao de prefixo)

Traduzir VGLUT2 para SLC17A6 EXIGE ter lido VGLUT2. Nao e memoria parametrica
do gene-alvo; e conhecimento de nomenclatura aplicado a um termo que estava na
tela. A pergunta da auditoria e se o modelo PODIA ter copiado, nao se copiou
literalmente.

No vazamento estrito as duas convencoes coincidem (36 acertos): o que vaza e o
proprio gabarito, identico para todos os modelos, entao todo acerto estrito no
item e sinalizado.

Uso:
  python3 scripts/evaluate/audited_accuracy.py
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
JULG = BASE / "results" / "judgments"

MODELOS = {
    "llama3.3_70b": "Llama 3.3 70B",
    "gemma4_31b": "Gemma 4 Denso 31B",
    "qwen3.6_35b": "Qwen 3.6 35B",
    "gemma4_26b": "Gemma 4 MoE 26B",
}
ESTRATOS = ["Q1_Super_Populares", "Q2_Medios", "Q3_Baixa_Popularidade", "Q4_Cauda_Longa"]
ROTULO = {"Q1_Super_Populares": "Q1", "Q2_Medios": "Q2",
          "Q3_Baixa_Popularidade": "Q3", "Q4_Cauda_Longa": "Q4"}


NOME_PARA_CHAVE = {v: k for k, v in {
    "gemma4_31b": "Gemma 4 Denso 31B", "gemma4_26b": "Gemma 4 MoE 26B",
    "qwen3.6_35b": "Qwen 3.6 35B", "llama3.3_70b": "Llama 3.3 70B",
}.items()}


def carregar_descontos(convencao):
    """(modelo, pmid, gold) dos acertos a descontar.

    estrita   -> so os acertos cuja string casou (linhas de *_sinalizados.csv)
    semantica -> todo acerto expandido de questao confirmada como expositora
    """
    def itens(caminho, ok):
        with open(BASE / "results" / "audits" / caminho, encoding="utf-8") as f:
            return {(r["pmid"], r["gold_answer"]) for r in csv.DictReader(f)
                    if r["veredito_manual"] == ok}

    itens_vaz = itens("leakage_strict_verdicts.csv", "Vazamento real")
    itens_eco = itens("echo_expanded_verdicts.csv", "eco_real")

    vaz, eco = set(), set()
    with open(BASE / "results" / "audits" / "leakage_strict_flagged.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r["pmid"], r["gold_answer"]) in itens_vaz:
                vaz.add((NOME_PARA_CHAVE[r["modelo"]], r["pmid"], r["gold_answer"]))
    if convencao == "estrita":
        with open(BASE / "results" / "audits" / "echo_expanded_flagged.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r["pmid"], r["gold_answer"]) in itens_eco:
                    eco.add((r["modelo"], r["pmid"], r["gold_answer"]))
    else:
        # semantica: qualquer acerto expandido na questao confirmada conta
        for chave in MODELOS:
            with open(JULG / f"judgment_{chave}.jsonl", encoding="utf-8") as f:
                for linha in f:
                    if not linha.strip():
                        continue
                    r = json.loads(linha)
                    k = (str(r["pmid"]), r["gold_answer"])
                    if r.get("acerto_nebb") and k in itens_eco:
                        eco.add((chave, k[0], k[1]))
    return vaz, eco


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convencao", choices=["semantica", "estrita"], default="semantica",
                    help="semantica (padrao): desconta todo acerto expandido de questao "
                         "expositora. estrita: so os acertos cuja string casou no texto.")
    ap.add_argument("--escopo", choices=["numerador", "ambos"], default="ambos",
                    help="numerador: o acerto contaminado vira ERRO (item fica no "
                         "denominador). ambos (padrao): o item contaminado e REMOVIDO do "
                         "benchmark, para todos os modelos -- pergunta invalida, nao errada.")
    args = ap.parse_args()

    vaz, eco = carregar_descontos(args.convencao)
    print(f"Convencao de desconto: {args.convencao.upper()} | escopo: {args.escopo.upper()}")
    print(f"Acertos a descontar: {len(vaz)} vazamento estrito, {len(eco)} eco expandido.")

    # Itens contaminados -- usados so no escopo "ambos". Um item cujo abstract
    # expoe a resposta e uma pergunta DEFEITUOSA: ela e invalida para TODOS os
    # modelos, inclusive os que erraram. Manter no denominador contabilizaria
    # como "erro" o fracasso numa questao que nao deveria existir.
    contaminados = {(p, g) for _, p, g in vaz} | {(p, g) for _, p, g in eco}
    if args.escopo == "ambos":
        print(f"Itens removidos do benchmark: {len(contaminados)} "
              f"(pergunta invalida, sai para os 4 modelos).")
    print()

    # dados[modelo][estrato] = Counter(casos, estrito, nebb, desc_vaz, desc_eco)
    dados = defaultdict(lambda: defaultdict(Counter))
    for chave in MODELOS:
        with open(JULG / f"judgment_{chave}.jsonl", encoding="utf-8") as f:
            for linha in f:
                if not linha.strip():
                    continue
                r = json.loads(linha)
                item = (str(r["pmid"]), r["gold_answer"])
                k = (chave, item[0], item[1])
                d = dados[chave][r["estrato"]]
                acerto = bool(r.get("acerto_estrito") or r.get("acerto_nebb"))

                # universo COMPLETO -- base da acuracia bruta, sempre 4.000
                d["casos"] += 1
                d["acertos"] += acerto
                if r.get("acerto_estrito") and k in vaz:
                    d["desc_vaz"] += 1
                elif r.get("acerto_nebb") and k in eco:
                    d["desc_eco"] += 1

                # universo ANULADO -- exclui os itens contaminados
                if item in contaminados:
                    d["removidos"] += 1
                else:
                    d["casos_val"] += 1
                    d["acertos_val"] += acerto

    # Conferencia: os descontos aplicados batem com o censo?
    tv = sum(dados[m][e]["desc_vaz"] for m in MODELOS for e in ESTRATOS)
    te = sum(dados[m][e]["desc_eco"] for m in MODELOS for e in ESTRATOS)
    if args.escopo == "numerador":
        esperado = {"estrita": (36, 306), "semantica": (36, 344)}[args.convencao]
        print(f"Descontos aplicados: {tv} vazamento estrito, {te} eco expandido.")
        if (tv, te) != esperado:
            raise SystemExit(f"  [ERRO] esperado {esperado} -- divergencia na juncao!")
    else:
        rem = sum(dados[m][e]["removidos"] for m in MODELOS for e in ESTRATOS)
        print(f"Casos removidos do denominador: {rem} "
              f"({rem // len(MODELOS)} por modelo).")
        if rem != len(contaminados) * len(MODELOS):
            raise SystemExit(f"  [ERRO] esperado {len(contaminados)*len(MODELOS)} removidos!")
    print()

    def tabela(titulo, modo):
        print("=" * 78)
        print(titulo)
        print("=" * 78)
        print(f"{'Modelo':<22}" + "".join(f"{ROTULO[e]:>10}" for e in ESTRATOS) + f"{'Global':>11}")
        print("-" * 78)
        linhas = []
        for chave, nome in MODELOS.items():
            vals, tot_a, tot_c = [], 0, 0
            for e in ESTRATOS:
                d = dados[chave][e]
                if modo == "bruta":
                    a, c = d["acertos"], d["casos"]
                elif modo == "numerador":
                    a, c = d["acertos"] - d["desc_vaz"] - d["desc_eco"], d["casos"]
                else:  # ambos -- item anulado sai do numerador E do denominador
                    a, c = d["acertos_val"], d["casos_val"]
                vals.append(100 * a / c)
                tot_a += a
                tot_c += c
            g = 100 * tot_a / tot_c
            linhas.append((nome, vals, g))
            print(f"{nome:<22}" + "".join(f"{v:>9.1f}%" for v in vals) + f"{g:>10.1f}%")
        return linhas

    brutas = tabela("ACURACIA BRUTA (4.000 casos, sem desconto)", "bruta")
    print()
    if args.escopo == "ambos":
        n_val = sum(dados["llama3.3_70b"][e]["casos_val"] for e in ESTRATOS)
        titulo = (f"ACURACIA AUDITADA ({n_val} casos -- {len(contaminados)} itens "
                  f"contaminados ANULADOS)")
    else:
        titulo = "ACURACIA AUDITADA (4.000 casos -- acerto contaminado vira ERRO)"
    auditadas = tabela(titulo, args.escopo)

    print()
    print("=" * 78)
    print("GRADIENTE Q1 -> Q4")
    print("=" * 78)
    print(f"{'Modelo':<22}{'bruto':>12}{'auditado':>12}{'variacao':>12}{'relativa':>11}")
    print("-" * 78)
    for (nome, vb, _), (_, va, _) in zip(brutas, auditadas):
        qb, qa = vb[0] - vb[3], va[0] - va[3]
        print(f"{nome:<22}{qb:>10.1f}pp{qa:>10.1f}pp{qa-qb:>+10.1f}pp"
              f"{-100*(va[3]-va[0])/va[0]:>10.1f}%")

    print()
    print("=" * 78)
    print("EFEITO DA AUDITORIA, POR ESTRATO (media dos 4 modelos)")
    print("=" * 78)
    print(f"{'Estrato':<26}{'bruta':>10}{'auditada':>11}{'queda':>10}")
    print("-" * 78)
    for i, e in enumerate(ESTRATOS):
        b = sum(v[i] for _, v, _ in brutas) / 4
        a = sum(v[i] for _, v, _ in auditadas) / 4
        print(f"{e:<26}{b:>9.1f}%{a:>10.1f}%{a-b:>+9.2f}pp")


if __name__ == "__main__":
    main()
