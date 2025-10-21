"""Automation engine for hydroponics system.

This module loads automation rules from JSON and evaluates them against
current sensor data and time conditions. Actions are only executed for
actuators in AUTO mode.
"""

import asyncio
import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from croniter import croniter

from .hydro_client import HydroAPIClient

if TYPE_CHECKING:
    from .agent import GardenerAgent

logger = logging.getLogger(__name__)


class AutomationEngine:
    """Automation engine that evaluates rules and executes actions."""

    def __init__(self, rules_path: Path, hydro_client: HydroAPIClient, agent: Optional['GardenerAgent'] = None):
        """Initialize the automation engine.

        Args:
            rules_path: Path to automation_rules.json file
            hydro_client: Client for communicating with hydro-app API
            agent: Optional GardenerAgent instance for AI actions
        """
        self.rules_path = rules_path
        self.hydro_client = hydro_client
        self.agent = agent
        self.rules: List[Dict[str, Any]] = []
        self._last_load_time: Optional[datetime] = None
        self._rule_last_executed: Dict[str, datetime] = {}  # Track cron rule execution times

    def load_rules(self) -> None:
        """Load automation rules from JSON file."""
        try:
            if not self.rules_path.exists():
                logger.warning(f"Rules file not found: {self.rules_path}")
                self.rules = []
                return

            with open(self.rules_path, 'r') as f:
                data = json.load(f)

            # Extract and sort rules by priority (higher priority first)
            self.rules = sorted(
                data.get('rules', []),
                key=lambda r: r.get('priority', 0),
                reverse=True
            )

            self._last_load_time = datetime.now()
            logger.info(f"Loaded {len(self.rules)} automation rules")

        except Exception as e:
            logger.error(f"Failed to load rules: {e}")
            self.rules = []

    def reload_rules_if_changed(self) -> None:
        """Reload rules file if it has been modified since last load."""
        try:
            if not self.rules_path.exists():
                return

            mtime = datetime.fromtimestamp(self.rules_path.stat().st_mtime)

            if self._last_load_time is None or mtime > self._last_load_time:
                logger.info("Rules file changed, reloading...")
                self.load_rules()

        except Exception as e:
            logger.error(f"Error checking rules file modification: {e}")

    async def evaluate_condition(
        self,
        condition: Dict[str, Any],
        sensor_data: Dict[str, List[Dict[str, Any]]]
    ) -> bool:
        """Evaluate a single condition.

        Args:
            condition: Condition dictionary from rule
            sensor_data: Current sensor readings by device

        Returns:
            True if condition is met, False otherwise
        """
        cond_type = condition.get('type')

        if cond_type == 'time_range':
            return self._evaluate_time_range(condition)

        elif cond_type == 'days_of_week':
            return self._evaluate_days_of_week(condition)

        elif cond_type == 'sensor_threshold':
            return self._evaluate_sensor_threshold(condition, sensor_data)

        elif cond_type == 'cron':
            return self._evaluate_cron(condition)

        else:
            logger.warning(f"Unknown condition type: {cond_type}")
            return False

    def _evaluate_time_range(self, condition: Dict[str, Any]) -> bool:
        """Check if current time is within the specified range."""
        try:
            start_str = condition.get('start_time', '00:00')
            end_str = condition.get('end_time', '23:59')

            # Parse time strings (HH:MM format)
            start_hour, start_min = map(int, start_str.split(':'))
            end_hour, end_min = map(int, end_str.split(':'))

            start_time = time(start_hour, start_min)
            end_time = time(end_hour, end_min)

            current_time = datetime.now().time()

            # Handle overnight ranges (e.g., 22:00 to 06:00)
            if start_time <= end_time:
                return start_time <= current_time <= end_time
            else:
                return current_time >= start_time or current_time <= end_time

        except Exception as e:
            logger.error(f"Error evaluating time_range condition: {e}")
            return False

    def _evaluate_days_of_week(self, condition: Dict[str, Any]) -> bool:
        """Check if current day is in the specified list."""
        try:
            days = condition.get('days', [])
            current_day = datetime.now().strftime('%A').lower()
            return current_day in [d.lower() for d in days]

        except Exception as e:
            logger.error(f"Error evaluating days_of_week condition: {e}")
            return False

    def _evaluate_cron(self, condition: Dict[str, Any]) -> bool:
        """Check if current time matches cron expression.

        Cron expressions follow standard format: minute hour day month weekday
        Example: "0 */2 * * *" = every 2 hours at the top of the hour
        """
        try:
            expression = condition.get('expression')
            if not expression:
                logger.error("Cron condition missing 'expression' field")
                return False

            # Get rule ID from parent context (will be set during evaluation)
            rule_id = condition.get('_rule_id', 'unknown')

            now = datetime.now()
            cron = croniter(expression, now)

            # Get the previous scheduled time
            prev_time = cron.get_prev(datetime)

            # Check if we haven't executed since the last scheduled time
            last_executed = self._rule_last_executed.get(rule_id)

            # If never executed, or last execution was before the previous scheduled time
            if last_executed is None or last_executed < prev_time:
                # Check if the previous scheduled time is recent (within last evaluation cycle)
                # This prevents executing multiple times for the same cron trigger
                time_since_prev = (now - prev_time).total_seconds()
                if time_since_prev < 60:  # Within last minute
                    return True

            return False

        except Exception as e:
            logger.error(f"Error evaluating cron condition: {e}")
            return False

    def _evaluate_sensor_threshold(
        self,
        condition: Dict[str, Any],
        sensor_data: Dict[str, List[Dict[str, Any]]]
    ) -> bool:
        """Check if sensor value meets the threshold condition."""
        try:
            device_key = condition.get('device_key')
            metric_key = condition.get('metric_key')
            operator = condition.get('operator')
            threshold = condition.get('value')

            # Get sensor readings for the device
            device_readings = sensor_data.get(device_key, [])

            # Find the specific metric
            metric_reading = None
            for reading in device_readings:
                if reading.metric_key == metric_key:
                    metric_reading = reading
                    break

            if metric_reading is None:
                logger.warning(f"Metric {metric_key} not found for device {device_key}")
                return False

            current_value = metric_reading.value

            # Handle numeric comparisons
            if operator == 'greater_than':
                return float(current_value) > float(threshold)
            elif operator == 'less_than':
                return float(current_value) < float(threshold)
            elif operator == 'equals':
                return float(current_value) == float(threshold)
            elif operator == 'greater_than_or_equal':
                return float(current_value) >= float(threshold)
            elif operator == 'less_than_or_equal':
                return float(current_value) <= float(threshold)
            else:
                logger.warning(f"Unknown operator: {operator}")
                return False

        except Exception as e:
            logger.error(f"Error evaluating sensor_threshold condition: {e}")
            return False

    async def evaluate_rule(
        self,
        rule: Dict[str, Any],
        sensor_data: Dict[str, List[Dict[str, Any]]]
    ) -> bool:
        """Evaluate all conditions for a rule.

        Args:
            rule: Rule dictionary
            sensor_data: Current sensor readings

        Returns:
            True if all conditions are met, False otherwise
        """
        conditions = rule.get('conditions', {})
        rule_id = rule.get('id', 'unknown')

        # Handle all_of logic (AND)
        all_of = conditions.get('all_of', [])
        for condition in all_of:
            # Inject rule_id for cron condition tracking
            condition['_rule_id'] = rule_id
            if not await self.evaluate_condition(condition, sensor_data):
                return False

        # Handle any_of logic (OR) - future expansion
        any_of = conditions.get('any_of', [])
        if any_of:
            any_met = False
            for condition in any_of:
                # Inject rule_id for cron condition tracking
                condition['_rule_id'] = rule_id
                if await self.evaluate_condition(condition, sensor_data):
                    any_met = True
                    break
            if not any_met:
                return False

        return True

    async def execute_actions(self, rule: Dict[str, Any]) -> None:
        """Execute actions for a rule.

        Only executes actions for actuators in AUTO mode.

        Args:
            rule: Rule dictionary with actions
        """
        actions = rule.get('actions', [])
        rule_name = rule.get('name', 'unknown')
        rule_id = rule.get('id', 'unknown')

        actions_executed = False

        for action in actions:
            action_type = action.get('type')

            if action_type == 'set_actuator':
                await self._execute_set_actuator(action, rule_name)
                actions_executed = True

            elif action_type == 'run_ai_agent':
                await self._execute_run_ai_agent(action, rule_name)
                actions_executed = True

            else:
                logger.warning(f"Unknown action type: {action_type}")

        if actions_executed:
            # Mark rule as executed (for cron tracking)
            self._rule_last_executed[rule_id] = datetime.now()

    async def _execute_set_actuator(self, action: Dict[str, Any], rule_name: str) -> None:
        """Execute a set_actuator action.

        Args:
            action: Action dictionary
            rule_name: Name of the rule (for logging)
        """
        try:
            device_key = action.get('device_key')
            actuator_key = action.get('actuator_key')
            state = action.get('state')

            # Get current control modes
            modes = await self.hydro_client.get_actuator_modes()

            # Check if actuator is in AUTO mode
            actuator_mode = modes.get(device_key, {}).get(actuator_key)

            if actuator_mode != 'auto':
                logger.debug(
                    f"Skipping actuator {device_key}:{actuator_key} - "
                    f"mode is {actuator_mode}, not 'auto'"
                )
                return

            # Get current state
            latest = await self.hydro_client.latest_readings()
            device_readings = latest.get(device_key, [])

            current_state = None
            for reading in device_readings:
                if reading.metric_key == actuator_key:
                    current_state = reading.value
                    break

            # Only send command if state needs to change
            if current_state == state:
                logger.debug(
                    f"Actuator {device_key}:{actuator_key} already in state '{state}'"
                )
                return

            # Send control command
            success = await self.hydro_client.control_actuator(
                device_key, actuator_key, state
            )

            if success:
                logger.info(
                    f"Rule '{rule_name}': Set {device_key}:{actuator_key} to '{state}'"
                )
            else:
                logger.warning(
                    f"Rule '{rule_name}': Failed to set {device_key}:{actuator_key} "
                    f"to '{state}'"
                )

        except Exception as e:
            logger.error(f"Error executing set_actuator action: {e}")

    async def _execute_run_ai_agent(self, action: Dict[str, Any], rule_name: str) -> None:
        """Execute a run_ai_agent action.

        Args:
            action: Action dictionary containing prompt and agent parameters
            rule_name: Name of the rule (for logging)
        """
        try:
            if self.agent is None:
                logger.error(f"Rule '{rule_name}': Cannot run AI agent - agent not initialized")
                return

            prompt = action.get('prompt', 'Analyze the current system state and recommend actions.')
            temperature = action.get('temperature', 0.3)
            max_iterations = action.get('max_iterations', 6)

            logger.info(f"Rule '{rule_name}': Starting AI agent run")

            # Run the agent with the specified prompt
            from .agent import ChatMessage
            messages = [ChatMessage(role="user", content=prompt)]

            result = await self.agent.run(messages=messages, temperature=temperature)

            logger.info(
                f"Rule '{rule_name}': AI agent completed. "
                f"Final response: {result.get('final', 'No response')[:200]}..."
            )

            # Log the agent trace for debugging
            trace = result.get('trace', [])
            logger.debug(f"Rule '{rule_name}': AI agent trace ({len(trace)} iterations)")

        except Exception as e:
            logger.error(f"Error executing run_ai_agent action: {e}", exc_info=True)

    async def run_once(self) -> None:
        """Run one evaluation cycle of all rules."""
        try:
            # Reload rules if file changed
            self.reload_rules_if_changed()

            # Get current sensor data
            sensor_data = await self.hydro_client.latest_readings()

            # Evaluate and execute enabled rules
            for rule in self.rules:
                if not rule.get('enabled', False):
                    continue

                try:
                    rule_met = await self.evaluate_rule(rule, sensor_data)

                    if rule_met:
                        await self.execute_actions(rule)

                except Exception as e:
                    logger.error(
                        f"Error evaluating rule '{rule.get('name', 'unknown')}': {e}"
                    )

        except Exception as e:
            logger.error(f"Error in automation cycle: {e}")

    async def run_loop(self, interval: int = 30) -> None:
        """Run the automation engine in a continuous loop.

        Args:
            interval: Seconds between evaluation cycles (default 30)
        """
        logger.info(f"Starting automation engine (interval: {interval}s)")

        # Load rules initially
        self.load_rules()

        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Unexpected error in automation loop: {e}")

            await asyncio.sleep(interval)


async def main():
    """Main entry point for automation engine."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Setup paths
    base_path = Path(__file__).parent
    rules_path = base_path / 'data' / 'automation_rules.json'

    # Create hydro client
    hydro_client = HydroAPIClient(base_url='http://localhost:8001')

    # Create and run engine
    engine = AutomationEngine(rules_path, hydro_client)

    try:
        await engine.run_loop(interval=30)
    except KeyboardInterrupt:
        logger.info("Automation engine stopped by user")


if __name__ == '__main__':
    asyncio.run(main())
