import json
import argparse
import os

def main():
    parser = argparse.ArgumentParser(description="Extrair respostas de um arquivo JSONL e salvá-las em arquivos txt (100 por arquivo).")
    parser.add_argument("--input", default="data/benchmark_v5.jsonl", help="Caminho para o arquivo JSONL de entrada (padrão: data/benchmark_v5.jsonl)")
    parser.add_argument("--out_dir", required=True, help="Diretório de saída para salvar os arquivos txt")
    
    args = parser.parse_args()

    # Cria o diretório de saída caso não exista
    os.makedirs(args.out_dir, exist_ok=True)
    
    respostas = []
    
    try:
        with open(args.input, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if not linha:
                    continue
                data = json.loads(linha)
                
                # Tenta buscar `resposta_do_modelo` (gerada); se não houver, busca `resposta_esperada` (gabarito)
                ans = data.get('resposta_do_modelo', data.get('resposta_esperada', ''))
                respostas.append(str(ans))
    except FileNotFoundError:
        print(f"Erro: O arquivo '{args.input}' não foi encontrado.")
        return
    except json.JSONDecodeError as e:
        print(f"Erro ao analisar o JSON do arquivo '{args.input}': {e}")
        return

    # Salva em arquivos txt a cada 100 respostas
    lote_tamanho = 100
    file_count = 1
    
    for i in range(0, len(respostas), lote_tamanho):
        lote = respostas[i:i+lote_tamanho]
        out_filename = os.path.join(args.out_dir, f"respostas_lote_{file_count}.txt")
        
        with open(out_filename, 'w', encoding='utf-8') as f_out:
            for ans in lote:
                f_out.write(f"{ans}\n")
                
        file_count += 1
        
    print(f"Total de {len(respostas)} respostas extraídas.")
    print(f"Foram salvos {file_count - 1} arquivos na pasta '{args.out_dir}'.")

if __name__ == "__main__":
    main()
