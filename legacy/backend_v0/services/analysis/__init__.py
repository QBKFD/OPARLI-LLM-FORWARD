# backend/services/analysis/__init__.py
"""
Analysis Services

Pure analysis logic shared between production agents and backtesting.
No database dependencies - only DataFrames and primitives.
"""

from services.analysis.market_structure import (
    MarketStructureAnalyzer,
    SwingPoint,
    OrderBlock,
    FairValueGap,
    get_market_structure_analyzer,
    create_market_structure_analyzer
)

from services.analysis.visual_analysis import (
    VisualAnalysisService,
    get_visual_analysis_service,
    create_visual_analysis_service
)

from services.analysis.meta_decision import (
    MetaDecisionService,
    get_meta_decision_service,
    create_meta_decision_service
)

__all__ = [
    # Market Structure
    'MarketStructureAnalyzer',
    'SwingPoint',
    'OrderBlock',
    'FairValueGap',
    'get_market_structure_analyzer',
    'create_market_structure_analyzer',
    # Visual Analysis
    'VisualAnalysisService',
    'get_visual_analysis_service',
    'create_visual_analysis_service',
    # Meta Decision
    'MetaDecisionService',
    'get_meta_decision_service',
    'create_meta_decision_service',
]
