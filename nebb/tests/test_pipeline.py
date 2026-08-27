import pytest
import os
import json
from typing import Any, Generator
from unittest.mock import patch
from pipeline import (
    NormalizationRequest,
    PipelineOrchestrator,
    normalizar_entidade_pura,
    DatabaseContext
)

@pytest.fixture(autouse=True)
def clear_cache() -> Generator[None, None, None]:
    """Roda cada teste com o cache vazio, sem destruir o cache real do repo.

    entity_cache.json versionado tem 3,7 MB de entidades ja resolvidas. Apagar e
    recriar seria pratico, mas os testes rodam a partir de nebb/ -- entao o alvo
    seria o arquivo de verdade. Por isso: guarda, zera, restaura.
    """
    backup = None
    if os.path.exists("entity_cache.json"):
        with open("entity_cache.json", "rb") as f:
            backup = f.read()
        os.remove("entity_cache.json")
    try:
        yield
    finally:
        if os.path.exists("entity_cache.json"):
            os.remove("entity_cache.json")
        if os.path.exists("config_sources_test.json"):
            os.remove("config_sources_test.json")
        if backup is not None:
            with open("entity_cache.json", "wb") as f:
                f.write(backup)

def test_integration_offline_hgnc() -> None:
    os.makedirs("data", exist_ok=True)
    with open("data/hgnc_test_mock.json", "w") as f:
        json.dump({"response": {"docs": [{"symbol": "HMOX1", "alias_symbol": ["HO-1"]}]}}, f)
    with open("config_sources_test.json", "w") as f:
        json.dump({"offline_paths": {"hgnc": "data/hgnc_test_mock.json"}}, f)
        
    orchestrator = PipelineOrchestrator("config_sources_test.json")
    request = NormalizationRequest("HO-1")
    result = orchestrator.process_request(request)
    assert result.canonical_identity == "HMOX1"
    assert result.primary_resolution_source == "HGNC"

def test_integration_offline_uniprot() -> None:
    os.makedirs("data", exist_ok=True)
    with open("data/uniprot_test_mock.tsv", "w") as f:
        f.write("Entry\tGene Names (primary)\n")
        f.write("P09601\tHMOX1\n")
    with open("config_sources_test.json", "w") as f:
        json.dump({"offline_paths": {"uniprot": "data/uniprot_test_mock.tsv"}}, f)
        
    orchestrator = PipelineOrchestrator("config_sources_test.json")
    request = NormalizationRequest("P09601")
    result = orchestrator.process_request(request)
    assert result.canonical_identity == "HMOX1"
    assert result.primary_resolution_source == "UniProt"

def test_pure_function_cascade() -> None:
    db = DatabaseContext(
        hgnc={"HO-1": {"canonical": "HMOX1", "field": "symbol", "rnam": [], "protein": [], "aliases": [], "prevs": []}},
        uniprot={"P123": {"canonical": "GENE1", "field": "accession"}},
        mirbase={"HSA-MIR-21": "MIR21"},
        fallback={"FallbackGene": "FBGENE"},
        config={}
    )
    req1 = NormalizationRequest("HO-1")
    res1 = normalizar_entidade_pura(req1, db)
    assert res1 is not None and res1.canonical_identity == "HMOX1"

    req2 = NormalizationRequest("P123")
    res2 = normalizar_entidade_pura(req2, db)
    assert res2 is not None and res2.canonical_identity == "GENE1"

    req3 = NormalizationRequest("Gene HEUR")
    res3 = normalizar_entidade_pura(req3, db)
    assert res3 is not None and res3.canonical_identity == "HEUR"
    
    req4 = NormalizationRequest("NO_MATCH")
    res4 = normalizar_entidade_pura(req4, db)
    assert res4 is None

def test_api_concurrency(requests_mock: Any) -> None:
    # Setup mock to fail offline and force remote
    with open("config_sources_test.json", "w") as f:
        json.dump({"offline_paths": {}, "apis": {
            "hgnc": {"endpoint": "http://mock.hgnc/", "timeout_seconds": 1},
            "uniprot": {"endpoint": "https://mock.uniprot/", "timeout_seconds": 1}
        }}, f)
        
    # Simulate an HTTP 500 error for HGNC
    requests_mock.get('http://mock.hgnc/NOTFOUND', status_code=500)
    # Simulate a successful JSON response for UniProt
    requests_mock.get('https://mock.uniprot/?query=(protein_name:NOTFOUND)&format=json', json={
        "results": [{"genes": [{"geneName": {"value": "API_PROT_GENE"}}]}]
    })

    orchestrator = PipelineOrchestrator("config_sources_test.json")
    request = NormalizationRequest("NOTFOUND")
    result = orchestrator.process_request(request)
    
    # UniProt should succeed due to parallelism + HGNC failing
    assert result.canonical_identity == "API_PROT_GENE"
    assert result.primary_resolution_source == "UniProt"

def test_api_both_fail(requests_mock: Any) -> None:
    with open("config_sources_test.json", "w") as f:
        json.dump({"offline_paths": {}, "apis": {
            "hgnc": {"endpoint": "http://mock.hgnc/", "timeout_seconds": 1},
            "uniprot": {"endpoint": "https://mock.uniprot/", "timeout_seconds": 1}
        }}, f)
        
    requests_mock.get('http://mock.hgnc/FAIL', status_code=500)
    requests_mock.get('https://mock.uniprot/?query=(protein_name:FAIL)&format=json', status_code=500)

    orchestrator = PipelineOrchestrator("config_sources_test.json")
    request = NormalizationRequest("FAIL")
    result = orchestrator.process_request(request)
    
    assert result.canonical_identity is None
    assert result.primary_resolution_source == "Unmapped"

def test_cache_bypasses_lookups() -> None:
    # Setup dummy orchestrator
    with open("config_sources_test.json", "w") as f:
        json.dump({"offline_paths": {}, "apis": {}}, f)
    orchestrator = PipelineOrchestrator("config_sources_test.json")
    
    # Inject fake DB
    orchestrator.db = DatabaseContext(
        hgnc={"HO-1": {"canonical": "HMOX1", "field": "symbol", "rnam": [], "protein": [], "aliases": [], "prevs": []}}, 
        uniprot={}, mirbase={}, fallback={}, config={}
    )
    
    # Request 1 -> Solved by HGNC, hits cache.set()
    req1 = NormalizationRequest("HO-1")
    res1 = orchestrator.process_request(req1)
    assert res1.canonical_identity == "HMOX1"
    
    # Wipe the database completely to prove it doesn't lookup anymore
    orchestrator.db = DatabaseContext({}, {}, {}, {}, {})
    
    # Request 2 -> Should hit cache.get() and return HMOX1!
    req2 = NormalizationRequest("HO-1")
    res2 = orchestrator.process_request(req2)
    assert res2.canonical_identity == "HMOX1"
    assert res2.primary_resolution_source == "HGNC"
    
    # Verify actual persistence
    assert os.path.exists("entity_cache.json")
    with open("entity_cache.json", "r") as f:
        data = json.load(f)
        assert "HO-1" in data
        assert data["HO-1"]["canonical_identity"] == "HMOX1"
