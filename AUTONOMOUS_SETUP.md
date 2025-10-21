# Autonomous Hydroponic System Setup

This guide explains how to set up and use the autonomous AI-powered hydroponic management system.

## Overview

The system has three main components running concurrently:

1. **Automation Engine** - Evaluates rules every 30 seconds and executes actions
2. **AI Agent** - Runs periodically (via automation rules) to analyze and optimize the system
3. **HTTP API** - Provides endpoints for manual control and monitoring (including MCP tools)

## Quick Start

### 1. Configure Environment Variables

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

Edit `.env` and configure:

- **For testing (no API costs)**: Set `GARDENER_LLM_PROVIDER=mock`
- **For production with OpenAI**:
  - Set `GARDENER_LLM_PROVIDER=openai`
  - Add your API key to `GARDENER_OPENAI_API_KEY`
- **For production with Grok**:
  - Set `GARDENER_LLM_PROVIDER=grok`
  - Add your API key to `GARDENER_GROK_API_KEY`

### 2. Install Dependencies

```bash
# Install gardener agent dependencies (includes croniter for scheduling)
cd agents/gardener
pip install -r requirements.txt
```

Or use Docker (recommended):

```bash
docker-compose build
```

### 3. Start the System

```bash
docker-compose up -d
```

The system will start:
- **Frontend**: http://localhost:3001
- **Backend API**: http://localhost:8001
- **Gardener API**: http://localhost:8600
- **Automation Engine**: Running in background

### 4. Enable AI Agent Automation

By default, the AI agent rule is **disabled and protected**. To enable it:

1. Edit `agents/gardener/data/automation_rules.json`
2. Find the rule with `id: "ai-agent-periodic-run"`
3. Change `"enabled": false` to `"enabled": true`
4. Save the file

The automation engine will hot-reload the rules automatically.

## Viewing Automation Rules

Navigate to **http://localhost:3001/automation** to view all automation rules, their status, and configuration.

## How It Works

### Automation Rules

Rules are defined in `agents/gardener/data/automation_rules.json`. Each rule has:

- **Conditions**: When to trigger (time ranges, cron schedules, sensor thresholds)
- **Actions**: What to do (set actuator states, run AI agent)
- **Priority**: Evaluation order (higher priority first)
- **Protected**: Whether AI can modify the rule

### Rule Types

**1. Simple Sensor-Based Rules**
```json
{
  "name": "Temperature Control",
  "enabled": true,
  "protected": false,
  "conditions": {
    "all_of": [
      {
        "type": "sensor_threshold",
        "device_key": "hydro-station-1",
        "metric_key": "temperature",
        "operator": "greater_than",
        "value": 28.0
      }
    ]
  },
  "actions": [
    {
      "type": "set_actuator",
      "device_key": "hydro-station-1",
      "actuator_key": "relay2",
      "state": "on"
    }
  ]
}
```

**2. Time-Based Rules (Cron)**
```json
{
  "name": "Daily System Check",
  "enabled": true,
  "protected": true,
  "conditions": {
    "all_of": [
      {
        "type": "cron",
        "expression": "0 8 * * *",
        "description": "Every day at 8 AM"
      }
    ]
  },
  "actions": [
    {
      "type": "run_ai_agent",
      "prompt": "Perform daily system health check and optimization",
      "temperature": 0.3,
      "max_iterations": 6
    }
  ]
}
```

**3. Combined Rules**
```json
{
  "name": "Night Light Schedule",
  "enabled": true,
  "protected": false,
  "conditions": {
    "all_of": [
      {
        "type": "time_range",
        "start_time": "18:00",
        "end_time": "06:00",
        "timezone": "America/Los_Angeles"
      },
      {
        "type": "days_of_week",
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday"]
      }
    ]
  },
  "actions": [
    {
      "type": "set_actuator",
      "device_key": "hydro-station-1",
      "actuator_key": "relay1",
      "state": "off"
    }
  ]
}
```

### Protected Rules

Protected rules **cannot be modified, deleted, enabled, or disabled by the AI agent**. This prevents the AI from:
- Disabling its own scheduling
- Changing critical safety rules
- Modifying rules that humans have marked as important

The AI can:
- See protected rules exist
- Know what they do
- Work around them when making decisions

### AI Agent Actions

When a `run_ai_agent` action executes:

1. The AI receives the configured prompt
2. It has access to all MCP tools:
   - `get_sensor_snapshot` - Current readings
   - `get_historical_readings` - Trends and statistics
   - `get_camera_image` - Visual inspection
   - `control_actuators` - Adjust settings
   - `create_automation_rule` - Create new rules (non-protected)
   - `update_automation_rule` - Modify existing rules (non-protected)
   - `delete_automation_rule` - Remove rules (non-protected)
   - `toggle_automation_rule` - Enable/disable rules (non-protected)
