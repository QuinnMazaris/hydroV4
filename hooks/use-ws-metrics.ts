"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  createCommitScheduler,
  manageWebSocketConnection,
  processReading,
  processSnapshot,
} from "./use-ws-metrics-helpers"

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

export type IncomingEvent =
  | { type: "snapshot"; devices: DevicesMap; latest: Record<string, DeviceLatest>; history?: Record<string, Record<string, MetricPoint[]>>; ts: number }
  | { type: "device"; device_id: string; is_active?: boolean; last_seen?: number; sensors?: Record<string, MetricMeta>; actuators?: Record<string, MetricMeta> }
  | { type: "reading"; device_id: string; timestamp: number; sensors?: Record<string, number>; actuators?: Record<string, any> }
  | { type: "error"; code?: string; message: string; context?: Record<string, unknown>; ts?: number }

export type SnapshotEvent = Extract<IncomingEvent, { type: "snapshot" }>
export type ReadingEvent = Extract<IncomingEvent, { type: "reading" }>

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
    const { schedule: scheduleCommit, cancel: cancelCommit } = createCommitScheduler({
      throttleRef,
      fps,
      sensorBuffersRef,
      actuatorBuffersRef,
      setSensorSnapshot,
      setActuatorSnapshot,
    })

    const handleEvent = (ev: IncomingEvent) => {
      if (ev.type === "snapshot") {
        processSnapshot(ev, {
          sensorBuffersRef,
          actuatorBuffersRef,
          setDevices,
          maxPointsPerSeries,
        })
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
          const next = [
            ...prev,
            {
              code: ev.code,
              message: ev.message,
              context: ev.context,
              ts: ev.ts ?? Date.now(),
            },
          ]
          return next.slice(-20)
        })
      } else if (ev.type === "reading") {
        processReading(ev, {
          sensorBuffersRef,
          actuatorBuffersRef,
          maxPointsPerSeries,
        })
        scheduleCommit()
      }
    }

    const cleanup = manageWebSocketConnection({
      url: resolvedUrl,
      wsRef,
      onEvent: handleEvent,
      onOpen: () => setStatus("live"),
      onClose: () => setStatus("disconnected"),
      onError: () => setStatus("disconnected"),
    })

    return () => {
      cancelCommit()
      cleanup()
    }
  }, [resolvedUrl, maxPointsPerSeries, fps])

  const api = useMemo(
    () => ({ sensorsByDevice: sensorSnapshot, actuatorsByDevice: actuatorSnapshot, devices, status, errors }),
    [sensorSnapshot, actuatorSnapshot, devices, status, errors],
  )
  return api
}
