import { useEffect, useMemo, useRef, useState } from "react"

export interface Camera {
  device_key: string
  name: string
  ready: boolean
  source_ready?: boolean
  tracks: string[]
  readers: number
  whep_url: string
  is_active: boolean
  online: boolean
  last_seen?: string
}

interface DeviceApiResponse {
  device_key: string
  name?: string | null
  description?: string | null
  device_metadata?: string | null
  device_type: string
  last_seen?: string
  is_active: boolean
}

interface CameraMetadata {
  ready?: boolean
  source_ready?: boolean
  tracks?: unknown
  readers?: unknown
  whep_url?: string
  last_sync?: string
}

const API_URL = "/api/devices?device_type=camera&active_only=false"

const parseMetadata = (raw: string | null | undefined): CameraMetadata => {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return typeof parsed === "object" && parsed !== null ? parsed as CameraMetadata : {}
  } catch {
    return {}
  }
}

const coerceTracks = (tracks: unknown): string[] => {
  if (Array.isArray(tracks)) {
    return tracks.filter((value) => typeof value === "string")
  }
  return []
}

export function useCameras() {
  const [rawDevices, setRawDevices] = useState<DeviceApiResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const hasLoadedRef = useRef(false)

  useEffect(() => {
    let ignore = false

    const fetchCameras = async () => {
      try {
        if (!hasLoadedRef.current) {
          setIsLoading(true)
        }
        const response = await fetch(API_URL)

        if (!response.ok) {
          throw new Error(`Failed to fetch cameras: ${response.statusText}`)
        }

        const data = await response.json()
        const devices = Array.isArray(data)
          ? data as DeviceApiResponse[]
          : Array.isArray((data as { devices?: unknown })?.devices)
            ? (data as { devices: DeviceApiResponse[] }).devices
            : []
        if (!ignore) {
          setRawDevices(devices)
          setError(null)
          hasLoadedRef.current = true
        }
      } catch (err) {
        console.error("Error fetching cameras:", err)
        if (!ignore) {
          setError(err instanceof Error ? err.message : "Failed to load cameras")
          setRawDevices([])
        }
      } finally {
        if (!ignore) {
          setIsLoading(false)
        }
      }
    }

    fetchCameras()

    const interval = setInterval(fetchCameras, 30000)

    return () => {
      ignore = true
      clearInterval(interval)
    }
  }, [])

  const cameras = useMemo<Camera[]>(() => {
    if (rawDevices.length === 0) {
      return []
    }

    const hostname = typeof window !== "undefined" ? window.location.hostname : "localhost"
    const mediamtxPort = process.env.NEXT_PUBLIC_MEDIAMTX_WEBRTC_PORT || "8889"

    return rawDevices.map((device) => {
      const metadata = parseMetadata(device.device_metadata ?? null)
      const ready = Boolean(metadata.ready)
      const sourceReady = metadata.source_ready === undefined ? undefined : Boolean(metadata.source_ready)
      const tracks = coerceTracks(metadata.tracks)
      const readers = typeof metadata.readers === "number" ? metadata.readers : 0
      const whepUrl = metadata.whep_url && metadata.whep_url.startsWith("http")
        ? metadata.whep_url
        : `http://${hostname}:${mediamtxPort}/${device.device_key}/whep`

      return {
        device_key: device.device_key,
        name: device.name || device.device_key,
        ready,
        source_ready: sourceReady,
        tracks,
        readers,
        whep_url: whepUrl,
        is_active: device.is_active,
        online: device.is_active,
        last_seen: device.last_seen,
      }
    })
  }, [rawDevices])

  return { cameras, isLoading, error }
}
