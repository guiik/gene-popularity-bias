#!/usr/bin/env python3
"""
Julgamento biológico de respostas de LLMs no benchmark NEBB.

Para cada entrada da fonte julga se a resposta do modelo corresponde
ao gabarito usando: match estrito, NEBB, normalização biológica.
"""

import json
import re
import sys
import os
from collections import defaultdict

# Base do HGNC, usada para resolver o gene pelo GeneID (ver load_hgnc_por_geneid)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HGNC_PATH = os.path.join(_REPO, 'nebb', 'data', 'hgnc_complete.json')
GOLD_STANDARD_PATH = os.path.join(_REPO, 'nebb', 'gold_standard.json')

# ── Normalização biológica ───────────────────────────────────────────────────

GREEK = {
    'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta',
    'ε': 'epsilon', 'ζ': 'zeta', 'η': 'eta', 'θ': 'theta',
    'κ': 'kappa', 'λ': 'lambda', 'μ': 'mu', 'ν': 'nu',
    'ξ': 'xi', 'π': 'pi', 'ρ': 'rho', 'σ': 'sigma',
    'τ': 'tau', 'υ': 'upsilon', 'φ': 'phi', 'χ': 'chi',
    'ψ': 'psi', 'ω': 'omega',
    'Α': 'alpha', 'Β': 'beta', 'Γ': 'gamma', 'Δ': 'delta',
    'Ε': 'epsilon', 'Κ': 'kappa', 'Λ': 'lambda', 'Μ': 'mu',
    'Ν': 'nu', 'Π': 'pi', 'Ρ': 'rho', 'Σ': 'sigma',
    'Τ': 'tau', 'Φ': 'phi', 'Χ': 'chi', 'Ψ': 'psi', 'Ω': 'omega',
}

# LaTeX → texto
LATEX_GREEK = {
    r'\alpha': 'alpha', r'\beta': 'beta', r'\gamma': 'gamma',
    r'\delta': 'delta', r'\epsilon': 'epsilon', r'\kappa': 'kappa',
    r'\lambda': 'lambda', r'\mu': 'mu', r'\nu': 'nu',
    r'\pi': 'pi', r'\rho': 'rho', r'\sigma': 'sigma',
    r'\tau': 'tau', r'\phi': 'phi', r'\chi': 'chi',
    r'\psi': 'psi', r'\omega': 'omega',
    r'\Alpha': 'alpha', r'\Beta': 'beta', r'\Gamma': 'gamma',
}

# ─────────────────────────────────────────────────────────────────────────────
# REMOVIDA: lista hardcoded de sinônimos.
#
# Era uma lista literal de 18 pares de genes (BNP, COX-2, TNF-alpha, ...), e o
# problema não era só ser hardcoded: ela fora construída OLHANDO AS RESPOSTAS
# DOS MODELOS (a estrutura era gold -> variantes que o modelo respondeu). Isso é
# ajustar a métrica aos dados de teste: só podia ajudar, nunca prejudicar, logo
# inflava a acurácia. Um modelo que usasse um sinônimo válido fora da lista era
# penalizado.
#
# Ficou desnecessária depois que o matcher passou a resolver o gene pelo GENEID
# do caso (ver load_hgnc_por_geneid): os aliases vêm do HGNC, que é uma
# autoridade externa e independente das respostas observadas.
#
# Impacto de removê-la: -0,19 p.p. em média. Ver results/comparacao_julgadores.md
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_SYNONYMS: list[tuple[set[str], set[str]]] = []



def normalize(text: str) -> str:
    """Normaliza texto para comparação biológica."""
    # Remove LaTeX $...$
    text = re.sub(r'\$([^$]+)\$', lambda m: m.group(1), text)
    # Substitui comandos LaTeX \alpha etc
    for latex, word in LATEX_GREEK.items():
        text = text.replace(latex, word)
    # Remove $ restantes
    text = text.replace('$', '')
    # Substitui letras gregas
    for greek, word in GREEK.items():
        text = text.replace(greek, word)
    # Remove pontuação trailing (ponto, vírgula)
    text = text.strip().rstrip('.,;:')
    return text.lower().strip()


