"use client"

import type React from "react"
import { useMemo, useState } from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer } from "recharts"
import { Activity, ChevronRight, Leaf } from "lucide-react"

import { describeValue, resolveMetricMeta } from "@/lib/metrics"
import { useWsSensors } from "@/hooks/use-ws-metrics"
import type { DeviceInfo } from "@/hooks/use-ws-metrics"

interface MetricCardProps {
  title: string
  valueLabel: string
  changeLabel: string
  trend: "up" | "down" | "neutral"
  icon: React.ReactNode
  data: Array<{ time: string; value: number; timestamp: number }>
  color: string
  yDomain?: [number, number]
}

function MetricCard({ title, valueLabel, changeLabel, trend, icon, data, color, yDomain }: MetricCardProps) {
  return (
    <Card
      className="border-white/10 backdrop-blur-xl shadow-xl transition-all duration-300 hover:bg-black/60"
      style={{ backgroundColor: "rgba(0, 0, 0, 0.55)" }}
    >
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <div className="text-muted-foreground">{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between mb-4">
          <div className="text-2xl font-bold text-foreground font-mono">{valueLabel}</div>
          <Badge
            variant={trend === "up" ? "default" : trend === "down" ? "destructive" : "secondary"}
            className="text-xs"
          >
            {changeLabel}
          </Badge>
        </div>
        <div className="h-16 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <YAxis hide domain={yDomain ? yDomain : ["auto", "auto"]} />
              <XAxis hide dataKey="time" />
              <Line
                type="monotone"
                dataKey="value"
                stroke={color}
                strokeWidth={2}
                dot={false}
                activeDot={false}
                strokeLinecap="round"
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}

export default function Dashboard() {
  const { sensorsByDevice, actuatorsByDevice, devices, status, errors } = useWsSensors()
  const timeWindowMs = 24 * 60 * 60 * 1000

  const formatRelativeTime = (timestamp?: number | null) => {
    if (!timestamp) return "Unknown"
    const deltaMs = Date.now() - timestamp
    if (deltaMs < 60_000) return "Just now"
    const minutes = Math.floor(deltaMs / 60_000)
    if (minutes < 60) return `${minutes} min ago`
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return `${hours} hr${hours > 1 ? "s" : ""} ago`
    const days = Math.floor(hours / 24)
    return `${days} day${days > 1 ? "s" : ""} ago`
  }


  type ActuatorInfo = {
    key: string
    label: string
    unit: string
    color: string
    currentState?: any
  }

  const [actuatorBusy, setActuatorBusy] = useState<Record<string, boolean>>({})

  const actuatorDevices = useMemo(() => {
    return Object.entries(devices)
      .map(([deviceId, info]) => {
        const actuatorEntries = info.actuators || {}
        const actuators: ActuatorInfo[] = Object.entries(actuatorEntries).map(([key, meta]) => {
          // Get current state from the actuator stream (live data)
          const currentState = actuatorsByDevice[deviceId]?.[key]?.[actuatorsByDevice[deviceId][key].length - 1]?.value
          return {
            key,
            label: meta.label || key,
            unit: meta.unit || '',
            color: meta.color || '#888888',
            currentState
          }
        })
        return {
          deviceId,
          deviceType: 'device',
          actuators,
        }
      })
      .filter((entry) => entry.actuators.length > 0)
      .sort((a, b) => a.deviceId.localeCompare(b.deviceId))
  }, [devices, actuatorsByDevice])

  const recentErrors = useMemo(() => errors.slice(-5).reverse(), [errors])

  const handleActuatorToggle = async (deviceId: string, actuator: ActuatorInfo) => {
    if (!actuator || !actuator.key) {
      console.warn('Invalid actuator', deviceId, actuator)
      return
    }

    const busyKey = `${deviceId}:${actuator.key}`
    setActuatorBusy((prev) => ({ ...prev, [busyKey]: true }))

    // Determine next state based on current state
    const currentState = actuator.currentState
    let nextState: string
    if (typeof currentState === 'boolean') {
      nextState = currentState ? 'off' : 'on'
    } else if (typeof currentState === 'string') {
      nextState = currentState.toLowerCase() === 'on' ? 'off' : 'on'
    } else {
      nextState = 'on' // Default to turning on if unknown state
    }

    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '')
      const res = await fetch(`${apiBase}/api/actuators/${deviceId}/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actuator_key: actuator.key,
          state: nextState
        }),
      })
      if (!res.ok) {
        const message = await res.text()
        throw new Error(message || 'Failed to send actuator command')
      }
    } catch (error) {
      console.error('Failed to send actuator command', error)
      if (typeof window !== 'undefined') {
        window.alert?.('Failed to send actuator command.')
      }
    } finally {
      setActuatorBusy((prev) => {
        const next = { ...prev }
        delete next[busyKey]
        return next
      })
    }
  }

  const deviceActivities = useMemo(() => {
    const now = Date.now()
    const items = Object.entries(devices).map(([deviceId, info]) => {
      const lastSeen = info.last_seen ?? null
      return {
        deviceId,
        deviceType: "device",
        isActive: info.is_active ?? false,
        lastSeen,
        ageMs: lastSeen ? now - lastSeen : Number.POSITIVE_INFINITY,
      }
    })

    items.sort((a, b) => a.ageMs - b.ageMs)
    return items.slice(0, 6)
  }, [devices])

  const cards = useMemo(() => {
    const now = Date.now()
    const start = now - timeWindowMs
    const results: Array<{
      key: string
      title: string
      valueLabel: string
      changeLabel: string
      trend: "up" | "down" | "neutral"
      icon: React.ReactNode
      data: Array<{ time: string; value: number; timestamp: number }>
      color: string
      yDomain?: [number, number]
    }> = []

    Object.entries(sensorsByDevice).forEach(([deviceId, seriesMap]) => {
      Object.entries(seriesMap).forEach(([sensorName, series]) => {
        const filtered = series.filter((p) => p.timestamp >= start)
        if (filtered.length === 0) return

        const deviceMeta = devices[deviceId]?.sensors?.[sensorName]
        const descriptor = resolveMetricMeta(sensorName, deviceMeta)
        const data = filtered.map((p) => ({
          timestamp: p.timestamp,
          value: p.value,
          time: new Date(p.timestamp).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }),
        }))
        const values = filtered.map((p) => p.value)
        const minVal = Math.min(...values)
        const maxVal = Math.max(...values)
        const span = Math.max(1e-6, maxVal - minVal)
        const pad = Math.max(span * 0.1, Math.max(0.01 * Math.abs(maxVal), 0.01))
        const yDomain: [number, number] = [minVal - pad, maxVal + pad]
        const first = filtered[0]?.value ?? 0
        const last = filtered[filtered.length - 1]?.value ?? 0
        const numericFirst = Number(first)
        const numericLast = Number(last)
        const diff = numericLast - numericFirst
        const pct = Number.isFinite(diff) && Number.isFinite(numericFirst) && numericFirst !== 0 ? (diff / numericFirst) * 100 : 0
        const rounded = Number.isFinite(numericLast)
          ? (Number.isInteger(numericLast) ? numericLast : parseFloat(numericLast.toFixed(2)))
          : 0
        const valueLabel = describeValue(rounded, descriptor.unit)
        const changeLabel = `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`

        results.push({
          key: `${deviceId}:${sensorName}`,
          title: `${deviceId} · ${descriptor.label}`,
          valueLabel,
          changeLabel,
          trend: pct > 0 ? "up" : pct < 0 ? "down" : "neutral",
          icon: <Activity className="w-4 h-4" />,
          data,
          color: descriptor.color,
          yDomain,
        })
      })
    })

    results.sort((a, b) => a.key.localeCompare(b.key))
    return results
  }, [devices, sensorsByDevice, timeWindowMs]);


  // No primary metric chart; sparklines serve as primary visualization

  return (
    <div className="relative min-h-screen overflow-hidden text-foreground">
      <div className="absolute inset-0">
        <img src="/bg.jpeg" alt="" className="h-full w-full object-cover" aria-hidden="true" />
        <div className="absolute inset-0 bg-black/70" aria-hidden="true" />
      </div>

      <div className="relative z-10 flex min-h-screen flex-col">
        {/* Header */}
        <header className="border-b border-white/10 bg-black/30 backdrop-blur-md">
          <div className="container mx-auto px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <div className="flex items-center space-x-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
                    <Leaf className="h-5 w-5 text-primary-foreground" />
                  </div>
                  <h1 className="text-xl font-semibold text-foreground">Hydro</h1>
                </div>
                <Badge
                  variant={status === "live" ? "secondary" : status === "connecting" ? "secondary" : "destructive"}
                  className="text-xs backdrop-blur"
                >
                  {status === "live" ? "Live" : status === "connecting" ? "Connecting" : "Disconnected"}
                </Badge>
              </div>
              <div />
            </div>
          </div>
        </header>

        <div className="container mx-auto flex-1 px-6 py-8">
          {/* Metrics Grid */}
          <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
            {cards.map((c) => (
              <MetricCard
                key={c.key}
                title={c.title}
                valueLabel={c.valueLabel}
                changeLabel={c.changeLabel}
                trend={c.trend}
                icon={c.icon}
                data={c.data}
                color={c.color}
                yDomain={c.yDomain}
              />
            ))}
          </div>

          {/* Bottom Section */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* System Status */}
            <Card className="bg-black/50 border-white/10 backdrop-blur-xl shadow-xl">
              <CardHeader>
                <CardTitle className="text-lg font-semibold text-foreground">System Status</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {Object.entries(devices).map(([deviceId, info]) => (
                  <div key={deviceId} className="rounded-xl border border-white/10 bg-black/40 p-3 backdrop-blur">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center space-x-3">
                        <div className={`h-2 w-2 rounded-full ${info.is_active ? "bg-chart-1" : "bg-destructive"}`}></div>
                        <span className="text-sm font-medium text-foreground">{deviceId}</span>
                      </div>
                      <Badge variant={info.is_active ? "default" : "destructive"} className="text-xs">
                        {info.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </div>
                    <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
                      <span className="capitalize">device</span>
                      <span>{formatRelativeTime(info.last_seen)}</span>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* Recent Activity */}
            <Card className="bg-black/50 border-white/10 backdrop-blur-xl shadow-xl">
              <CardHeader>
                <CardTitle className="text-lg font-semibold text-foreground">Recent Activity</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {deviceActivities.length === 0 ? (
                  <div className="text-sm text-muted-foreground">No recent device activity yet.</div>
                ) : (
                  deviceActivities.map((activity) => {
                    const statusColor = activity.isActive
                      ? "bg-chart-1"
                      : activity.ageMs < 60 * 60 * 1000
                        ? "bg-chart-3"
                        : "bg-destructive"

                    return (
                      <div
                        key={activity.deviceId}
                        className="flex items-center justify-between rounded-xl border border-white/10 bg-black/40 p-3 backdrop-blur transition-colors hover:bg-black/30"
                      >
                        <div className="flex items-center space-x-3">
                          <div className={`h-2 w-2 rounded-full ${statusColor}`}></div>
                          <div className="flex flex-col">
                            <span className="text-sm text-foreground">{activity.deviceId}</span>
                            <span className="text-xs text-muted-foreground">{activity.deviceType}</span>
                          </div>
                        </div>
                        <div className="flex items-center space-x-2">
                          <span className="text-xs text-muted-foreground">{formatRelativeTime(activity.lastSeen)}</span>
                          <ChevronRight className="h-3 w-3 text-muted-foreground" />
                        </div>
                      </div>
                    )
                  })
                )}
              </CardContent>
            </Card>

            <div className="space-y-6">
              {recentErrors.length > 0 && (
                <Card className="bg-black/50 border-white/10 backdrop-blur-xl shadow-xl">
                  <CardHeader>
                    <CardTitle className="text-lg font-semibold text-foreground">Incoming Issues</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {recentErrors.map((error, index) => (
                      <div key={`${error.code || 'error'}:${index}`} className="rounded-xl border border-destructive/30 bg-destructive/30 p-3 text-sm backdrop-blur">
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-destructive">{error.code || 'Unknown data'}</span>
                          <span className="text-xs text-muted-foreground">{formatRelativeTime(error.ts)}</span>
                        </div>
                        <p className="mt-1 text-destructive-foreground">{error.message}</p>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}

              <Card className="bg-black/50 border-white/10 backdrop-blur-xl shadow-xl">
                <CardHeader>
                  <CardTitle className="text-lg font-semibold text-foreground">Device Controls</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {actuatorDevices.length === 0 ? (
                    <div className="text-sm text-muted-foreground">Awaiting actuator discovery data.</div>
                  ) : (
                    actuatorDevices.map(({ deviceId, deviceType, actuators }) => (
                      <div key={deviceId} className="space-y-3 rounded-xl border border-white/10 bg-black/40 p-3 backdrop-blur">
                        <div className="flex items-center justify-between text-sm text-foreground">
                          <span className="font-medium">{deviceId}</span>
                          <span className="text-xs text-muted-foreground">{deviceType}</span>
                        </div>
                        <div className="space-y-2">
                          {actuators.map((actuator) => {
                            const busyKey = `${deviceId}:${actuator.key}`
                            const busy = actuatorBusy[busyKey] ?? false

                            // Determine current state from actuator stream
                            let stateLabel: string
                            let isOn: boolean
                            if (typeof actuator.currentState === 'boolean') {
                              isOn = actuator.currentState
                              stateLabel = isOn ? 'on' : 'off'
                            } else if (typeof actuator.currentState === 'string') {
                              stateLabel = actuator.currentState.toLowerCase()
                              isOn = stateLabel === 'on'
                            } else {
                              stateLabel = 'unknown'
                              isOn = false
                            }

                            const label = actuator.label || actuator.key

                            return (
                              <div key={busyKey} className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/30 px-3 py-2 backdrop-blur">
                                <div className="flex flex-col">
                                  <span className="text-sm text-foreground">{label}</span>
                                  <span className="text-xs text-muted-foreground capitalize">{stateLabel}</span>
                                </div>
                                <Button
                                  size="sm"
                                  variant={isOn ? 'destructive' : 'default'}
                                  disabled={busy}
                                  onClick={() => handleActuatorToggle(deviceId, actuator)}
                                  className="backdrop-blur"
                                >
                                  {busy ? 'Sending...' : isOn ? 'Turn Off' : 'Turn On'}
                                </Button>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
