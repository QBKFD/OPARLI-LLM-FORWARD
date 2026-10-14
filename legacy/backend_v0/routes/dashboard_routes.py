# backend/routes/dashboard_routes.py
"""
Dashboard API routes

Protected endpoints for the agent dashboard.
Requires valid JWT token from /api/auth/login
"""

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import asyncio
import logging
import os
import sys
import traceback

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

# Store for active WebSocket connections (for progress updates)
analysis_websockets: List[WebSocket] = []


class AnalyzeRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframes: list = ["5min", "15min", "1h"]
    include_technical: bool = True


class AnalyzeResponse(BaseModel):
    symbol: str
    timestamp: str
    market_data: Dict[str, Any]
    visual: Optional[Dict[str, Any]] = None
    technical: Optional[Dict[str, Any]] = None
    sentiment: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    logs: Optional[List[Dict[str, Any]]] = None


async def broadcast_progress(stage: str, message: str, level: str = "info", data: Dict = None):
    """Broadcast progress update to all connected WebSocket clients"""
    update = {
        "type": "progress",
        "stage": stage,
        "message": message,
        "level": level,
        "timestamp": datetime.now().isoformat(),
        "data": data
    }

    disconnected = []
    for ws in analysis_websockets:
        try:
            await ws.send_json(update)
        except Exception:
            disconnected.append(ws)

    # Remove disconnected clients
    for ws in disconnected:
        analysis_websockets.remove(ws)


