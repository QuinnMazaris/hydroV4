import { useEffect, useState } from "react"

export interface Camera {
  device_key: string
  name: string
  ready: boolean
  source_ready: boolean
  tracks: string[]
  readers: number
  whep_url: string
  online?: boolean
}

export function useCameras() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchCameras = async () => {
      try {
        setIsLoading(true)
        const response = await fetch("http://localhost:8001/api/camera/list")

        if (!response.ok) {
          throw new Error(`Failed to fetch cameras: ${response.statusText}`)
        }

        const data = await response.json()
        setCameras(data.cameras || [])
        setError(null)
      } catch (err) {
        console.error("Error fetching cameras:", err)
        setError(err instanceof Error ? err.message : "Failed to load cameras")
        setCameras([])
      } finally {
        setIsLoading(false)
      }
    }

    fetchCameras()

    // Refresh camera list every 30 seconds
    const interval = setInterval(fetchCameras, 30000)

    return () => clearInterval(interval)
  }, [])

  return { cameras, isLoading, error }
}
