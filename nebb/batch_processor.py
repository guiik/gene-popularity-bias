import os
import sys
import glob
import json
from pathlib import Path
from typing import Set, Dict, Any, List
from pipeline import PipelineOrchestrator, NormalizationRequest

def get_unique_strings_from_dir(path_dir: str) -> Set[str]:
    unique_entities: Set[str] = set()
    txt_files = glob.glob(os.path.join(path_dir, "*.txt"))
    for file_path in txt_files:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    unique_entities.add(line)
    return unique_entities

def limpar_prefixos(txt: str) -> str:
    # simple prefix cleansing
    if txt.lower().startswith("gene "):
        return txt[5:]
    if txt.lower().startswith("protein "):
        return txt[8:]
    return txt

def compile_gabarito(input_dir: str, output_file: str, config_path: str = "config_sources.json") -> bool:
    Path(input_dir).mkdir(exist_ok=True, parents=True)
    unique_entities = get_unique_strings_from_dir(input_dir)
    print(f"Extraídas {len(unique_entities)} entidades únicas do diretório {input_dir}.")

    orchestrator = PipelineOrchestrator(config_path)
    
    gabarito: Dict[str, Dict[str, Any]] = {}
    
    unique_entities_sorted = sorted(list(unique_entities))

    for i, raw_entity in enumerate(unique_entities_sorted, 1):
        print(f"[{i}/{len(unique_entities)}] Processando: {raw_entity}...", end=" ", flush=True)
        
        clean_entity = limpar_prefixos(raw_entity)
        request = NormalizationRequest(clean_entity)
        result = orchestrator.process_request(request)
        
        print(f"-> {result.primary_resolution_source} ({result.canonical_identity})")
        
        gene_upper = result.canonical_identity.upper() if result.canonical_identity else f"UNMAPPED_{clean_entity.upper()}"
        if gene_upper not in gabarito:
            gabarito[gene_upper] = {
                "canonical_gene": result.canonical_identity,
                "identidade_biologica": result.identidade_biologica if hasattr(result, 'identidade_biologica') else {},
                "termos_originais": [],
                "gene": {},
                "rnam": {},
                "proteina": {},
                "classificacao_composta": []
            }
        
        g_node = gabarito[gene_upper]
        
        # Se tiver identidade_biologica, mantem
        if not g_node.get("identidade_biologica") and hasattr(result, 'identidade_biologica'):
            g_node["identidade_biologica"] = result.identidade_biologica
        
        # Rastrear termo original
        if raw_entity not in g_node["termos_originais"]:
            g_node["termos_originais"].append(raw_entity)
        
        # Merge sets
        g_node["classificacao_composta"].extend(result.classification)
        g_node["classificacao_composta"] = list(set(g_node["classificacao_composta"]))
        
        hub = result.hub_metadata
        
        for key, sources in hub.get("gene", {}).items():
            if key not in g_node["gene"]: g_node["gene"][key] = []
            for s in sources:
                if s not in g_node["gene"][key]: g_node["gene"][key].append(s)
                
        for key, sources in hub.get("rnam", {}).items():
            if key not in g_node["rnam"]: g_node["rnam"][key] = []
            for s in sources:
                if s not in g_node["rnam"][key]: g_node["rnam"][key].append(s)
                
        for key, sources in hub.get("protein", {}).items():
            if key not in g_node["proteina"]: g_node["proteina"][key] = []
            for s in sources:
                if s not in g_node["proteina"][key]: g_node["proteina"][key].append(s)
        
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(gabarito, f, indent=4)
        
    print(f"Gabarito Agrupado compilado. Hub contém {len(gabarito)} genes âncoras em {output_file}.")
    return True

if __name__ == "__main__":
    compile_gabarito(sys.argv[1] if len(sys.argv) > 1 else "saida", "gold_standard.json")
