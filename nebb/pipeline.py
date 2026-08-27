from dataclasses import dataclass, field
from typing import Literal, Dict, Any, Optional, List
import json
import re
import requests
from pathlib import Path
from cache import EntidadeCache
from concurrent.futures import ThreadPoolExecutor, as_completed

@dataclass
class NormalizationRequest:
    raw_string: str

@dataclass
class SemanticEntity:
    original_string: str
    canonical_identity: str | None
    entity_type: Literal["Gene", "Protein", "miRNA", "Variant", "Unknown"]
    primary_resolution_source: Literal["HGNC", "UniProt", "miRBase", "MyGene", "Heuristic", "Fallback", "Unmapped"]
    pipeline_path: list[str]
    confidence_level: float
    used_heuristic: bool
    classification: List[str]
    identidade_biologica: Dict[str, str] = field(default_factory=dict)
    hub_metadata: Dict[str, Dict[str, List[str]]] = field(default_factory=lambda: {"gene": {}, "rnam": {}, "protein": {}})

@dataclass
class DatabaseContext:
    hgnc: Dict[str, Dict[str, Any]]
    uniprot: Dict[str, Dict[str, Any]]
    mirbase: Dict[str, str]
    fallback: Dict[str, str]
    config: Dict[str, Any]

# === FUNÇÕES PURAS (SEM I/O) ===
def aplicar_heuristica(entity: str) -> str | None:
    match = re.search(r'(?i)(?:gene|protein)\s+([a-zA-Z0-9]+)', entity)
    if match:
        return match.group(1).upper()
    return None

def normalizar_entidade_pura(request: NormalizationRequest, db: DatabaseContext) -> SemanticEntity | None:
    raw = request.raw_string
    up_raw = raw.upper()
    
    matches_found = []
    classifications = []
    hub_meta: Dict[str, Dict[str, List[str]]] = {"gene": {}, "rnam": {}, "protein": {}}
    path = []
    
    # 1. HGNC
    hgnc_match = db.hgnc.get(up_raw)
    if hgnc_match:
        classifications.append(f"Gene ({hgnc_match['field']})")
        for r in hgnc_match.get("rnam", []): hub_meta["rnam"][r] = ["HGNC"]
        for p in hgnc_match.get("protein", []): hub_meta["protein"][p] = ["HGNC"]
        matches_found.append({"canonical": hgnc_match["canonical"], "source": "HGNC", "type": "Gene"})
        path.append("HGNC: Hit")
    else:
        path.append("HGNC: Miss")
        
    # 2. UniProt
    uniprot_match = db.uniprot.get(up_raw)
    if uniprot_match:
        classifications.append(f"Proteína ({uniprot_match['field']})")
        hub_meta["protein"][up_raw] = ["UniProt"]
        matches_found.append({"canonical": uniprot_match["canonical"], "source": "UniProt", "type": "Protein"})
        path.append("UniProt: Hit")
    else:
        path.append("UniProt: Miss")
        
    # 3. miRBase
    mirbase_match = db.mirbase.get(up_raw)
    if mirbase_match:
        classifications.append("miRNA (Alias)")
        hub_meta["rnam"][mirbase_match] = ["miRBase"]
        matches_found.append({"canonical": mirbase_match, "source": "miRBase", "type": "miRNA"})
        path.append("miRBase: Hit")
    else:
        path.append("miRBase: Miss")
        
    # 4. Heurística
    heur_match = aplicar_heuristica(raw)
    if heur_match:
        classifications.append("Regex (Heuristic)")
        matches_found.append({"canonical": heur_match, "source": "Heuristic", "type": "Unknown"})
        path.append("Heuristic: Hit")
    else:
        path.append("Heuristic: Miss")
        
    # 5. Fallback Manual
    fb_match = db.fallback.get(raw)
    if fb_match:
        classifications.append("Manual (Fallback)")
        matches_found.append({"canonical": fb_match, "source": "Fallback", "type": "Unknown"})
        path.append("Fallback: Hit")
    else:
        path.append("Fallback: Miss")
        
    if not matches_found:
        return None
        
    primary = matches_found[0]
    from typing import cast
    src_literal = cast(Literal["HGNC", "UniProt", "miRBase", "MyGene", "Heuristic", "Fallback", "Unmapped"], primary["source"])
    type_literal = cast(Literal["Gene", "Protein", "miRNA", "Variant", "Unknown"], primary["type"])
    
    return SemanticEntity(
        original_string=raw,
        canonical_identity=primary["canonical"],
        entity_type=type_literal,
        primary_resolution_source=src_literal,
        pipeline_path=path,
        confidence_level=1.0 if primary["source"] in ["HGNC", "UniProt"] else 0.5,
        used_heuristic=bool(heur_match),
        classification=list(set(classifications)),
        hub_metadata=hub_meta
    )

