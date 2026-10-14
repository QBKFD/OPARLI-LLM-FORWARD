"""
Regime Classifier Module

Standalone 3-state regime classification for XAUUSD intraday data.
Uses Jump Models (Nystrup et al. 2020) with Sparse feature selection
and Random Forest next-regime prediction.
"""

from .regime_config import RegimeConfig
from .regime_features import RegimeFeatureEngine
from .regime_classifier import RegimeClassifier
from .regime_validator import RegimeValidator, ValidationReport, FoldResult

__all__ = [
    "RegimeConfig",
    "RegimeFeatureEngine",
    "RegimeClassifier",
    "RegimeValidator",
    "ValidationReport",
    "FoldResult",
]
