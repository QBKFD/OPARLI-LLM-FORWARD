# backend/agents/base_agent.py
"""
Base Agent Class

Foundation for all trading agents in the multi-agent system
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import json


logger = logging.getLogger(__name__)


class AgentType(Enum):
    """Types of agents in the system"""
    SCANNER = "scanner"
    VISUAL_ANALYST = "visual_analyst"
    TECHNICAL_ANALYST = "technical_analyst"
    QUANT_ANALYST = "quant_analyst"
    SENTIMENT_ANALYST = "sentiment_analyst"
    PATTERN_RECOGNITION = "pattern_recognition"
    META_AGENT = "meta_agent"
    RISK_MANAGER = "risk_manager"
    EXECUTION = "execution"
    TRADE_MANAGER = "trade_manager"


class MessageType(Enum):
    """Types of messages agents can send"""
    MARKET_SCAN = "market_scan"
    ANALYSIS_REQUEST = "analysis_request"
    ANALYSIS_RESULT = "analysis_result"
    TRADE_SIGNAL = "trade_signal"
    TRADE_REQUEST = "trade_request"
    TRADE_APPROVED = "trade_approved"
    TRADE_REJECTED = "trade_rejected"
    RISK_ASSESSMENT = "risk_assessment"
    EXECUTION_REQUEST = "execution_request"
    EXECUTION_RESULT = "execution_result"
    EXECUTION_ERROR = "execution_error"
    STATUS_UPDATE = "status_update"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    HARD_EXIT = "hard_exit"
    SOFT_TRIGGER = "soft_trigger"
    MID_TRADE_ACTION = "mid_trade_action"


class Message:
    """
    Message passed between agents
    """

    def __init__(
        self,
        msg_type: MessageType,
        sender: AgentType,
        recipient: Optional[AgentType] = None,
        data: Optional[Dict] = None,
        priority: int = 5
    ):
        self.id = f"{datetime.now().timestamp()}_{sender.value}"
        self.type = msg_type
        self.sender = sender
        self.recipient = recipient  # None = broadcast to all
        self.data = data or {}
        self.timestamp = datetime.now()
        self.priority = priority  # 1-10, higher = more urgent

    def to_dict(self) -> Dict:
        """Convert message to dictionary"""
        return {
            'id': self.id,
            'type': self.type.value,
            'sender': self.sender.value,
            'recipient': self.recipient.value if self.recipient else None,
            'data': self.data,
            'timestamp': self.timestamp.isoformat(),
            'priority': self.priority
        }

    def to_json(self) -> str:
        """Convert message to JSON"""
        return json.dumps(self.to_dict())


class BaseAgent(ABC):
    """
    Abstract base class for all agents

    All agents must implement:
    - process_message(): Handle incoming messages
    - run(): Execute agent's main logic
    """

    def __init__(self, agent_type: AgentType, config: Optional[Dict] = None):
        self.agent_type = agent_type
        self.config = config or {}
        self.inbox: List[Message] = []
        self.outbox: List[Message] = []
        self.state: Dict[str, Any] = {}
        self.is_active = False

        logger.info(f"✓ Initialized {self.agent_type.value} agent")

    @abstractmethod
    def process_message(self, message: Message) -> Optional[Message]:
        """
        Process an incoming message

        Args:
            message: Message to process

        Returns:
            Optional response message
        """
        pass

    @abstractmethod
    def run(self) -> List[Message]:
        """
        Execute agent's main logic

        Returns:
            List of messages to send to other agents
        """
        pass

    def receive_message(self, message: Message):
        """
        Add message to inbox

        Args:
            message: Incoming message
        """
        # Check if message is for this agent (or broadcast)
        if message.recipient is None or message.recipient == self.agent_type:
            self.inbox.append(message)
            logger.debug(f"{self.agent_type.value} received message: {message.type.value}")

    def send_message(
        self,
        msg_type: MessageType,
        recipient: Optional[AgentType] = None,
        data: Optional[Dict] = None,
        priority: int = 5
    ) -> Message:
        """
        Send message to another agent

        Args:
            msg_type: Type of message
            recipient: Recipient agent (None = broadcast)
            data: Message payload
            priority: Message priority (1-10)

        Returns:
            Created message
        """
        message = Message(
            msg_type=msg_type,
            sender=self.agent_type,
            recipient=recipient,
            data=data,
            priority=priority
        )

        self.outbox.append(message)
        logger.debug(f"{self.agent_type.value} sent message: {msg_type.value}")
        return message

    def process_inbox(self) -> List[Message]:
        """
        Process all messages in inbox

        Returns:
            List of response messages
        """
        responses = []

        # Sort by priority (highest first)
        self.inbox.sort(key=lambda m: m.priority, reverse=True)

        while self.inbox:
            message = self.inbox.pop(0)
            try:
                response = self.process_message(message)
                if response:
                    responses.append(response)
            except Exception as e:
                logger.error(f"Error processing message in {self.agent_type.value}: {e}")

        return responses

    def get_outbox(self) -> List[Message]:
        """
        Retrieve and clear outbox

        Returns:
            List of messages to send
        """
        messages = self.outbox.copy()
        self.outbox.clear()
        return messages

    def activate(self):
        """Activate agent"""
        self.is_active = True
        logger.info(f"✓ {self.agent_type.value} activated")

    def deactivate(self):
        """Deactivate agent"""
        self.is_active = False
        logger.info(f"✗ {self.agent_type.value} deactivated")

    def get_state(self) -> Dict:
        """Get current agent state"""
        return {
            'agent_type': self.agent_type.value,
            'is_active': self.is_active,
            'inbox_size': len(self.inbox),
            'outbox_size': len(self.outbox),
            'state': self.state
        }


class AgentOrchestrator:
    """
    Coordinates communication between agents

    Acts as message broker - routes messages between agents
    """

    def __init__(self):
        self.agents: Dict[AgentType, BaseAgent] = {}
        self.message_history: List[Message] = []
        self.max_history = 1000  # Keep last 1000 messages

    def register_agent(self, agent: BaseAgent):
        """Register an agent with the orchestrator"""
        self.agents[agent.agent_type] = agent
        logger.info(f"✓ Registered {agent.agent_type.value} with orchestrator")

    def deliver_messages(self):
        """
        Collect messages from all agents and deliver to recipients

        This is the message routing system
        """
        # Collect all outgoing messages
        all_messages = []
        for agent in self.agents.values():
            messages = agent.get_outbox()
            all_messages.extend(messages)

        # Deliver messages to recipients
        for message in all_messages:
            # Store in history
            self.message_history.append(message)
            if len(self.message_history) > self.max_history:
                self.message_history.pop(0)

            # Deliver to recipient(s)
            if message.recipient is None:
                # Broadcast to all agents
                for agent in self.agents.values():
                    if agent.agent_type != message.sender:
                        agent.receive_message(message)
            else:
                # Deliver to specific agent
                recipient = self.agents.get(message.recipient)
                if recipient:
                    recipient.receive_message(message)
                else:
                    logger.warning(f"Recipient {message.recipient.value} not found")

    def process_cycle(self):
        """
        Run one processing cycle:
        1. All agents process their inboxes
        2. Collect and route all outgoing messages
        """
        # Process all inboxes
        for agent in self.agents.values():
            if agent.is_active:
                try:
                    responses = agent.process_inbox()
                    # Responses are automatically added to outbox by process_message
                except Exception as e:
                    logger.error(f"Error in {agent.agent_type.value} process cycle: {e}")

        # Deliver messages
        self.deliver_messages()

    def get_system_status(self) -> Dict:
        """Get status of all agents"""
        return {
            'agents': {
                agent_type.value: agent.get_state()
                for agent_type, agent in self.agents.items()
            },
            'message_history_size': len(self.message_history),
            'active_agents': sum(1 for agent in self.agents.values() if agent.is_active)
        }
