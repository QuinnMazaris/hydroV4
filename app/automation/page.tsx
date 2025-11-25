"use client"

import { useEffect, useState, useCallback } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { 
  Trash2, 
  Plus, 
  Clock, 
  Thermometer, 
  Zap, 
  Bot, 
  ChevronDown, 
  ChevronUp,
  Shield,
  Calendar,
  Activity,
  X
} from "lucide-react"
import { cn } from "@/lib/utils"
import { AppHeader } from "@/components/app-header"

interface AutomationRule {
  id: string
  name: string
  description: string
  enabled: boolean
  protected: boolean
  priority: number
  conditions: {
    all_of?: Condition[]
    any_of?: Condition[]
  }
  actions: Action[]
}

interface Condition {
  type: "cron" | "time_range" | "days_of_week" | "sensor_threshold"
  expression?: string
  description?: string
  start_time?: string
  end_time?: string
  timezone?: string
  days?: string[]
  device_key?: string
  metric_key?: string
  operator?: "greater_than" | "less_than" | "equal_to" | "not_equal_to"
  value?: number
}

interface Action {
  type: "set_actuator" | "run_ai_agent"
  device_key?: string
  actuator_key?: string
  state?: string
  prompt?: string
  temperature?: number
  max_iterations?: number
}

interface AutomationRulesResponse {
  rules: AutomationRule[]
  total_count: number
  enabled_count: number
  protected_count: number
  metadata?: {
    created_at?: string
    last_modified?: string
    modified_by?: string
  }
}

