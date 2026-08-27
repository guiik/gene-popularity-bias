#!/usr/bin/env python3
"""
Auditoria da causa do descarte pelo filtro de anotacao do PubTator.

Para cada candidato (PMID, GeneID) sorteado na construcao do benchmark, determina
se ele SOBREVIVEU (o PubTator anotou o gene-alvo no abstract, logo vira caso de
teste) ou foi DESCARTADO. Para os descartados, classifica a CAUSA:

  (a) gene NAO esta no abstract        -> neutro para a hipotese
  (b) gene ESTA no abstract, mas o NER nao o reconheceu -> vies conservador
  (u) gene sem simbolo/nome no HGNC    -> nao verificavel (obscuridade extrema)

A distincao importa: (a) e mero atrito de construcao (nao ha lacuna a mascarar);
so (b) remove sistematicamente os genes mais dificeis, enviesando o Q4.

Saidas:
  results/audits/pubtator_discard.csv   -> uma linha por candidato, com a evidencia
  results/cache_pubtator/<pmid>.json    -> resposta bruta do PubTator (reproducibilidade)

Regra de casamento (documentada e conservadora):
  - simbolos (symbol/alias_symbol/prev_symbol): token inteiro, delimitado por
    fronteira de nao-alfanumerico, case-insensitive, comprimento >= 2;
  - nomes por extenso (name/alias_name/prev_name): substring case-insensitive,
    comprimento > 4 (evita nomes curtos genericos).
  Cada acerto registra a FORMA que casou e um trecho de +-40 caracteres, de modo
  que toda classificacao (b) e verificavel a olho.

Limitacao conhecida: (b) e um LIMITE INFERIOR. Se o abstract grafou o gene de uma
forma que o HGNC nao lista como alias, o caso cai em (a) por engano. Logo o vies
real e, no maximo, igual ou maior que o medido -- nunca menor.

Uso:
  python3 scripts/audit/pubtator_discard.py            # amostra (seed=42): 150 Q1 + 300 Q4
  python3 scripts/audit/pubtator_discard.py --full     # todos os candidatos de cada estrato
  python3 scripts/audit/pubtator_discard.py --estratos Q1_Super_Populares Q4_Cauda_Longa
"""
import argparse, csv, json, os, re, random, sys, time, urllib.request
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
CSV_CANDIDATOS = str(BASE / "data" / "benchmark_pmids.csv")
HGNC = str(BASE / "nebb" / "data" / "hgnc_complete.json")
# cache das respostas do PubTator: fora do git (30 MB), recriado sob demanda
CACHE_DIR = str(BASE / "results" / "cache_pubtator")
OUT_CSV = str(BASE / "results" / "audits" / "pubtator_discard.csv")
API = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson?pmids="
SEED = 42
AMOSTRA = {"Q1_Super_Populares": 150, "Q4_Cauda_Longa": 300}


def carregar_formas_hgnc():
    """entrez_id -> {'sym': set(tokens), 'name': set(frases)} a partir do HGNC."""
    hg = json.load(open(HGNC))
    formas = defaultdict(lambda: {"sym": set(), "name": set()})
    for d in hg["response"]["docs"]:
        e = d.get("entrez_id")
        if not e:
            continue
        e = str(e)
        if d.get("symbol"):
            formas[e]["sym"].add(d["symbol"])
        for k in ("alias_symbol", "prev_symbol"):
            v = d.get(k)
            if isinstance(v, list):
                formas[e]["sym"].update(v)
            elif v:
                formas[e]["sym"].add(v)
        if d.get("name"):
            formas[e]["name"].add(d["name"])
        for k in ("alias_name", "prev_name"):
            v = d.get(k)
            if isinstance(v, list):
                formas[e]["name"].update(v)
            elif v:
                formas[e]["name"].add(v)
    return formas


