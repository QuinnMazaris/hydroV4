import { useCallback, useEffect, useRef, useState } from "react"

type OptimisticEntry = {
  state: "on" | "off"
  queuedAt: number
}

type PendingActuatorRequest = {
  deviceId: string
  actuatorKey: string
  optimisticKey: string
  nextState: "on" | "off"
}

type ActuatorSeries = Array<{ value: unknown }>

type ActuatorMap = Record<string, Record<string, ActuatorSeries | undefined> | undefined>

export type ActuatorState = "on" | "off" | "unknown"

export const normalizeActuatorState = (value: unknown): ActuatorState => {
  if (typeof value === "boolean") return value ? "on" : "off"
  if (typeof value === "string") {
    const lowered = value.toLowerCase()
    if (lowered === "on" || lowered === "off") return lowered
    const numeric = Number(lowered)
    if (!Number.isNaN(numeric)) {
      return numeric > 0 ? "on" : "off"
    }
  }
  if (typeof value === "number") return value > 0 ? "on" : "off"
  return "unknown"
}

interface UseActuatorQueueOptions {
  actuatorsByDevice: ActuatorMap
  flushDelayMs?: number
  optimisticTimeoutMs?: number
  /** Force override for controlling actuators in AUTO mode (emergency) */
  forceOverride?: boolean
}

export const useActuatorQueue = ({
  actuatorsByDevice,
  flushDelayMs = 100,
  optimisticTimeoutMs = 5_000,
  forceOverride = false,
}: UseActuatorQueueOptions) => {
  const [optimisticActuatorStates, setOptimisticActuatorStates] = useState<Record<string, OptimisticEntry>>({})

  const pendingActuatorsRef = useRef<Record<string, PendingActuatorRequest>>({})
  const pendingOrderRef = useRef<string[]>([])
  const flushTimerRef = useRef<number | null>(null)
  const isFlushingRef = useRef(false)

  const flushPendingActuators = useCallback(async () => {
    if (isFlushingRef.current) {
      if (Object.keys(pendingActuatorsRef.current).length > 0) {
        if (flushTimerRef.current == null) {
          flushTimerRef.current = window.setTimeout(() => {
            flushTimerRef.current = null
            void flushPendingActuators()
          }, flushDelayMs)
        }
      }
      return
    }

    const snapshotKeys = pendingOrderRef.current.slice()
    const snapshotEntries = snapshotKeys
      .map((key) => pendingActuatorsRef.current[key])
      .filter((entry): entry is PendingActuatorRequest => Boolean(entry))

    if (snapshotEntries.length === 0) {
      return
    }

    pendingActuatorsRef.current = {}
    pendingOrderRef.current = []

    isFlushingRef.current = true
    try {
      const body = {
        commands: snapshotEntries.map((entry) => ({
          device_id: entry.deviceId,
          actuator_key: entry.actuatorKey,
          state: entry.nextState,
        })),
        source: "user",
        force: forceOverride,
      }

      console.log(
        "📡 Sending batch:",
        body.commands.map((command) => `${command.actuator_key}→${command.state}`).join(", "),
        forceOverride ? "(FORCE OVERRIDE)" : ""
      )

      const res = await fetch("/api/actuators/batch-control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const message = await res.text()
        console.error("❌ API Batch Error:", { status: res.status, message })
        throw new Error(message || "Failed to send actuator commands")
      }
    } catch (error) {
      console.error("Failed to send actuator commands", error)
      setOptimisticActuatorStates((prev) => {
        const next = { ...prev }
        for (const entry of snapshotEntries) {
          const optimistic = next[entry.optimisticKey]
          if (optimistic && optimistic.state === entry.nextState) {
            delete next[entry.optimisticKey]
          }
        }
        return next
      })
      if (typeof window !== "undefined") {
        window.alert?.("Failed to send actuator command.")
      }
    } finally {
      isFlushingRef.current = false
      if (Object.keys(pendingActuatorsRef.current).length > 0) {
        if (flushTimerRef.current == null) {
          flushTimerRef.current = window.setTimeout(() => {
            flushTimerRef.current = null
            void flushPendingActuators()
          }, flushDelayMs)
        }
      }
    }
  }, [flushDelayMs])

  const scheduleFlush = useCallback(() => {
    if (flushTimerRef.current != null) return
    flushTimerRef.current = window.setTimeout(() => {
      flushTimerRef.current = null
      void flushPendingActuators()
    }, flushDelayMs)
  }, [flushDelayMs, flushPendingActuators])

  const enqueueActuatorRequest = useCallback(
    ({ deviceId, actuatorKey, nextState }: { deviceId: string; actuatorKey: string; nextState: "on" | "off" }) => {
      const optimisticKey = `${deviceId}:${actuatorKey}`
      pendingActuatorsRef.current[optimisticKey] = {
        deviceId,
        actuatorKey,
        optimisticKey,
        nextState,
      }
      if (!pendingOrderRef.current.includes(optimisticKey)) {
        pendingOrderRef.current.push(optimisticKey)
      }

      setOptimisticActuatorStates((prev) => ({
        ...prev,
        [optimisticKey]: { state: nextState, queuedAt: Date.now() },
      }))

      scheduleFlush()
    },
    [scheduleFlush]
  )

  useEffect(() => {
    setOptimisticActuatorStates((prev) => {
      if (!Object.keys(prev).length) return prev
      let updated = false
      const next = { ...prev }
      const now = Date.now()

      for (const [optimisticKey, entry] of Object.entries(prev)) {
        const [deviceId, actuatorKey] = optimisticKey.split(":")
        const series = actuatorsByDevice[deviceId]?.[actuatorKey]
        const liveValue = series?.[series.length - 1]?.value
        const liveState = normalizeActuatorState(liveValue)
        const age = now - entry.queuedAt

        if (
          liveState === entry.state ||
          age >= optimisticTimeoutMs ||
          (liveState !== entry.state && liveState !== "unknown" && age > optimisticTimeoutMs / 2)
        ) {
          delete next[optimisticKey]
          updated = true
        }
      }
      return updated ? next : prev
    })
  }, [actuatorsByDevice, optimisticTimeoutMs])

  const cleanup = useCallback(() => {
    if (flushTimerRef.current != null) {
      window.clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    pendingActuatorsRef.current = {}
    pendingOrderRef.current = []
    isFlushingRef.current = false
  }, [])

  useEffect(() => cleanup, [cleanup])

  return {
    optimisticActuatorStates,
    enqueueActuatorRequest,
    cleanup,
  }
}

export type UseActuatorQueueReturn = ReturnType<typeof useActuatorQueue>
