#!/usr/bin/env python3
"""
Auditoria do ECO nos ACERTOS EXPANDIDOS (via NEBB) -- rodada v5.

=============================================================================
O QUE E "ECO"
=============================================================================

Um acerto expandido acontece quando o modelo NAO produziu o gabarito literal,
mas produziu um SINONIMO que o NEBB reconhece como o mesmo gene canonico. O
credito e concedido pelo julgador (`judge.py`) com `acerto_nebb=True`,
e o sinonimo que justificou o credito fica registrado em `alias_nebb`.

Chamamos de ECO o caso em que esse sinonimo -- ou a resposta verbatim do modelo
-- esta VISIVEL, SEM MASCARA, no proprio texto que o modelo leu.

    ECO != vazamento do gabarito.

    O mascaramento remove exatamente a string que o PubTator anotou, que e o
    gold. Entao o gold em si quase nunca sobrevive no texto (0,9% -- e isso e
    o que `leakage_strict.py` mede). O que sobrevive sao as OUTRAS
    formas de nomear o mesmo gene, que o PubTator nao anotou e portanto o
    mascaramento nao cobriu.

Mecanismo, em uma frase: o PubTator anotou UMA forma de nomear o gene (virou o
gold, foi mascarada) e deixou OUTRA forma visivel; o modelo copia a visivel; o
NEBB credita como sinonimo valido. O acerto nao mede memoria parametrica --
mede leitura.

Casos reais (verificados manualmente em 2026-07-20):

  gold `mir-4638` (mascarado no corpo como "[MASK]-3p")
  resposta `MicroRNA-4638-3p`
  titulo: "...functional role of MicroRNA-4638-3p in breast cancer bone
  metastasis" <- a resposta esta no titulo, sem mascara

  gold `Sortilin` (mascarado em todas as ocorrencias)
  resposta `NTSR2/gp95`
  texto: "The identification of gp95[MASK], a sorting protein..."
  <- o alias gp95 ficou visivel colado na mascara

  gold `cytochrome P450 (CYP) 2C9 and 2C19`
  resposta `CYP2C9`
  texto contem "CYP2C9" literal, sem mascara

=============================================================================
A REGRA DE DETECCAO, EXATAMENTE
=============================================================================

Para cada julgamento com `acerto_nebb=True`:

1. TERMOS BUSCADOS (needles) -- duas, e basta UMA casar:
     (a) `alias_nebb`     -- o alias que o julgador usou para conceder o credito.
                             E a razao formal do acerto.
     (b) `resposta_modelo` -- o que o modelo escreveu, verbatim.
   Por que as duas: `alias_nebb` vem em forma canonica/normalizada e nem sempre
   e a grafia que aparece no texto; a resposta e a grafia real do modelo. Se
   qualquer uma esta visivel, a copia era possivel.
   Medido: 278 flags casam pelas duas, 26 so pelo alias, 6 so pela resposta.

2. TEXTO (haystack) -- o campo `prompt` do resultado bruto, que e
   literalmente o texto que o modelo leu, MENOS a linha de instrucao (tudo ate
   e incluindo "in the following abstract:"). Remover o cabecalho e por
   robustez: um alias como "gene" ou "name" casaria na instrucao. Medido: 0 dos
   309 flags atuais vem do cabecalho, entao a remocao nao altera o numero hoje.

   NAO se usa `abstract_trecho` do arquivo de julgamento -- ele e TRUNCADO em
   ~200 caracteres e perderia a maior parte dos ecos.

3. NORMALIZACAO -- `judge.normalize()` nos dois lados (termo e
   texto): letra grega -> nome por extenso, comandos LaTeX, minusculas.
   Usar a MESMA normalizacao do julgador e deliberado: o eco tem de ser medido
   no mesmo espaco em que o credito foi concedido.

4. CASAMENTO -- token inteiro:

       (?<![a-z0-9]) <termo escapado> (?![a-z0-9])

   Sem a fronteira, `RELA` casaria dentro de "cor-RELA-tes" -- foi exatamente
   o bug do detector ingenuo da v2. Note que hifen E fronteira: por isso o
   alias `CYP` nao casa dentro de "CYP2C9", mas a resposta `CYP2C9` casa.

5. COMPRIMENTO MINIMO -- termos com menos de 2 caracteres sao ignorados.

=============================================================================
CRITERIO DA REVISAO MANUAL (decidido em 2026-07-20)
=============================================================================

O revisor responde: "A STRING ESTAVA DISPONIVEL PARA COPIA?" -- e nao "a string
visivel se referia ao gene-alvo?".

E o criterio CONSERVADOR. Se o token estava no texto sem mascara, nao ha como
provar que a resposta veio da memoria parametrica do modelo em vez da leitura,
entao o acerto nao e creditado. O argumento e que a acuracia
auditada nao deve creditar acertos que o proprio texto entregou.

Caso limpo (eco_real): gold `androgen receptor`, resposta `AR`, texto
"identification of gelsolin (GSN) as an AR-associated protein" -- a sigla do
gene-alvo esta visivel.

Caso ambiguo: gold `interleukin (IL)1alpha`, resposta `IL1`, texto
"...[MASK](-889), il1beta(-511), il1 receptor agonist (RA)...". O token `il1`
esta visivel, mas ali ele e parte de IL1RN -- OUTRO gene. Pelo criterio adotado
conta como eco_real (a string estava la), mas vai marcado em `nota_revisor` com
o prefixo "AMBIGUO:" para que a taxa possa ser recalculada sem esses casos e se
mostre que a conclusao nao depende da escolha do criterio.

=============================================================================
LIMITACAO CONHECIDA -- ESTE SCRIPT SINALIZA, NAO DECIDE
=============================================================================

A saida e uma lista de CANDIDATOS que precisa de revisao humana. O modo de
falso alarme conhecido e o TERMO CURTO: 96 dos 309 flags (31%) casam por um
termo de <=3 caracteres, e em texto biomedico esses colidem com vocabulario
comum -- gold `LINC01546` / resposta `VAL`: "Val" no abstract e quase
certamente VALINA, o aminoacido, e nao o gene. Idem `C2`, `C3`, `AR`.
Por isso o relatorio quebra a precisao por comprimento do termo.

No sentido oposto (falso NEGATIVO): o detector so acha o termo grafado da
mesma forma. Variantes ortograficas nao cobertas pela normalizacao --
hifenizacao, espacamento, grafia britanica -- nao sao sinalizadas e nunca
chegam ao revisor. Portanto a taxa medida e um LIMITE INFERIOR.

=============================================================================
SAIDAS -- DOIS ARQUIVOS, E SO
=============================================================================

  results/audits/echo_expanded_flagged.csv
      Um registro por ACERTO sinalizado (309). Saida bruta do detector,
      regerada a cada execucao. Nunca editar a mao.

  results/audits/echo_expanded_verdicts.csv
      Um registro por QUESTAO (127) -- a populacao inteira. Coluna
      `na_amostra` marca as 60 sorteadas; `veredito_manual` e preenchido a
      mao. Reexecutar o script PRESERVA os vereditos ja escritos e avisa se
      alguma questao da amostra ficou sem veredito.
      Para virar censo, basta revisar as linhas com na_amostra=nao tambem.

O mesmo par existe para o vazamento estrito (`*_sinalizados.csv` /
`*_vereditos.csv`), com a mesma semantica.

Uso:
  python3 scripts/audit/echo_expanded.py [--n-amostra 60] [--seed 31]
"""
import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "scripts" / "evaluate"))