def buscar_pubtator(pmids):
    """Busca com cache em disco. Devolve {pmid: doc_bioc}."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    faltando = [p for p in pmids if not os.path.exists(f"{CACHE_DIR}/{p}.json")]
    for i in range(0, len(faltando), 100):
        lote = faltando[i : i + 100]
        url = API + ",".join(lote)
        for tentativa in range(3):
            try:
                with urllib.request.urlopen(url, timeout=90) as r:
                    data = r.read().decode()
                break
            except Exception:
                time.sleep(3)
        else:
            print(f"  falha ao buscar lote {i}", file=sys.stderr)
            continue
        for linha in data.splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                j = json.loads(linha)
            except json.JSONDecodeError:
                continue
            for doc in (j.get("PubTator3", []) if isinstance(j, dict) else []):
                pm = str(doc.get("pmid"))
                json.dump(doc, open(f"{CACHE_DIR}/{pm}.json", "w"))
        time.sleep(1)
    docs = {}
    for p in pmids:
        fn = f"{CACHE_DIR}/{p}.json"
        if os.path.exists(fn):
            docs[p] = json.load(open(fn))
    return docs


def genes_e_texto(doc):
    """Devolve (set de GeneIDs anotados, texto integral do registro)."""
    genes, textos = set(), []
    for p in doc.get("passages", []):
        textos.append(p.get("text", ""))
        for a in p.get("annotations", []):
            if a.get("infons", {}).get("type") == "Gene":
                ident = str(a.get("infons", {}).get("identifier") or "")
                for g in re.split(r"[;,]", ident):
                    if g.strip():
                        genes.add(g.strip())
    return genes, " \n ".join(textos)


def casa_simbolo(termo, texto):
    if len(termo) < 2:
        return None
    m = re.search(r"(?<![A-Za-z0-9])" + re.escape(termo) + r"(?![A-Za-z0-9])", texto, re.IGNORECASE)
    return m


def casa_nome(termo, texto):
    if len(termo) <= 4:
        return None
    i = texto.lower().find(termo.lower())
    return i if i >= 0 else None


def classificar(gid, genes, texto, formas):
    """Devolve (status, forma_casada, trecho)."""
    if gid in genes:
        return "sobreviveu", "", ""
    f = formas.get(gid)
    if not f or (not f["sym"] and not f["name"]):
        return "descarte_nao_verificavel", "", ""
    # (b): alguma forma aparece no texto?
    for termo in sorted(f["sym"], key=len, reverse=True):
        m = casa_simbolo(termo, texto)
        if m:
            ini = max(0, m.start() - 40)
            return "descarte_b_no_abstract", termo, texto[ini : m.end() + 40].replace("\n", " ")
    for termo in sorted(f["name"], key=len, reverse=True):
        i = casa_nome(termo, texto)
        if i is not None:
            ini = max(0, i - 40)
            return "descarte_b_no_abstract", termo, texto[ini : i + len(termo) + 40].replace("\n", " ")
    return "descarte_a_fora_abstract", "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="usa todos os candidatos do estrato")
    ap.add_argument("--estratos", nargs="+", default=list(AMOSTRA.keys()))
    args = ap.parse_args()

    formas = carregar_formas_hgnc()
    cands = defaultdict(list)
    for row in csv.DictReader(open(CSV_CANDIDATOS)):
        cands[row["estrato"]].append((row["PubMed_ID"], str(row["GeneID"])))

    random.seed(SEED)
    sel = {}
    for s in args.estratos:
        if args.full:
            sel[s] = cands[s]
        else:
            sel[s] = random.sample(cands[s], min(AMOSTRA.get(s, len(cands[s])), len(cands[s])))

    todos = list(dict.fromkeys([p for s in sel for p, g in sel[s]]))
    print(f"buscando {len(todos)} PMIDs no PubTator (com cache em {CACHE_DIR}) ...")
    docs = buscar_pubtator(todos)

    os.makedirs("results", exist_ok=True)
    w = csv.writer(open(OUT_CSV, "w", newline=""))
    w.writerow(["estrato", "pmid", "geneid", "hgnc_symbol", "status", "forma_casada", "trecho_evidencia"])
    tally = {s: defaultdict(int) for s in sel}
    for s in sel:
        for pmid, gid in sel[s]:
            doc = docs.get(pmid)
            sym = "; ".join(sorted(formas.get(gid, {}).get("sym", set()))) or ""
            if not doc:
                w.writerow([s, pmid, gid, sym, "sem_doc", "", ""])
                tally[s]["sem_doc"] += 1
                continue
            genes, texto = genes_e_texto(doc)
            status, forma, trecho = classificar(gid, genes, texto, formas)
            w.writerow([s, pmid, gid, sym, status, forma, trecho])
            tally[s][status] += 1

    print(f"\nCSV auditavel: {OUT_CSV}\n")
    for s in sel:
        t = tally[s]
        n = sum(t.values()) - t.get("sem_doc", 0)
        surv = t.get("sobreviveu", 0)
        b = t.get("descarte_b_no_abstract", 0)
        a = t.get("descarte_a_fora_abstract", 0)
        u = t.get("descarte_nao_verificavel", 0)
        print(f"== {s} (n={n}, sem_doc={t.get('sem_doc',0)}) ==")
        print(f"  sobreviveu ................................. {surv:4d}  ({100*surv/n:5.1f}%)")
        print(f"  descarte (a) fora do abstract [neutro] ..... {a:4d}  ({100*a/n:5.1f}%)")
        print(f"  descarte (u) sem nome HGNC [nao verif.] .... {u:4d}  ({100*u/n:5.1f}%)")
        print(f"  descarte (b) no abstract, NER falhou [vies]. {b:4d}  ({100*b/n:5.1f}%)")
        print()


if __name__ == "__main__":
    main()
