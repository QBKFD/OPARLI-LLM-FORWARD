# backend/core/message_bus.py
"""
Message Bus

Central message routing system for inter-agent communication
Handles message delivery, priority queues, and broadcast routing
"""

import logging
from itertools import count
from typing import Dict, List, Optional, Callable
from queue import PriorityQueue
from threading import Lock
from datetime import datetime

from agents.base_agent import Message, AgentType, MessageType

logger = logging.getLogger(__name__)


class MessageBus:
    """
    Central message bus for agent communication

    Features:
    - Priority-based message queuing
    - Broadcast and direct messaging
    - Message routing by agent type
    - Thread-safe message delivery
    - Message history tracking
    """

    def __init__(self):
        # Agent mailboxes (one queue per agent)
        self._mailboxes: Dict[AgentType, PriorityQueue] = {}

        # Agent subscribers (callbacks for message delivery)
        self._subscribers: Dict[AgentType, Callable] = {}

        # Thread safety
        self._lock = Lock()

        # Monotonic tiebreaker for the priority queues (see _deliver_message)
        self._sequence = count()

        # Message history (for debugging)
        self._message_history: List[Message] = []
        self._max_history = 1000

        # Statistics
        self.stats = {
            'total_messages': 0,
            'messages_by_type': {},
            'messages_by_sender': {},
            'messages_by_recipient': {}
        }

        logger.info("✓ Message Bus initialized")

    def register_agent(self, agent_type: AgentType, callback: Callable[[Message], None]):
        """
        Register an agent with the message bus

        Args:
            agent_type: Type of agent
            callback: Function to call when message arrives (agent's process_message)
        """
        with self._lock:
            if agent_type not in self._mailboxes:
                self._mailboxes[agent_type] = PriorityQueue()
                logger.info(f"✓ Registered agent: {agent_type.value}")

            self._subscribers[agent_type] = callback

    def unregister_agent(self, agent_type: AgentType):
        """
        Unregister an agent from the message bus

        Args:
            agent_type: Type of agent
        """
        with self._lock:
            if agent_type in self._mailboxes:
                del self._mailboxes[agent_type]
                logger.info(f"✓ Unregistered agent: {agent_type.value}")

            if agent_type in self._subscribers:
                del self._subscribers[agent_type]

    def send_message(self, message: Message):
        """
        Send a message to recipient(s)

        Args:
            message: Message to send
        """
        try:
            with self._lock:
                # Update stats
                self.stats['total_messages'] += 1

                msg_type = message.type.value
                if msg_type not in self.stats['messages_by_type']:
                    self.stats['messages_by_type'][msg_type] = 0
                self.stats['messages_by_type'][msg_type] += 1

                sender = message.sender.value
                if sender not in self.stats['messages_by_sender']:
                    self.stats['messages_by_sender'][sender] = 0
                self.stats['messages_by_sender'][sender] += 1

                # Add to history
                self._message_history.append(message)
                if len(self._message_history) > self._max_history:
                    self._message_history.pop(0)

                # Route message
                if message.recipient is None:
                    # Broadcast to all agents except sender
                    self._broadcast_message(message)
                else:
                    # Direct message to specific agent
                    self._deliver_message(message, message.recipient)

                    # Update recipient stats
                    recipient = message.recipient.value
                    if recipient not in self.stats['messages_by_recipient']:
                        self.stats['messages_by_recipient'][recipient] = 0
                    self.stats['messages_by_recipient'][recipient] += 1

        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)

    def _broadcast_message(self, message: Message):
        """
        Broadcast message to all agents except sender

        Args:
            message: Message to broadcast
        """
        for agent_type in self._mailboxes.keys():
            if agent_type != message.sender:
                self._deliver_message(message, agent_type)

    def _deliver_message(self, message: Message, recipient: AgentType):
        """
        Deliver message to specific agent's mailbox

        Args:
            message: Message to deliver
            recipient: Recipient agent type
        """
        if recipient not in self._mailboxes:
            logger.warning(f"Agent {recipient.value} not registered, message dropped")
            return

        # Priority queue entries are (priority, sequence, message).
        # Lower priority number = higher priority, so we negate.
        #
        # The sequence number is required, not cosmetic: heapq compares tuples
        # element by element, so two messages of EQUAL priority would fall
        # through to comparing Message objects, which are not orderable, and
        # PriorityQueue.put would raise TypeError. That is reachable on any
        # normal scan — the Visual and Sentiment analysts both reply at
        # priority 7. The counter also makes delivery FIFO within a priority.
        entry = (-message.priority, next(self._sequence), message)
        self._mailboxes[recipient].put(entry)

        logger.debug(f"Message delivered: {message.sender.value} → {recipient.value} "
                    f"({message.type.value}, priority {message.priority})")

    def get_messages(self, agent_type: AgentType, max_messages: int = 10) -> List[Message]:
        """
        Get pending messages for an agent

        Args:
            agent_type: Type of agent
            max_messages: Maximum messages to retrieve

        Returns:
            List of messages (up to max_messages)
        """
        messages = []

        if agent_type not in self._mailboxes:
            return messages

        mailbox = self._mailboxes[agent_type]

        # Get up to max_messages from queue
        for _ in range(max_messages):
            if mailbox.empty():
                break

            _priority, _sequence, message = mailbox.get()
            messages.append(message)

        return messages

    def process_messages(self, agent_type: AgentType, max_messages: int = 10):
        """
        Process pending messages for an agent using its callback

        Args:
            agent_type: Type of agent
            max_messages: Maximum messages to process
        """
        if agent_type not in self._subscribers:
            logger.warning(f"Agent {agent_type.value} has no callback registered")
            return

        callback = self._subscribers[agent_type]
        messages = self.get_messages(agent_type, max_messages)

        for message in messages:
            try:
                # Call agent's process_message method
                response = callback(message)

                # If agent returns a response message, send it
                if response:
                    self.send_message(response)

            except Exception as e:
                logger.error(f"Error processing message in {agent_type.value}: {e}", exc_info=True)

    def get_pending_count(self, agent_type: AgentType) -> int:
        """
        Get number of pending messages for an agent

        Args:
            agent_type: Type of agent

        Returns:
            Number of pending messages
        """
        if agent_type not in self._mailboxes:
            return 0

        return self._mailboxes[agent_type].qsize()

    def get_stats(self) -> Dict:
        """
        Get message bus statistics

        Returns:
            Statistics dict
        """
        with self._lock:
            return {
                **self.stats,
                'pending_by_agent': {
                    agent_type.value: self.get_pending_count(agent_type)
                    for agent_type in self._mailboxes.keys()
                },
                'history_size': len(self._message_history)
            }

    def clear_mailbox(self, agent_type: AgentType):
        """
        Clear all pending messages for an agent

        Args:
            agent_type: Type of agent
        """
        if agent_type in self._mailboxes:
            with self._lock:
                # Create new empty queue
                self._mailboxes[agent_type] = PriorityQueue()
                logger.info(f"Cleared mailbox for {agent_type.value}")

    def get_message_history(self, limit: int = 100) -> List[Dict]:
        """
        Get recent message history

        Args:
            limit: Maximum messages to return

        Returns:
            List of message dicts (most recent first)
        """
        with self._lock:
            history = self._message_history[-limit:]
            return [msg.to_dict() for msg in reversed(history)]


# Singleton instance
_message_bus: Optional[MessageBus] = None


def get_message_bus() -> MessageBus:
    """
    Get singleton instance of MessageBus

    Returns:
        MessageBus instance
    """
    global _message_bus
    if _message_bus is None:
        _message_bus = MessageBus()
    return _message_bus
