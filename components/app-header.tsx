"use client"

import { useEffect, useState } from "react"
import { usePathname } from "next/navigation"
import { Activity, AlertTriangle, Bot, Zap, ChevronDown, Trash2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type ControlMode = "auto" | "manual"

interface AppHeaderProps {
  /** Connection status for dashboard */
  connectionStatus?: "live" | "connecting" | "disconnected"
  /** Callback to clear chat (only shown on chat page when messages exist) */
  onClearChat?: () => void
  /** Whether there are messages to clear */
  hasMessages?: boolean
}

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: Activity },
  { href: "/chat", label: "Chat", icon: Bot },
  { href: "/automation", label: "Automation", icon: Zap },
]

export function AppHeader({ connectionStatus, onClearChat, hasMessages }: AppHeaderProps) {
  const pathname = usePathname()
  const [globalMode, setGlobalMode] = useState<ControlMode>("auto")
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [isChangingMode, setIsChangingMode] = useState(false)

  // Fetch current mode on mount
  useEffect(() => {
    fetch("/api/actuators/modes")
      .then((res) => res.json())
      .then((data: { modes?: Record<string, Record<string, ControlMode>> }) => {
        const modes = data.modes || {}
        const allModes = Object.values(modes).flatMap((device) => Object.values(device))
        if (allModes.length > 0) {
          const allAuto = allModes.every((mode) => mode === "auto")
          setGlobalMode(allAuto ? "auto" : "manual")
        }
      })
      .catch((err) => console.error("Failed to fetch control modes:", err))
  }, [])

  // Close dropdown on Escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isDropdownOpen) {
        setIsDropdownOpen(false)
      }
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [isDropdownOpen])

  const handleModeChange = async (newMode: ControlMode) => {
    if (newMode === globalMode) {
      setIsDropdownOpen(false)
      return
    }

    setIsChangingMode(true)
    try {
      const res = await fetch(`/api/actuators/mode/global?mode=${newMode}`, {
        method: "POST",
      })

      if (res.ok) {
        setGlobalMode(newMode)
        // Dispatch event so other components can react to mode change
        window.dispatchEvent(new CustomEvent("actuator-mode-changed", { detail: { mode: newMode } }))
      } else {
        throw new Error("Failed to change mode")
      }
    } catch (error) {
      console.error("Error changing mode:", error)
    } finally {
      setIsChangingMode(false)
      setIsDropdownOpen(false)
    }
  }

  const currentPage = NAV_ITEMS.find((item) => item.href === pathname)

  return (
    <header className="border-b border-white/10 bg-black/30 backdrop-blur-md sticky top-0 z-50">
      <div className="px-4 md:px-6 lg:px-8 py-3">
        <div className="flex items-center justify-between">
          {/* Left: Logo and Status */}
          <div className="flex items-center gap-3">
            <a href="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
                <Activity className="h-5 w-5 text-primary-foreground" />
              </div>
              <span className="text-lg font-semibold text-foreground hidden sm:inline">HydroV4</span>
            </a>
            {connectionStatus && (
              <Badge
                variant={connectionStatus === "live" ? "secondary" : connectionStatus === "connecting" ? "secondary" : "destructive"}
                className="text-xs"
              >
                {connectionStatus === "live" ? "● Live" : connectionStatus === "connecting" ? "Connecting..." : "Disconnected"}
              </Badge>
            )}
          </div>

          {/* Center: Navigation */}
          <nav className="flex items-center gap-1 bg-white/5 rounded-lg p-1">
            {NAV_ITEMS.map((item) => {
              const isActive = pathname === item.href
              const Icon = item.icon
              return (
                <a
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-white/10"
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span className="hidden sm:inline">{item.label}</span>
                </a>
              )
            })}
          </nav>

          {/* Right: Controls */}
          <div className="flex items-center gap-2">
            {/* Clear Chat button (only on chat page with messages) */}
            {pathname === "/chat" && hasMessages && onClearChat && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onClearChat}
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-4 w-4 sm:mr-1" />
                <span className="hidden sm:inline">Clear</span>
              </Button>
            )}

            {/* Separator */}
            {pathname === "/chat" && hasMessages && onClearChat && (
              <div className="w-px h-6 bg-white/20" />
            )}

            {/* Mode Toggle Dropdown */}
            <div className="relative">
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                disabled={isChangingMode}
                className={cn(
                  "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all",
                  globalMode === "auto"
                    ? "bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 border border-blue-500/30"
                    : "bg-orange-500/20 text-orange-300 hover:bg-orange-500/30 border border-orange-500/30",
                  isChangingMode && "opacity-50 cursor-not-allowed"
                )}
              >
                {globalMode === "auto" ? (
                  <Bot className="h-4 w-4" aria-hidden />
                ) : (
                  <AlertTriangle className="h-4 w-4" aria-hidden />
                )}
                <span className="hidden sm:inline">
                  {globalMode === "auto" ? "AI Control" : "Manual"}
                </span>
                <ChevronDown className={cn("h-4 w-4 transition-transform", isDropdownOpen && "rotate-180")} />
              </button>

              {isDropdownOpen && (
                <>
                  {/* Backdrop */}
                  <div
                    className="fixed inset-0 z-40"
                    onClick={() => setIsDropdownOpen(false)}
                  />
                  {/* Dropdown */}
                  <div className="absolute right-0 mt-2 w-64 bg-black/90 backdrop-blur-xl border border-white/10 rounded-lg shadow-xl z-50 overflow-hidden">
                    <div className="p-2 space-y-1">
                      <button
                        onClick={() => handleModeChange("auto")}
                        className={cn(
                          "w-full flex items-start gap-3 p-3 rounded-lg text-left transition-all",
                          globalMode === "auto"
                            ? "bg-blue-500/20 border border-blue-500/30"
                            : "hover:bg-white/5"
                        )}
                      >
                        <Bot className="h-5 w-5 text-blue-200" aria-hidden />
                        <div>
                          <div className="font-medium text-foreground">AI Control</div>
                          <div className="text-xs text-muted-foreground">
                            Normal operation. AI + automation control actuators.
                          </div>
                        </div>
                        {globalMode === "auto" && (
                          <span className="ml-auto text-blue-400">✓</span>
                        )}
                      </button>

                      <button
                        onClick={() => handleModeChange("manual")}
                        className={cn(
                          "w-full flex items-start gap-3 p-3 rounded-lg text-left transition-all",
                          globalMode === "manual"
                            ? "bg-orange-500/20 border border-orange-500/30"
                            : "hover:bg-white/5"
                        )}
                      >
                        <AlertTriangle className="h-5 w-5 text-orange-200" aria-hidden />
                        <div>
                          <div className="font-medium text-foreground">Manual Override</div>
                          <div className="text-xs text-muted-foreground">
                            Emergency mode. Only you can control actuators.
                          </div>
                        </div>
                        {globalMode === "manual" && (
                          <span className="ml-auto text-orange-400">✓</span>
                        )}
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}
