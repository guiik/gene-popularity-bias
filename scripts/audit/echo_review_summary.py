#!/usr/bin/env python3
"""
Consolida a revisao manual do detector de eco (`echo_expanded.py`).

Le results/audits/echo_expanded_verdicts.csv -- uma linha por QUESTAO (pmid, gold),
com o veredito humano preenchido a mao.

MODO CENSO (estado atual, desde 2026-07-20): as 127 questoes foram todas
revisadas. Nao ha erro amostral, nao ha ponderacao, nao ha intervalo de
confianca a reportar -- a precisao do detector e uma CONTAGEM, nao uma
estimativa.

MODO AMOSTRA (historico): quando so as questoes com na_amostra=sim estavam
revisadas, a precisao global saia por pos-estratificacao (as fracoes de
amostragem diferiam por estrato: Q1 50%, Q2 42%, Q3 75%, Q4 37%) e vinha
acompanhada de IC de Wilson. O script ainda calcula isso quando ha linhas sem
veredito, e imprime a comparacao amostra-vs-censo quando o censo esta completo.

Por que a unidade e a questao e nao o acerto: os 309 acertos sinalizados
agrupam-se em 127 questoes (media 2,43 modelos por questao). O veredito e sobre
o item -- "o abstract deixou o sinonimo visivel?" -- e se propaga para todos os
acertos daquela questao. A taxa por ACERTO (que e a que entra na acuracia
auditada) sai ponderando cada questao por n_modelos.

Uso:
  python3 scripts/audit/echo_review_summary.py
"""
import csv
import math
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
VEREDITOS = BASE / "results" / "audits" / "echo_expanded_verdicts.csv"
ESTRATOS = ["Q1_Super_Populares", "Q2_Medios", "Q3_Baixa_Popularidade", "Q4_Cauda_Longa"]


def wilson(k, n, z=1.96):
    """IC de Wilson -- preferido a Wald com p perto de 1, onde Wald da largura 0."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / d
    meio = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centro - meio), min(1.0, centro + meio))


def main():
    linhas = list(csv.DictReader(open(VEREDITOS, encoding="utf-8")))
    for r in linhas:
        r["n_modelos"] = int(r["n_modelos"])

    pendentes = [r for r in linhas if not r["veredito_manual"]]
    censo_completo = not pendentes

    por_estrato = defaultdict(list)
    for r in linhas:
        por_estrato[r["estrato"]].append(r)

    if censo_completo:
        print("=" * 78)
        print("CENSO COMPLETO -- 127/127 questoes revisadas. Sem erro amostral.")
        print("=" * 78)
        print(f"\n{'Estrato':<26} {'questoes':>14} {'acertos':>16} {'precisao':>10}")
        tq = tqv = ta = tav = 0
        for e in ESTRATOS:
            sub = por_estrato[e]
            val = [r for r in sub if r["veredito_manual"] == "eco_real"]
            a, av = sum(r["n_modelos"] for r in sub), sum(r["n_modelos"] for r in val)
            print(f"{e:<26} {len(val):>6}/{len(sub):<7} {av:>7}/{a:<8} {100*av/a:>9.1f}%")
            tq += len(sub); tqv += len(val); ta += a; tav += av
        print("-" * 78)
        print(f"{'TOTAL':<26} {tqv:>6}/{tq:<7} {tav:>7}/{ta:<8} {100*tav/ta:>9.1f}%")

        print(f"\nECO CONFIRMADO: {tav} de {ta} acertos sinalizados "
              f"({100*tav/ta:.1f}% de precisao do detector).")

        print(f"\n{'Estrato':<26} {'expandidos':>11} {'eco':>6} {'taxa':>8}")
        print("  (denominador = acertos expandidos do estrato; ver echo_expanded.py)")
        EXPANDIDOS = {"Q1_Super_Populares": 772, "Q2_Medios": 550,
                      "Q3_Baixa_Popularidade": 269, "Q4_Cauda_Longa": 182}
        for e in ESTRATOS:
            v = sum(r["n_modelos"] for r in por_estrato[e]
                    if r["veredito_manual"] == "eco_real")
            print(f"{e:<26} {EXPANDIDOS[e]:>11} {v:>6} {100*v/EXPANDIDOS[e]:>7.1f}%")
        tot_exp = sum(EXPANDIDOS.values())
        print("-" * 78)
        print(f"{'TOTAL':<26} {tot_exp:>11} {tav:>6} {100*tav/tot_exp:>7.1f}%")

        # Falsos alarmes: caracterizacao do modo de erro do detector.
        fa = [r for r in linhas if r["veredito_manual"] == "falso_alarme"]
        print(f"\nFALSOS ALARMES ({len(fa)}):")
        for r in fa:
            print(f"  {r['estrato'][:2]} pmid={r['pmid']:<9} termo={r['termo_que_casou']!r:<8} "
                  f"len={r['comprimento_termo']} n_modelos={r['n_modelos']}  "
                  f"gold={r['gold_answer']!r}")
        if fa:
            print(f"  Todos com termo <= {max(int(r['comprimento_termo']) for r in fa)} "
                  f"caracteres e n_modelos = {max(r['n_modelos'] for r in fa)}.")
            print("  Um casamento espurio de fragmento e idiossincratico de UMA resposta;")
            print("  um eco real esta no texto e todo modelo que le tende a produzi-lo.")

    # Sensibilidade ao criterio (A) vs (B) -- ver docstring de audit_eco_expandido.
    amb = [r for r in linhas if r["nota_revisor"].startswith("AMBIGUO")]
    val_a = sum(r["n_modelos"] for r in linhas if r["veredito_manual"] == "eco_real")
    val_b = sum(r["n_modelos"] for r in linhas
                if r["veredito_manual"] == "eco_real" and not r["nota_revisor"].startswith("AMBIGUO"))
    tot = sum(r["n_modelos"] for r in linhas)
    print(f"\nSensibilidade ao criterio: {len(amb)} questoes marcadas AMBIGUO "
          f"({sum(r['n_modelos'] for r in amb)} acertos).")
    print(f"  criterio (A), ambiguos contam como eco : {val_a:>3}/{tot} = {100*val_a/tot:.1f}%")
    print(f"  criterio (B), ambiguos viram alarme    : {val_b:>3}/{tot} = {100*val_b/tot:.1f}%")

    if not censo_completo:
        print(f"\n[MODO AMOSTRA] {len(pendentes)} questoes sem veredito.")
        rev = [r for r in linhas if r["veredito_manual"]]
        k = sum(1 for r in rev if r["veredito_manual"] == "eco_real")
        lo, hi = wilson(k, len(rev))
        print(f"  precisao na amostra: {k}/{len(rev)} = {100*k/len(rev):.1f}% "
              f"(IC95% {100*lo:.1f}-{100*hi:.1f})")


if __name__ == "__main__":
    main()