3. It analyzes the system state
4. It takes actions or makes recommendations
5. All reasoning and actions are logged

### Cron Expressions

Cron expressions follow the standard 5-field format:

```
┌─────── minute (0 - 59)
│ ┌────── hour (0 - 23)
│ │ ┌───── day of month (1 - 31)
│ │ │ ┌───── month (1 - 12)
│ │ │ │ ┌──── day of week (0 - 6) (Sunday to Saturday)
│ │ │ │ │
* * * * *
```

Examples:
- `0 */2 * * *` - Every 2 hours
- `0 8 * * *` - Every day at 8 AM
- `0 0 * * 0` - Every Sunday at midnight
- `30 14 1 * *` - 2:30 PM on the 1st of every month

## Safety Features

1. **Actuator Modes**: Actuators in "MANUAL" mode cannot be controlled by automation
2. **Protected Rules**: Critical rules cannot be modified by AI
3. **Dry Run Mode**: Test without actual hardware commands (`GARDENER_ACTUATOR_DRY_RUN=true`)
4. **Logging**: All AI actions and reasoning are logged for review
5. **Rule Validation**: Invalid rules are rejected before execution

## Monitoring

### Logs

View logs in real-time:

```bash
# All services
docker-compose logs -f

# Just gardener agent
docker-compose logs -f hydro-gardener

# Just automation engine activity
docker-compose logs -f hydro-gardener | grep "automation"
```

### Automation Rules UI

Visit **http://localhost:3001/automation** to see:
- All active rules
- Enabled/disabled status
- Protected status
- Rule conditions and actions
- Last modified timestamp

### MCP Integration

The system is integrated with Claude Code via MCP. You can:
- View current system state
- Manually trigger AI agent runs
- Create/modify automation rules
- All via the Claude Code interface

## Troubleshooting

### AI Agent Not Running

1. Check if the rule is enabled in `automation_rules.json`
2. Verify LLM provider is configured in `.env`
3. Check API key is valid
4. View logs: `docker-compose logs hydro-gardener`

### Rules Not Triggering

1. Verify rule is `enabled: true`
2. Check conditions are being met
3. For actuator actions, ensure actuators are in AUTO mode
4. View logs for evaluation errors

### Cron Schedule Not Working

1. Verify cron expression is valid: https://crontab.guru/
2. Check system time is correct
3. Ensure rule hasn't been executed recently (won't re-trigger same schedule)

### API Connection Issues

1. Verify all containers are running: `docker-compose ps`
2. Check network connectivity between containers
3. Verify `GARDENER_HYDRO_API_BASE_URL` in `.env`

## Advanced Configuration

### Custom AI Prompts

Modify the AI agent prompt in the automation rule:

```json
{
  "type": "run_ai_agent",
  "prompt": "Your custom instructions here. Be specific about what to analyze and what actions to take.",
  "temperature": 0.3,
  "max_iterations": 6
}
```

- **Temperature** (0.0-1.0): Lower = more deterministic, Higher = more creative
- **Max Iterations**: How many tool calls the agent can make

### Scheduling Frequency

Modify the cron expression to change AI agent frequency:

- Every 1 hour: `0 * * * *`
- Every 4 hours: `0 */4 * * *`
- Every 6 hours: `0 */6 * * *`
- Twice daily (8 AM & 8 PM): `0 8,20 * * *`

### Multiple AI Agents

You can create multiple AI agent rules with different:
- Schedules (different frequencies)
- Prompts (different responsibilities)
- Priorities (different execution order)

All protected, ensuring no single rule can disable the others.

## Example Workflows

### 1. Start with Mock Provider (No Cost)

```bash
# .env
GARDENER_LLM_PROVIDER=mock
```

This simulates AI responses without API costs. Use for testing automation logic.

### 2. Enable Real AI with OpenAI

```bash
# .env
GARDENER_LLM_PROVIDER=openai
GARDENER_OPENAI_API_KEY=sk-...
```

Enable the AI agent rule and let it run every 2 hours.

### 3. Gradual Automation

Start with disabled AI rule, manually monitor for a few days, then:
1. Enable with `dry_run: true` first
2. Review logs to see what it would do
3. Switch `dry_run: false` to allow real actions
4. Monitor closely for first 24 hours

## Support

For issues or questions:
- Check logs first
- Review automation rules configuration
- Verify environment variables
- Check GitHub issues: https://github.com/QuinnMazaris/hydroV4/issues