from judge import normalize  # noqa: E402

MODELOS = ["gemma4_26b", "gemma4_31b", "llama3.3_70b", "qwen3.6_35b"]
VEREDITOS_CSV = BASE / "results" / "audits" / "echo_expanded_verdicts.csv"
ESTRATOS = ["Q1_Super_Populares", "Q2_Medios", "Q3_Baixa_Popularidade", "Q4_Cauda_Longa"]

FIM_DO_CABECALHO = "in the following abstract:"
CONTEXTO = 60  # caracteres de cada lado do casamento, para a revisao manual


def corpo_do_prompt(prompt: str) -> str:
    """Descarta a linha de instrucao, devolve so o abstract que o modelo leu."""
    if FIM_DO_CABECALHO in prompt:
        return prompt.split(FIM_DO_CABECALHO, 1)[1]
    return prompt


def casa_token(termo: str, texto_norm: str):
    """Casa o termo como token inteiro. Devolve o match ou None."""
    a = normalize(termo).strip()
    if len(a) < 2:
        return None
    return re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", texto_norm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-amostra", type=int, default=60,
                    help="tamanho total da amostra para revisao manual")
    ap.add_argument("--seed", type=int, default=31)
    args = ap.parse_args()

    sinalizados = []
    expandidos_por_estrato = Counter()
    expandidos_por_modelo = Counter()

    for modelo in MODELOS:
        bruto = BASE / "results" / "answers" / f"answers_{modelo}.jsonl"
        julg = BASE / "results" / "judgments" / f"judgment_{modelo}.jsonl"

        prompts = {}
        with open(bruto, encoding="utf-8") as f:
            for linha in f:
                r = json.loads(linha)
                prompts[(r["pmid"], r["gold_answer"])] = r["prompt"]

        with open(julg, encoding="utf-8") as f:
            for linha in f:
                r = json.loads(linha)
                if not r.get("acerto_nebb"):
                    continue

                estrato = r["estrato"]
                expandidos_por_estrato[estrato] += 1
                expandidos_por_modelo[modelo] += 1

                prompt = prompts.get((r["pmid"], r["gold_answer"]))
                if prompt is None:
                    print(f"[AVISO] sem prompt: {modelo} {r['pmid']} {r['gold_answer']}")
                    continue

                corpo = normalize(corpo_do_prompt(prompt))

                alias = r.get("alias_nebb") or ""
                resposta = r.get("resposta_modelo") or ""
                m_alias = casa_token(alias, corpo)
                m_resp = casa_token(resposta, corpo)
                if not (m_alias or m_resp):
                    continue

                # O termo reportado e o que efetivamente casou; se os dois
                # casaram, reporta o mais curto (o de maior risco de falso alarme).
                candidatos = []
                if m_alias:
                    candidatos.append((len(normalize(alias).strip()), "alias_nebb", alias, m_alias))
                if m_resp:
                    candidatos.append((len(normalize(resposta).strip()), "resposta", resposta, m_resp))
                comprimento, origem, termo, match = min(candidatos, key=lambda t: t[0])

                ini = max(0, match.start() - CONTEXTO)
                fim = min(len(corpo), match.end() + CONTEXTO)

                sinalizados.append({
                    "modelo": modelo,
                    "estrato": estrato,
                    "pmid": r["pmid"],
                    "gold_answer": r["gold_answer"],
                    "resposta_modelo": resposta,
                    "alias_nebb": alias,
                    "termo_que_casou": termo,
                    "origem_termo": origem,
                    "comprimento_termo": comprimento,
                    "casou_por_alias": bool(m_alias),
                    "casou_por_resposta": bool(m_resp),
                    "contexto": "..." + corpo[ini:fim].replace("\n", " ") + "...",
                    "veredito_manual": "",      # PREENCHER: eco_real | falso_alarme | duvidoso
                    "nota_revisor": "",
                })

    total_exp = sum(expandidos_por_estrato.values())
    total_eco = len(sinalizados)

    print(f"\nAcertos expandidos (acerto_nebb=True): {total_exp}")
    print(f"Sinalizados como eco:                  {total_eco} ({100*total_eco/total_exp:.1f}%)\n")

    print(f"{'Estrato':<26} {'Expandidos':>10} {'Eco':>6} {'Taxa':>8}")
    eco_por_estrato = Counter(s["estrato"] for s in sinalizados)
    for e in ESTRATOS:
        n, k = expandidos_por_estrato[e], eco_por_estrato[e]
        print(f"{e:<26} {n:>10} {k:>6} {100*k/max(n,1):>7.1f}%")

    print(f"\n{'Modelo':<26} {'Expandidos':>10} {'Eco':>6} {'Taxa':>8}")
    eco_por_modelo = Counter(s["modelo"] for s in sinalizados)
    for m in MODELOS:
        n, k = expandidos_por_modelo[m], eco_por_modelo[m]
        print(f"{m:<26} {n:>10} {k:>6} {100*k/max(n,1):>7.1f}%")

    # Corte por comprimento do termo -- declarado ANTES da revisao manual,
    # porque e o modo de falso alarme conhecido (ver docstring).
    print(f"\n{'Comprimento do termo':<26} {'Sinalizados':>12}")
    faixas = [("<=3 (risco alto)", lambda c: c <= 3),
              ("4-6", lambda c: 4 <= c <= 6),
              (">=7", lambda c: c >= 7)]
    for rotulo, teste in faixas:
        k = sum(1 for s in sinalizados if teste(s["comprimento_termo"]))
        print(f"{rotulo:<26} {k:>12} ({100*k/max(total_eco,1):.1f}%)")

    itens_unicos = {(s["pmid"], s["gold_answer"], s["termo_que_casou"]) for s in sinalizados}
    print(f"\nItens unicos (pmid, gold, termo): {len(itens_unicos)} "
          f"-- o mesmo abstract e sinalizado uma vez por modelo que o acertou.")

    campos = list(sinalizados[0].keys())
    saida = BASE / "results" / "audits" / "echo_expanded_flagged.csv"
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(sinalizados)
    print(f"\nSinalizados -> {saida.relative_to(BASE)}")

    # ── Unidade de revisao: a QUESTAO, nao o acerto ──────────────────────────
    #
    # Os 309 acertos sinalizados NAO sao independentes: agrupam-se em questoes
    # (pmid, gold), com media de 2,43 modelos por questao -- 31 questoes foram
    # ecoadas pelos QUATRO modelos e sozinhas respondem por 124 dos 309 acertos.
    #
    # Isso e esperado: se um sinonimo do gene-alvo esta visivel no texto, todo
    # modelo que le o texto tende a produzi-lo. O eco e propriedade do ITEM (de
    # como o abstract foi escrito e do que o PubTator deixou de anotar), nao do
    # modelo. O veredito manual tambem e sobre o item -- "o abstract deixou o
    # sinonimo visivel?" -- e portanto se PROPAGA para todos os acertos da
    # questao.
    #
    # Consequencia estatistica: amostrar ACERTOS trataria observacoes
    # correlacionadas como independentes e produziria um IC otimista (o efeito
    # de desenho com cluster medio 2,43 e correlacao intra-classe alta inflaria
    # a variancia real de ~+40%). Por isso a amostragem e por QUESTAO, e a taxa
    # por acerto sai depois ponderando cada questao pelo numero de modelos que
    # a acertaram.
    questoes = defaultdict(list)
    for s in sinalizados:
        questoes[(s["pmid"], s["gold_answer"])].append(s)

    print(f"\nQuestoes distintas sinalizadas: {len(questoes)} "
          f"(media de {len(sinalizados)/len(questoes):.2f} modelos por questao)")
    d = Counter(len(v) for v in questoes.values())
    for k in sorted(d):
        print(f"  ecoada por {k} modelo(s): {d[k]:>3} questoes -> {d[k]*k:>3} acertos")

    # Uma linha por questao, com os modelos agregados.
    def linha_questao(acertos):
        base = min(acertos, key=lambda s: s["comprimento_termo"])
        return {
            "pmid": base["pmid"],
            "estrato": base["estrato"],
            "gold_answer": base["gold_answer"],
            "n_modelos": len(acertos),
            "modelos": ";".join(sorted(s["modelo"] for s in acertos)),
            "respostas": " | ".join(sorted({s["resposta_modelo"] for s in acertos})),
            "termo_que_casou": base["termo_que_casou"],
            "comprimento_termo": base["comprimento_termo"],
            "contexto": base["contexto"],
            "veredito_manual": "",   # PREENCHER: eco_real | falso_alarme | duvidoso
            "nota_revisor": "",
        }

    campos_q = list(linha_questao(next(iter(questoes.values()))).keys())

    por_estrato = defaultdict(list)
    for chave, acertos in questoes.items():
        por_estrato[acertos[0]["estrato"]].append(linha_questao(acertos))

    # Amostra estratificada, alocacao IGUAL por estrato (nao proporcional): a
    # suspeita e que a precisao do detector VARIA por estrato, e o Q3/Q4 teriam
    # poucos itens numa amostra proporcional. A taxa global sai por
    # pos-estratificacao, ponderada pelos tamanhos reais de cada estrato.
    rng = random.Random(args.seed)
    cota = args.n_amostra // len(ESTRATOS)
    sorteados = set()
    print(f"\nAmostra por questao (seed={args.seed}, cota={cota} por estrato):")
    for e in ESTRATOS:
        # ordena antes de sortear -> reprodutivel independente da ordem de leitura
        pool = sorted(por_estrato[e], key=lambda r: (r["pmid"], r["gold_answer"]))
        k = min(cota, len(pool))
        if k < cota:
            print(f"  [AVISO] {e}: so ha {len(pool)} questoes, cota era {cota}")
        print(f"  {e:<26} {k:>3} de {len(pool):>3}  (fracao {k/len(pool):.0%})")
        sorteados.update(r["pmid"] for r in rng.sample(pool, k))

    # UM arquivo com a populacao inteira. `na_amostra` marca as sorteadas; o
    # censo e simplesmente ignorar essa coluna e revisar todas as linhas.
    # Ate 2026-07-20 isto eram tres arquivos (censo, amostra, vereditos) e a
    # relacao entre eles nao era obvia.
    vereditos = {}
    if VEREDITOS_CSV.exists():
        with open(VEREDITOS_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                vereditos[r["pmid"]] = (r.get("veredito_manual", ""),
                                        r.get("nota_revisor", ""))

    linhas = []
    for e in ESTRATOS:
        for r in sorted(por_estrato[e], key=lambda r: (r["pmid"], r["gold_answer"])):
            v, nota = vereditos.get(r["pmid"], ("", ""))
            r["na_amostra"] = "sim" if r["pmid"] in sorteados else "nao"
            r["veredito_manual"] = v
            r["nota_revisor"] = nota
            linhas.append(r)

    ordem = ["pmid", "estrato", "gold_answer", "n_modelos", "modelos", "respostas",
             "termo_que_casou", "comprimento_termo", "na_amostra",
             "veredito_manual", "nota_revisor", "contexto"]
    with open(VEREDITOS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ordem)
        w.writeheader()
        w.writerows([{k: r[k] for k in ordem} for r in linhas])

    n_ok = sum(1 for r in linhas if r["veredito_manual"])
    pend_am = [r for r in linhas if r["na_amostra"] == "sim" and not r["veredito_manual"]]
    pend = [r for r in linhas if not r["veredito_manual"]]
    print(f"\n{VEREDITOS_CSV.relative_to(BASE)}: {len(linhas)} questoes "
          f"({sum(r['n_modelos'] for r in linhas)} acertos), {n_ok} com veredito.")
    if not pend:
        print("  CENSO COMPLETO -- 100% da populacao revisada, sem erro amostral.")
    elif pend_am:
        print(f"  [ATENCAO] {len(pend_am)} questao(oes) DA AMOSTRA sem veredito: "
              f"{', '.join(r['pmid'] for r in pend_am[:8])}")
        print("  Preencher 'veredito_manual' com: eco_real | falso_alarme | duvidoso")
    else:
        print(f"  Amostra completa; faltam {len(pend)} questoes para fechar o censo.")
        print("  Preencher 'veredito_manual' com: eco_real | falso_alarme | duvidoso")


if __name__ == "__main__":
    main()
