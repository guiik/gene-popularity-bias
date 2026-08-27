import os
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[2]

def criar_benchmark_amostrado():
    arquivo_local = str(BASE / 'data' / 'sources' / 'gene2pubmed.gz')
    os.makedirs(os.path.dirname(arquivo_local), exist_ok=True)
    url_ncbi = "ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/gene2pubmed.gz"

    # Checa se já baixou antes
    if not os.path.exists(arquivo_local):
        print("Baixando gene2pubmed do NCBI pela primeira e única vez...")
        urllib.request.urlretrieve(url_ncbi, arquivo_local)
    else:
        print("Arquivo já encontrado localmente! Carregando direto...")

    # Lê do arquivo que tá salvo no seu computador
    df = pd.read_csv(arquivo_local, sep='\t', compression='gzip', comment='#', 
                     names=['tax_id', 'GeneID', 'PubMed_ID'])

    print("Filtrando apenas genes humanos (Tax_ID = 9606)...")
    df_human = df[df['tax_id'] == 9606].copy()

    # Calcular a "popularidade"
    gene_counts = df_human['GeneID'].value_counts().reset_index()
    gene_counts.columns = ['GeneID', 'paper_count']

    def classificar_estrato(count):
        if count >= 1000: return 'Q1_Super_Populares'
        elif count >= 100: return 'Q2_Medios'
        elif count >= 10: return 'Q3_Baixa_Popularidade'
        else: return 'Q4_Cauda_Longa'

    print("Classificando estratos de popularidade...")
    gene_counts['estrato'] = gene_counts['paper_count'].apply(classificar_estrato)
    df_human = df_human.merge(gene_counts[['GeneID', 'estrato']], on='GeneID')

    # A função corrigida para bater os 1000 no Q1
    def amostragem_balanceada(group, target_size=1000):
        shuffled = group.sample(frac=1, random_state=42)
        estrato_atual = group['estrato'].iloc[0]
        
        # Limite dinâmico: Q1 deixa pegar até 10 papers por gene
        limite_por_gene = 10 if estrato_atual == 'Q1_Super_Populares' else 5
            
        pool_balanceado = shuffled.groupby('GeneID').head(limite_por_gene)
        pool_balanceado = pool_balanceado.drop_duplicates(subset=['PubMed_ID'])

        if len(pool_balanceado) <= target_size:
            return pool_balanceado
        else:
            return pool_balanceado.sample(n=target_size, random_state=42)

    print("Realizando amostragem estatística balanceada...")
    amostra_final = df_human.groupby('estrato').apply(amostragem_balanceada, target_size=1000).reset_index(drop=True)

    print("\nResumo da Amostra Gerada (PMIDs por Estrato):")
    print(amostra_final['estrato'].value_counts())

    arquivo_saida = str(BASE / 'data' / 'benchmark_pmids.csv')
    amostra_final[['PubMed_ID', 'GeneID', 'estrato', 'tax_id']].to_csv(arquivo_saida, index=False)
    print(f"\nPronto! Lista salva em '{arquivo_saida}'")

if __name__ == "__main__":
    criar_benchmark_amostrado()