# === CHAMADAS API ASSÍNCRONAS / THREADED (I/O BOUND) ===
def fetch_hgnc_api(entity: str, config: Dict[str, Any]) -> tuple[Literal["HGNC"], str | None]:
    api_config = config.get("apis", {}).get("hgnc", {})
    endpoint = api_config.get("endpoint", "http://rest.genenames.org/search/")
    timeout = api_config.get("timeout_seconds", 5)
    from urllib.parse import quote
    url = f"{endpoint}{quote(entity)}"
    try:
        response = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        docs = data.get("response", {}).get("docs", [])
        if docs:
            from typing import cast
            return "HGNC", cast(str | None, docs[0].get("symbol"))
    except Exception:
        pass
    return "HGNC", None

def fetch_uniprot_api(entity: str, config: Dict[str, Any]) -> tuple[Literal["UniProt"], Optional[Dict[str, Any]]]:
    api_config = config.get("apis", {}).get("uniprot", {})
    endpoint = api_config.get("endpoint", "https://rest.uniprot.org/uniprotkb/search")
    timeout = api_config.get("timeout_seconds", 10)
    from urllib.parse import quote
    url = f"{endpoint}?query=(protein_name:{quote(entity)})&format=json"
    try:
        response = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if results:
            genes = results[0].get("genes", [])
            primary_gene = None
            if genes:
                gene_name = genes[0].get("geneName", {})
                if gene_name:
                    primary_gene = gene_name.get("value")
            
            if primary_gene:
                organism = results[0].get("organism", {})
                sci_name = organism.get("scientificName", "Unknown")
                tax_id = str(organism.get("taxonId", "Unknown"))
                
                rnams = []
                proteins = [results[0].get("primaryAccession")] if results[0].get("primaryAccession") else []
                for dbr in results[0].get("uniProtKBCrossReferences", []):
                    ref_type = dbr.get("database") or dbr.get("type")
                    if ref_type in ["RefSeq", "Ensembl"]:
                        r_id = dbr.get("id")
                        if r_id: rnams.append(r_id)
                
                payload = {
                    "canonical": primary_gene,
                    "organism_name": sci_name,
                    "organism_id": tax_id,
                    "rnam": rnams,
                    "protein": proteins
                }
                return "UniProt", payload
    except Exception:
        pass
    return "UniProt", None

def fetch_mygene_api(entity: str, config: Dict[str, Any]) -> tuple[Literal["MyGene"], str | None]:
    api_config = config.get("apis", {}).get("mygene", {})
    endpoint = api_config.get("endpoint", "https://mygene.info/v3/query")
    timeout = api_config.get("timeout_seconds", 10)
    from urllib.parse import quote
    url = f"{endpoint}?q={quote(entity)}&species=human&limit=1"
    try:
        response = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        hits = data.get("hits", [])
        if hits:
            symbol = hits[0].get("symbol")
            if symbol:
                from typing import cast
                return "MyGene", cast(str | None, symbol)
    except Exception:
        pass
    return "MyGene", None

