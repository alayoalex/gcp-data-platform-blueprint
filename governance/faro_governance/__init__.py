"""Faro: gobierno de datos como codigo.

Motor de politicas que evalua activos de datos -- nombres, datos personales y
contratos -- y devuelve hallazgos accionables. Corre como gate en el merge request,
como escaneo de un warehouse existente, o como libreria.
"""

from faro_governance.contracts import ContractEngine, DataContract, diff_contracts, load_contracts
from faro_governance.models import (
    Assessment,
    AssetType,
    Column,
    CriterionScore,
    DataAsset,
    Layer,
    Report,
    Sensitivity,
    Severity,
)
from faro_governance.naming import NamingEngine, NamingPolicy
from faro_governance.privacy import Classification, PrivacyCatalog, PrivacyClassifier, PrivacyEngine

__version__ = "0.1.0"

__all__ = [
    "Assessment",
    "AssetType",
    "Classification",
    "Column",
    "ContractEngine",
    "CriterionScore",
    "DataAsset",
    "DataContract",
    "Layer",
    "NamingEngine",
    "NamingPolicy",
    "PrivacyCatalog",
    "PrivacyClassifier",
    "PrivacyEngine",
    "Report",
    "Sensitivity",
    "Severity",
    "__version__",
    "diff_contracts",
    "load_contracts",
]
