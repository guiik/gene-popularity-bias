#!/usr/bin/env python3
"""
Verificacao pre-voo: checa tudo que da para checar por codigo antes de gastar GPU
no Santos Dumont.

Cada item corresponde a um problema real encontrado na revisao de 2026-07-13.
Se algum FALHAR, nao suba para o cluster.

Uso:  python3 scripts/build/preflight_check.py
"""
import json
import re
import sys
import statistics
from pathlib import Path
from collections import Counter

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE / "scripts" / "evaluate"))

DATASET = BASE / "data" / "benchmark_v5.jsonl"
GABARITO = BASE / "nebb" / "gold_standard.json"
EVAL = BASE / "hpc" / "run_benchmark.py"
SLURMS = [
    BASE / "hpc" / "jobs" / "producao_qwen.slurm",
    BASE / "hpc" / "jobs" / "producao_llama_70b.slurm",
    BASE / "hpc" / "jobs" / "run_full_gemma4_denso.slurm",
    BASE / "hpc" / "jobs" / "run_full_gemma4_moe.slurm",
]
ESTRATOS = ["Q1_Super_Populares", "Q2_Medios",
            "Q3_Baixa_Popularidade", "Q4_Cauda_Longa"]
ALVO = 1000
NUM_CTX = 8192
NUM_PREDICT = 4096

resultados = []


def check(nome, ok, detalhe="", critico=True):
    resultados.append((nome, ok, detalhe, critico))
    marca = "OK  " if ok else ("FALHA" if critico else "AVISO")
    print(f"  [{marca:5}] {nome}" + (f"  -- {detalhe}" if detalhe else ""))
    return ok