def normalize_loose(text: str) -> str:
    """Normalização agressiva: remove hífens/parênteses/capitalização para comparação."""
    text = normalize(text)
    # Remove parênteses em torno de números: N(1) → N1
    text = re.sub(r'\((\d+)\)', r'\1', text)
    # Normaliza hífens, apóstrofos e espaços para espaço único
    text = re.sub(r"[-''\s]+", ' ', text)
    # Remove markdown bold/italic
    text = re.sub(r'\*+', '', text)
    return text.strip()


def contains_token(haystack: str, needle: str) -> bool:
    """needle aparece em haystack como token inteiro (nao como pedaco de outro
    simbolo). Evita que gold 'IL4' case dentro de 'IL4RA'."""
    if not needle or not haystack:
        return False
    return re.search(
        r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', haystack
    ) is not None


def extract_core(text: str) -> str:
    """Extrai apenas a resposta-núcleo de um texto que pode ter explicação longa."""
    # Se contém uma linha em branco, provavelmente o núcleo está na última linha não vazia
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return ''
    # Tenta última linha (muitos modelos colocam a resposta no final)
    last = lines[-1]
    # Remove markdown bold **...**
    last = re.sub(r'\*\*([^*]+)\*\*', r'\1', last)
    # Remove itálico *...*
    last = re.sub(r'\*([^*]+)\*', r'\1', last)
    return last.strip()


def load_nebb(gabarito_path: str):
    try:
        with open(gabarito_path, 'r', encoding='utf-8') as f:
            gabarito = json.load(f)
    except Exception:
        return {}, {}

    alias_to_canonical: dict[str, str] = {}
    canonical_to_aliases: dict[str, set[str]] = defaultdict(set)

    for canonical_name, data in gabarito.items():
        canonical = data.get('canonical_gene')
        if not canonical:
            continue
        genes = list(data.get('gene', {}).keys())
        rnas = list(data.get('rnam', {}).keys())
        proteins = list(data.get('proteina', {}).keys())
        # 'termos_originais' guarda a string bruta que o NEBB resolveu -- e justamente
        # a forma que aparece como gold no benchmark (ex: '11beta-HSD1' -> HSD11B1).
        # Sem ela, o gold nao e encontrado no dicionario e o acerto expandido fica
        # impossivel, mesmo com a entidade corretamente resolvida.
        originais = list(data.get('termos_originais', []))
        all_aliases = genes + rnas + proteins + originais + [canonical]
        for alias in set(all_aliases):
            if not alias:
                continue
            al = alias.lower()
            alias_to_canonical[al] = canonical
            canonical_to_aliases[canonical].add(al)

    return alias_to_canonical, canonical_to_aliases


def load_hgnc_por_geneid(hgnc_path: str = HGNC_PATH):
    """GeneID (Entrez) -> conjunto de aliases do HGNC.

    Indexar pelo GENEID, e nao pela string do gold, elimina a ambiguidade que
    corrompia o julgamento. Resolver o gold pelo texto e intrinsecamente ambiguo:
    'FAS' e simbolo do gene FAS mas tambem alias de FASN; 'p16' e alias de CDKN2A
    e de um pseudogene; 'NOS3' e simbolo de NOS3 e alias de NANOS3. O indice
    antigo (alias -> canonico) resolvia por 'ultimo a escrever vence', e apontava
    para o gene errado em 133 dos 4.000 gabaritos (4,6%).

    O benchmark ja carrega o GeneID correto (do gene2pubmed/PubTator) em cada
    caso. Usa-lo torna a resolucao ineoquivoca.
    """
    try:
        with open(hgnc_path, 'r', encoding='utf-8') as f:
            dados = json.load(f)
    except Exception:
        return {}
    docs = dados['response']['docs'] if 'response' in dados else dados.get('docs', dados)

    gid_para_aliases: dict[str, set[str]] = {}
    for d in docs:
        entrez = d.get('entrez_id')
        if not entrez:
            continue
        aliases = {
            x.lower().strip()
            for x in ([d.get('symbol'), d.get('name')]
                      + d.get('alias_symbol', []) + d.get('prev_symbol', [])
                      + d.get('alias_name', []))
            if x
        }
        gid_para_aliases[str(entrez)] = aliases
    return gid_para_aliases


