#!/usr/bin/env python3
"""
Cura os golds que o NEBB nao conseguiu resolver (entradas UNMAPPED_*).

Tres camadas, da mais segura para a menos:

  1. AUTOMATICA SEGURA -- variantes que seguem convencao real do HGNC:
     remover hifen/espaco (IGF-1R -> IGF1R) e converter letra grega em letra
     (GSK-3beta -> GSK3B). NUNCA apaga a letra grega: 'hCGbeta' com o grego
     apagado casaria com CGA (subunidade ALFA), que e o gene errado.

  2. CURADORIA MANUAL -- genes legitimos cuja grafia coloquial o HGNC nao
     carrega (connexin50 -> GJA8). Cada alvo e VALIDADO contra o HGNC: se o
     simbolo nao existir la, o mapeamento e rejeitado.

  3. DEIXADOS DE FORA -- golds compostos ou ambiguos (BRCA, Smad2/3,
     PPARalpha/gamma). Nao sao mapeados de proposito: escolher um gene seria
     inventar informacao. Continuam respondiveis por match estrito.

Gera o gabarito curado e reporta tudo que foi feito.
"""
import json
import re
import argparse
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
HGNC = BASE / "nebb" / "data" / "hgnc_complete.json"

GREEK = {"alpha": "a", "beta": "b", "gamma": "g", "delta": "d",
         "epsilon": "e", "zeta": "z"}

# ── Camada 2: curadoria manual (raw -> simbolo HGNC pretendido) ──────────────
# Cada um e validado contra o HGNC antes de ser aplicado.
CURADORIA = {
    "C/EBPdelta":         "CEBPD",     # CCAAT enhancer binding protein delta
    "GRalpha":            "NR3C1",     # receptor de glicocorticoide, isoforma alfa
    "Galpha13":           "GNA13",     # subunidade alfa-13 da proteina G
    "IL-4Ralpha":         "IL4R",      # cadeia alfa do receptor de IL-4
    "PITPbeta":           "PITPNB",    # phosphatidylinositol transfer protein beta
    "TGFBIp":             "TGFBI",     # 'p' = produto proteico do gene TGFBI
    "Tribbles1":          "TRIB1",
    "WIPI-1alpha":        "WIPI1",
    "beta2-GPI":          "APOH",      # beta-2-glicoproteina I = apolipoproteina H
    "beta2-adrenoceptor": "ADRB2",
    "connexin50":         "GJA8",      # conexina 50 = gap junction protein alpha 8
    "MicroRNA29a":        "MIR29A",
    "miR-126-3p":         "MIR126",    # fita madura -> gene do miRNA
    "miR-448-3p":         "MIR448",
    "CYP1A1MspI":         "CYP1A1",    # gene + polimorfismo MspI colados
    "KCNG2*rs62":         "KCNG2",     # gene + SNP colados
}

# ── Camada 3: deixados de proposito (compostos/ambiguos) ─────────────────────
# Documentado para que a decisao seja rastreavel, nao esquecida.
NAO_MAPEAR = {
    "BRCA":               "ambiguo: BRCA1 ou BRCA2?",
    "Smad2/3":            "composto: dois genes",
    "PPARalpha/gamma":    "composto: dois genes",
    "C4,C3,C5,C9":        "composto: quatro genes do complemento",
    "CD300a/c":           "composto: dois genes",
    "KCNQ2-5":            "composto: faixa KCNQ2..KCNQ5",
    "SNORD50A/B":         "composto: dois snoRNAs",
    "TIKI1/2":            "composto: dois genes",
    "TBCA-TBCE":          "composto: dois genes",
    "microRNA-4474/4717": "composto: dois miRNAs",
    "miR-3,178":          "gold malformado (virgula)",
    "MIR30":              "ambiguo: familia MIR30A..E",
    "miR-548a-3p":        "ambiguo: MIR548A1/A2/A3",
    "RNATapSaki":         "string nao interpretavel",
    "hCGbeta":            "arriscado: heuristica casaria CGA (subunidade ALFA), gene errado",
    "Tctex2beta":         "variante beta incerta de TCTEX2/DYNLT2",
    "DEFB25":             "provavel ortologo murino; sem equivalente humano claro",
    "FXRbeta":            "NR1H5P e pseudogene; mapeamento incerto",
    "SR-B1":              "ja resolvido pela camada automatica",
}


