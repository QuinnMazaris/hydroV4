"use client"

import { useEffect, useMemo, useRef, useState } from "react"

export type MetricPoint = { timestamp: number; value: number }

export type DeviceLatest = {
  timestamp: number
  metrics: Record<string, number>
}

export type MetricMeta = { label?: string; unit?: string; color?: string }

export type DataError = {
  code?: string
  message: string
  context?: Record<string, unknown>
  ts: number
}

export type DeviceInfo = {
  device_type?: string
  is_active?: boolean
  last_seen?: number | null
  metrics?: Record<string, MetricMeta>
  actuators?: Array<Record<string, any>>
}

export type DevicesMap = Record<string, DeviceInfo>

type IncomingEvent =
  | { type: "snapshot"; devices: DevicesMap; latest: Record<string, DeviceLatest>; ts: number }
  | { type: "device"; device_id: string; device_type?: string; is_active?: boolean; last_seen?: number; metrics?: Record<string, MetricMeta>; actuators?: Array<Record<string, any>> }
  | { type: "reading"; device_id: string; timestamp: number; metrics: Record<string, number> }
  | { type: "error"; code?: string; message: string; context?: Record<string, unknown>; ts?: number }

export function useWsMetrics(options?: {
  url?: string
  maxPointsPerSeries?: number
  fps?: number
}) {
  const defaultUrl = typeof window !== "undefined" ? `ws://${window.location.hostname}:8000/ws/metrics` : "ws://localhost:8000/ws/metrics"
  const { url, maxPointsPerSeries = 2000, fps = 4 } = options || {}
  const resolvedUrl = url || defaultUrl

  const buffersRef = useRef<Record<string, Record<string, MetricPoint[]>>>({})
  const [devices, setDevices] = useState<DevicesMap>({})
  const [snapshot, setSnapshot] = useState<Record<string, Record<string, MetricPoint[]>>>({})
  const [status, setStatus] = useState<"connecting" | "live" | "disconnected">("connecting")
  const [errors, setErrors] = useState<DataError[]>([])
  const throttleRef = useRef<number | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let closed = false
    let reconnectTimer: number | null = null

    const connect = () => {
      if (closed) return
      try {
        const ws = new WebSocket(resolvedUrl)
        wsRef.current = ws

        ws.onopen = () => {
          setStatus("live")
        }

        ws.onclose = () => {
          setStatus("disconnected")
          if (!closed) {
            reconnectTimer = window.setTimeout(connect, 1500)
          }
        }

        ws.onerror = () => {
          setStatus("disconnected")
          try { ws.close() } catch {}
        }

        ws.onmessage = (e) => {
          try {
            const ev: IncomingEvent = JSON.parse(e.data)
            if (ev.type === "snapshot") {
              setDevices((prev) => {
                const next = { ...prev }
                for (const [deviceId, info] of Object.entries(ev.devices || {})) {
                  const current = next[deviceId] || {}
                  next[deviceId] = {
                    ...current,
                    ...info,
                    metrics: { ...(current.metrics || {}), ...(info.metrics || {}) },
                    actuators: info.actuators ?? current.actuators,
                    last_seen: info.last_seen ?? current.last_seen ?? null,
                  }
                }
                return next
              })
              const buffers = buffersRef.current
              for (const [deviceId, latest] of Object.entries(ev.latest || {})) {
                const series = (buffers[deviceId] ||= {})
                const ts = latest.timestamp
                for (const [metricName, value] of Object.entries(latest.metrics)) {
                  const arr = (series[metricName] ||= [])
                  arr.push({ timestamp: ts, value })
                  if (arr.length > maxPointsPerSeries) series[metricName] = arr.slice(-maxPointsPerSeries)
                }
              }
              scheduleCommit()
            } else if (ev.type === "device") {
              setDevices((prev) => {
                const current = prev[ev.device_id] || {}
                return {
                  ...prev,
                  [ev.device_id]: {
                    ...current,
                    device_type: ev.device_type ?? current.device_type,
                    is_active: ev.is_active ?? current.is_active,
                    last_seen: ev.last_seen ?? current.last_seen ?? null,
                    metrics: { ...(current.metrics || {}), ...(ev.metrics || {}) },
                    actuators: ev.actuators ?? current.actuators,
                  },
                }
              })
            } else if (ev.type === "error") {
              setErrors((prev) => {
                const next = [...prev, {
                  code: ev.code,
                  message: ev.message,
                  context: ev.context,
                  ts: ev.ts ?? Date.now(),
                }]
                return next.slice(-20)
              })
            } else if (ev.type === "reading") {
              const buffers = buffersRef.current
              const series = (buffers[ev.device_id] ||= {})
              const ts = ev.timestamp
              for (const [metricName, value] of Object.entries(ev.metrics)) {
                const arr = (series[metricName] ||= [])
                const last = arr[arr.length - 1]
                // If same timestamp as last point, update in place to avoid shape changes
                if (last && last.timestamp === ts) {
                  last.value = value
                } else {
                  arr.push({ timestamp: ts, value })
                }
                if (arr.length > maxPointsPerSeries) series[metricName] = arr.slice(-maxPointsPerSeries)
              }
              scheduleCommit()
            }
          } catch {}
        }
      } catch {
        setStatus("disconnected")
        reconnectTimer = window.setTimeout(connect, 1500)
      }
    }

    const scheduleCommit = () => {
      if (throttleRef.current != null) return
      throttleRef.current = window.setTimeout(() => {
        throttleRef.current = null
        // shallow copy to freeze structure for React
        setSnapshot({ ...buffersRef.current })
      }, 1000 / Math.max(1, fps))
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      if (throttleRef.current) window.clearTimeout(throttleRef.current)
      try { wsRef.current?.close() } catch {}
    }
  }, [resolvedUrl, maxPointsPerSeries, fps])

  const api = useMemo(() => ({ metricsByDevice: snapshot, devices, status, errors }), [snapshot, devices, status, errors])
  return api
}