def check_nebb(gold: str, response: str, alias_to_canonical, canonical_to_aliases,
               gene_id: str = '', gid_para_aliases=None):
    """Acerto expandido: a resposta contem um alias do MESMO gene do gabarito.

    Prioridade 1: aliases do HGNC obtidos pelo GENEID do caso (inequivoco).
    Prioridade 2: dicionario NEBB via a string do gold (fallback, ambiguo).

    O alias precisa casar como TOKEN INTEIRO. Com substring cru (a versao antiga),
    o alias 'bnip3' casava dentro de 'BNIP3L' -- gene diferente (NIX). Idem
    'siglec-1' dentro de 'Siglec-10'.
    """
    r = normalize(response)
    r_loose = normalize_loose(response)

    def procurar(aliases):
        # Alias mais longo primeiro: casa a forma mais especifica disponivel.
        # O desempate alfabetico nao e enfeite: `aliases` e um set, e ordenar so
        # por comprimento deixava empates ('er beta' e 'er-beta', ambos 7) na
        # ordem de iteracao do set -- que varia com o PYTHONHASHSEED. O veredito
        # nunca mudava, mas o alias_nebb registrado sim, e ele e um dos termos buscados
        # que o detector de eco procura no abstract.
        for alias in sorted(aliases, key=lambda a: (-len(a), a)):
            if len(alias) < 2:
                continue
            if contains_token(r, alias) or contains_token(r_loose, normalize_loose(alias)):
                return alias
        return ''

    # ── 1. pelo GeneID: AUTORIDADE quando existe ─────────────────────────────
    # Se o GeneID do caso esta no HGNC, ele decide sozinho. NAO se cai no
    # fallback: o dicionario por string resolveria 'FAS' -> FASN e aceitaria
    # 'FASN' como acerto (gene diferente). O GeneID nao tem essa ambiguidade.
    if gene_id and gid_para_aliases and str(gene_id) in gid_para_aliases:
        alias = procurar(gid_para_aliases[str(gene_id)])
        return (True, alias) if alias else (False, '')

    # ── 2. fallback: dicionario NEBB pela string do gold ─────────────────────
    # So para os ~2% de casos cujo GeneID nao existe no HGNC (lncRNAs recentes,
    # LOC*, pseudogenes). Aqui a ambiguidade e inevitavel.
    canonical = alias_to_canonical.get(normalize(gold))
    if canonical:
        alias = procurar(canonical_to_aliases[canonical])
        if alias:
            return True, alias
    return False, ''


def check_known_synonyms(gold: str, response: str) -> tuple[bool, str]:
    g = normalize(gold)
    r = normalize(response)
    for gold_set, resp_set in KNOWN_SYNONYMS:
        if any(s in g for s in gold_set):
            for rs in resp_set:
                if rs in r:
                    return True, rs
    return False, ''


def extract_abstract_trecho(prompt: str) -> str:
    """Extrai o trecho do abstract do prompt."""
    # O prompt tem formato: "...in the following abstract:\n\n{abstract}"
    marker = 'following abstract:\n\n'
    idx = prompt.find(marker)
    if idx >= 0:
        text = prompt[idx + len(marker):]
        return text[:220]
    # Fallback: tudo após a instrução
    idx2 = prompt.find('\n\n')
    if idx2 >= 0:
        return prompt[idx2 + 2:idx2 + 222]
    return prompt[:220]


