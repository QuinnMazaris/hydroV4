"use client"

import type React from "react"

import { useMemo, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip } from "recharts"
import { Activity, TrendingUp, Users, Zap, Database, Globe, ChevronRight } from "lucide-react"
import { useWsMetrics } from "@/hooks/use-ws-metrics"

interface MetricCardProps {
  title: string
  value: string | number
  change: string
  trend: "up" | "down" | "neutral"
  icon: React.ReactNode
  data: Array<{ time: string; value: number; timestamp: number }>
  color: string
}

function MetricCard({ title, value, change, trend, icon, data, color }: MetricCardProps) {
  return (
    <Card className="bg-card/50 backdrop-blur-sm border-border/50 hover:bg-card/70 transition-all duration-200">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <div className="text-muted-foreground">{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between mb-4">
          <div className="text-2xl font-bold text-foreground font-mono">{value}</div>
          <Badge
            variant={trend === "up" ? "default" : trend === "down" ? "destructive" : "secondary"}
            className="text-xs"
          >
            {change}
          </Badge>
        </div>
        <div className="h-16 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <Line
                type="monotone"
                dataKey="value"
                stroke={color}
                strokeWidth={2}
                dot={false}
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
  const [timeRange, setTimeRange] = useState("1h")
  const { metricsByDevice, devices, status } = useWsMetrics({ url: "ws://localhost:8000/ws/metrics" })

  const timeWindowMs = useMemo(() => {
    switch (timeRange) {
      case "1h":
        return 60 * 60 * 1000
      case "6h":
        return 6 * 60 * 60 * 1000
      case "24h":
        return 24 * 60 * 60 * 1000
      case "7d":
        return 7 * 24 * 60 * 60 * 1000
      default:
        return 60 * 60 * 1000
    }
  }, [timeRange])

  const metricDisplayName = (name: string) =>
    name
      .replace(/_/g, " ")
      .replace(/\b\w/g, (m) => m.toUpperCase())

  const metricColor = (name: string) => {
    switch (name) {
      case "temperature":
      case "water_temp_c":
        return "oklch(0.7 0.15 142)"
      case "humidity":
        return "oklch(0.65 0.18 220)"
      case "pressure":
      case "vpd_kpa":
        return "oklch(0.75 0.12 60)"
      case "ph":
        return "oklch(0.65 0.2 25)"
      case "tds_ppm":
        return "oklch(0.68 0.16 300)"
      case "lux":
        return "oklch(0.72 0.14 180)"
      default:
        return "oklch(0.7 0.15 142)"
    }
  }

  const cards = useMemo(() => {
    const now = Date.now()
    const start = now - timeWindowMs
    const results: Array<{
      key: string
      title: string
      value: string | number
      change: string
      trend: "up" | "down" | "neutral"
      icon: React.ReactNode
      data: Array<{ time: string; value: number; timestamp: number }>
      color: string
    }> = []

    Object.entries(metricsByDevice).forEach(([deviceId, seriesMap]) => {
      Object.entries(seriesMap).forEach(([metricName, series]) => {
        const filtered = series.filter((p) => p.timestamp >= start)
        if (filtered.length === 0) return
        const data = filtered.map((p) => ({
          timestamp: p.timestamp,
          value: p.value,
          time: new Date(p.timestamp).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }),
        }))
        const first = filtered[0]?.value ?? 0
        const last = filtered[filtered.length - 1]?.value ?? 0
        const diff = last - first
        const pct = first !== 0 ? (diff / first) * 100 : 0
        results.push({
          key: `${deviceId}:${metricName}`,
          title: `${deviceId} · ${metricDisplayName(metricName)}`,
          value: Number.isInteger(last) ? last : parseFloat(last.toFixed(2)),
          change: `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`,
          trend: pct > 0 ? "up" : pct < 0 ? "down" : "neutral",
          icon: <Activity className="w-4 h-4" />,
          data,
          color: metricColor(metricName),
        })
      })
    })

    // Sort for stable rendering: by device then metric
    results.sort((a, b) => a.key.localeCompare(b.key))
    return results
  }, [metricsByDevice, timeWindowMs])

  const mainChartData = useMemo(() => {
    return cards[0]?.data || []
  }, [cards])

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="border-b border-border/50 bg-card/30 backdrop-blur-sm">
        <div className="container mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-4">
              <div className="flex items-center space-x-2">
                <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center">
                  <Activity className="w-5 h-5 text-primary-foreground" />
                </div>
                <h1 className="text-xl font-semibold text-foreground">Analytics Dashboard</h1>
              </div>
              <Badge variant={status === "live" ? "secondary" : status === "connecting" ? "secondary" : "destructive"} className="text-xs">
                {status === "live" ? "Live" : status === "connecting" ? "Connecting" : "Disconnected"}
              </Badge>
            </div>
            <div className="flex items-center space-x-4">
              <Select value={timeRange} onValueChange={setTimeRange}>
                <SelectTrigger className="w-32 bg-card/50 border-border/50">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1h">Last hour</SelectItem>
                  <SelectItem value="6h">Last 6 hours</SelectItem>
                  <SelectItem value="24h">Last 24 hours</SelectItem>
                  <SelectItem value="7d">Last 7 days</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" className="bg-card/50 border-border/50">
                Export
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-6 py-8">
        {/* Metrics Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
          {cards.map((c) => (
            <MetricCard
              key={c.key}
              title={c.title}
              value={c.value}
              change={c.change}
              trend={c.trend}
              icon={c.icon}
              data={c.data}
              color={c.color}
            />
          ))}
        </div>

        {/* Main Chart */}
        <Card className="bg-card/50 backdrop-blur-sm border-border/50 mb-8">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg font-semibold text-foreground">Primary Metric</CardTitle>
              <div className="flex items-center space-x-2 text-sm text-muted-foreground">
                <div className="flex items-center space-x-1">
                  <div className="w-2 h-2 rounded-full bg-chart-1"></div>
                  <span>Live</span>
                </div>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-80 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={mainChartData}>
                  <defs>
                    <linearGradient id="requestGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="oklch(0.7 0.15 142)" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="oklch(0.7 0.15 142)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="oklch(0.22 0 0)" />
                  <XAxis dataKey="time" stroke="oklch(0.65 0 0)" fontSize={12} tickLine={false} axisLine={false} interval={"preserveStartEnd"} minTickGap={20} />
                  <YAxis stroke="oklch(0.65 0 0)" fontSize={12} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "oklch(0.12 0 0)",
                      border: "1px solid oklch(0.22 0 0)",
                      borderRadius: "8px",
                      color: "oklch(0.98 0 0)",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="oklch(0.7 0.15 142)"
                    strokeWidth={2}
                    fill="url(#requestGradient)"
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Bottom Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* System Status */}
          <Card className="bg-card/50 backdrop-blur-sm border-border/50">
            <CardHeader>
              <CardTitle className="text-lg font-semibold text-foreground">System Status</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {Object.entries(devices).map(([deviceId, info]) => (
                <div key={deviceId} className="flex items-center justify-between p-3 rounded-lg bg-accent/30">
                  <div className="flex items-center space-x-3">
                    <div className={`w-2 h-2 rounded-full ${info.is_active ? "bg-chart-1" : "bg-destructive"}`}></div>
                    <span className="text-sm font-medium text-foreground">{deviceId}</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <span className="text-xs text-muted-foreground">{info.device_type || "sensor"}</span>
                    <Badge variant={info.is_active ? "default" : "destructive"} className="text-xs">
                      {info.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Recent Activity */}
          <Card className="bg-card/50 backdrop-blur-sm border-border/50">
            <CardHeader>
              <CardTitle className="text-lg font-semibold text-foreground">Recent Activity</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {[
                { event: "High traffic detected", time: "2 min ago", type: "warning" },
                { event: "Database backup completed", time: "15 min ago", type: "success" },
                { event: "New deployment started", time: "32 min ago", type: "info" },
                { event: "Cache cleared successfully", time: "1 hour ago", type: "success" },
              ].map((activity, index) => (
                <div
                  key={index}
                  className="flex items-center justify-between p-3 rounded-lg bg-accent/30 hover:bg-accent/50 transition-colors cursor-pointer"
                >
                  <div className="flex items-center space-x-3">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        activity.type === "success"
                          ? "bg-chart-1"
                          : activity.type === "warning"
                            ? "bg-chart-3"
                            : "bg-chart-2"
                      }`}
                    ></div>
                    <span className="text-sm text-foreground">{activity.event}</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <span className="text-xs text-muted-foreground">{activity.time}</span>
                    <ChevronRight className="w-3 h-3 text-muted-foreground" />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
