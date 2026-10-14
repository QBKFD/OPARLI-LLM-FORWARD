# backend/core/agent_orchestrator.py
"""
Agent Orchestrator

Central controller that wires all agents together
Manages agent lifecycle, message routing, and scheduling
"""

import logging
from typing import Dict, Optional, List
from datetime import datetime

from agents.base_agent import BaseAgent, AgentType, Message
from agents.scanner_agent import ScannerAgentNew
from agents.technicalanalyst_agent import TechnicalAnalystAgent
from agents.visual_analyst_agent import VisualAnalystAgent
from agents.sentimentanalyst_agent import SentimentAnalystAgent
from agents.meta_agent import MetaAgentNew
from agents.riskmanager_agent import RiskManagerAgentNew
from agents.execution_agent import ExecutionAgentNew
from agents.trademanager_agent import TradeManagerAgent

from core.message_bus import get_message_bus
from core.scheduler import get_scheduler

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Agent Orchestrator - Central system controller

    Responsibilities:
    1. Initialize all agents
    2. Register agents with message bus
    3. Schedule agents with scheduler
    4. Manage agent lifecycle (start/stop)
    5. Monitor system health
    6. Provide system statistics

    Agent Execution Schedule:
    - Scanner: Every 1 second (event-driven trigger checks)
    - Trade Manager: Every 1 second (monitors active trades)
    - Technical Analyst: On-demand (responds to Scanner)
    - Visual Analyst: On-demand (responds to Scanner)
    - Meta-Agent: On-demand (responds to analyses)
    - Risk Manager: On-demand (responds to Meta-Agent)
    - Execution: On-demand (responds to Risk Manager)
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

        # Get singletons
        self.message_bus = get_message_bus()
        self.scheduler = get_scheduler()

        # Agent instances
        self.agents: Dict[AgentType, BaseAgent] = {}

        # System state
        self.running = False
        self.start_time: Optional[datetime] = None

        logger.info("✓ Agent Orchestrator initialized")

    def initialize_agents(self):
        """
        Initialize all agents

        Creates agent instances and configures them
        """
        logger.info("Initializing agents...")

        try:
            # Get agent configs from main config
            agent_configs = self.config.get('agents', {})

            # Initialize each agent
            self.agents[AgentType.SCANNER] = ScannerAgentNew(
                config=agent_configs.get('scanner', {})
            )

            self.agents[AgentType.TECHNICAL_ANALYST] = TechnicalAnalystAgent(
                config=agent_configs.get('technical_analyst', {})
            )

            self.agents[AgentType.VISUAL_ANALYST] = VisualAnalystAgent(
                config=agent_configs.get('visual_analyst', {})
            )

            # The Meta-Agent blocks until all three analysts report, so leaving
            # Sentiment out of the roster stalled every decision permanently.
            self.agents[AgentType.SENTIMENT_ANALYST] = SentimentAnalystAgent(
                config=agent_configs.get('sentiment_analyst', {})
            )

            self.agents[AgentType.META_AGENT] = MetaAgentNew(
                config=agent_configs.get('meta_agent', {})
            )

            self.agents[AgentType.RISK_MANAGER] = RiskManagerAgentNew(
                config=agent_configs.get('risk_manager', {})
            )

            self.agents[AgentType.EXECUTION] = ExecutionAgentNew(
                config=agent_configs.get('execution', {})
            )

            self.agents[AgentType.TRADE_MANAGER] = TradeManagerAgent(
                config=agent_configs.get('trade_manager', {})
            )

            logger.info(f"✓ Initialized {len(self.agents)} agents")

        except Exception as e:
            logger.error(f"Error initializing agents: {e}", exc_info=True)
            raise

    def register_agents_with_message_bus(self):
        """
        Register all agents with message bus

        Connects each agent's process_message method to the bus
        """
        logger.info("Registering agents with message bus...")

        for agent_type, agent in self.agents.items():
            # Register agent's process_message as callback
            self.message_bus.register_agent(agent_type, agent.process_message)

        logger.info(f"✓ Registered {len(self.agents)} agents with message bus")

    def activate_agents(self):
        """
        Activate all registered agents.

        Agents gate their own work on `is_active`; an inactive agent silently
        drops every message it receives.
        """
        for agent in self.agents.values():
            agent.activate()

        logger.info(f"✓ Activated {len(self.agents)} agents")

    def schedule_agents(self):
        """
        Schedule agents with scheduler

        Sets up execution intervals for each agent
        """
        logger.info("Scheduling agents...")

        # Scanner: Every 1 second (event-driven trigger checks)
        self.scheduler.schedule_task(
            agent_type=AgentType.SCANNER,
            callback=self._run_scanner,
            interval_seconds=1.0,
            enabled=True
        )

        # Trade Manager: Every 1 second (monitors active trades)
        self.scheduler.schedule_task(
            agent_type=AgentType.TRADE_MANAGER,
            callback=self._run_trade_manager,
            interval_seconds=1.0,
            enabled=True
        )

        # Other agents are on-demand (message-driven)
        # They don't need scheduled tasks, they respond to messages

        logger.info("✓ Scheduled agents")

    def _run_scanner(self):
        """
        Run Scanner Agent

        Called by scheduler every second
        """
        try:
            # Run Scanner's event-driven checks
            messages = self.agents[AgentType.SCANNER].run()

            # Send any messages generated by Scanner
            for message in messages:
                self.message_bus.send_message(message)

            # Process any pending messages for Scanner
            self.message_bus.process_messages(AgentType.SCANNER, max_messages=10)

        except Exception as e:
            logger.error(f"Error running Scanner: {e}", exc_info=True)

    def _run_trade_manager(self):
        """
        Run Trade Manager Agent

        Called by scheduler every second
        """
        try:
            # Run Trade Manager's monitoring logic
            messages = self.agents[AgentType.TRADE_MANAGER].run()

            # Send any messages generated by Trade Manager
            for message in messages:
                self.message_bus.send_message(message)

            # Process any pending messages for Trade Manager
            self.message_bus.process_messages(AgentType.TRADE_MANAGER, max_messages=10)

        except Exception as e:
            logger.error(f"Error running Trade Manager: {e}", exc_info=True)

    def process_agent_messages(self):
        """
        Process pending messages for all on-demand agents

        Called periodically to ensure message queues don't back up
        """
        on_demand_agents = [
            AgentType.TECHNICAL_ANALYST,
            AgentType.VISUAL_ANALYST,
            AgentType.SENTIMENT_ANALYST,
            AgentType.META_AGENT,
            AgentType.RISK_MANAGER,
            AgentType.EXECUTION
        ]

        for agent_type in on_demand_agents:
            try:
                self.message_bus.process_messages(agent_type, max_messages=10)
            except Exception as e:
                logger.error(f"Error processing messages for {agent_type.value}: {e}")

    def start(self):
        """
        Start the orchestrator

        Initializes agents, registers with bus, starts scheduler
        """
        if self.running:
            logger.warning("Orchestrator already running")
            return

        try:
            logger.info("=" * 60)
            logger.info("Starting Agent Orchestrator...")
            logger.info("=" * 60)

            # 1. Initialize all agents
            self.initialize_agents()

            # 2. Register agents with message bus
            self.register_agents_with_message_bus()

            # 3. Activate agents. Every agent short-circuits its run() and
            #    process_message() on `is_active`, which defaults to False, so
            #    without this the whole pipeline started up idle and silent.
            self.activate_agents()

            # 4. Schedule agents
            self.schedule_agents()

            # 5. Start scheduler
            self.scheduler.start()

            self.running = True
            self.start_time = datetime.utcnow()

            logger.info("=" * 60)
            logger.info("✓ Agent Orchestrator started successfully")
            logger.info("=" * 60)

            # Log system status
            self._log_system_status()

        except Exception as e:
            logger.error(f"Error starting orchestrator: {e}", exc_info=True)
            self.stop()
            raise

    def stop(self):
        """
        Stop the orchestrator

        Stops scheduler and cleans up
        """
        if not self.running:
            return

        logger.info("Stopping Agent Orchestrator...")

        # Stop scheduler
        self.scheduler.stop()

        self.running = False

        logger.info("✓ Agent Orchestrator stopped")

    def _log_system_status(self):
        """
        Log current system status
        """
        logger.info("")
        logger.info("System Status:")
        logger.info(f"  Agents: {len(self.agents)}")
        logger.info(f"  Message Bus: Active")
        logger.info(f"  Scheduler: Running")
        logger.info("")
        logger.info("Agent Status:")
        for agent_type, agent in self.agents.items():
            status = "ACTIVE" if agent.is_active else "INACTIVE"
            logger.info(f"  {agent_type.value:20s}: {status}")
        logger.info("")

    def get_system_stats(self) -> Dict:
        """
        Get comprehensive system statistics

        Returns:
            System stats dict
        """
        uptime_seconds = (datetime.utcnow() - self.start_time).total_seconds() if self.start_time else 0

        return {
            'running': self.running,
            'uptime_seconds': uptime_seconds,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'agents': {
                agent_type.value: {
                    'active': agent.is_active,
                    'stats': agent.get_stats() if hasattr(agent, 'get_stats') else {}
                }
                for agent_type, agent in self.agents.items()
            },
            'message_bus': self.message_bus.get_stats(),
            'scheduler': self.scheduler.get_stats()
        }

    def get_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """
        Get agent instance by type

        Args:
            agent_type: Type of agent

        Returns:
            Agent instance or None
        """
        return self.agents.get(agent_type)

    def enable_agent(self, agent_type: AgentType):
        """
        Enable an agent

        Args:
            agent_type: Type of agent
        """
        if agent_type in self.agents:
            self.agents[agent_type].is_active = True
            logger.info(f"✓ Enabled agent: {agent_type.value}")

    def disable_agent(self, agent_type: AgentType):
        """
        Disable an agent

        Args:
            agent_type: Type of agent
        """
        if agent_type in self.agents:
            self.agents[agent_type].is_active = False
            logger.info(f"✓ Disabled agent: {agent_type.value}")


# Singleton instance
_orchestrator: Optional[AgentOrchestrator] = None


def get_orchestrator(config: Optional[Dict] = None) -> AgentOrchestrator:
    """
    Get singleton instance of AgentOrchestrator

    Args:
        config: Configuration dict (only used on first call)

    Returns:
        AgentOrchestrator instance
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator(config)
    return _orchestrator