# === ORQUESTRADOR ===
class PipelineOrchestrator:
    def __init__(self, config_path: str = "config_sources.json") -> None:
        self.cache = EntidadeCache()
        self.db = self._load_databases(config_path)

    def _load_databases(self, config_path: str) -> DatabaseContext:
        config: Dict[str, Any] = {"offline_paths": {}, "apis": {}}
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception:
            pass

        hgnc: Dict[str, Dict[str, Any]] = {}
        uniprot: Dict[str, Dict[str, Any]] = {}
        mirbase: Dict[str, str] = {}
        fallback: Dict[str, str] = {"P53_GENE": "TP53", "WeirdName": "WNAME", "FailingEntity": "REC1"}

        paths = config.get("offline_paths", {})
        
        # Load HGNC
        hgnc_path = paths.get("hgnc", "")
        if Path(hgnc_path).exists():
            try:
                with open(hgnc_path, "r") as f:
                    data = json.load(f)
                docs = data.get("response", {}).get("docs", [])
                for doc in docs:
                    try:
                        sym = doc.get("symbol")
                        if not sym or not isinstance(sym, str): continue
                        
                        rnam = doc.get("refseq_accession", [])
                        if isinstance(rnam, str): rnam = [rnam]
                        ens = doc.get("ensembl_gene_id", [])
                        if isinstance(ens, str): ens = [ens]
                        prot = doc.get("uniprot_ids", [])
                        if isinstance(prot, str): prot = [prot]
                        
                        rnam_clean = [r.strip() for r in (rnam + ens) if isinstance(r, str)]
                        prot_clean = [p.strip() for p in prot if isinstance(p, str)]
                        
                        sym_canon = sym.strip()
                        sym_upper = sym_canon.upper()
                        
                        aliases = doc.get("alias_symbol", [])
                        if isinstance(aliases, str): aliases = [aliases]
                        aliases_clean = [a.strip() for a in aliases if isinstance(a, str)] if isinstance(aliases, list) else []

                        prevs = doc.get("prev_symbol", [])
                        if isinstance(prevs, str): prevs = [prevs]
                        prevs_clean = [p.strip() for p in prevs if isinstance(p, str)] if isinstance(prevs, list) else []

                        base_meta = {"canonical": sym_canon, "rnam": rnam_clean, "protein": prot_clean, "aliases": aliases_clean, "prevs": prevs_clean}
                        
                        hgnc[sym_upper] = {**base_meta, "field": "symbol"}
                        
                        name = doc.get("name")
                        if name and isinstance(name, str):
                            hgnc[name.strip().upper()] = {**base_meta, "field": "name"}
                            
                        # aliases map
                        for alias in aliases_clean:
                            hgnc[alias.upper()] = {**base_meta, "field": "alias_symbol"}
                                    
                        # prev_symbols map
                        for prev in prevs_clean:
                            hgnc[prev.upper()] = {**base_meta, "field": "prev_symbol"}
                    except Exception:
                        continue # Safely skip bad mappings
            except Exception: pass

        # Load UniProt
        uniprot_path = paths.get("uniprot", "")
        if Path(uniprot_path).exists():
            try:
                with open(uniprot_path, "r") as f:
                    # fields expected: accession, gene_primary, organism_name, organism_id (if we add them later or ignore if missing)
                    next(f, None)
                    for line in f:
                        try:
                            line = line.strip()
                            if line:
                                parts = line.split("\t")
                                if len(parts) >= 2:
                                    acc = parts[0].strip()
                                    gene_part = parts[1].strip()
                                    organism_name = parts[2].strip() if len(parts) > 2 else "Homo sapiens (Human)"
                                    organism_id = parts[3].strip() if len(parts) > 3 else "9606"
                                    if gene_part:
                                        uniprot[acc.upper()] = {"canonical": gene_part, "field": "accession", "organism_name": organism_name, "organism_id": organism_id}
                        except Exception:
                            continue
            except Exception: pass

        # Load miRBase
        mirbase_path = paths.get("mirbase", "")
        if Path(mirbase_path).exists():
            try:
                with open(mirbase_path, "r") as f:
                    for line in f:
                        try:
                            line = line.strip()
                            if line: 
                                mirbase[line.upper()] = line.upper().replace("-", "")
                        except Exception:
                            continue
            except Exception: pass

        return DatabaseContext(hgnc, uniprot, mirbase, fallback, config)

    def _assemble_universal_hub(self, entity: SemanticEntity) -> None:
        if not entity.canonical_identity:
            return
            
        if "gene" not in entity.hub_metadata:
            entity.hub_metadata["gene"] = {}
        if "rnam" not in entity.hub_metadata:
            entity.hub_metadata["rnam"] = {}
        if "protein" not in entity.hub_metadata:
            entity.hub_metadata["protein"] = {}
            
        canon_up = entity.canonical_identity.upper()
        hgnc_node = self.db.hgnc.get(canon_up)
        
        genes = [entity.canonical_identity]
        if hgnc_node:
            genes.extend(hgnc_node.get("aliases", []))
            genes.extend(hgnc_node.get("prevs", []))
            
            for r in hgnc_node.get("rnam", []):
                if r not in entity.hub_metadata["rnam"]: entity.hub_metadata["rnam"][r] = []
                if "HGNC" not in entity.hub_metadata["rnam"][r]: entity.hub_metadata["rnam"][r].append("HGNC")
            
            for p in hgnc_node.get("protein", []):
                if p not in entity.hub_metadata["protein"]: entity.hub_metadata["protein"][p] = []
                if "HGNC" not in entity.hub_metadata["protein"][p]: entity.hub_metadata["protein"][p].append("HGNC")
        
        for g in genes:
            if g not in entity.hub_metadata["gene"]: entity.hub_metadata["gene"][g] = []
            src = "HGNC" if hgnc_node else entity.primary_resolution_source
            if src not in entity.hub_metadata["gene"][g]: entity.hub_metadata["gene"][g].append(src)
            
        # Try finding biological identity if it maps back to Uniprot accession
        # (Though canonical identity is gene symbol usually, we can iterate Uniprot to find biological identity if needed)
        # Using a highly simplified cross layer if primary source was Uniprot
        if entity.primary_resolution_source == "UniProt":
            for acc, info in self.db.uniprot.items():
                if info.get("canonical") == entity.canonical_identity:
                    entity.identidade_biologica = {
                        "organism_name": info.get("organism_name", "Unknown"),
                        "organism_id": info.get("organism_id", "Unknown")
                    }
                    path_prot = acc # Which is the UniProt ID
                    if path_prot not in entity.hub_metadata["protein"]: entity.hub_metadata["protein"][path_prot] = []
                    if "UniProt" not in entity.hub_metadata["protein"][path_prot]: entity.hub_metadata["protein"][path_prot].append("UniProt")
                    break

    def process_request(self, request: NormalizationRequest) -> SemanticEntity:
        raw = request.raw_string
        
        cached = self.cache.get(raw)
        if cached is not None:
             # Fast cast of legacy cache objects or serialized SemanticEntity
             # In production, Cache should store the full JSON of SemanticEntity.
             # We assume here that cache.get() might return full dictionaries or just basic ones.
             # To ensure 100% fidelity, we must recreate the SemanticEntity.
             import json
             if "original_string" in cached:
                 return SemanticEntity(**cached)
             else:
                 return SemanticEntity(
                     original_string=raw,
                     canonical_identity=cached.get("canonical_gene"),
                     entity_type="Unknown", 
                     primary_resolution_source=cached.get("source", "Unmapped"),
                     pipeline_path=["Cache: Hit (Legacy)"],
                     confidence_level=1.0,
                     used_heuristic=False,
                     classification=["Cached Record (Legacy)"],
                     identidade_biologica=cached.get("identidade_biologica", {}),
                     hub_metadata=cached.get("hub_metadata", {"gene": {}, "rnam": {}, "protein": {}})
                 )

        pure_res = normalizar_entidade_pura(request, self.db)
        if pure_res is not None:
            self._assemble_universal_hub(pure_res)
            import dataclasses
            self.cache.set(raw, dataclasses.asdict(pure_res))
            return pure_res

        path = ["HGNC: Miss", "UniProt: Miss", "miRBase: Miss", "Heuristic: Miss", "Fallback: Miss"]
        api_result = None
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_hgnc = executor.submit(fetch_hgnc_api, raw, self.db.config)
            future_uniprot = executor.submit(fetch_uniprot_api, raw, self.db.config)
            future_mygene = executor.submit(fetch_mygene_api, raw, self.db.config)
            
            futures: list[Any] = [future_hgnc, future_uniprot, future_mygene]
            hgnc_ok: str | None = None
            uniprot_ok: Dict[str, Any] | None = None
            mygene_ok: str | None = None
            
            for f in as_completed(futures):
                source, value = f.result()
                if source == "HGNC" and value is not None: hgnc_ok = value
                elif source == "UniProt" and value is not None: uniprot_ok = value
                elif source == "MyGene" and value is not None: mygene_ok = value

        if hgnc_ok is not None:
            path.append("HGNC API: Hit")
            api_result = SemanticEntity(raw, hgnc_ok, "Gene", "HGNC", path, 1.0, False, ["API Resolution"], {}, {"gene": {}, "rnam": {}, "protein": {}})
        elif uniprot_ok is not None:
            path.append("UniProt API: Hit")
            ident_bio = {
                "organism_name": uniprot_ok.get("organism_name", "Unknown"),
                "organism_id": uniprot_ok.get("organism_id", "Unknown")
            }
            hub_meta: Dict[str, Dict[str, List[str]]] = {"gene": {}, "rnam": {}, "protein": {}}
            for r in uniprot_ok.get("rnam", []):
                hub_meta["rnam"][r] = ["UniProt API"]
            for p in uniprot_ok.get("protein", []):
                hub_meta["protein"][p] = ["UniProt API"]
            api_result = SemanticEntity(raw, uniprot_ok["canonical"], "Protein", "UniProt", path, 1.0, False, ["API Resolution"], ident_bio, hub_meta)
        elif mygene_ok is not None:
            path.append("MyGene API: Hit")
            api_result = SemanticEntity(raw, mygene_ok, "Gene", "MyGene", path, 1.0, False, ["API Resolution"], {}, {"gene": {}, "rnam": {}, "protein": {}})
        else:
            path.append("API: Miss")
            api_result = SemanticEntity(raw, None, "Unknown", "Unmapped", path, 0.0, False, ["Unmapped"], {}, {"gene": {}, "rnam": {}, "protein": {}})

        self._assemble_universal_hub(api_result)

        import dataclasses
        self.cache.set(raw, dataclasses.asdict(api_result))
        return api_result