const DAYS_OF_WEEK = [
  "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]

const OPERATORS = [
  { value: "greater_than", label: ">" },
  { value: "less_than", label: "<" },
  { value: "equal_to", label: "=" },
  { value: "not_equal_to", label: "≠" },
]

const COMMON_TIMEZONES = [
  "America/New_York",
  "America/Chicago", 
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Australia/Sydney",
  "UTC",
]

// Helper to render conditions in human-readable format
function ConditionDisplay({ condition }: { condition: Condition }) {
  switch (condition.type) {
    case "cron":
      return (
        <div className="flex items-center gap-2 text-sm">
          <Clock className="h-4 w-4 text-purple-400" />
          <span className="text-purple-200">Schedule:</span>
          <code className="bg-purple-500/20 px-2 py-0.5 rounded text-purple-100">
            {condition.expression}
          </code>
          {condition.description && (
            <span className="text-muted-foreground">({condition.description})</span>
          )}
        </div>
      )
    case "time_range":
      return (
        <div className="flex items-center gap-2 text-sm">
          <Clock className="h-4 w-4 text-blue-400" />
          <span className="text-blue-200">Time:</span>
          <span className="text-blue-100">
            {condition.start_time} – {condition.end_time}
          </span>
          {condition.timezone && (
            <span className="text-muted-foreground">({condition.timezone})</span>
          )}
        </div>
      )
    case "days_of_week":
      return (
        <div className="flex items-center gap-2 text-sm">
          <Calendar className="h-4 w-4 text-green-400" />
          <span className="text-green-200">Days:</span>
          <span className="text-green-100">
            {condition.days?.length === 7 
              ? "Every day" 
              : condition.days?.map(d => d.slice(0, 3)).join(", ")}
          </span>
        </div>
      )
    case "sensor_threshold":
      const opSymbol = OPERATORS.find(o => o.value === condition.operator)?.label || condition.operator
      return (
        <div className="flex items-center gap-2 text-sm">
          <Thermometer className="h-4 w-4 text-orange-400" />
          <span className="text-orange-200">Sensor:</span>
          <span className="text-orange-100">
            {condition.metric_key} {opSymbol} {condition.value}
          </span>
          {condition.device_key && (
            <span className="text-muted-foreground">on {condition.device_key}</span>
          )}
        </div>
      )
    default:
      return <span className="text-muted-foreground text-sm">Unknown condition</span>
  }
}

// Helper to render actions in human-readable format
function ActionDisplay({ action }: { action: Action }) {
  switch (action.type) {
    case "set_actuator":
      return (
        <div className="flex items-center gap-2 text-sm">
          <Zap className="h-4 w-4 text-yellow-400" />
          <span className="text-yellow-200">Set</span>
          <code className="bg-yellow-500/20 px-2 py-0.5 rounded text-yellow-100">
            {action.actuator_key}
          </code>
          <span className="text-yellow-200">to</span>
          <Badge 
            variant="outline" 
            className={cn(
              "uppercase text-xs",
              action.state === "on" 
                ? "border-green-500 text-green-400" 
                : "border-red-500 text-red-400"
            )}
          >
            {action.state}
          </Badge>
          {action.device_key && (
            <span className="text-muted-foreground">on {action.device_key}</span>
          )}
        </div>
      )
    case "run_ai_agent":
      return (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm">
            <Bot className="h-4 w-4 text-cyan-400" />
            <span className="text-cyan-200">Run AI Agent</span>
            {action.temperature && (
              <span className="text-muted-foreground">(temp: {action.temperature})</span>
            )}
          </div>
          {action.prompt && (
            <p className="text-xs text-muted-foreground ml-6 line-clamp-2">
              "{action.prompt}"
            </p>
          )}
        </div>
      )
    default:
      return <span className="text-muted-foreground text-sm">Unknown action</span>
  }
}

// Rule Editor Component
interface RuleEditorProps {
  rule?: AutomationRule
  onSave: (rule: Partial<AutomationRule>) => Promise<void>
  onCancel: () => void
  isSaving: boolean
}

function RuleEditor({ rule, onSave, onCancel, isSaving }: RuleEditorProps) {
  const isNew = !rule
  const [name, setName] = useState(rule?.name || "")
  const [description, setDescription] = useState(rule?.description || "")
  const [priority, setPriority] = useState(rule?.priority?.toString() || "100")
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  
  // Conditions
  const [conditionType, setConditionType] = useState<string>("time_range")
  const [conditions, setConditions] = useState<Condition[]>(
    rule?.conditions?.all_of || []
  )
  
  // New condition form state
  const [newCondition, setNewCondition] = useState<Partial<Condition>>({
    type: "time_range",
    start_time: "06:00",
    end_time: "22:00",
    timezone: "UTC",
  })
  
  // Actions
  const [actions, setActions] = useState<Action[]>(rule?.actions || [])
  const [newAction, setNewAction] = useState<Partial<Action>>({
    type: "set_actuator",
    state: "on",
  })

  const addCondition = () => {
    if (!newCondition.type) return
    setConditions([...conditions, newCondition as Condition])
    // Reset form
    setNewCondition({
      type: "time_range",
      start_time: "06:00",
      end_time: "22:00",
      timezone: "UTC",
    })
  }

  const removeCondition = (index: number) => {
    setConditions(conditions.filter((_, i) => i !== index))
  }

  const addAction = () => {
    if (!newAction.type) return
    setActions([...actions, newAction as Action])
    setNewAction({
      type: "set_actuator",
      state: "on",
    })
  }

  const removeAction = (index: number) => {
    setActions(actions.filter((_, i) => i !== index))
  }

  const handleSave = async () => {
    const ruleData: Partial<AutomationRule> = {
      name,
      description,
      priority: parseInt(priority) || 100,
      enabled,
      conditions: { all_of: conditions },
      actions,
    }
    if (rule?.id) {
      ruleData.id = rule.id
    }
    await onSave(ruleData)
  }

  return (
    <Card className="border-primary/30 bg-black/60 backdrop-blur-xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Plus className="h-5 w-5" />
          {isNew ? "Create New Rule" : "Edit Rule"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Basic Info */}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="name">Rule Name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Morning Light Schedule"
              className="bg-black/40 border-white/20"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="priority">Priority (higher runs first)</Label>
            <Input
              id="priority"
              type="number"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              placeholder="100"
              className="bg-black/40 border-white/20"
            />
          </div>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does this rule do?"
            className="bg-black/40 border-white/20 min-h-[60px]"
          />
        </div>

        <div className="flex items-center gap-2">
          <Switch checked={enabled} onCheckedChange={setEnabled} />
          <Label>Enable rule immediately</Label>
        </div>

        {/* Conditions Section */}
        <div className="space-y-4">
          <Label className="text-lg font-semibold flex items-center gap-2">
            <Activity className="h-5 w-5 text-blue-400" />
            Conditions (ALL must match)
          </Label>
          
          {/* Existing conditions */}
          {conditions.length > 0 && (
            <div className="space-y-2">
              {conditions.map((cond, idx) => (
                <div key={idx} className="flex items-center gap-2 bg-white/5 rounded-lg p-3">
                  <ConditionDisplay condition={cond} />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="ml-auto h-8 w-8 text-destructive hover:text-destructive"
                    onClick={() => removeCondition(idx)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          {/* Add new condition */}
          <div className="border border-white/10 rounded-lg p-4 space-y-4">
            <div className="flex items-center gap-4">
              <Select
                value={newCondition.type}
                onValueChange={(val) => setNewCondition({ type: val as Condition["type"] })}
              >
                <SelectTrigger className="w-48 bg-black/40 border-white/20">
                  <SelectValue placeholder="Condition type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="time_range">Time Range</SelectItem>
                  <SelectItem value="cron">Cron Schedule</SelectItem>
                  <SelectItem value="days_of_week">Days of Week</SelectItem>
                  <SelectItem value="sensor_threshold">Sensor Threshold</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Time Range Fields */}
            {newCondition.type === "time_range" && (
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <Label>Start Time</Label>
                  <Input
                    type="time"
                    value={newCondition.start_time || ""}
                    onChange={(e) => setNewCondition({ ...newCondition, start_time: e.target.value })}
                    className="bg-black/40 border-white/20"
                  />
                </div>
                <div className="space-y-2">
                  <Label>End Time</Label>
                  <Input
                    type="time"
                    value={newCondition.end_time || ""}
                    onChange={(e) => setNewCondition({ ...newCondition, end_time: e.target.value })}
                    className="bg-black/40 border-white/20"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Timezone</Label>
                  <Select
                    value={newCondition.timezone || "UTC"}
                    onValueChange={(val) => setNewCondition({ ...newCondition, timezone: val })}
                  >
                    <SelectTrigger className="bg-black/40 border-white/20">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {COMMON_TIMEZONES.map((tz) => (
                        <SelectItem key={tz} value={tz}>{tz}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            )}

            {/* Cron Fields */}
            {newCondition.type === "cron" && (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Cron Expression</Label>
                  <Input
                    value={newCondition.expression || ""}
                    onChange={(e) => setNewCondition({ ...newCondition, expression: e.target.value })}
                    placeholder="0 */2 * * *"
                    className="bg-black/40 border-white/20 font-mono"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Description (optional)</Label>
                  <Input
                    value={newCondition.description || ""}
                    onChange={(e) => setNewCondition({ ...newCondition, description: e.target.value })}
                    placeholder="Every 2 hours"
                    className="bg-black/40 border-white/20"
                  />
                </div>
              </div>
            )}

            {/* Days of Week Fields */}
            {newCondition.type === "days_of_week" && (
              <div className="space-y-2">
                <Label>Select Days</Label>
                <div className="flex flex-wrap gap-2">
                  {DAYS_OF_WEEK.map((day) => {
                    const isSelected = newCondition.days?.includes(day)
                    return (
                      <Button
                        key={day}
                        variant={isSelected ? "default" : "outline"}
                        size="sm"
                        onClick={() => {
                          const days = newCondition.days || []
                          setNewCondition({
                            ...newCondition,
                            days: isSelected
                              ? days.filter(d => d !== day)
                              : [...days, day]
                          })
                        }}
                        className={cn(
                          "capitalize",
                          !isSelected && "bg-black/40 border-white/20"
                        )}
                      >
                        {day.slice(0, 3)}
                      </Button>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Sensor Threshold Fields */}
            {newCondition.type === "sensor_threshold" && (
              <div className="grid gap-4 md:grid-cols-4">
                <div className="space-y-2">
                  <Label>Device</Label>
                  <Input
                    value={newCondition.device_key || ""}
                    onChange={(e) => setNewCondition({ ...newCondition, device_key: e.target.value })}
                    placeholder="hydro-station-1"
                    className="bg-black/40 border-white/20"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Metric</Label>
                  <Input
                    value={newCondition.metric_key || ""}
                    onChange={(e) => setNewCondition({ ...newCondition, metric_key: e.target.value })}
                    placeholder="temperature"
                    className="bg-black/40 border-white/20"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Operator</Label>
                  <Select
                    value={newCondition.operator || "greater_than"}
                    onValueChange={(val) => setNewCondition({ ...newCondition, operator: val as Condition["operator"] })}
                  >
                    <SelectTrigger className="bg-black/40 border-white/20">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {OPERATORS.map((op) => (
                        <SelectItem key={op.value} value={op.value}>
                          {op.label} {op.value.replace("_", " ")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Value</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={newCondition.value ?? ""}
                    onChange={(e) => setNewCondition({ ...newCondition, value: parseFloat(e.target.value) })}
                    placeholder="28.0"
                    className="bg-black/40 border-white/20"
                  />
                </div>
              </div>
            )}

            <Button variant="outline" onClick={addCondition} className="mt-2">
              <Plus className="h-4 w-4 mr-2" />
              Add Condition
            </Button>
          </div>
        </div>

        {/* Actions Section */}
        <div className="space-y-4">
          <Label className="text-lg font-semibold flex items-center gap-2">
            <Zap className="h-5 w-5 text-yellow-400" />
            Actions
          </Label>
          
          {/* Existing actions */}
          {actions.length > 0 && (
            <div className="space-y-2">
              {actions.map((action, idx) => (
                <div key={idx} className="flex items-center gap-2 bg-white/5 rounded-lg p-3">
                  <ActionDisplay action={action} />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="ml-auto h-8 w-8 text-destructive hover:text-destructive"
                    onClick={() => removeAction(idx)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          {/* Add new action */}
          <div className="border border-white/10 rounded-lg p-4 space-y-4">
            <div className="flex items-center gap-4">
              <Select
                value={newAction.type}
                onValueChange={(val) => setNewAction({ type: val as Action["type"] })}
              >
                <SelectTrigger className="w-48 bg-black/40 border-white/20">
                  <SelectValue placeholder="Action type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="set_actuator">Set Actuator</SelectItem>
                  <SelectItem value="run_ai_agent">Run AI Agent</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Set Actuator Fields */}
            {newAction.type === "set_actuator" && (
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <Label>Device</Label>
                  <Input
                    value={newAction.device_key || ""}
                    onChange={(e) => setNewAction({ ...newAction, device_key: e.target.value })}
                    placeholder="hydro-station-1"
                    className="bg-black/40 border-white/20"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Actuator Key</Label>
                  <Input
                    value={newAction.actuator_key || ""}
                    onChange={(e) => setNewAction({ ...newAction, actuator_key: e.target.value })}
                    placeholder="relay1"
                    className="bg-black/40 border-white/20"
                  />
                </div>
                <div className="space-y-2">
                  <Label>State</Label>
                  <Select
                    value={newAction.state || "on"}
                    onValueChange={(val) => setNewAction({ ...newAction, state: val })}
                  >
                    <SelectTrigger className="bg-black/40 border-white/20">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="on">ON</SelectItem>
                      <SelectItem value="off">OFF</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            )}

            {/* Run AI Agent Fields */}
            {newAction.type === "run_ai_agent" && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>Prompt</Label>
                  <Textarea
                    value={newAction.prompt || ""}
                    onChange={(e) => setNewAction({ ...newAction, prompt: e.target.value })}
                    placeholder="Analyze system state and recommend adjustments..."
                    className="bg-black/40 border-white/20 min-h-[100px]"
                  />
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Temperature (0-1)</Label>
                    <Input
                      type="number"
                      step="0.1"
                      min="0"
                      max="1"
                      value={newAction.temperature ?? 0.3}
                      onChange={(e) => setNewAction({ ...newAction, temperature: parseFloat(e.target.value) })}
                      className="bg-black/40 border-white/20"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Max Iterations</Label>
                    <Input
                      type="number"
                      min="1"
                      max="20"
                      value={newAction.max_iterations ?? 6}
                      onChange={(e) => setNewAction({ ...newAction, max_iterations: parseInt(e.target.value) })}
                      className="bg-black/40 border-white/20"
                    />
                  </div>
                </div>
              </div>
            )}

            <Button variant="outline" onClick={addAction} className="mt-2">
              <Plus className="h-4 w-4 mr-2" />
              Add Action
            </Button>
          </div>
        </div>

        {/* Save/Cancel */}
        <div className="flex gap-3 pt-4 border-t border-white/10">
          <Button onClick={handleSave} disabled={isSaving || !name.trim() || conditions.length === 0 || actions.length === 0}>
            {isSaving ? "Saving..." : isNew ? "Create Rule" : "Save Changes"}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export default function AutomationPage() {
  const [data, setData] = useState<AutomationRulesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [togglingRules, setTogglingRules] = useState<Set<string>>(new Set())
  const [deletingRules, setDeletingRules] = useState<Set<string>>(new Set())
  const [expandedRules, setExpandedRules] = useState<Set<string>>(new Set())
  const [isCreating, setIsCreating] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  const fetchRules = async () => {
    try {
      const apiPort = process.env.NEXT_PUBLIC_API_PORT || '8001'
      const response = await fetch(`http://${window.location.hostname}:${apiPort}/api/automation/rules`)

      if (!response.ok) {
        throw new Error('Failed to fetch automation rules')
      }

      const result = await response.json()
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRules()
    const interval = setInterval(fetchRules, 30000)
    return () => clearInterval(interval)
  }, [])

  const toggleExpanded = (ruleId: string) => {
    setExpandedRules(prev => {
      const next = new Set(prev)
      if (next.has(ruleId)) {
        next.delete(ruleId)
      } else {
        next.add(ruleId)
      }
      return next
    })
  }

  const toggleRule = async (ruleId: string, currentEnabled: boolean) => {
    setTogglingRules(prev => new Set(prev).add(ruleId))

    try {
      const apiPort = process.env.NEXT_PUBLIC_API_PORT || '8001'
      const response = await fetch(
        `http://${window.location.hostname}:${apiPort}/api/automation/rules/${ruleId}/toggle`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: !currentEnabled })
        }
      )

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to toggle rule')
      }

      await fetchRules()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to toggle rule')
    } finally {
      setTogglingRules(prev => {
        const next = new Set(prev)
        next.delete(ruleId)
        return next
      })
    }
  }

  const deleteRule = async (ruleId: string, ruleName: string) => {
    if (!confirm(`Are you sure you want to delete "${ruleName}"?`)) {
      return
    }

    setDeletingRules(prev => new Set(prev).add(ruleId))

    try {
      const apiPort = process.env.NEXT_PUBLIC_API_PORT || '8001'
      const response = await fetch(
        `http://${window.location.hostname}:${apiPort}/api/automation/rules/${ruleId}`,
        {
          method: 'DELETE'
        }
      )

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to delete rule')
      }

      await fetchRules()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete rule')
    } finally {
      setDeletingRules(prev => {
        const next = new Set(prev)
        next.delete(ruleId)
        return next
      })
    }
  }

  const createRule = async (ruleData: Partial<AutomationRule>) => {
    setIsSaving(true)
    try {
      const apiPort = process.env.NEXT_PUBLIC_API_PORT || '8001'
      const response = await fetch(
        `http://${window.location.hostname}:${apiPort}/api/automation/rules`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(ruleData)
        }
      )

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || 'Failed to create rule')
      }

      await fetchRules()
      setIsCreating(false)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to create rule')
    } finally {
      setIsSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="relative min-h-screen overflow-hidden text-foreground">
        <div className="absolute inset-0">
          <img src="/bg.jpeg" alt="" className="h-full w-full object-cover" aria-hidden="true" />
          <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
        </div>
        <div className="relative z-10 px-4 md:px-8 lg:px-12 py-8">
          <div className="space-y-2 mb-8">
            <Skeleton className="h-8 w-64 bg-white/10" />
            <Skeleton className="h-4 w-96 bg-white/10" />
          </div>
          <div className="grid gap-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-32 w-full bg-white/10" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="relative min-h-screen overflow-hidden text-foreground">
        <div className="absolute inset-0">
          <img src="/bg.jpeg" alt="" className="h-full w-full object-cover" aria-hidden="true" />
          <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
        </div>
        <div className="relative z-10 px-4 md:px-8 lg:px-12 py-8">
          <Card className="border-destructive bg-black/60 backdrop-blur-xl">
            <CardHeader>
              <CardTitle className="text-destructive">Error</CardTitle>
              <CardDescription>Failed to load automation rules</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{error}</p>
              <Button onClick={() => {
                setLoading(true)
                fetchRules()
              }} className="mt-4">
                Retry
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  return (
    <div className="relative min-h-screen overflow-hidden text-foreground">
      {/* Background */}
      <div className="absolute inset-0">
        <img src="/bg.jpeg" alt="" className="h-full w-full object-cover" aria-hidden="true" />
        <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
      </div>

      <div className="relative z-10 flex min-h-screen flex-col">
        <AppHeader />

        {/* Main Content */}
        <div className="flex-1 px-4 md:px-8 lg:px-12 py-8 space-y-6">
          {/* Page Title Bar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-foreground">Automation Rules</h1>
              <Badge variant="secondary" className="text-xs">
                {data?.enabled_count ?? 0} Active
              </Badge>
            </div>
            <Button onClick={() => setIsCreating(true)} disabled={isCreating}>
              <Plus className="h-4 w-4 mr-2" />
              New Rule
            </Button>
          </div>
          {/* Stats Cards */}
          <div className="grid gap-4 md:grid-cols-3">
            <Card className="border-white/10 bg-black/55 backdrop-blur-xl">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-muted-foreground">Total Rules</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-bold">{data?.total_count ?? 0}</div>
              </CardContent>
            </Card>
            <Card className="border-white/10 bg-black/55 backdrop-blur-xl">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-muted-foreground">Enabled Rules</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-bold text-green-400">
                  {data?.enabled_count ?? 0}
                </div>
              </CardContent>
            </Card>
            <Card className="border-white/10 bg-black/55 backdrop-blur-xl">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium text-muted-foreground">Protected Rules</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-bold text-blue-400 flex items-center gap-2">
                  <Shield className="h-6 w-6" />
                  {data?.protected_count ?? 0}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Rule Editor */}
          {isCreating && (
            <RuleEditor
              onSave={createRule}
              onCancel={() => setIsCreating(false)}
              isSaving={isSaving}
            />
          )}

          {/* Rules List */}
          <div className="space-y-4">
            {data?.rules && data.rules.length > 0 ? (
              data.rules.map((rule) => {
                const isToggling = togglingRules.has(rule.id)
                const isDeleting = deletingRules.has(rule.id)
                const isExpanded = expandedRules.has(rule.id)
                const conditions = rule.conditions?.all_of || rule.conditions?.any_of || []

                return (
                  <Card 
                    key={rule.id} 
                    className={cn(
                      "border-white/10 bg-black/55 backdrop-blur-xl transition-all duration-300",
                      rule.enabled && "border-l-4 border-l-green-500",
                      !rule.enabled && "opacity-70"
                    )}
                  >
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between">
                        <div className="space-y-2 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <CardTitle className="text-xl">{rule.name}</CardTitle>
                            {rule.enabled ? (
                              <Badge className="bg-green-600/80">Enabled</Badge>
                            ) : (
                              <Badge variant="secondary">Disabled</Badge>
                            )}
                            {rule.protected && (
                              <Badge variant="outline" className="border-blue-500 text-blue-400 flex items-center gap-1">
                                <Shield className="h-3 w-3" />
                                Protected
                              </Badge>
                            )}
                            <Badge variant="outline" className="text-muted-foreground">
                              Priority: {rule.priority}
                            </Badge>
                          </div>
                          <CardDescription className="text-sm">{rule.description}</CardDescription>
                        </div>
                        <div className="flex items-center gap-3 ml-4">
                          <Switch
                            checked={rule.enabled}
                            onCheckedChange={() => toggleRule(rule.id, rule.enabled)}
                            disabled={isToggling || isDeleting}
                          />
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => deleteRule(rule.id, rule.name)}
                            disabled={isDeleting || isToggling || rule.protected}
                            className="text-destructive hover:text-destructive hover:bg-destructive/10"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => toggleExpanded(rule.id)}
                          >
                            {isExpanded ? (
                              <ChevronUp className="h-4 w-4" />
                            ) : (
                              <ChevronDown className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                      </div>
                    </CardHeader>
                    
                    {/* Collapsed preview */}
                    {!isExpanded && (
                      <CardContent className="pt-0">
                        <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <Activity className="h-4 w-4" />
                            {conditions.length} condition{conditions.length !== 1 ? "s" : ""}
                          </span>
                          <span className="flex items-center gap-1">
                            <Zap className="h-4 w-4" />
                            {rule.actions.length} action{rule.actions.length !== 1 ? "s" : ""}
                          </span>
                        </div>
                      </CardContent>
                    )}
                    
                    {/* Expanded details */}
                    {isExpanded && (
                      <CardContent className="space-y-4 pt-0">
                        <div className="space-y-3">
                          <h4 className="text-sm font-semibold flex items-center gap-2">
                            <Activity className="h-4 w-4 text-blue-400" />
                            Conditions
                            {rule.conditions?.all_of && (
                              <span className="text-xs text-muted-foreground">(ALL must match)</span>
                            )}
                            {rule.conditions?.any_of && (
                              <span className="text-xs text-muted-foreground">(ANY must match)</span>
                            )}
                          </h4>
                          <div className="space-y-2 pl-6">
                            {conditions.map((condition, idx) => (
                              <ConditionDisplay key={idx} condition={condition} />
                            ))}
                          </div>
                        </div>
                        <div className="space-y-3">
                          <h4 className="text-sm font-semibold flex items-center gap-2">
                            <Zap className="h-4 w-4 text-yellow-400" />
                            Actions
                          </h4>
                          <div className="space-y-2 pl-6">
                            {rule.actions.map((action, idx) => (
                              <ActionDisplay key={idx} action={action} />
                            ))}
                          </div>
                        </div>
                      </CardContent>
                    )}
                  </Card>
                )
              })
            ) : (
              <Card className="border-white/10 bg-black/55 backdrop-blur-xl">
                <CardContent className="py-12">
                  <div className="text-center">
                    <Zap className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
                    <p className="text-muted-foreground mb-4">
                      No automation rules configured yet
                    </p>
                    <Button onClick={() => setIsCreating(true)}>
                      <Plus className="h-4 w-4 mr-2" />
                      Create Your First Rule
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Metadata footer */}
          {data?.metadata && (
            <Card className="border-white/10 bg-black/40 backdrop-blur">
              <CardContent className="py-4 text-sm text-muted-foreground flex items-center gap-4">
                {data.metadata.last_modified && (
                  <span>Last Modified: {new Date(data.metadata.last_modified).toLocaleString()}</span>
                )}
                {data.metadata.modified_by && (
                  <span>By: {data.metadata.modified_by}</span>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