@router.websocket("/ws/progress")
async def analysis_progress_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time analysis progress updates"""
    await websocket.accept()
    analysis_websockets.append(websocket)
    logger.info("Dashboard WebSocket connected")

    try:
        while True:
            # Keep connection alive, handle pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket disconnected")
    finally:
        if websocket in analysis_websockets:
            analysis_websockets.remove(websocket)


@router.post("/analyze", response_model=AnalyzeResponse)
async def run_analysis(
    request: AnalyzeRequest,
    user: dict = Depends(get_current_user)
):
    """
    Run the full agent analysis pipeline with progress updates

    Returns results from all agents:
    - Visual Analyst (multiple timeframes)
    - Technical Analyst
    - Meta Agent (decision synthesis)
    - Risk Manager (validation)
    """
    logger.info(f"Dashboard analysis requested for {request.symbol}")
    logs = []  # Collect logs for response

    def add_log(agent: str, message: str, level: str = "info"):
        """Add log entry and broadcast via WebSocket"""
        log_entry = {
            "agent": agent,
            "message": message,
            "level": level,
            "time": datetime.now().strftime("%H:%M:%S")
        }
        logs.append(log_entry)
        logger.info(f"[{agent}] {message}")
        # Fire and forget the broadcast
        asyncio.create_task(broadcast_progress(agent.lower().replace(" ", "_"), message, level))

    try:
        add_log("System", "Starting analysis pipeline...", "info")

        # Import components
        from config.database import get_database

        db = get_database()

        # 1. Get latest market data
        add_log("System", "Fetching market data...", "info")
        await broadcast_progress("visual", "Fetching market data...", "info")

        try:
            with db.get_cursor() as cur:
                cur.execute("""
                    SELECT timestamp, open, high, low, close
                    FROM ohlcv_1min
                    WHERE symbol = %s
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, (request.symbol,))
                row = cur.fetchone()

            if not row:
                add_log("System", f"No data found for {request.symbol}", "error")
                raise HTTPException(status_code=404, detail=f"No data for {request.symbol}")

            market_data = {
                'price': float(row['close']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'timestamp': str(row['timestamp'])
            }
            add_log("System", f"Price: ${market_data['price']:.2f}", "info")

        except HTTPException:
            raise
        except Exception as e:
            add_log("System", f"Database error: {str(e)}", "error")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

        result = {
            'symbol': request.symbol,
            'timestamp': datetime.now().isoformat(),
            'market_data': market_data,
            'visual': None,
            'technical': None,
            'sentiment': None,
            'meta': None,
            'risk': None,
            'logs': logs
        }

        # 2. Generate charts and run Visual Analyst
        add_log("Visual Analyst", "Starting visual analysis...", "info")
        await broadcast_progress("visual", "Generating charts...", "info")

        try:
            from services.chart_generator import ChartGenerator, ChartConfig
            from agents.visual_analyst_agent import VisualAnalystAgent
            from agents.base_agent import Message, MessageType, AgentType

            chart_config = ChartConfig(
                timeframes=request.timeframes,
                lookback_bars=200,
                show_session_levels=True,
                save_to_file=True,
                output_dir='/tmp'
            )

            add_log("Visual Analyst", "Initializing chart generator...", "info")
            chart_gen = ChartGenerator(chart_config)

            add_log("Visual Analyst", "Initializing Visual Analyst agent...", "info")
            visual_analyst = VisualAnalystAgent()
            visual_analyst.is_active = True

            # Generate all charts with timeout
            add_log("Visual Analyst", f"Generating charts for {len(request.timeframes)} timeframes...", "info")

            try:
                all_charts = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: chart_gen.generate_charts(request.symbol)
                    ),
                    timeout=30.0  # 30 second timeout for chart generation
                )
            except asyncio.TimeoutError:
                add_log("Visual Analyst", "Chart generation timed out after 30s", "error")
                all_charts = {}

            visual_results = {}
            signals = []
            confidences = []

            for tf in request.timeframes:
                chart_data = all_charts.get(tf, {})
                screenshot = chart_data.get('image_base64')

                if screenshot:
                    add_log("Visual Analyst", f"Analyzing {tf} chart with AI...", "info")
                    await broadcast_progress("visual", f"Analyzing {tf} chart...", "info")

                    analysis_message = Message(
                        msg_type=MessageType.ANALYSIS_REQUEST,
                        sender=AgentType.SCANNER,
                        data={
                            'symbol': request.symbol,
                            'timeframe': tf,
                            'screenshot': screenshot,
                            'market_data': {
                                'price': market_data['price'],
                                'bid': market_data['price'] - 0.5,
                                'ask': market_data['price'] + 0.5
                            }
                        }
                    )

                    # Run visual analysis with timeout
                    try:
                        result_message = await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: visual_analyst.process_message(analysis_message)
                            ),
                            timeout=60.0  # 60 second timeout per timeframe
                        )

                        if result_message and result_message.data:
                            tf_result = result_message.data
                            # Normalize confidence to 0-1
                            conf = tf_result.get('confidence', 0)
                            if conf > 1:
                                conf = conf / 100
                            tf_result['confidence'] = conf

                            visual_results[tf] = tf_result
                            signals.append(tf_result.get('signal', 'NEUTRAL'))
                            confidences.append(conf)
                            add_log("Visual Analyst", f"{tf}: {tf_result.get('signal', 'N/A')} ({int(conf*100)}%)", "success")

                    except asyncio.TimeoutError:
                        add_log("Visual Analyst", f"{tf}: Analysis timed out", "warning")
                        visual_results[tf] = {
                            'signal': 'NEUTRAL',
                            'confidence': 0,
                            'error': 'Timeout'
                        }
                else:
                    add_log("Visual Analyst", f"{tf}: No chart generated", "warning")

            # Combine visual results
            if visual_results:
                long_count = signals.count('LONG')
                short_count = signals.count('SHORT')

                if long_count > short_count:
                    combined_signal = 'LONG'
                elif short_count > long_count:
                    combined_signal = 'SHORT'
                else:
                    combined_signal = 'NEUTRAL'

                avg_confidence = sum(confidences) / len(confidences) if confidences else 0

                result['visual'] = {
                    'signal': combined_signal,
                    'confidence': avg_confidence,
                    'timeframes': visual_results,
                    'reasoning': f"Analyzed {len(visual_results)} timeframes. "
                                f"Signals: {', '.join(signals)}"
                }
                add_log("Visual Analyst", f"Combined: {combined_signal} ({int(avg_confidence*100)}%)", "success")
            else:
                add_log("Visual Analyst", "No visual results available", "warning")
                result['visual'] = {
                    'signal': 'NEUTRAL',
                    'confidence': 0,
                    'error': 'No charts analyzed'
                }

        except Exception as e:
            error_msg = f"Visual analysis error: {str(e)}"
            add_log("Visual Analyst", error_msg, "error")
            logger.error(f"Visual analysis error: {traceback.format_exc()}")
            result['visual'] = {
                'signal': 'ERROR',
                'confidence': 0,
                'error': str(e)
            }

        # 3. Run Technical Analyst (if enabled)
        if request.include_technical:
            add_log("Technical Analyst", "Starting technical analysis...", "info")
            await broadcast_progress("technical", "Computing indicators...", "info")

            try:
                from agents.technicalanalyst_agent import TechnicalAnalystAgent
                tech_analyst = TechnicalAnalystAgent()

                tech_result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: tech_analyst.get_latest_analysis(request.symbol)
                    ),
                    timeout=30.0
                )

                if tech_result:
                    result['technical'] = {
                        'signal': tech_result.get('signal', 'NEUTRAL'),
                        'confidence': tech_result.get('confidence', 0),
                        'trend': tech_result.get('trend_direction', 'unknown'),
                        'patterns': tech_result.get('patterns', [])
                    }
                    add_log("Technical Analyst", f"Signal: {tech_result.get('signal', 'N/A')}", "success")
                else:
                    add_log("Technical Analyst", "No technical data available", "warning")
                    result['technical'] = {
                        'signal': 'NEUTRAL',
                        'confidence': 0,
                        'error': 'No data'
                    }

            except asyncio.TimeoutError:
                add_log("Technical Analyst", "Analysis timed out", "error")
                result['technical'] = {
                    'signal': 'NEUTRAL',
                    'confidence': 0,
                    'error': 'Timeout'
                }
            except Exception as e:
                add_log("Technical Analyst", f"Error: {str(e)}", "error")
                result['technical'] = {
                    'signal': 'ERROR',
                    'confidence': 0,
                    'error': str(e)
                }
        else:
            add_log("Technical Analyst", "Skipped (disabled)", "info")

        # 4. Run Meta Agent
        add_log("Meta Agent", "Synthesizing agent signals...", "info")
        await broadcast_progress("meta", "Making decision...", "info")

        try:
            from agents.meta_agent import MetaAgentNew

            meta_agent = MetaAgentNew()

            # Prepare analyses for meta agent
            analyses = {
                'technical': {
                    'signal': result['technical']['signal'] if result['technical'] else 'NEUTRAL',
                    'confidence': result['technical']['confidence'] if result['technical'] else 0,
                    'analysis': result['technical'] or {}
                },
                'visual': {
                    'signal': result['visual']['signal'] if result['visual'] else 'NEUTRAL',
                    'confidence': result['visual']['confidence'] if result['visual'] else 0,
                    'analysis': result['visual'] or {}
                },
                'sentiment': {
                    'signal': 'NEUTRAL',
                    'confidence': 0.5,
                    'analysis': {'note': 'Not implemented'}
                },
                'timestamp': datetime.now().isoformat()
            }

            meta_result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: meta_agent._make_pre_trade_decision(request.symbol, analyses)
                ),
                timeout=30.0
            )

            result['meta'] = {
                'decision': meta_result.get('decision', 'PASS'),
                'confidence': meta_result.get('confidence', 0),
                'weighted_score': meta_result.get('weighted_score', 0),
                'agreement': meta_result.get('agreement', 0),
                'reasoning': meta_result.get('reasoning', '')
            }
            add_log("Meta Agent", f"Decision: {meta_result.get('decision', 'N/A')}", "success")

        except asyncio.TimeoutError:
            add_log("Meta Agent", "Decision timed out", "error")
            result['meta'] = {
                'decision': 'ERROR',
                'confidence': 0,
                'error': 'Timeout'
            }
        except Exception as e:
            add_log("Meta Agent", f"Error: {str(e)}", "error")
            result['meta'] = {
                'decision': 'ERROR',
                'confidence': 0,
                'error': str(e)
            }

        # 5. Run Risk Manager (if trade signal)
        should_trade = result['meta'] and result['meta'].get('decision') not in ['PASS', 'NEUTRAL', 'ERROR']

        if should_trade:
            add_log("Risk Manager", "Validating trade parameters...", "info")
            await broadcast_progress("risk", "Checking risk limits...", "info")

            try:
                from agents.riskmanager_agent import RiskManagerAgentNew

                risk_manager = RiskManagerAgentNew()

                trade_proposal = {
                    'symbol': request.symbol,
                    'direction': result['meta']['decision'],
                    'entry_price': market_data['price'],
                    'confidence': result['meta']['confidence']
                }

                risk_result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: risk_manager.validate_trade_request(trade_proposal)
                    ),
                    timeout=15.0
                )

                result['risk'] = {
                    'checked': True,
                    'approved': risk_result.get('approved', False),
                    'position_size': risk_result.get('position_size'),
                    'stop_loss': risk_result.get('stop_loss'),
                    'take_profit': risk_result.get('take_profit'),
                    'rejection_reason': risk_result.get('rejection_reason')
                }

                if risk_result.get('approved'):
                    add_log("Risk Manager", "Trade APPROVED", "success")
                else:
                    add_log("Risk Manager", f"Trade REJECTED: {risk_result.get('rejection_reason', 'Unknown')}", "warning")

            except asyncio.TimeoutError:
                add_log("Risk Manager", "Validation timed out", "error")
                result['risk'] = {
                    'checked': True,
                    'approved': False,
                    'error': 'Timeout'
                }
            except Exception as e:
                add_log("Risk Manager", f"Error: {str(e)}", "error")
                result['risk'] = {
                    'checked': True,
                    'approved': False,
                    'error': str(e)
                }
        else:
            add_log("Risk Manager", "No trade signal - skipping", "info")
            result['risk'] = {
                'checked': False,
                'approved': False,
                'rejection_reason': 'No trade signal'
            }

        add_log("System", "Analysis complete!", "success")
        await broadcast_progress("complete", "Analysis complete", "success", result)

        result['logs'] = logs
        logger.info(f"Dashboard analysis complete: {result['meta'].get('decision', 'N/A')}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Analysis failed: {str(e)}"
        add_log("System", error_msg, "error")
        logger.error(f"Dashboard analysis failed: {traceback.format_exc()}")
        await broadcast_progress("error", error_msg, "error")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze-simple")
async def run_simple_analysis(
    request: AnalyzeRequest,
    user: dict = Depends(get_current_user)
):
    """
    Run a simplified analysis without visual AI (faster, for testing)

    Only runs Technical Analyst and Meta Agent
    """
    logger.info(f"Simple analysis requested for {request.symbol}")
    logs = []

    def add_log(agent: str, message: str, level: str = "info"):
        logs.append({
            "agent": agent,
            "message": message,
            "level": level,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        logger.info(f"[{agent}] {message}")

    try:
        add_log("System", "Starting simple analysis...", "info")

        from config.database import get_database
        db = get_database()

        # Get market data
        with db.get_cursor() as cur:
            cur.execute("""
                SELECT timestamp, open, high, low, close
                FROM ohlcv_1min
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (request.symbol,))
            row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"No data for {request.symbol}")

        market_data = {
            'price': float(row['close']),
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'timestamp': str(row['timestamp'])
        }

        result = {
            'symbol': request.symbol,
            'timestamp': datetime.now().isoformat(),
            'market_data': market_data,
            'visual': {
                'signal': 'NEUTRAL',
                'confidence': 0,
                'reasoning': 'Visual analysis skipped in simple mode'
            },
            'technical': None,
            'sentiment': None,
            'meta': None,
            'risk': None,
            'logs': logs
        }

        # Technical Analysis
        add_log("Technical Analyst", "Running technical analysis...", "info")
        try:
            from agents.technicalanalyst_agent import TechnicalAnalystAgent
            tech_analyst = TechnicalAnalystAgent()
            tech_result = tech_analyst.get_latest_analysis(request.symbol)

            if tech_result:
                result['technical'] = {
                    'signal': tech_result.get('signal', 'NEUTRAL'),
                    'confidence': tech_result.get('confidence', 0),
                    'trend': tech_result.get('trend_direction', 'unknown'),
                    'patterns': tech_result.get('patterns', [])
                }
                add_log("Technical Analyst", f"Signal: {tech_result.get('signal')}", "success")
            else:
                result['technical'] = {'signal': 'NEUTRAL', 'confidence': 0}
                add_log("Technical Analyst", "No data available", "warning")
        except Exception as e:
            add_log("Technical Analyst", f"Error: {str(e)}", "error")
            result['technical'] = {'signal': 'ERROR', 'confidence': 0, 'error': str(e)}

        # Meta Agent
        add_log("Meta Agent", "Making decision...", "info")
        try:
            from agents.meta_agent import MetaAgentNew
            meta_agent = MetaAgentNew()

            analyses = {
                'technical': {
                    'signal': result['technical']['signal'] if result['technical'] else 'NEUTRAL',
                    'confidence': result['technical']['confidence'] if result['technical'] else 0,
                    'analysis': result['technical'] or {}
                },
                'visual': {'signal': 'NEUTRAL', 'confidence': 0, 'analysis': {}},
                'sentiment': {'signal': 'NEUTRAL', 'confidence': 0.5, 'analysis': {}},
                'timestamp': datetime.now().isoformat()
            }

            meta_result = meta_agent._make_pre_trade_decision(request.symbol, analyses)
            result['meta'] = {
                'decision': meta_result.get('decision', 'PASS'),
                'confidence': meta_result.get('confidence', 0),
                'weighted_score': meta_result.get('weighted_score', 0),
                'agreement': meta_result.get('agreement', 0),
                'reasoning': meta_result.get('reasoning', '')
            }
            add_log("Meta Agent", f"Decision: {meta_result.get('decision')}", "success")
        except Exception as e:
            add_log("Meta Agent", f"Error: {str(e)}", "error")
            result['meta'] = {'decision': 'ERROR', 'confidence': 0, 'error': str(e)}

        # Risk check skipped in simple mode
        result['risk'] = {
            'checked': False,
            'approved': False,
            'rejection_reason': 'Simple mode - no trade execution'
        }

        add_log("System", "Simple analysis complete!", "success")
        result['logs'] = logs
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Simple analysis failed: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_system_status(user: dict = Depends(get_current_user)):
    """Get current system status"""
    try:
        from config.database import get_database

        db = get_database()

        # Get latest data timestamp
        with db.get_cursor() as cur:
            cur.execute("""
                SELECT MAX(timestamp) as latest
                FROM ohlcv_1min
                WHERE symbol = 'XAUUSD'
            """)
            row = cur.fetchone()

        latest_data = str(row['latest']) if row and row['latest'] else None

        return {
            'status': 'online',
            'database': 'connected',
            'latest_data': latest_data,
            'timestamp': datetime.now().isoformat(),
            'websocket_clients': len(analysis_websockets)
        }

    except Exception as e:
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }


@router.get("/history")
async def get_analysis_history(
    limit: int = 20,
    user: dict = Depends(get_current_user)
):
    """Get recent analysis history (placeholder for future implementation)"""
    # TODO: Store and retrieve analysis history from database
    return {
        'message': 'Analysis history not yet implemented',
        'entries': []
    }
