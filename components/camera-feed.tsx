"use client"

import { useEffect, useRef, useState } from "react"
import Hls from "hls.js"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Camera, Activity } from "lucide-react"

interface CameraFeedProps {
  deviceKey?: string
  showControls?: boolean
}

export function CameraFeed({ deviceKey = "camera_1", showControls = true }: CameraFeedProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const hlsRef = useRef<Hls | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isLive, setIsLive] = useState(false)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const apiPort = process.env.NEXT_PUBLIC_API_PORT || "8000"
    const streamUrl = `http://${window.location.hostname}:${apiPort}/api/camera/stream/stream.m3u8`

    if (Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        backBufferLength: 0,  // Disable back buffer for live streaming
        maxBufferLength: 2,   // Keep only 2 seconds of buffer
        maxMaxBufferLength: 3,
        liveSyncDurationCount: 1,
        liveMaxLatencyDurationCount: 2,
      })

      hlsRef.current = hls

      hls.loadSource(streamUrl)
      hls.attachMedia(video)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setIsLoading(false)
        setIsLive(true)
        video.play().catch((e) => {
          console.error("Failed to autoplay:", e)
        })
      })

      hls.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          setError("Stream connection failed")
          setIsLive(false)

          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.error("Network error, trying to recover...")
              hls.startLoad()
              break
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.error("Media error, trying to recover...")
              hls.recoverMediaError()
              break
            default:
              console.error("Fatal error, cannot recover")
              hls.destroy()
              break
          }
        }
      })

      return () => {
        hls.destroy()
        hlsRef.current = null
      }
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Native HLS support (Safari)
      video.src = streamUrl
      video.addEventListener("loadedmetadata", () => {
        setIsLoading(false)
        setIsLive(true)
        video.play()
      })

      video.addEventListener("error", () => {
        setError("Stream playback failed")
        setIsLive(false)
      })
    } else {
      setError("HLS not supported in this browser")
    }
  }, [deviceKey])

  return (
    <Card className="relative overflow-hidden border-white/10 backdrop-blur-xl shadow-xl bg-black/50">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2">
          <Camera className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base font-medium text-foreground">Camera Feed</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="relative aspect-video bg-black">
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <Activity className="h-8 w-8 animate-spin text-primary" />
                <p className="text-sm text-muted-foreground">Connecting to camera...</p>
              </div>
            </div>
          )}

          {error && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-center px-4">
                <Camera className="h-8 w-8 text-destructive" />
                <p className="text-sm text-destructive">{error}</p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => window.location.reload()}
                >
                  Retry
                </Button>
              </div>
            </div>
          )}

          <video
            ref={videoRef}
            className="w-full h-full object-contain"
            autoPlay
            muted
            playsInline
          />
        </div>

      </CardContent>
    </Card>
  )
}