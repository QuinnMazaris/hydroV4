"""Shared rule management logic for automation rules.

This module provides DRY functionality for managing automation rules,
used by both the AI tools (with protection) and HTTP API (without protection).
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class RuleManager:
    """Manages automation rules with file persistence."""

    def __init__(self, rules_path: Path):
        """Initialize the rule manager.

        Args:
            rules_path: Path to automation_rules.json file
        """
        self.rules_path = rules_path

    def load_rules_file(self) -> Dict[str, Any]:
        """Load the automation rules JSON file.

        Returns:
            Dictionary containing rules and metadata
        """
        if not self.rules_path.exists():
            return {"version": "1.0", "rules": [], "metadata": {}}

        with open(self.rules_path, 'r') as f:
            return json.load(f)

    def save_rules_file(self, data: Dict[str, Any], modified_by: str = "api") -> None:
        """Save the automation rules JSON file.

        Args:
            data: Rules data to save
            modified_by: Identifier of who made the change (default: "api")
        """
        # Ensure parent directory exists
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)

        # Update metadata
        data.setdefault("metadata", {})
        data["metadata"]["last_modified"] = datetime.now().isoformat()
        data["metadata"]["modified_by"] = modified_by

        with open(self.rules_path, 'w') as f:
            json.dump(data, f, indent=2)

    def find_rule_by_id(self, rules: List[Dict[str, Any]], rule_id: str) -> Optional[tuple[int, Dict[str, Any]]]:
        """Find a rule by ID in the rules list.

        Args:
            rules: List of rule dictionaries
            rule_id: Rule ID to search for

        Returns:
            Tuple of (index, rule) if found, None otherwise
        """
        for i, rule in enumerate(rules):
            if rule.get("id") == rule_id:
                return (i, rule)
        return None

    def validate_rule(self, rule: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate a rule dictionary.

        Args:
            rule: Rule dictionary to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Required fields
        if not rule.get("name"):
            return (False, "Rule name is required")

        if not rule.get("conditions"):
            return (False, "Rule conditions are required")

        if not rule.get("actions"):
            return (False, "Rule actions are required")

        conditions = rule.get("conditions", {})
        if not conditions.get("all_of") and not conditions.get("any_of"):
            return (False, "Rule must have either 'all_of' or 'any_of' conditions")

        actions = rule.get("actions", [])
        if not isinstance(actions, list) or len(actions) == 0:
            return (False, "Rule must have at least one action")

        return (True, None)

    def list_rules(self) -> Dict[str, Any]:
        """List all automation rules.

        Returns:
            Dictionary with rules and counts
        """
        data = self.load_rules_file()
        rules = data.get("rules", [])

        return {
            "rules": rules,
            "total_count": len(rules),
            "enabled_count": sum(1 for r in rules if r.get("enabled", False)),
            "protected_count": sum(1 for r in rules if r.get("protected", False)),
            "metadata": data.get("metadata", {})
        }

    def create_rule(
        self,
        name: str,
        conditions: Dict[str, Any],
        actions: List[Dict[str, Any]],
        description: str = "",
        enabled: bool = False,
        protected: bool = False,
        priority: int = 100,
        modified_by: str = "api"
    ) -> Dict[str, Any]:
        """Create a new automation rule.

        Args:
            name: Rule name
            conditions: Rule conditions dictionary
            actions: List of action dictionaries
            description: Rule description
            enabled: Whether rule is enabled
            protected: Whether rule is protected (default: False for API/human creation)
            priority: Rule priority
            modified_by: Who created the rule

        Returns:
            Dictionary with status and created rule
        """
        data = self.load_rules_file()

        # Generate unique ID
        rule_id = f"rule-{uuid.uuid4().hex[:8]}"

        # Create new rule
        new_rule = {
            "id": rule_id,
            "name": name,
            "description": description,
            "enabled": enabled,
            "protected": protected,
            "priority": priority,
            "conditions": conditions,
            "actions": actions
        }

        # Validate
        is_valid, error_msg = self.validate_rule(new_rule)
        if not is_valid:
            return {
                "status": "error",
                "message": error_msg
            }

        # Add to rules list
        data.setdefault("rules", [])
        data["rules"].append(new_rule)

        # Save file
        self.save_rules_file(data, modified_by=modified_by)

        return {
            "status": "success",
            "rule_id": rule_id,
            "rule": new_rule
        }

    def update_rule(
        self,
        rule_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        protected: Optional[bool] = None,
        priority: Optional[int] = None,
        conditions: Optional[Dict[str, Any]] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        modified_by: str = "api"
    ) -> Dict[str, Any]:
        """Update an existing automation rule.

        Args:
            rule_id: Rule ID to update
            name: New rule name (optional)
            description: New description (optional)
            enabled: New enabled status (optional)
            protected: New protected status (optional)
            priority: New priority (optional)
            conditions: New conditions (optional)
            actions: New actions (optional)
            modified_by: Who updated the rule

        Returns:
            Dictionary with status and updated rule
        """
        data = self.load_rules_file()

        # Find rule
        result = self.find_rule_by_id(data.get("rules", []), rule_id)
        if result is None:
            return {
                "status": "error",
                "message": f"Rule with ID '{rule_id}' not found"
            }

        rule_index, rule = result

        # Update rule fields
        if name is not None:
            rule["name"] = name
        if description is not None:
            rule["description"] = description
        if enabled is not None:
            rule["enabled"] = enabled
        if protected is not None:
            rule["protected"] = protected
        if priority is not None:
            rule["priority"] = priority
        if conditions is not None:
            rule["conditions"] = conditions
        if actions is not None:
            rule["actions"] = actions

        # Validate updated rule
        is_valid, error_msg = self.validate_rule(rule)
        if not is_valid:
            return {
                "status": "error",
                "message": error_msg
            }

        # Save file
        self.save_rules_file(data, modified_by=modified_by)

        return {
            "status": "success",
            "rule_id": rule_id,
            "rule": rule
        }

    def delete_rule(self, rule_id: str, modified_by: str = "api") -> Dict[str, Any]:
        """Delete an automation rule.

        Args:
            rule_id: Rule ID to delete
            modified_by: Who deleted the rule

        Returns:
            Dictionary with status
        """
        data = self.load_rules_file()

        # Find and remove rule
        original_count = len(data.get("rules", []))
        data["rules"] = [r for r in data.get("rules", []) if r.get("id") != rule_id]

        if len(data["rules"]) == original_count:
            return {
                "status": "error",
                "message": f"Rule with ID '{rule_id}' not found"
            }

        # Save file
        self.save_rules_file(data, modified_by=modified_by)

        return {
            "status": "success",
            "rule_id": rule_id,
            "message": "Rule deleted successfully"
        }

    def toggle_rule(self, rule_id: str, enabled: bool, modified_by: str = "api") -> Dict[str, Any]:
        """Enable or disable an automation rule.

        Args:
            rule_id: Rule ID to toggle
            enabled: New enabled state
            modified_by: Who toggled the rule

        Returns:
            Dictionary with status
        """
        data = self.load_rules_file()

        # Find rule
        result = self.find_rule_by_id(data.get("rules", []), rule_id)
        if result is None:
            return {
                "status": "error",
                "message": f"Rule with ID '{rule_id}' not found"
            }

        rule_index, rule = result

        # Toggle rule
        rule["enabled"] = enabled

        # Save file
        self.save_rules_file(data, modified_by=modified_by)

        return {
            "status": "success",
            "rule_id": rule_id,
            "enabled": enabled,
            "message": f"Rule {'enabled' if enabled else 'disabled'} successfully"
        }
