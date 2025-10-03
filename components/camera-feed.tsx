"use client"

import { useEffect, useRef, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Camera, Activity, PlayCircle } from "lucide-react"

interface CameraFeedProps {
  deviceKey?: string
}

export function CameraFeed({ deviceKey = "camera_1" }: CameraFeedProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const isUnmountedRef = useRef(false)
  const [isConnecting, setIsConnecting] = useState(false)
  const [isLive, setIsLive] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connectWebRTC = async () => {
    // Don't reconnect if component unmounted
    if (isUnmountedRef.current) return

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
        ],
        // Safari-specific optimizations
        bundlePolicy: 'max-bundle',
        rtcpMuxPolicy: 'require'
      })

      pcRef.current = pc

      // Handle incoming tracks (video/audio)
      pc.ontrack = (event) => {
        if (isUnmountedRef.current) return
        
        if (videoRef.current && event.streams[0]) {
          videoRef.current.srcObject = event.streams[0]
          // Force play immediately - critical for Safari
          videoRef.current.play().catch(err => console.warn('Play failed:', err))
          setIsLive(true)
          setIsConnecting(false)
          reconnectAttemptsRef.current = 0 // Reset on success
        }
      }

      // Monitor ICE connection state (critical for Safari)
      pc.oniceconnectionstatechange = () => {
        console.log('ICE connection state:', pc.iceConnectionState)
        
        if (isUnmountedRef.current) return

        if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
          console.warn('ICE connection lost, attempting reconnect...')
          setIsLive(false)
          scheduleReconnect()
        } else if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
          setIsLive(true)
          setIsConnecting(false)
          setError(null)
          reconnectAttemptsRef.current = 0
        }
      }

      // Monitor connection state
      pc.onconnectionstatechange = () => {
        console.log('WebRTC connection state:', pc.connectionState)
        
        if (isUnmountedRef.current) return

        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setIsLive(false)
          scheduleReconnect()
        } else if (pc.connectionState === 'connected') {
          setIsLive(true)
          setError(null)
        }
      }

      // Add transceivers for receiving video and audio
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.addTransceiver('audio', { direction: 'recvonly' })

      // Create offer
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)

      // Send offer to MediaMTX WHEP endpoint
      const mediamtxPort = process.env.NEXT_PUBLIC_MEDIAMTX_WEBRTC_PORT || '8889'
      const whepUrl = `http://${window.location.hostname}:${mediamtxPort}/${deviceKey}/whep`

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
      if (isUnmountedRef.current) return
      
      setError(err instanceof Error ? err.message : 'Failed to connect to camera')
      setIsConnecting(false)
      setIsLive(false)
      scheduleReconnect()
    }
  }

  const scheduleReconnect = () => {
    // Clear any existing reconnect timeout
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
    }

    // Exponential backoff: 2s, 4s, 8s, max 10s
    reconnectAttemptsRef.current++
    const delay = Math.min(2000 * Math.pow(2, reconnectAttemptsRef.current - 1), 10000)

    console.log(`Scheduling reconnect attempt ${reconnectAttemptsRef.current} in ${delay}ms`)
    
    reconnectTimeoutRef.current = setTimeout(() => {
      if (!isUnmountedRef.current) {
        connectWebRTC()
      }
    }, delay)
  }

  const disconnect = () => {
    // Clear reconnect timeout
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }

    if (pcRef.current) {
      pcRef.current.close()
      pcRef.current = null
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null
    }
    setIsLive(false)
  }

  // Auto-connect on mount and handle page visibility for Safari
  useEffect(() => {
    isUnmountedRef.current = false
    connectWebRTC()

    // Handle page visibility changes (critical for mobile Safari)
    const handleVisibilityChange = () => {
      if (document.hidden) {
        // Page went to background - Safari may pause the connection
        console.log('Page hidden, pausing video')
        if (videoRef.current) {
          videoRef.current.pause()
        }
      } else {
        // Page came back to foreground
        console.log('Page visible, resuming video')
        if (videoRef.current && videoRef.current.srcObject) {
          videoRef.current.play().catch(err => console.warn('Resume play failed:', err))
        }
        // Check connection state and reconnect if needed
        if (pcRef.current) {
          const state = pcRef.current.iceConnectionState
          if (state === 'disconnected' || state === 'failed' || state === 'closed') {
            console.log('Connection lost while hidden, reconnecting...')
            connectWebRTC()
          }
        } else {
          // No connection exists, reconnect
          connectWebRTC()
        }
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      isUnmountedRef.current = true
      document.removeEventListener('visibilitychange', handleVisibilityChange)
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

      </CardContent>
    </Card>
  )
}
