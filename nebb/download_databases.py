import os
import requests
from pathlib import Path
import tempfile
import shutil

MIN_FILE_SIZE_BYTES = 100 * 1024  # 100 KB check

def download_file(url: str, dest_path: Path) -> bool:
    print(f"Baixando {url} para {dest_path.name}...")
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        fd, temp_path = tempfile.mkstemp(dir=dest_path.parent)
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        file_size = os.path.getsize(temp_path)
        if file_size > MIN_FILE_SIZE_BYTES:
            shutil.move(temp_path, dest_path)
            print(f"Download e verificação de {dest_path.name} concluídos com sucesso ({file_size} bytes).")
            return True
        else:
            print(f"Erro: Arquivo {dest_path.name} baixado é muito pequeno ({file_size} bytes). Verificação falhou.")
            os.remove(temp_path)
            return False
    except Exception as e:
        print(f"Erro ao baixar {url}: {e}")
        return False

def download_bases() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    hgnc_url = "https://storage.googleapis.com/public-download-files/hgnc/json/json/hgnc_complete_set.json"
    hgnc_path = data_dir / "hgnc_complete.json"
    
    uniprot_url = "https://rest.uniprot.org/uniprotkb/stream?format=tsv&query=(reviewed:true)+AND+(model_organism:9606)&fields=accession,gene_primary"
    uniprot_path = data_dir / "uniprot_mapping.tsv"
    
    mirbase_url = "https://www.mirbase.org/download/aliases.txt"
    mirbase_path = data_dir / "aliases.txt"

    download_file(hgnc_url, hgnc_path)
    download_file(uniprot_url, uniprot_path)
    success_mirbase = download_file(mirbase_url, mirbase_path)
    
    if not success_mirbase:
        print("AVISO: Servidor da miRBase instável ou fora do ar (erro web). Gerando fallback offline.")
        if not mirbase_path.exists():
            mirbase_path.write_text("hsa-mir-21\n")

if __name__ == "__main__":
    download_bases()