def build_hgnc_index():
    h = json.load(open(HGNC, encoding="utf-8"))
    docs = h["response"]["docs"] if "response" in h else h.get("docs", h)
    idx = {}
    simbolos = set()
    info = {}
    for d in docs:
        s = d.get("symbol")
        if not s:
            continue
        simbolos.add(s)
        info[s] = d
        chaves = ([s] + d.get("alias_symbol", []) + d.get("prev_symbol", [])
                  + [d.get("name", "")] + d.get("alias_name", []))
        for k in chaves:
            if k:
                idx.setdefault(k.lower().strip(), s)
    return idx, simbolos, info


def variantes_seguras(t: str) -> set[str]:
    """Só convenções reais do HGNC. Nunca apaga a letra grega."""
    b = t.lower().strip()
    v = {b, re.sub(r"[-\s]", "", b)}
    for g, l in GREEK.items():
        if g in b:
            v.add(re.sub(r"[-\s]", "", b).replace(g, l))
            v.add(b.replace(g, l))
    v.add(re.sub(r"(\D)(\d)$", r"\1 \2", b))     # paraoxonase1 -> "paraoxonase 1"
    return {x for x in v if x}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gabarito", default=str(BASE / "nebb" / "gold_standard.json"))
    ap.add_argument("--out", default=str(BASE / "nebb" / "gold_standard.json"))
    args = ap.parse_args()

    idx, simbolos, info = build_hgnc_index()
    gab = json.load(open(args.gabarito, encoding="utf-8"))

    unmapped = {k: v for k, v in gab.items() if not v.get("canonical_gene")}
    print(f"entradas UNMAPPED no gabarito: {len(unmapped)}\n")

    resolvidos = {}     # raw -> (simbolo, camada)

    # camada 1
    for k, v in unmapped.items():
        for raw in v.get("termos_originais", []):
            hit = next((idx[x] for x in variantes_seguras(raw) if x in idx), None)
            if hit:
                resolvidos[raw] = (hit, "automatica")

    # camada 2 (valida contra HGNC)
    rejeitados = []
    for raw, alvo in CURADORIA.items():
        if raw in resolvidos:
            continue
        if alvo not in simbolos:
            rejeitados.append((raw, alvo))
            continue
        resolvidos[raw] = (alvo, "manual")

    print(f"[camada 1] automatica segura : {sum(1 for _,(s,c) in resolvidos.items() if c=='automatica')}")
    print(f"[camada 2] curadoria manual  : {sum(1 for _,(s,c) in resolvidos.items() if c=='manual')}")
    if rejeitados:
        print(f"\n!! REJEITADOS (simbolo nao existe no HGNC): {rejeitados}")

    # ── aplica: move o termo para a entrada canonica ─────────────────────────
    for raw, (simbolo, camada) in sorted(resolvidos.items()):
        chave = simbolo.upper()
        d = info[simbolo]
        if chave not in gab:
            gab[chave] = {
                "canonical_gene": simbolo,
                "identidade_biologica": {},
                "termos_originais": [],
                "gene": {}, "rnam": {}, "proteina": {},
                "classificacao_composta": ["Gene (symbol)"],
            }
        node = gab[chave]
        if not node.get("canonical_gene"):
            node["canonical_gene"] = simbolo
        if raw not in node["termos_originais"]:
            node["termos_originais"].append(raw)
        # aliases do HGNC
        for a in [simbolo] + d.get("alias_symbol", []) + d.get("prev_symbol", []):
            if a and a not in node["gene"]:
                node["gene"][a] = ["HGNC"]
        for a in d.get("refseq_accession", []) or []:
            node["rnam"].setdefault(a, ["HGNC"])
        for a in ([d["uniprot_ids"]] if isinstance(d.get("uniprot_ids"), str)
                  else d.get("uniprot_ids", []) or []):
            node["proteina"].setdefault(a, ["HGNC"])
        print(f"  [{camada:10}] {raw!r:22} -> {simbolo}")

    # remove entradas UNMAPPED cujos termos foram todos resolvidos
    removidas = 0
    for k, v in list(unmapped.items()):
        restantes = [t for t in v.get("termos_originais", []) if t not in resolvidos]
        if not restantes:
            del gab[k]
            removidas += 1
        else:
            gab[k]["termos_originais"] = restantes

    ainda = {k: v for k, v in gab.items() if not v.get("canonical_gene")}
    print(f"\nentradas UNMAPPED removidas: {removidas}")
    print(f"UNMAPPED restantes (deliberadamente nao mapeados): {len(ainda)}")
    for k, v in sorted(ainda.items()):
        for t in v.get("termos_originais", []):
            motivo = NAO_MAPEAR.get(t, "sem mapeamento seguro")
            print(f"  {t!r:22} -- {motivo}")

    json.dump(gab, open(args.out, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
    print(f"\nGabarito curado salvo em: {args.out}")


if __name__ == "__main__":
    main()
