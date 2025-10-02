"use client"

import { useEffect, useRef, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Camera, Activity, PlayCircle } from "lucide-react"

interface CameraFeedProps {
  deviceKey?: string
  showControls?: boolean
}

export function CameraFeed({ deviceKey = "camera_1", showControls = true }: CameraFeedProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const [isConnecting, setIsConnecting] = useState(false)
  const [isLive, setIsLive] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connectWebRTC = async () => {
    try {
      setIsConnecting(true)
      setError(null)

      // Clean up existing connection
      if (pcRef.current) {
        pcRef.current.close()
        pcRef.current = null
      }

      // Create RTCPeerConnection with STUN server
      const pc = new RTCPeerConnection({
        iceServers: [
          { urls: 'stun:stun.l.google.com:19302' }
        ]
      })

      pcRef.current = pc

      // Handle incoming tracks (video/audio)
      pc.ontrack = (event) => {
        if (videoRef.current && event.streams[0]) {
          videoRef.current.srcObject = event.streams[0]
          setIsLive(true)
          setIsConnecting(false)
        }
      }

      // Monitor connection state
      pc.onconnectionstatechange = () => {
        console.log('WebRTC connection state:', pc.connectionState)
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setError('Connection lost')
          setIsLive(false)
        }
      }

      // Add transceivers for receiving video and audio
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.addTransceiver('audio', { direction: 'recvonly' })

      // Create offer
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)

      // Send offer to MediaMTX WHEP endpoint
      const whepUrl = `http://${window.location.hostname}:8889/${deviceKey}/whep`

      const response = await fetch(whepUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/sdp'
        },
        body: offer.sdp
      })

      if (!response.ok) {
        throw new Error(`WHEP request failed: ${response.status} ${response.statusText}`)
      }

      // Set remote description from answer
      const answerSdp = await response.text()
      await pc.setRemoteDescription({
        type: 'answer',
        sdp: answerSdp
      })

      console.log('WebRTC connection established')
    } catch (err) {
      console.error('WebRTC connection failed:', err)
      setError(err instanceof Error ? err.message : 'Failed to connect to camera')
      setIsConnecting(false)
      setIsLive(false)
    }
  }

  const disconnect = () => {
    if (pcRef.current) {
      pcRef.current.close()
      pcRef.current = null
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null
    }
    setIsLive(false)
  }

  // Auto-connect on mount
  useEffect(() => {
    connectWebRTC()

    return () => {
      disconnect()
    }
  }, [deviceKey])

  return (
    <Card className="relative overflow-hidden border-white/10 backdrop-blur-xl shadow-xl bg-black/50">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2">
          <Camera className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base font-medium text-foreground">Live Camera Feed</CardTitle>
        </div>
        {isLive && (
          <Badge variant="default" className="gap-1">
            <Activity className="h-3 w-3 animate-pulse" />
            Live
          </Badge>
        )}
        {isConnecting && (
          <Badge variant="secondary" className="gap-1">
            <Activity className="h-3 w-3 animate-spin" />
            Connecting
          </Badge>
        )}
      </CardHeader>
      <CardContent className="p-0">
        <div className="relative aspect-video bg-black">
          {isConnecting && (
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
                  onClick={connectWebRTC}
                >
                  <PlayCircle className="h-4 w-4 mr-2" />
                  Retry
                </Button>
              </div>
            </div>
          )}

          <video
            ref={videoRef}
            className="w-full h-full object-contain"
            autoPlay
            playsInline
            muted
          />
        </div>

        {showControls && isLive && (
          <div className="p-3 border-t border-white/10 flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={connectWebRTC}
            >
              Reconnect
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={disconnect}
            >
              Disconnect
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