def judge_entry(entry: dict, alias_to_canonical, canonical_to_aliases,
                gid_para_aliases=None) -> dict:
    pmid = str(entry.get('pmid', ''))
    estrato = entry.get('estrato', '')
    gold = entry.get('gold_answer', '').strip()
    response_raw = entry.get('model_response', '').strip()
    abstract_trecho = extract_abstract_trecho(entry.get('prompt', ''))

    # Resposta limpa (para exibição)
    resposta_modelo = response_raw[:300] if response_raw else ''

    # ── Em Branco ────────────────────────────────────────────────────────────
    if not response_raw or response_raw.strip() in ('', '[ERRO', '[TIMEOUT'):
        return {
            'pmid': pmid, 'estrato': estrato,
            'abstract_trecho': abstract_trecho,
            'gold_answer': gold, 'resposta_modelo': resposta_modelo,
            'acerto_estrito': False, 'acerto_nebb': False, 'alias_nebb': '',
            'acerto_julgador': 'Em Branco',
            'justificativa_julgador': 'Modelo não gerou resposta.',
        }

    # Tenta extrair núcleo da resposta (última linha significativa)
    core = extract_core(response_raw)
    r_norm = normalize(response_raw)
    g_norm = normalize(gold)

    # ── Match Estrito ─────────────────────────────────────────────────────────
    # gold contido na resposta, mas como TOKEN inteiro: sem word boundary,
    # gold 'IL4' casaria dentro de 'IL4RA' e 'FAS' dentro de 'FASLG' -- genes
    # distintos contados como acerto.
    g_loose = normalize_loose(gold)
    r_loose_full = normalize_loose(response_raw[:300])
    r_loose_core = normalize_loose(core)

    acerto_estrito = (
        contains_token(r_norm, g_norm) or
        contains_token(normalize(core), g_norm) or
        contains_token(r_loose_full, g_loose) or
        contains_token(r_loose_core, g_loose)
    )

    if acerto_estrito:
        return {
            'pmid': pmid, 'estrato': estrato,
            'abstract_trecho': abstract_trecho,
            'gold_answer': gold, 'resposta_modelo': resposta_modelo,
            'acerto_estrito': True, 'acerto_nebb': False, 'alias_nebb': '',
            'acerto_julgador': 'Sim',
            'justificativa_julgador': (
                f"Acerto estrito: resposta '{resposta_modelo[:50]}' "
                f"contém o gabarito '{gold}'."
            ),
        }

    # ── Match NEBB ────────────────────────────────────────────────────────────
    nebb_ok, alias = check_nebb(
        gold, response_raw, alias_to_canonical, canonical_to_aliases,
        gene_id=str(entry.get('gene_id_gabarito', '')),
        gid_para_aliases=gid_para_aliases,
    )
    if nebb_ok:
        return {
            'pmid': pmid, 'estrato': estrato,
            'abstract_trecho': abstract_trecho,
            'gold_answer': gold, 'resposta_modelo': resposta_modelo,
            'acerto_estrito': False, 'acerto_nebb': True, 'alias_nebb': alias,
            'acerto_julgador': 'Sim',
            'justificativa_julgador': (
                f"[NEBB Coerente] Coerente: o alias '{alias}' identificado na resposta "
                f"e sinonimo reconhecido de {gold.split()[0].upper()} (gold: {gold}); "
                f"o casamento do NEBB procede."
            ),
        }

    # ── Abreviação Parentética ────────────────────────────────────────────────
    # gold="sorting nexin (SNX) 3" e resp="SNX3" → Sim
    #
    # ATENCAO: nao basta casar a abreviacao. A versao antiga aceitava QUALQUER
    # sufixo depois dela, o que produzia falsos positivos com genes distintos:
    #   gold 'metalloproteinase (MMP)-2'  -> resp 'MMP-8'    (gene diferente!)
    #   gold 'interleukin (IL)1alpha'     -> resp 'IL1beta'  (IL1A != IL1B!)
    # Agora o numero e a letra grega da resposta precisam ser COMPATIVEIS com os
    # do gabarito.
    paren_abbr = re.findall(r'\(([A-Za-z]{2,8})\)', gold)
    for abbr in paren_abbr:
        abbr_l = abbr.lower()
        r_l = r_norm
        if not re.search(r'\b' + re.escape(abbr_l) + r'[\s\-]?\w*', r_l):
            continue

        # especificadores do gabarito (tudo que vem depois do parentese)
        cauda_gold = normalize(gold.split(')', 1)[1]) if ')' in gold else ''
        nums_gold = set(re.findall(r'\d+', cauda_gold))
        greg_gold = set(re.findall(r'alpha|beta|gamma|delta|zeta|kappa', cauda_gold))

        # o que a resposta diz DEPOIS da abreviacao
        m_resp = re.search(re.escape(abbr_l) + r'[\s\-]?(.*)', r_l)
        cauda_resp = m_resp.group(1) if m_resp else ''
        nums_resp = set(re.findall(r'\d+', cauda_resp))
        greg_resp = set(re.findall(r'alpha|beta|gamma|delta|zeta|kappa', cauda_resp))

        # numero incompativel -> gene diferente (MMP-2 vs MMP-8)
        if nums_gold and nums_resp and not (nums_resp & nums_gold):
            continue
        # letra grega incompativel -> gene diferente (IL1alpha vs IL1beta)
        if greg_gold and greg_resp and not (greg_resp & greg_gold):
            continue

        if True:
            if ',' not in gold or re.search(r'\b' + re.escape(abbr_l) + r'[\s\-]?\d+', r_l):
                return {
                    'pmid': pmid, 'estrato': estrato,
                    'abstract_trecho': abstract_trecho,
                    'gold_answer': gold, 'resposta_modelo': resposta_modelo,
                    'acerto_estrito': False, 'acerto_nebb': True,
                    'alias_nebb': abbr,
                    'acerto_julgador': 'Sim',
                    'justificativa_julgador': (
                        f"[Abrev. Parentética] '{abbr}' é a abreviação de '{gold}'; "
                        f"resposta '{resposta_modelo[:40]}' usa o símbolo correto."
                    ),
                }

    # ── miRNA variants ────────────────────────────────────────────────────────
    mir_g = re.search(r'(?:microrna|mirna|mir|hsa-mir)-?(\w+)', g_norm)
    mir_r = re.search(r'(?:microrna|mirna|mir|hsa-mir)-?(\w+)', r_norm)
    if mir_g and mir_r and mir_g.group(1) == mir_r.group(1):
        return {
            'pmid': pmid, 'estrato': estrato,
            'abstract_trecho': abstract_trecho,
            'gold_answer': gold, 'resposta_modelo': resposta_modelo,
            'acerto_estrito': False, 'acerto_nebb': True,
            'alias_nebb': mir_r.group(0),
            'acerto_julgador': 'Sim',
            'justificativa_julgador': (
                f"[miRNA Variante] '{mir_r.group()}' é variante de nomenclatura de "
                f"'{mir_g.group()}' (gold: {gold})."
            ),
        }

    # ── c- prefix convention ──────────────────────────────────────────────────
    # c-K-ras-1 = K-ras-1 (historical cellular prefix)
    if g_norm.startswith('c-'):
        g_no_c = g_norm[2:]
        if g_no_c and g_no_c in r_norm:
            return {
                'pmid': pmid, 'estrato': estrato,
                'abstract_trecho': abstract_trecho,
                'gold_answer': gold, 'resposta_modelo': resposta_modelo,
                'acerto_estrito': False, 'acerto_nebb': True,
                'alias_nebb': g_no_c,
                'acerto_julgador': 'Sim',
                'justificativa_julgador': (
                    f"[Convenção c-] '{resposta_modelo[:40]}' corresponde a '{gold}' "
                    f"sem o prefixo histórico 'c-'."
                ),
            }

    # ── TCR/receptor chain specificity ────────────────────────────────────────
    # TCRbeta = T-cell receptor beta (but TCR alone ≠ TCR beta)

    # ── Sinônimos Biológicos Conhecidos ───────────────────────────────────────
    syn_ok, used_syn = check_known_synonyms(gold, response_raw)
    if syn_ok:
        return {
            'pmid': pmid, 'estrato': estrato,
            'abstract_trecho': abstract_trecho,
            'gold_answer': gold, 'resposta_modelo': resposta_modelo,
            'acerto_estrito': False, 'acerto_nebb': True, 'alias_nebb': used_syn,
            'acerto_julgador': 'Sim',
            'justificativa_julgador': (
                f"[Sinônimo Biológico] '{resposta_modelo[:50]}' é sinônimo "
                f"biologicamente equivalente de '{gold}'."
            ),
        }

    # ── Não ──────────────────────────────────────────────────────────────────
    return {
        'pmid': pmid, 'estrato': estrato,
        'abstract_trecho': abstract_trecho,
        'gold_answer': gold, 'resposta_modelo': resposta_modelo,
        'acerto_estrito': False, 'acerto_nebb': False, 'alias_nebb': '',
        'acerto_julgador': 'Não',
        'justificativa_julgador': (
            f"Resposta '{resposta_modelo[:50]}' não corresponde ao gabarito "
            f"'{gold}' nem a sinônimo reconhecido."
        ),
    }


