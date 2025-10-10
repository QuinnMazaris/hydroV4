import { Dispatch, MutableRefObject, SetStateAction } from "react"
import type {
  DevicesMap,
  MetricPoint,
  SnapshotEvent,
  ReadingEvent,
  IncomingEvent,
} from "./use-ws-metrics"

export type CommitSchedulerOptions = {
  throttleRef: MutableRefObject<number | null>
  fps: number
  sensorBuffersRef: MutableRefObject<Record<string, Record<string, MetricPoint[]>>>
  actuatorBuffersRef: MutableRefObject<Record<string, Record<string, MetricPoint[]>>>
  setSensorSnapshot: Dispatch<SetStateAction<Record<string, Record<string, MetricPoint[]>>>>
  setActuatorSnapshot: Dispatch<SetStateAction<Record<string, Record<string, MetricPoint[]>>>>
}

export function createCommitScheduler({
  throttleRef,
  fps,
  sensorBuffersRef,
  actuatorBuffersRef,
  setSensorSnapshot,
  setActuatorSnapshot,
}: CommitSchedulerOptions) {
  const schedule = () => {
    if (throttleRef.current != null) return
    throttleRef.current = window.setTimeout(() => {
      throttleRef.current = null
      setSensorSnapshot({ ...sensorBuffersRef.current })
      setActuatorSnapshot({ ...actuatorBuffersRef.current })
    }, 1000 / Math.max(1, fps))
  }

  const cancel = () => {
    if (throttleRef.current) {
      window.clearTimeout(throttleRef.current)
      throttleRef.current = null
    }
  }

  return { schedule, cancel }
}

export type SnapshotContext = {
  sensorBuffersRef: MutableRefObject<Record<string, Record<string, MetricPoint[]>>>
  actuatorBuffersRef: MutableRefObject<Record<string, Record<string, MetricPoint[]>>>
  setDevices: Dispatch<SetStateAction<DevicesMap>>
  maxPointsPerSeries: number
}

export function processSnapshot(event: SnapshotEvent, context: SnapshotContext) {
  const { sensorBuffersRef, actuatorBuffersRef, setDevices, maxPointsPerSeries } = context

  setDevices((prev) => {
    const next = { ...prev }
    for (const [deviceId, info] of Object.entries(event.devices || {})) {
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

  if (event.history) {
    for (const [deviceId, metrics] of Object.entries(event.history)) {
      const deviceInfo = event.devices?.[deviceId]
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
          } else if (!arr.some((x) => x.timestamp === p.timestamp)) {
            arr.push({ timestamp: p.timestamp, value: p.value })
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

  for (const [deviceId, latest] of Object.entries(event.latest || {})) {
    const deviceInfo = event.devices?.[deviceId]
    const sensorSeries = (sensorBuffers[deviceId] ||= {})
    const actuatorSeries = (actuatorBuffers[deviceId] ||= {})
    const ts = latest.timestamp

    for (const [metricName, rawValue] of Object.entries(latest.metrics)) {
      const metricData = rawValue && typeof rawValue === "object" ? (rawValue as Record<string, unknown>) : null
      const value = metricData && "value" in metricData ? (metricData.value as unknown) : rawValue
      const valueTimestamp = metricData && typeof metricData.timestamp === "number" ? metricData.timestamp : ts
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
}

export type ReadingContext = {
  sensorBuffersRef: MutableRefObject<Record<string, Record<string, MetricPoint[]>>>
  actuatorBuffersRef: MutableRefObject<Record<string, Record<string, MetricPoint[]>>>
  maxPointsPerSeries: number
}

export function processReading(event: ReadingEvent, context: ReadingContext) {
  const { sensorBuffersRef, actuatorBuffersRef, maxPointsPerSeries } = context
  const sensorSeries = (sensorBuffersRef.current[event.device_id] ||= {})
  const actuatorSeries = (actuatorBuffersRef.current[event.device_id] ||= {})
  const ts = event.timestamp

  if (event.sensors) {
    for (const [sensorName, value] of Object.entries(event.sensors)) {
      const arr = (sensorSeries[sensorName] ||= [])
      const last = arr[arr.length - 1]
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

  if (event.actuators) {
    for (const [actuatorName, value] of Object.entries(event.actuators)) {
      const arr = (actuatorSeries[actuatorName] ||= [])
      const last = arr[arr.length - 1]
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
}

export type WebSocketManagerOptions = {
  url: string
  wsRef: MutableRefObject<WebSocket | null>
  onEvent: (event: IncomingEvent) => void
  onOpen?: (ev: Event) => void
  onClose?: (ev: CloseEvent) => void
  onError?: (ev: Event) => void
  reconnectDelay?: number
}

export function manageWebSocketConnection({
  url,
  wsRef,
  onEvent,
  onOpen,
  onClose,
  onError,
  reconnectDelay = 1500,
}: WebSocketManagerOptions) {
  let closed = false
  let reconnectTimer: number | null = null

  const connect = () => {
    if (closed) return
    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = (event) => {
        onOpen?.(event)
      }

      ws.onclose = (event) => {
        onClose?.(event)
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, reconnectDelay)
        }
      }

      ws.onerror = (event) => {
        onError?.(event)
        try {
          ws.close()
        } catch {}
      }

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as IncomingEvent
          onEvent(data)
        } catch {}
      }
    } catch (err) {
      onError?.(err instanceof Event ? err : new Event("error"))
      reconnectTimer = window.setTimeout(connect, reconnectDelay)
    }
  }

  connect()

  const cleanup = () => {
    closed = true
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer)
    }
    try {
      wsRef.current?.close()
    } catch {}
  }

  return cleanup
}
