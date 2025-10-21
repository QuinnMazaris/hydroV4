"use client"

import { useEffect, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Trash2 } from "lucide-react"

interface AutomationRule {
  id: string
  name: string
  description: string
  enabled: boolean
  protected: boolean
  priority: number
  conditions: any
  actions: any
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

export default function AutomationPage() {
  const [data, setData] = useState<AutomationRulesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [togglingRules, setTogglingRules] = useState<Set<string>>(new Set())
  const [deletingRules, setDeletingRules] = useState<Set<string>>(new Set())

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
    const interval = setInterval(fetchRules, 30000) // Refresh every 30 seconds

    return () => clearInterval(interval)
  }, [])

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

      // Refresh data
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

      // Refresh data
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

  if (loading) {
    return (
      <div className="container mx-auto p-6 space-y-6">
        <div className="space-y-2">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-96" />
        </div>
        <div className="grid gap-4">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <Card className="border-destructive">
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
    )
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Automation Rules</h1>
        <p className="text-muted-foreground">
          Manage and monitor automation rules for your hydroponic system
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Total Rules</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{data?.total_count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Enabled Rules</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-600 dark:text-green-400">
              {data?.enabled_count ?? 0}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Protected Rules</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-600 dark:text-blue-400">
              {data?.protected_count ?? 0}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        {data?.rules && data.rules.length > 0 ? (
          data.rules.map((rule) => {
            const isToggling = togglingRules.has(rule.id)
            const isDeleting = deletingRules.has(rule.id)

            return (
              <Card key={rule.id} className="hover:shadow-md transition-shadow">
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <div className="space-y-1 flex-1">
                      <div className="flex items-center gap-2">
                        <CardTitle className="text-xl">{rule.name}</CardTitle>
                        {rule.enabled && (
                          <Badge variant="default" className="bg-green-600">
                            Enabled
                          </Badge>
                        )}
                        {!rule.enabled && (
                          <Badge variant="secondary">Disabled</Badge>
                        )}
                        {rule.protected && (
                          <Badge variant="outline" className="border-blue-600 text-blue-600">
                            Protected
                          </Badge>
                        )}
                      </div>
                      <CardDescription>{rule.description}</CardDescription>
                    </div>
                    <div className="flex items-center gap-3 ml-4">
                      <Badge variant="outline">Priority: {rule.priority}</Badge>
                      <div className="flex items-center gap-2">
                        <Switch
                          checked={rule.enabled}
                          onCheckedChange={() => toggleRule(rule.id, rule.enabled)}
                          disabled={isToggling || isDeleting}
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => deleteRule(rule.id, rule.name)}
                          disabled={isDeleting || isToggling}
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <h4 className="text-sm font-semibold">Conditions</h4>
                      <div className="rounded-lg bg-muted p-3">
                        <pre className="text-xs overflow-x-auto">
                          {JSON.stringify(rule.conditions, null, 2)}
                        </pre>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <h4 className="text-sm font-semibold">Actions</h4>
                      <div className="rounded-lg bg-muted p-3">
                        <pre className="text-xs overflow-x-auto">
                          {JSON.stringify(rule.actions, null, 2)}
                        </pre>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })
        ) : (
          <Card>
            <CardContent className="py-8">
              <p className="text-center text-muted-foreground">
                No automation rules configured yet
              </p>
            </CardContent>
          </Card>
        )}
      </div>

      {data?.metadata && (
        <Card className="border-muted">
          <CardHeader>
            <CardTitle className="text-sm">Metadata</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground space-y-1">
            {data.metadata.last_modified && (
              <p>Last Modified: {new Date(data.metadata.last_modified).toLocaleString()}</p>
            )}
            {data.metadata.modified_by && (
              <p>Modified By: {data.metadata.modified_by}</p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
