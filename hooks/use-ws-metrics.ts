"use client"

import { useEffect, useMemo, useRef, useState } from "react"

export type MetricPoint = { timestamp: number; value: any }

export type DeviceLatest = {
  timestamp: number
  metrics: Record<string, unknown>
}

export type MetricMeta = { label?: string; unit?: string; color?: string }

export type DataError = {
  code?: string
  message: string
  context?: Record<string, unknown>
  ts: number
}

export type DeviceInfo = {
  is_active?: boolean
  last_seen?: number | null
  sensors?: Record<string, MetricMeta>
  actuators?: Record<string, MetricMeta>
}

export type DevicesMap = Record<string, DeviceInfo>

type IncomingEvent =
  | { type: "snapshot"; devices: DevicesMap; latest: Record<string, DeviceLatest>; history?: Record<string, Record<string, MetricPoint[]>>; ts: number }
  | { type: "device"; device_id: string; is_active?: boolean; last_seen?: number; sensors?: Record<string, MetricMeta>; actuators?: Record<string, MetricMeta> }
  | { type: "reading"; device_id: string; timestamp: number; sensors?: Record<string, number>; actuators?: Record<string, any> }
  | { type: "error"; code?: string; message: string; context?: Record<string, unknown>; ts?: number }

export function useWsSensors(options?: {
  url?: string
  maxPointsPerSeries?: number
  fps?: number
}) {
  const apiPort = process.env.NEXT_PUBLIC_API_PORT || "8000"
  const defaultUrl = typeof window !== "undefined" ? `ws://${window.location.hostname}:${apiPort}/ws/sensors` : `ws://localhost:${apiPort}/ws/sensors`
  const { url, maxPointsPerSeries = 2000, fps = 10 } = options || {}
  const resolvedUrl = url || defaultUrl

  const sensorBuffersRef = useRef<Record<string, Record<string, MetricPoint[]>>>({})
  const actuatorBuffersRef = useRef<Record<string, Record<string, MetricPoint[]>>>({})
  const [devices, setDevices] = useState<DevicesMap>({})
  const [sensorSnapshot, setSensorSnapshot] = useState<Record<string, Record<string, MetricPoint[]>>>({})
  const [actuatorSnapshot, setActuatorSnapshot] = useState<Record<string, Record<string, MetricPoint[]>>>({})
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
                    sensors: { ...(current.sensors || {}), ...(info.sensors || {}) },
                    actuators: info.actuators ?? current.actuators,
                    last_seen: info.last_seen ?? current.last_seen ?? null,
                  }
                }
                return next
              })
              const sensorBuffers = sensorBuffersRef.current
              const actuatorBuffers = actuatorBuffersRef.current

              // 1) Hydrate history (last 24h) first
              if (ev.history) {
                for (const [deviceId, metrics] of Object.entries(ev.history)) {
                  const deviceInfo = ev.devices?.[deviceId]
                  const sensorSeries = (sensorBuffers[deviceId] ||= {})
                  const actuatorSeries = (actuatorBuffers[deviceId] ||= {})

                  const resolveSeries = (metricName: string) => {
                    if (actuatorSeries[metricName]) return actuatorSeries[metricName]
                    if (sensorSeries[metricName]) return sensorSeries[metricName]
                    if (deviceInfo?.actuators && metricName in deviceInfo.actuators) {
                      actuatorSeries[metricName] = []
                      return actuatorSeries[metricName]
                    }
                    sensorSeries[metricName] = []
                    return sensorSeries[metricName]
                  }

                  for (const [metricName, points] of Object.entries(metrics)) {
                    const arr = resolveSeries(metricName)
                    for (const p of points) {
                      const last = arr[arr.length - 1]
                      if (!last || p.timestamp > last.timestamp) {
                        arr.push({ timestamp: p.timestamp, value: p.value })
                      } else if (p.timestamp === last.timestamp) {
                        last.value = p.value
                      } else {
                        // out-of-order safeguard: append if not duplicate
                        if (!arr.some((x) => x.timestamp === p.timestamp)) arr.push({ timestamp: p.timestamp, value: p.value })
                      }
                    }
                    if (arr.length > maxPointsPerSeries) {
                      const trimmed = arr.slice(-maxPointsPerSeries)
                      if (actuatorSeries[metricName] === arr) {
                        actuatorSeries[metricName] = trimmed
                      } else {
                        sensorSeries[metricName] = trimmed
                      }
                    }
                  }
                }
              }

              // 2) Then apply single latest point per device (skip if duplicate timestamp)
              for (const [deviceId, latest] of Object.entries(ev.latest || {})) {
                const deviceInfo = ev.devices?.[deviceId]
                const sensorSeries = (sensorBuffers[deviceId] ||= {})
                const actuatorSeries = (actuatorBuffers[deviceId] ||= {})
                const ts = latest.timestamp
                for (const [metricName, rawValue] of Object.entries(latest.metrics)) {
                  const metricData = (rawValue && typeof rawValue === 'object') ? (rawValue as Record<string, unknown>) : null
                  const value = metricData && 'value' in metricData ? (metricData.value as unknown) : rawValue
                  const valueTimestamp = metricData && typeof metricData.timestamp === 'number' ? metricData.timestamp : ts
                  const isActuator = !!(deviceInfo?.actuators && metricName in deviceInfo.actuators) || !!actuatorSeries[metricName]
                  const seriesMap = isActuator ? actuatorSeries : sensorSeries
                  const arr = (seriesMap[metricName] ||= [])
                  const last = arr[arr.length - 1]
                  if (!last || valueTimestamp > last.timestamp) {
                    arr.push({ timestamp: valueTimestamp, value })
                  } else if (last.timestamp === valueTimestamp) {
                    last.value = value
                  }
                  if (arr.length > maxPointsPerSeries) {
                    seriesMap[metricName] = arr.slice(-maxPointsPerSeries)
                  }
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
                    is_active: ev.is_active ?? current.is_active,
                    last_seen: ev.last_seen ?? current.last_seen ?? null,
                    sensors: { ...(current.sensors || {}), ...(ev.sensors || {}) },
                    actuators: { ...(current.actuators || {}), ...(ev.actuators || {}) },
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
              const sensorSeries = (sensorBuffersRef.current[ev.device_id] ||= {})
              const actuatorSeries = (actuatorBuffersRef.current[ev.device_id] ||= {})
              const ts = ev.timestamp

              // Process sensor readings
              if (ev.sensors) {
                for (const [sensorName, value] of Object.entries(ev.sensors)) {
                  const arr = (sensorSeries[sensorName] ||= [])
                  const last = arr[arr.length - 1]
                  // If same timestamp as last point, update in place to avoid shape changes
                  if (last && last.timestamp === ts) {
                    last.value = value
                  } else {
                    arr.push({ timestamp: ts, value })
                  }
                  if (arr.length > maxPointsPerSeries) {
                    sensorSeries[sensorName] = arr.slice(-maxPointsPerSeries)
                  }
                }
              }

              // Process actuator readings
              if (ev.actuators) {
                for (const [actuatorName, value] of Object.entries(ev.actuators)) {
                  const arr = (actuatorSeries[actuatorName] ||= [])
                  const last = arr[arr.length - 1]
                  // If same timestamp as last point, update in place to avoid shape changes
                  if (last && last.timestamp === ts) {
                    last.value = value
                  } else {
                    arr.push({ timestamp: ts, value })
                  }
                  if (arr.length > maxPointsPerSeries) {
                    actuatorSeries[actuatorName] = arr.slice(-maxPointsPerSeries)
                  }
                }
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
        setSensorSnapshot({ ...sensorBuffersRef.current })
        setActuatorSnapshot({ ...actuatorBuffersRef.current })
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

  const api = useMemo(
    () => ({ sensorsByDevice: sensorSnapshot, actuatorsByDevice: actuatorSnapshot, devices, status, errors }),
    [sensorSnapshot, actuatorSnapshot, devices, status, errors],
  )
  return api
}