def main():
    print("=" * 78)
    print("VERIFICACAO PRE-VOO -- benchmark NEBB")
    print("=" * 78)

    # ── 1. Dataset ───────────────────────────────────────────────────────────
    print("\n[1] DATASET")
    if not DATASET.exists():
        check("dataset existe", False, str(DATASET))
        return finalizar()
    rows = [json.loads(l) for l in open(DATASET, encoding="utf-8") if l.strip()]
    check("dataset existe", True, DATASET.name)

    c = Counter(r["estrato"] for r in rows)
    check(f"{ALVO} casos por estrato",
          all(c[e] == ALVO for e in ESTRATOS),
          " ".join(f"{e.split('_')[0]}={c[e]}" for e in ESTRATOS))
    check("total = 4000", len(rows) == ALVO * len(ESTRATOS), str(len(rows)))

    pmids = [str(r["pmid"]) for r in rows]
    dups = len(pmids) - len(set(pmids))
    check("sem PMID duplicado", dups == 0, f"{dups} duplicados "
          "(o resume do eval e do julgamento deduplica por PMID)")

    check("todo prompt tem [MASK]",
          all("[MASK]" in r["prompt"] for r in rows))

    degen = [r for r in rows if len(r["resposta_esperada"].strip()) < 2]
    check("sem gold degenerado", not degen,
          f"{len(degen)} golds com <2 chars (ex: gold '.' do PubTator)")

    # teto de publicacoes por gene (metodologia: Q1=10, demais=5)
    uso = Counter(str(r["gene_id_gabarito"]) for r in rows)
    est_de = {str(r["gene_id_gabarito"]): r["estrato"] for r in rows}
    viol = [g for g, n in uso.items()
            if n > (10 if est_de[g] == "Q1_Super_Populares" else 5)]
    check("teto de pubs/gene respeitado", not viol, f"{len(viol)} violacoes")

    # ── 2. Mascaramento (o bug que quebrou a rodada anterior) ────────────────
    print("\n[2] MASCARAMENTO")
    palavras = [len(r["prompt"].split()) for r in rows]
    med = statistics.median(palavras)
    check("prompts tem o ABSTRACT, nao so o titulo", med > 150,
          f"mediana={med:.0f} palavras (na rodada quebrada era 17)")

    curtos = sum(1 for w in palavras if w < 40)
    check("poucos prompts curtos", curtos / len(rows) < 0.05,
          f"{curtos} ({100*curtos/len(rows):.1f}%) com <40 palavras",
          critico=False)

    vaz = [r for r in rows
           if len(r["resposta_esperada"].strip()) > 2 and
           re.search(r"(?<![a-z0-9])" + re.escape(r["resposta_esperada"].strip().lower())
                     + r"(?![a-z0-9])", r["prompt"].lower())]
    check("vazamento do gabarito < 1%", len(vaz) / len(rows) < 0.01,
          f"{len(vaz)} casos ({100*len(vaz)/len(rows):.2f}%)")

    # ── 3. NEBB ──────────────────────────────────────────────────────────────
    print("\n[3] NEBB")
    from judge import load_nebb, normalize, contains_token
    a2c, c2a = load_nebb(str(GABARITO))
    check("dicionario NEBB carrega", len(a2c) > 0,
          f"{len(a2c)} aliases, {len(c2a)} genes canonicos")

    check("NEBB foi reconstruido para este benchmark", len(a2c) > 15000,
          f"{len(a2c)} aliases (o dicionario antigo tinha 10.914)")

    fora_por_estrato = {}
    for e in ESTRATOS:
        sub = [r for r in rows if r["estrato"] == e]
        fora_por_estrato[e] = sum(
            1 for r in sub if normalize(r["resposta_esperada"]) not in a2c)
    tot_fora = sum(fora_por_estrato.values())
    check("golds resolvidos pelo NEBB > 95%",
          (len(rows) - tot_fora) / len(rows) > 0.95,
          f"{len(rows)-tot_fora}/{len(rows)} ({100*(len(rows)-tot_fora)/len(rows):.1f}%)")

    # o ponto critico: a lacuna nao pode ser concentrada no Q4
    taxas = [fora_por_estrato[e] / ALVO for e in ESTRATOS]
    spread = max(taxas) - min(taxas)
    check("lacuna do NEBB NAO enviesada por estrato", spread < 0.05,
          " ".join(f"{e.split('_')[0]}={100*fora_por_estrato[e]/ALVO:.1f}%"
                   for e in ESTRATOS) +
          "  (se o Q4 tiver lacuna maior, a acuracia da cauda longa cai "
          "artificialmente -- viés a favor da hipotese)")

    # ── 4. Julgamento ────────────────────────────────────────────────────────
    print("\n[4] JULGAMENTO")
    check("match estrito usa token inteiro",
          not contains_token("il4ra", "il4") and contains_token("the il4 gene", "il4"),
          "'IL4' nao casa dentro de 'IL4RA'; 'FAS' nao casa em 'FASLG'")

    gab = json.load(open(GABARITO, encoding="utf-8"))
    tem_orig = any(v.get("termos_originais") for v in gab.values())
    idx_orig = normalize("11beta-HSD1") in a2c
    check("load_nebb indexa 'termos_originais'", tem_orig and idx_orig,
          "sem isso, golds como '11beta-HSD1' (=HSD11B1) ficam sem expansao")

    # ── 5. Script de inferencia ──────────────────────────────────────────────
    print("\n[5] SCRIPT DE INFERENCIA")
    src = EVAL.read_text(encoding="utf-8")
    check("chain-of-thought REMOVIDO do prompt",
          "Think step-by-step" not in src,
          "o CoT causava deliberacao ate estourar num_predict -> resposta vazia")
    check("instrucao answer-only presente",
          "Respond with ONLY" in src)
    check("prompt final e salvo nos resultados",
          '"full_prompt"' in src,
          "sem isso, o prompt que o modelo viu nao fica registrado")
    check("saida inclui o nome do modelo",
          "safe_model" in src and "results_{safe_model}" in src,
          "senao os 4 modelos escrevem no mesmo arquivo e se sobrescrevem")
    check(f"num_ctx = {NUM_CTX}", f'"num_ctx": {NUM_CTX}' in src)
    check(f"num_predict = {NUM_PREDICT}", f'"num_predict": {NUM_PREDICT}' in src)

    # cabe na janela de contexto?
    sufixo = 200  # chars da instrucao answer-only
    toks = [(len(r["prompt"]) + sufixo) / 3.5 for r in rows]
    pior = max(toks) + NUM_PREDICT
    check(f"pior caso cabe em num_ctx={NUM_CTX}", pior <= NUM_CTX,
          f"prompt max ~{max(toks):.0f} tok + {NUM_PREDICT} geracao = ~{pior:.0f}")

    # ── 6. SLURM ─────────────────────────────────────────────────────────────
    print("\n[6] SLURM")
    task_esperado = DATASET.stem
    for s in SLURMS:
        if not s.exists():
            check(f"{s.name} existe", False)
            continue
        txt = s.read_text(encoding="utf-8")
        m = re.search(r'^TASK_NAME="([^"]+)"', txt, re.M)
        ok = bool(m) and m.group(1) == task_esperado
        check(f"{s.name}: TASK_NAME", ok,
              f"={m.group(1) if m else '?'} (esperado {task_esperado})")

    ctxs = set()
    for s in SLURMS:
        if s.exists():
            m = re.search(r'OLLAMA_NUM_CTX="(\d+)"', s.read_text(encoding="utf-8"))
            if m:
                ctxs.add(m.group(1))
    check("OLLAMA_NUM_CTX uniforme entre os slurm", len(ctxs) <= 1,
          f"valores encontrados: {ctxs or 'nenhum (usa o do payload)'}",
          critico=False)

    return finalizar()


def finalizar():
    print("\n" + "=" * 78)
    falhas = [r for r in resultados if not r[1] and r[3]]
    avisos = [r for r in resultados if not r[1] and not r[3]]
    print(f"  {sum(1 for r in resultados if r[1])} OK  |  "
          f"{len(falhas)} FALHAS  |  {len(avisos)} avisos")
    if falhas:
        print("\n  NAO SUBA PARA O CLUSTER. Falhas:")
        for nome, _, det, _ in falhas:
            print(f"    - {nome}: {det}")
    else:
        print("\n  Tudo certo do lado automatico.")
        print("  Falta so o que nao da para checar daqui (ver CHECKLIST.md):")
        print("    - subir o dataset e o script ao SDumont (com o rename)")
        print("    - conferir que os modelos estao no Ollama do cluster")
        print("    - rodar um teste-piloto de ~20 casos ANTES da rodada cheia")
    print("=" * 78)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