def process_file(source_path: str, output_path: str, gabarito_path: str,
                 estrato_filter: str | None = None):
    print(f"Carregando NEBB de {gabarito_path}...")
    alias_to_canonical, canonical_to_aliases = load_nebb(gabarito_path)
    print(f"  {len(alias_to_canonical)} aliases carregados.")

    print("Carregando HGNC indexado por GeneID (resolucao inequivoca)...")
    gid_para_aliases = load_hgnc_por_geneid()
    print(f"  {len(gid_para_aliases)} GeneIDs indexados.")

    print(f"Processando {source_path}...")
    with open(source_path, 'r', encoding='utf-8') as f:
        entries = [json.loads(l) for l in f if l.strip()]

    if estrato_filter:
        entries = [e for e in entries if e.get('estrato') == estrato_filter]
        print(f"  Filtrado para estrato '{estrato_filter}': {len(entries)} entradas.")

    # Ordena por estrato para manter consistência
    estrato_order = ['Q4_Cauda_Longa', 'Q3_Baixa_Popularidade', 'Q2_Medios', 'Q1_Super_Populares']
    entries.sort(key=lambda e: (
        estrato_order.index(e.get('estrato', '')) if e.get('estrato', '') in estrato_order else 99,
        e.get('pmid', '')
    ))

    # Verifica entradas já processadas (resumption)
    processed_pmids: set[str] = set()
    if os.path.exists(output_path):
        with open(output_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        j = json.loads(line)
                        key = f"{j.get('pmid','')}__{j.get('estrato','')}"
                        processed_pmids.add(key)
                    except Exception:
                        pass
        print(f"  Resumindo: {len(processed_pmids)} já processadas.")

    written = 0
    skipped = 0
    with open(output_path, 'a', encoding='utf-8') as out:
        for entry in entries:
            key = f"{entry.get('pmid','')}__{entry.get('estrato','')}"
            if key in processed_pmids:
                skipped += 1
                continue
            result = judge_entry(entry, alias_to_canonical, canonical_to_aliases,
                                 gid_para_aliases)
            out.write(json.dumps(result, ensure_ascii=False) + '\n')
            out.flush()
            written += 1

    print(f"  Escritas: {written} | Puladas (já existiam): {skipped}")
    print(f"  Arquivo: {output_path}")
    return written


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('source', help='Arquivo JSONL fonte (respostas do modelo)')
    p.add_argument('output', help='Arquivo JSONL de saída (julgamento)')
    p.add_argument('--gabarito', default=GOLD_STANDARD_PATH)
    p.add_argument('--estrato', default=None, help='Filtrar por estrato')
    args = p.parse_args()
    process_file(args.source, args.output, args.gabarito, args.estrato)
