#!/usr/bin/env python3
import json
import re
from collections import defaultdict
from pathlib import Path

def load_gabarito(gabarito_path):
    print(f"Carregando índice reverso do gabarito: {gabarito_path}")
    try:
        with open(gabarito_path, "r", encoding="utf-8") as f:
            gabarito = json.load(f)
    except Exception as e:
        print(f"Erro ao carregar gabarito: {e}")
        return {}, {}

    alias_to_canonical = {}
    canonical_to_aliases = defaultdict(set)
    
    for canonical_name, data in gabarito.items():
        canonical = data.get("canonical_gene")
        if not canonical:
            continue
            
        genes = list(data.get("gene", {}).keys())
        rnas = list(data.get("rnam", {}).keys())
        proteins = list(data.get("proteina", {}).keys())
        # 'termos_originais' e a string bruta que o NEBB resolveu -- a mesma forma que
        # aparece como gold no benchmark. Sem ela o gold nao e achado no dicionario.
        originais = list(data.get("termos_originais", []))
        all_aliases = genes + rnas + proteins + originais

        # O próprio canonical é um alias
        all_aliases.append(canonical)
        
        for alias in set(all_aliases):
            if not alias: continue
            al_lower = alias.lower()
            alias_to_canonical[al_lower] = canonical
            canonical_to_aliases[canonical].add(al_lower)
            
    return alias_to_canonical, canonical_to_aliases

def contains_token(haystack, needle):
    """needle aparece em haystack como token inteiro, nao como pedaco de outro
    simbolo. Sem isso, gold 'IL4' casa dentro de 'IL4RA' (gene diferente)."""
    if not needle or not haystack:
        return False
    return re.search(
        r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', haystack
    ) is not None


def check_expanded_match(gold_answer, model_response, alias_to_canonical, canonical_to_aliases):
    g_lower = gold_answer.lower()
    r_lower = model_response.lower()
    
    # Busca o canônico
    canonical = alias_to_canonical.get(g_lower)
    if not canonical:
        return False, None
        
    # Token inteiro para TODOS os aliases, nao so os curtos. Com substring cru,
    # 'bnip3' casava dentro de 'BNIP3L' (gene diferente), 'siglec-1' dentro de
    # 'Siglec-10', 'sall1p' dentro de 'SALL1P1'. Alias mais longo primeiro.
    for alias in sorted(canonical_to_aliases[canonical], key=len, reverse=True):
        if contains_token(r_lower, alias):
            return True, alias

    return False, None

import sys

def print_metrics(name, data):
    total = data['total']
    if total == 0: return
    
    strict = data['strict']
    expanded = data['expanded']
    errors = data['errors']
    failures = data['failures'] # APIs timeout/etc
    blanks = data.get('blanks', 0)
    
    total_acertos = strict + expanded
    
    print(f"\n[{name}]")
    print(f"  Total testado: {total}")
    print(f"  Falhas de API: {failures} (Descartados para % se contados como falha? Mantendo no total)")
    print(f"  Em Branco: {blanks} ({blanks/total*100:.1f}%)")
    print(f"  Acertos Estritos: {strict} ({strict/total*100:.1f}%)")
    print(f"  Acertos Expandidos (NEBB Hub): {expanded} ({expanded/total*100:.1f}%)")
    print(f"  Acertos Totais (Acurácia Final): {total_acertos} ({total_acertos/total*100:.1f}%)")

def main():
    if len(sys.argv) < 2:
        print("Uso: python metrics.py <caminho_para_results.jsonl>")
        sys.exit(1)
        
    gabarito_path = str(Path(__file__).resolve().parents[2] / "nebb" / "gold_standard.json")
    results_path = sys.argv[1]
    
    alias_to_canonical, canonical_to_aliases = load_gabarito(gabarito_path)
    print(f"Hub mapeado com {len(alias_to_canonical)} aliases distintos para Busca Semântica.")
    
    global_metrics = {'total': 0, 'strict': 0, 'expanded': 0, 'errors': 0, 'failures': 0, 'blanks': 0}
    strata_metrics = defaultdict(lambda: {'total': 0, 'strict': 0, 'expanded': 0, 'errors': 0, 'failures': 0, 'blanks': 0})
    
    print(f"Avaliando resultados do modelo: {results_path}")
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            estrato = row.get("estrato", "Desconhecido")
            gold_answer = row.get("gold_answer", "")
            response = row.get("model_response", "")
            status = row.get("status", "success")
            
            global_metrics['total'] += 1
            strata_metrics[estrato]['total'] += 1
            
            if status == "failed" or "[ERRO:" in response:
                global_metrics['failures'] += 1
                strata_metrics[estrato]['failures'] += 1
                global_metrics['errors'] += 1
                strata_metrics[estrato]['errors'] += 1
                continue
                
            if not response.strip():
                global_metrics['blanks'] += 1
                strata_metrics[estrato]['blanks'] += 1
                global_metrics['errors'] += 1
                strata_metrics[estrato]['errors'] += 1
                continue
                
            # Match Estrito (token inteiro: 'IL4' nao pode casar dentro de 'IL4RA')
            if contains_token(response.lower(), gold_answer.lower()):
                global_metrics['strict'] += 1
                strata_metrics[estrato]['strict'] += 1
            else:
                # Match Expandido
                match_nebb, used_alias = check_expanded_match(gold_answer, response, alias_to_canonical, canonical_to_aliases)
                if match_nebb:
                    global_metrics['expanded'] += 1
                    strata_metrics[estrato]['expanded'] += 1
                else:
                    global_metrics['errors'] += 1
                    strata_metrics[estrato]['errors'] += 1
                    
    print("\n" + "="*50)
    print("MÉTRICAS GLOBAIS DO MODELO")
    print("="*50)
    print_metrics("TODOS OS ESTRATOS", global_metrics)
    
    print("\n" + "="*50)
    print("MÉTRICAS POR ESTRATO")
    print("="*50)
    for estrato_nome, metrics in sorted(strata_metrics.items()):
        print_metrics(estrato_nome, metrics)
        
if __name__ == "__main__":
    main()
