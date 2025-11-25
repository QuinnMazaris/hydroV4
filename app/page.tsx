"use client"

import type React from "react"
import { useEffect, useMemo, useState } from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer } from "recharts"
import { Activity, ChevronRight, Bot } from "lucide-react"

import { describeValue, resolveMetricMeta } from "@/lib/metrics"
import { useWsSensors } from "@/hooks/use-ws-metrics"
import { CameraFeed } from "@/components/camera-feed"
import { useCameras } from "@/hooks/use-cameras"
import { cn } from "@/lib/utils"
import { ActuatorDeviceGrid } from "@/components/actuator-device-grid"
import type { ActuatorCard } from "@/components/actuator-device-grid"
import { normalizeActuatorState, useActuatorQueue } from "@/hooks/use-actuator-queue"

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
  const { cameras, isLoading: camerasLoading, error: cameraError } = useCameras()
  const timeWindowMs = 24 * 60 * 60 * 1000

  const { optimisticActuatorStates, enqueueActuatorRequest } = useActuatorQueue({ actuatorsByDevice })

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


  type ActuatorInfo = ActuatorCard

  // Global control mode state
  const [globalMode, setGlobalMode] = useState<'auto' | 'manual'>('manual')
  const [controlModes, setControlModes] = useState<Record<string, Record<string, 'auto' | 'manual'>>>({})

  const actuatorDevices = useMemo(() => {
    return Object.entries(devices)
      .map(([deviceId, info]) => {
        const actuatorEntries = info.actuators || {}
        const actuators: ActuatorInfo[] = Object.entries(actuatorEntries).map(([key, meta]) => {
          // Get current state from the actuator stream (live data)
          const liveValue = actuatorsByDevice[deviceId]?.[key]?.[actuatorsByDevice[deviceId][key].length - 1]?.value
          const optimisticKey = `${deviceId}:${key}`
          const liveState = normalizeActuatorState(liveValue)
          const optimisticEntry = optimisticActuatorStates[optimisticKey]
          // Use optimistic state if available, otherwise fall back to live state
          const currentState = optimisticEntry ? optimisticEntry.state : liveState
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
          actuators,
        }
      })
      .filter((entry) => entry.actuators.length > 0)
      .sort((a, b) => a.deviceId.localeCompare(b.deviceId))
  }, [devices, actuatorsByDevice, optimisticActuatorStates])

  const recentErrors = useMemo(() => errors.slice(-5).reverse(), [errors])

  // Fetch control modes on mount
  useEffect(() => {
    fetch('/api/actuators/modes')
      .then(res => res.json())
      .then(data => {
        setControlModes(data.modes || {})

        // Determine global mode based on actuators
        const allModes = Object.values(data.modes || {}).flatMap(device => Object.values(device))
        if (allModes.length > 0) {
          const allAuto = allModes.every((mode: string) => mode === 'auto')
          setGlobalMode(allAuto ? 'auto' : 'manual')
        }
      })
      .catch(err => console.error('Failed to fetch control modes:', err))
  }, [])

  // Global mode toggle handler
  const handleGlobalModeToggle = async () => {
    const newMode = globalMode === 'manual' ? 'auto' : 'manual'

    if (newMode === 'auto') {
      const confirmed = window.confirm(
        'Switch to AUTO mode?\n\nAutomation will take control of all relays based on configured rules.'
      )
      if (!confirmed) return
    }

    try {
      const res = await fetch(`/api/actuators/mode/global?mode=${newMode}`, {
        method: 'POST'
      })

      if (res.ok) {
        setGlobalMode(newMode)

        // Update all control modes locally
        setControlModes(prev => {
          const updated = { ...prev }
          Object.keys(updated).forEach(deviceKey => {
            Object.keys(updated[deviceKey]).forEach(actuatorKey => {
              updated[deviceKey][actuatorKey] = newMode
            })
          })
          return updated
        })

        console.log(`Switched to ${newMode.toUpperCase()} mode`)
      } else {
        throw new Error('Failed to change mode')
      }
    } catch (error) {
      console.error('Error changing mode:', error)
      alert('Failed to change control mode')
    }
  }

  const handleActuatorToggle = (deviceId: string, actuator: ActuatorInfo) => {
    console.log('Toggle clicked:', { deviceId, actuator })
    if (!actuator || !actuator.key) {
      console.warn('Invalid actuator', deviceId, actuator)
      return
    }

    // Check if actuator is in AUTO mode
    const mode = controlModes[deviceId]?.[actuator.key] || 'manual'
    if (mode === 'auto') {
      alert('This actuator is in AUTO mode.\n\nSwitch to MANUAL mode to control it manually.')
      return
    }

    // Determine next state based on current state
    const currentState = actuator.currentState
    const nextState: 'on' | 'off' = currentState === 'on' ? 'off' : 'on'

    enqueueActuatorRequest({
      deviceId,
      actuatorKey: actuator.key,
      nextState,
    })
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
        <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
      </div>

      <div className="relative z-10 flex min-h-screen flex-col">
        {/* Header */}
        <header className="border-b border-white/10 bg-black/30 backdrop-blur-md">
          <div className="container mx-auto px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <div className="flex items-center space-x-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
                    <Activity className="h-5 w-5 text-primary-foreground" />
                  </div>
                  <h1 className="text-xl font-semibold text-foreground">Hydro Dashboard</h1>
                </div>
                <Badge
                  variant={status === "live" ? "secondary" : status === "connecting" ? "secondary" : "destructive"}
                  className="text-xs backdrop-blur"
                >
                  {status === "live" ? "Live" : status === "connecting" ? "Connecting" : "Disconnected"}
                </Badge>
              </div>
              <div className="flex items-center gap-2">
                <a href="/chat">
                  <Button variant="ghost" size="sm">
                    Chat with AI
                  </Button>
                </a>
                <a href="/automation">
                  <Button variant="ghost" size="sm">
                    Automation
                  </Button>
                </a>
              </div>
            </div>
          </div>
        </header>

        <div className="container mx-auto flex-1 px-6 py-8">
          {/* Global Control Mode Toggle */}
          <Card className="mb-6 border-white/10 bg-black/55 backdrop-blur-xl">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <CardTitle className="text-xl">
                    Current Mode: <span className={globalMode === 'auto' ? 'text-blue-400' : 'text-gray-400'}>
                      {globalMode === 'auto' ? 'AUTOMATIC' : 'MANUAL'}
                    </span>
                  </CardTitle>
                  <p className="text-sm text-muted-foreground mt-1">
                    {globalMode === 'auto'
                      ? 'All relays are controlled by automation rules'
                      : 'You have direct control of all relays'}
                  </p>
                </div>

                <button
                  onClick={handleGlobalModeToggle}
                  className={cn(
                    "px-6 py-3 rounded-lg font-semibold transition-all duration-200",
                    globalMode === 'auto'
                      ? "bg-gray-600 hover:bg-gray-700 text-white"
                      : "bg-blue-600 hover:bg-blue-700 text-white"
                  )}
                >
                  {globalMode === 'auto' ? 'Switch to Manual' : 'Switch to Auto'}
                </button>
              </div>
            </CardHeader>
          </Card>

          {/* Camera Feeds - Dynamically loaded from MediaMTX */}
          <div className="mb-8 space-y-4">
            {cameraError && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                {cameraError}
              </div>
            )}
            {camerasLoading ? (
              <Card className="border-white/10 bg-black/40">
                <CardContent className="py-6">
                  <p className="text-sm text-muted-foreground">Loading camera feeds…</p>
                </CardContent>
              </Card>
            ) : cameras.length > 0 ? (
              <div className="space-y-6">
                {cameras.map((camera) => (
                  <CameraFeed key={camera.device_key} deviceKey={camera.device_key} />
                ))}
              </div>
            ) : (
              <Card className="border-white/10 bg-black/40">
                <CardContent className="py-6">
                  <p className="text-sm text-muted-foreground">
                    No cameras discovered. Confirm MediaMTX paths and camera sync are configured.
                  </p>
                </CardContent>
              </Card>
            )}
          </div>

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

          <ActuatorDeviceGrid
            devices={actuatorDevices}
            controlModes={controlModes}
            onToggle={handleActuatorToggle}
          />

          <div className="space-y-6">
            {recentErrors.length > 0 && (
              <Card className="bg-black/50 border-white/10 backdrop-blur-xl shadow-xl">
                <CardHeader>
                  <CardTitle className="text-lg font-semibold text-foreground">Incoming Issues</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {recentErrors.map((error, index) => (
                    <div
                      key={`${error.code || 'error'}:${index}`}
                      className="rounded-xl border border-destructive/30 bg-destructive/30 p-3 text-sm backdrop-blur"
                    >
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
                <CardTitle className="text-lg font-semibold text-foreground">Device Overview</CardTitle>
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
          </div>
        </div>
      </div>
    </div>
  )
}
