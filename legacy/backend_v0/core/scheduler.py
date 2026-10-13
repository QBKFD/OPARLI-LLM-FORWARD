# backend/core/scheduler.py
"""
Scheduler

Manages timing and execution of agents
Runs agents at appropriate intervals based on their requirements
"""

import logging
import time
from typing import Dict, List, Optional, Callable
from threading import Thread, Event
from datetime import datetime, timedelta

from agents.base_agent import AgentType

logger = logging.getLogger(__name__)


class ScheduledTask:
    """
    Represents a scheduled task for an agent

    Attributes:
        agent_type: Type of agent
        interval_seconds: Interval between executions (None = run once)
        callback: Function to call
        last_run: Last execution time
        enabled: Whether task is enabled
    """

    def __init__(
        self,
        agent_type: AgentType,
        callback: Callable,
        interval_seconds: Optional[float] = None,
        enabled: bool = True
    ):
        self.agent_type = agent_type
        self.interval_seconds = interval_seconds
        self.callback = callback
        self.last_run: Optional[datetime] = None
        self.enabled = enabled
        self.run_count = 0
        self.error_count = 0

    def should_run(self) -> bool:
        """
        Check if task should run now

        Returns:
            True if task should run
        """
        if not self.enabled:
            return False

        if self.last_run is None:
            return True

        if self.interval_seconds is None:
            # One-time task, already ran
            return False

        elapsed = (datetime.utcnow() - self.last_run).total_seconds()
        return elapsed >= self.interval_seconds

    def execute(self):
        """
        Execute the task

        Returns:
            True if successful, False if error
        """
        try:
            logger.debug(f"Executing task: {self.agent_type.value}")
            start_time = time.time()

            # Call the callback
            self.callback()

            execution_time = (time.time() - start_time) * 1000  # ms

            self.last_run = datetime.utcnow()
            self.run_count += 1

            logger.debug(f"✓ Task complete: {self.agent_type.value} ({execution_time:.0f}ms)")
            return True

        except Exception as e:
            logger.error(f"Error executing task {self.agent_type.value}: {e}", exc_info=True)
            self.error_count += 1
            return False


class Scheduler:
    """
    Scheduler for agent execution

    Features:
    - Interval-based scheduling (run every N seconds)
    - One-time tasks
    - Enable/disable tasks
    - Thread-safe execution
    - Statistics tracking

    Agent Intervals:
    - Scanner: 1 second (event-driven trigger checks)
    - Technical Analyst: On-demand (responds to Scanner)
    - Visual Analyst: On-demand (responds to Scanner)
    - Meta-Agent: On-demand (responds to analyses)
    - Risk Manager: On-demand (responds to Meta-Agent)
    - Execution: On-demand (responds to Risk Manager)
    - Trade Manager: 1 second (monitors active trades)
    """

    def __init__(self):
        # Scheduled tasks
        self._tasks: Dict[AgentType, ScheduledTask] = {}

        # Scheduler control
        self._running = False
        self._thread: Optional[Thread] = None
        self._stop_event = Event()

        # Scheduler tick rate (how often to check if tasks should run)
        self._tick_rate = 0.1  # 100ms

        logger.info("✓ Scheduler initialized")

    def schedule_task(
        self,
        agent_type: AgentType,
        callback: Callable,
        interval_seconds: Optional[float] = None,
        enabled: bool = True
    ):
        """
        Schedule a task for an agent

        Args:
            agent_type: Type of agent
            callback: Function to call (agent's run() method)
            interval_seconds: Interval between runs (None = one-time)
            enabled: Whether task is enabled
        """
        task = ScheduledTask(agent_type, callback, interval_seconds, enabled)
        self._tasks[agent_type] = task

        logger.info(f"✓ Scheduled task: {agent_type.value} "
                   f"({'every ' + str(interval_seconds) + 's' if interval_seconds else 'one-time'})")

    def unschedule_task(self, agent_type: AgentType):
        """
        Remove a scheduled task

        Args:
            agent_type: Type of agent
        """
        if agent_type in self._tasks:
            del self._tasks[agent_type]
            logger.info(f"✓ Unscheduled task: {agent_type.value}")

    def enable_task(self, agent_type: AgentType):
        """
        Enable a task

        Args:
            agent_type: Type of agent
        """
        if agent_type in self._tasks:
            self._tasks[agent_type].enabled = True
            logger.info(f"✓ Enabled task: {agent_type.value}")

    def disable_task(self, agent_type: AgentType):
        """
        Disable a task

        Args:
            agent_type: Type of agent
        """
        if agent_type in self._tasks:
            self._tasks[agent_type].enabled = False
            logger.info(f"✓ Disabled task: {agent_type.value}")

    def start(self):
        """
        Start the scheduler

        Runs in background thread
        """
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._stop_event.clear()

        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        logger.info("✓ Scheduler started")

    def stop(self):
        """
        Stop the scheduler

        Waits for current tasks to complete
        """
        if not self._running:
            return

        logger.info("Stopping scheduler...")
        self._running = False
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=5.0)

        logger.info("✓ Scheduler stopped")

    def _run_loop(self):
        """
        Main scheduler loop

        Checks tasks every tick_rate seconds
        """
        logger.info("Scheduler loop started")

        while self._running and not self._stop_event.is_set():
            try:
                # Check each task
                for task in self._tasks.values():
                    if task.should_run():
                        task.execute()

                # Sleep until next tick
                time.sleep(self._tick_rate)

            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
                time.sleep(1.0)  # Back off on error

        logger.info("Scheduler loop ended")

    def run_once(self, agent_type: AgentType):
        """
        Run a task immediately (manual trigger)

        Args:
            agent_type: Type of agent
        """
        if agent_type not in self._tasks:
            logger.warning(f"Task {agent_type.value} not scheduled")
            return

        task = self._tasks[agent_type]
        task.execute()

    def get_stats(self) -> Dict:
        """
        Get scheduler statistics

        Returns:
            Statistics dict
        """
        return {
            'running': self._running,
            'tick_rate': self._tick_rate,
            'tasks': {
                agent_type.value: {
                    'enabled': task.enabled,
                    'interval_seconds': task.interval_seconds,
                    'run_count': task.run_count,
                    'error_count': task.error_count,
                    'last_run': task.last_run.isoformat() if task.last_run else None,
                    'should_run': task.should_run()
                }
                for agent_type, task in self._tasks.items()
            }
        }

    def get_task_status(self, agent_type: AgentType) -> Optional[Dict]:
        """
        Get status of a specific task

        Args:
            agent_type: Type of agent

        Returns:
            Task status dict or None
        """
        if agent_type not in self._tasks:
            return None

        task = self._tasks[agent_type]
        return {
            'agent_type': agent_type.value,
            'enabled': task.enabled,
            'interval_seconds': task.interval_seconds,
            'run_count': task.run_count,
            'error_count': task.error_count,
            'last_run': task.last_run.isoformat() if task.last_run else None,
            'should_run': task.should_run()
        }


# Singleton instance
_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    """
    Get singleton instance of Scheduler

    Returns:
        Scheduler instance
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
