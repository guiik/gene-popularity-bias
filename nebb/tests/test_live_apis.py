import pytest
from typing import Dict, Any
from pipeline import fetch_hgnc_api, fetch_uniprot_api, fetch_mygene_api

config: Dict[str, Any] = {"apis": {}} # Usa os baselines das func

# Os testes dependem de acesso direto à web real para provar resiliência!

def test_fetch_hgnc_real_success() -> None:
    source, val = fetch_hgnc_api("HMOX1", config)
    assert source == "HGNC"
    assert val is not None
    # HMOX1 is the canonical symbol, so we expect exactly that or it's an alias resolving to it.

def test_fetch_hgnc_real_invalid() -> None:
    source, val = fetch_hgnc_api("INVALID_GENE_12345XYZ", config)
    assert source == "HGNC"
    assert val is None

def test_fetch_uniprot_real_success() -> None:
    source, val = fetch_uniprot_api("mTOR", config)
    assert source == "UniProt"
    assert val is not None

def test_fetch_uniprot_real_invalid() -> None:
    source, val = fetch_uniprot_api("INVALID_PROT_12345XYZ", config)
    assert source == "UniProt"
    assert val is None

def test_fetch_mygene_real_success() -> None:
    source, val = fetch_mygene_api("hsa-mir-21", config)
    assert source == "MyGene"
    assert val is not None

def test_fetch_mygene_real_invalid() -> None:
    source, val = fetch_mygene_api("INVALID_MIR_12345XYZ", config)
    assert source == "MyGene"
    assert val is None
