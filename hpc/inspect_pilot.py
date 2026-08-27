#!/usr/bin/env python3
"""
Inspeciona a saida do teste-piloto NO CLUSTER, antes de liberar a rodada cheia.

Roda com o Python do sistema (so stdlib). Uso:
    python3 inspecionar_piloto.py [caminho_do_jsonl]

Se nao passar caminho, procura em $USER_SCRATCH/output_piloto/.
"""
import json
import sys
import glob
import statistics
from pathlib import Path

SCRATCH = Path("/scratch/ppg-lncc/guilherme.bittencourt")
ALVO_TOTAL = 4000          # tamanho da rodada cheia
NUM_PREDICT = 4096

falhas = []


def check(nome, ok, detalhe="", critico=True):
    print(f"  [{'OK   ' if ok else ('FALHA' if critico else 'AVISO')}] {nome}"
          + (f"  -- {detalhe}" if detalhe else ""))
    if not ok and critico:
        falhas.append(nome)
    return ok


def main():
    if len(sys.argv) > 1:
        arqs = [sys.argv[1]]
    else:
        arqs = sorted(glob.glob(str(SCRATCH / "output_piloto" / "*.jsonl")))
    if not arqs:
        print("Nenhum .jsonl encontrado em output_piloto/. O job rodou?")
        return 1

    arq = arqs[-1]
    print("=" * 76)
    print(f"INSPECAO DO PILOTO: {arq}")
    print("=" * 76)

    rows = [json.loads(l) for l in open(arq, encoding="utf-8") if l.strip()]
    print(f"\n{len(rows)} casos\n")

    # 1. o script novo mesmo rodou?
    tem_fp = all("full_prompt" in r for r in rows)
    check("campo 'full_prompt' presente", tem_fp,
          "se faltar, o cluster rodou a VERSAO VELHA do script")

    if tem_fp:
        fp = rows[0]["full_prompt"]
        check("prompt termina com a instrucao answer-only",
              "Respond with ONLY the name of the masked biological entity" in fp)
        check("prompt NAO tem chain-of-thought",
              "Think step-by-step" not in fp,
              "se tiver, o modelo vai deliberar ate estourar o teto de tokens")

    # 2. o prompt tem o abstract, nao so o titulo?
    palavras = [len(r.get("prompt", "").split()) for r in rows]
    med = statistics.median(palavras)
    check("prompt tem o ABSTRACT (nao so o titulo)", med > 150,
          f"mediana={med:.0f} palavras (na rodada quebrada era 17)")

    # 3. a RESPOSTA (o texto) e curta e direta?
    #    Atencao: 'completion_tokens' conta os tokens GERADOS, incluindo o
    #    raciocinio interno dos modelos que pensam (Gemma 4, Qwen 3.6). Uma
    #    resposta curta e correta pode vir com 900 tokens gerados. Sao coisas
    #    diferentes: aqui olhamos o TEXTO; o custo em tokens vai no item 4.
    palavras_resp = [len((r.get("model_response") or "").split()) for r in rows]
    med_resp = statistics.median(palavras_resp) if palavras_resp else 0
    check("RESPOSTA e curta e direta (o texto)", med_resp <= 12,
          f"mediana={med_resp:.0f} palavras -- deve ser so o nome da entidade")

    brancos = sum(1 for r in rows if not (r.get("model_response") or "").strip())
    check("poucas respostas em branco", brancos / len(rows) < 0.15,
          f"{brancos}/{len(rows)}", critico=False)

    # 4. custo em tokens: e aqui que mora o problema de TEMPO
    print()
    ct = [r.get("completion_tokens", 0) for r in rows if r.get("completion_tokens")]
    if ct:
        medct = statistics.median(ct)
        no_teto = sum(1 for x in ct if x >= NUM_PREDICT)
        check("modelo NAO esta gastando tokens pensando", medct < 100,
              f"completion_tokens mediano={medct:.0f} "
              "-- se for alto, o modo de raciocinio esta ligado (use LLM_THINK=false)",
              critico=False)
        if no_teto:
            print(f"         {no_teto}/{len(ct)} estouraram o teto de {NUM_PREDICT} "
                  "-> resposta vazia (abstencao)")

    # 5. cabe no tempo da rodada cheia?
    el = [r.get("elapsed_sec", 0) for r in rows if r.get("elapsed_sec")]
    if el:
        media = statistics.mean(el)
        horas = media * ALVO_TOTAL / 3600
        check("tempo da rodada cheia cabe no --time do job", horas < 20,
              f"{media:.1f}s/caso x {ALVO_TOTAL} = ~{horas:.1f}h "
              "(jobs tem 24h; o Llama 70B tem 20h E e o mais lento)")

    # 5. amostra para olhar com os proprios olhos
    print("\n" + "-" * 76)
    print("OLHE ESTES 3 CASOS. A resposta faz sentido? E um nome de gene?")
    print("-" * 76)
    for r in rows[:3]:
        resp = (r.get("model_response") or "").strip().replace("\n", " ")
        print(f"\n  PMID {r.get('pmid')} [{r.get('estrato')}]")
        print(f"    gold     : {r.get('gold_answer')!r}")
        print(f"    resposta : {resp[:160]!r}")
        print(f"    tokens   : prompt={r.get('prompt_tokens')} "
              f"resposta={r.get('completion_tokens')} | {r.get('elapsed_sec')}s")

    print("\n" + "=" * 76)
    if falhas:
        print("  NAO LIBERE A RODADA CHEIA. Falhas:")
        for f in falhas:
            print(f"    - {f}")
    else:
        print("  Piloto OK. Pode submeter os 4 jobs de producao.")
    print("=" * 76)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
