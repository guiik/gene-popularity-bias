#!/usr/bin/env python3
"""
Auditoria do vazamento residual do gabarito dentro dos ACERTOS ESTRITOS (rodada v5).

Pergunta: nos casos em que o modelo produziu literalmente o gabarito, quanto disso
pode ter sido COPIADO do proprio abstract, em vez de recuperado da memoria do modelo?

O mascaramento remove exatamente a string que o PubTator anotou -- que e o gold. Logo o
gold em si quase nunca sobrevive no texto. O que sobrevive sao os casos em que o gold
ficou EMBUTIDO em outro token, que o PubTator anota como uma entidade DIFERENTE (uma
mutacao, um miRNA maduro) e portanto nao mascara. Dai os dois niveis de deteccao:

  N1 -- o gold aparece como token inteiro em algum ponto do abstract
        (ex.: gold 'glutaminyl-tRNA Synthetase' visivel no titulo)
  N2 -- o gold e PREFIXO de um token maior
        (ex.: 'KRAS' dentro de 'KRASG12D'; 'miR-524' dentro de 'miR-524-3p')

Sobre o N2: exige inicio de token (?<![a-z0-9]) seguido de alfanumerico. A versao
ingenua usava substring livre e produzia falsos positivos absurdos -- o gold 'RELA'
casando dentro de 'cor-RELA-tes'. Exigir inicio de token elimina essa classe.

IMPORTANTE -- este script SINALIZA, nao decide. A saida e uma lista de candidatos que
precisa de revisao humana caso a caso. Os vereditos estao versionados em
results/audits/leakage_strict_verdicts.csv; o que nao estiver la recebe VEREDITO_PADRAO.

Estado atual (2026-07-20, apos o auditor passar a consumir acerto_estrito do julgador):
o detector sinaliza 51 acertos / 17 itens unicos. A revisao manual rejeitou 3 itens como
falso alarme (gold 'HLA-C' casando com o inicio de 'HLA class I' -- nao e o gene; tres
pmids distintos) e marcou 2 como duvidosos ('parkin' dentro de 'parkinsonism', nome da
doenca; 'HOXB' dentro de 'HOXB6', outro gene do cluster). Sobram 12 itens = 36 acertos
(o mesmo item conta uma vez por modelo que o acertou), ou seja 36/3.865 = 0,9%.

Historico: ate 2026-07-20 o script recalculava o acerto estrito por conta propria e
chegava a 46 acertos / 15 itens sobre uma base de 3.776. A taxa nao mudou (0,9%).

Limitacao conhecida: o detector so acha o gold grafado da mesma forma (modulo a
normalizacao de `judge.normalize`, que cobre letra grega e LaTeX). Variantes
ortograficas nao cobertas -- hifenizacao, espacamento, grafia britanica -- nao sao
sinalizadas e nunca chegam ao revisor. Portanto 0,9% e um LIMITE INFERIOR. O vies
resultante e conservador: subestimar o vazamento no Q4 (onde ele se concentra) apenas
ATENUA o gradiente Q1->Q4 reportado.

Saidas:
  results/audits/leakage_strict_flagged.csv  -- um registro por acerto sinalizado
  stdout                                        -- tabelas por estrato e por modelo

Uso:
  python3 scripts/audit/leakage_strict.py
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "scripts" / "evaluate"))
from judge import contains_token, normalize, normalize_loose  # noqa: E402

DATASET = BASE / "data" / "benchmark_v5.jsonl"
JULGAMENTO_DIR = BASE / "results" / "judgments"
OUT_CSV = BASE / "results" / "audits" / "leakage_strict_flagged.csv"
VEREDITOS_CSV = BASE / "results" / "audits" / "leakage_strict_verdicts.csv"

MARK = "following abstract:\n\n"

VEREDITO_PADRAO = ("Vazamento real",
                   "O gold esta exposto no abstract; o modelo pode te-lo copiado.")


def carregar_vereditos():
    """Vereditos da revisao manual (2026-07-14, ampliada em 2026-07-20).
    Chave: (pmid, gold_answer).

    O CSV lista TODOS os itens sinalizados, um por linha -- inclusive os que sao
    vazamento real. Ate 2026-07-20 ele listava so as excecoes e o resto herdava
    VEREDITO_PADRAO implicitamente, o que tornava impossivel auditar a revisao
    sem reexecutar o script.

    VEREDITO_PADRAO agora so se aplica a item NOVO, que aparece no detector e
    ainda nao foi revisado -- e o script avisa quando isso acontece.
    """
    vereditos = {}
    if not VEREDITOS_CSV.exists():
        return vereditos
    with open(VEREDITOS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            vereditos[(r["pmid"], r["gold_answer"])] = (
                r["veredito_manual"], r["justificativa"])
    return vereditos


def gravar_vereditos(sinalizados, vereditos):
    """Reescreve o CSV de vereditos com TODOS os itens sinalizados.

    Preserva o veredito ja registrado para cada item; itens novos entram com
    VEREDITO_PADRAO e ficam marcados como pendentes de revisao.
    """
    itens = {}
    for s in sinalizados:
        chave = (s["pmid"], s["gold_answer"])
        itens.setdefault(chave, {"acertos": 0, "nivel": s["nivel"],
                                 "trecho": s["trecho_abstract"]})
        itens[chave]["acertos"] += 1

    novos = [c for c in itens if c not in vereditos]
    linhas = []
    for (pmid, gold), info in sorted(itens.items()):
        veredito, justificativa = vereditos.get((pmid, gold), VEREDITO_PADRAO)
        linhas.append({
            "pmid": pmid,
            "gold_answer": gold,
            "nivel": info["nivel"],
            "n_acertos": info["acertos"],
            "veredito_manual": veredito,
            "justificativa": justificativa,
            "revisado": "nao" if (pmid, gold) in novos else "sim",
            "trecho_abstract": info["trecho"],
        })

    with open(VEREDITOS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)

    if novos:
        print(f"\n  [ATENCAO] {len(novos)} item(ns) NOVO(S) sem revisao manual, "
              f"marcados revisado=nao em {VEREDITOS_CSV.name}:")
        for pmid, gold in novos:
            print(f"    {pmid}  {gold!r}")
    return len(novos)

MODELOS = {
    "gemma4_31b": "Gemma 4 Denso 31B",
    "gemma4_26b": "Gemma 4 MoE 26B",
    "qwen3.6_35b": "Qwen 3.6 35B",
    "llama3.3_70b": "Llama 3.3 70B",
}

ORDEM = [
    "Q1_Super_Populares",
    "Q2_Medios",
    "Q3_Baixa_Popularidade",
    "Q4_Cauda_Longa",
]


def carregar_abstracts():
    """pmid -> corpo do abstract (ja mascarado), sem o cabecalho de instrucao."""
    abstracts = {}
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            p = d["prompt"]
            abstracts[str(d["pmid"])] = p[p.find(MARK) + len(MARK):]
    return abstracts


def exposicao(gold, body):
    """Retorna 'N1', 'N2' ou None -- o nivel em que o gold esta exposto no abstract."""
    g, b = normalize(gold), normalize(body)
    gl, bl = normalize_loose(gold), normalize_loose(body)
    if not gl or len(gl) < 2:
        return None
    if contains_token(b, g) or contains_token(bl, gl):
        return "N1"
    # N2: inicio de token seguido de alfanumerico. Exclui 'rela' em 'correlates'.
    if len(gl) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(gl) + r"(?=[a-z0-9])", bl):
        return "N2"
    return None


def trecho(gold, body, janela=55):
    """Contexto ao redor da ocorrencia, para o revisor humano julgar."""
    bl, gl = normalize_loose(body), normalize_loose(gold)
    i = bl.find(gl)
    if i < 0:
        return ""
    return bl[max(0, i - janela):i + len(gl) + janela]


def main():
    abstracts = carregar_abstracts()
    VEREDITOS = carregar_vereditos()
    por_estrato = defaultdict(Counter)
    por_modelo = defaultdict(Counter)
    sinalizados = []

    for chave, nome in MODELOS.items():
        caminho = JULGAMENTO_DIR / f"judgment_{chave}.jsonl"
        with open(caminho, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                gold = (r.get("gold_answer") or "").strip()
                resp = (r.get("resposta_modelo") or "").strip()
                # Fonte unica da verdade: o veredito de acerto estrito vem do
                # julgador oficial (judge.py), NAO e recalculado aqui.
                #
                # Ate 2026-07-20 este script recalculava com contains_token e
                # chegava a 3.776 acertos, contra 3.865 do julgador. Os 89 de
                # diferenca eram inteiramente NORMALIZACAO -- o julgador
                # converte letra grega e LaTeX, o contains_token cru nao:
                # hCGbeta/hCGbeta(unicode), IRE1alpha/IRE1$\alpha$, Wnt3A./Wnt3a,
                # p120 catenin/p120-catenin. Esses 89 acertos nunca eram
                # varridos em busca de vazamento.
                if not r.get("acerto_estrito"):
                    continue

                estrato = r["estrato"]
                pmid = str(r["pmid"])
                body = abstracts.get(pmid, "")
                por_estrato[estrato]["tot"] += 1
                por_modelo[chave]["tot"] += 1

                nivel = exposicao(gold, body)
                if not nivel:
                    continue

                por_estrato[estrato][nivel] += 1
                por_estrato[estrato]["vaz"] += 1
                por_modelo[chave][nivel] += 1
                por_modelo[chave]["vaz"] += 1
                veredito, justificativa = VEREDITOS.get((pmid, gold), VEREDITO_PADRAO)
                sinalizados.append({
                    "modelo": nome,
                    "pmid": pmid,
                    "estrato": estrato,
                    "nivel": nivel,
                    "gold_answer": gold,
                    "resposta_modelo": resp[:120],
                    "trecho_abstract": trecho(gold, body),
                    "veredito_manual": veredito,
                    "justificativa": justificativa,
                })

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sinalizados[0].keys()))
        w.writeheader()
        w.writerows(sinalizados)

    gravar_vereditos(sinalizados, VEREDITOS)

    itens = {(s["pmid"], s["gold_answer"]) for s in sinalizados}

    print("=" * 94)
    print("VAZAMENTO NOS ACERTOS ESTRITOS (detector refinado)")
    print("  N1 = gold aparece como token inteiro no abstract")
    print("  N2 = gold e prefixo de um token maior (KRAS->KRASG12D, miR-524->miR-524-3p)")
    print("=" * 94)
    print(f"{'Estrato':26}{'estritos':>9}{'N1':>13}{'N2':>13}{'TOTAL sinal.':>14}")
    print("-" * 94)
    T = Counter()
    for e in ORDEM:
        d = por_estrato[e]
        T.update(d)
        print(f"{e:26}{d['tot']:>9}{d['N1']:>6} {100*d['N1']/d['tot']:5.1f}%"
              f"{d['N2']:>6} {100*d['N2']/d['tot']:5.1f}%"
              f"{d['vaz']:>7} {100*d['vaz']/d['tot']:5.1f}%")
    print("-" * 94)
    print(f"{'TOTAL':26}{T['tot']:>9}{T['N1']:>6} {100*T['N1']/T['tot']:5.1f}%"
          f"{T['N2']:>6} {100*T['N2']/T['tot']:5.1f}%"
          f"{T['vaz']:>7} {100*T['vaz']/T['tot']:5.1f}%")

    print(f"\n{'Modelo':22}{'estritos':>9}{'sinalizados':>13}")
    print("-" * 45)
    for chave, nome in MODELOS.items():
        d = por_modelo[chave]
        print(f"{nome:22}{d['tot']:>9}{d['vaz']:>8} {100*d['vaz']/d['tot']:5.1f}%")

    print(f"\n{len(sinalizados)} acertos sinalizados, correspondendo a {len(itens)} itens "
          f"unicos (pmid, gold).")

    print("\n" + "=" * 94)
    print("APOS A REVISAO MANUAL (vereditos em leakage_strict_verdicts.csv)")
    print("=" * 94)
    vd = Counter(s["veredito_manual"] for s in sinalizados)
    itens_por_veredito = defaultdict(set)
    for s in sinalizados:
        itens_por_veredito[s["veredito_manual"]].add((s["pmid"], s["gold_answer"]))
    for v, n in vd.most_common():
        print(f"  {v:18}{n:>4} acertos   ({len(itens_por_veredito[v])} itens)")

    confirmados = vd["Vazamento real"]
    print("-" * 94)
    print(f"  VAZAMENTO CONFIRMADO: {confirmados} de {T['tot']} acertos estritos "
          f"= {100*confirmados/T['tot']:.1f}%")
    print(f"  (itens 'Falso alarme' e 'Duvidoso' sao DESCARTADOS -- criterio conservador:\n"
          f"   na duvida, nao se acusa vazamento, o que subestima o desconto.)")
    print(f"\nCSV com vereditos e contexto: {OUT_CSV.relative_to(BASE)}")


if __name__ == "__main__":
    main()
