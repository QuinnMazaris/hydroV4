"use client"

import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader } from "@/components/ui/card"

interface DeviceToggleProps {
  id: string
  label: string
  checked: boolean
  loading?: boolean
  disabled?: boolean
  onToggle?: () => void
}

export function DeviceToggle({ id, label, checked, loading = false, disabled = false, onToggle }: DeviceToggleProps) {
  const handleClick = () => {
    if (disabled || loading) return
    onToggle?.()
  }

  const PlantAnimation = ({ isActive, id }: { isActive: boolean, id: string }) => (
    <div className="relative w-full h-16 sm:h-20 md:h-24 flex items-center justify-center">
      {isActive ? (
        <svg viewBox="0 0 24 24" className="w-full h-full" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id={`tulip-petal-${id}`} x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#ff7ab6"/>
              <stop offset="60%" stopColor="#f43f5e"/>
              <stop offset="100%" stopColor="#be123c"/>
            </linearGradient>
            <linearGradient id={`tulip-leaf-${id}`} x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#34d399"/>
              <stop offset="100%" stopColor="#10b981"/>
            </linearGradient>
          </defs>

          {/* Longer stem */}
          <rect x="11.2" y="6" width="1.6" height="12" rx="0.8" fill={`url(#tulip-leaf-${id})`} />

          {/* Leaves (slender, vertical) */}
          <path d="M11 11.8 C 8.8 12.2, 7.2 14.8, 6.2 17.8 C 8.9 17.2, 10.4 15.5, 11 13 Z" fill={`url(#tulip-leaf-${id})`} opacity="0.9" />
          <path d="M13 12 C 15.3 12.3, 16.7 14.4, 17.6 17.2 C 15.2 16.7, 13.9 15.3, 13 13.2 Z" fill={`url(#tulip-leaf-${id})`} opacity="0.85" />

          {/* Smooth, single-bud tulip head */}
          <path d="M12 5.6 C 10.2 5.6, 9 7.2, 9 9.3 C 9 12.3, 12 14.2, 12 14.2 C 12 14.2, 15 12.3, 15 9.3 C 15 7.2, 13.8 5.6, 12 5.6 Z" fill={`url(#tulip-petal-${id})`} />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" className="w-full h-full text-emerald-300/70" fill="currentColor">
          {/* Small seedling */}
          <rect x="11.5" y="12" width="1" height="4" />
          <ellipse cx="10.2" cy="12.5" rx="2" ry="0.9" />
          <ellipse cx="13.8" cy="13" rx="1.6" ry="0.8" />
        </svg>
      )}
    </div>
  )

  return (
    <Card
      className={cn(
        "h-24 cursor-pointer transition-all duration-300 border-white/10 backdrop-blur-xl shadow-xl",
        "hover:scale-[1.02] hover:shadow-2xl",
        loading && "cursor-wait opacity-70",
        disabled && "cursor-not-allowed opacity-50",
        checked
          ? "bg-emerald-950/30 border-emerald-400/30 shadow-emerald-400/20"
          : "bg-black/55 hover:bg-black/60"
      )}
      onClick={handleClick}
      style={{
        backgroundColor: checked ? "rgba(16, 185, 129, 0.1)" : "rgba(0, 0, 0, 0.55)",
        boxShadow: checked
          ? "0 20px 25px -5px rgba(16, 185, 129, 0.3), 0 10px 10px -5px rgba(16, 185, 129, 0.1)"
          : undefined
      }}
    >
      <CardHeader className="pb-1 px-2 pt-2">
        <div className="flex flex-col items-center gap-0.5">
          <div className="transition-all duration-300 flex items-center justify-center w-full">
            {loading ? (
              <Loader2 className="h-5 w-5 animate-spin text-emerald-300" />
            ) : (
              <PlantAnimation isActive={checked} id={id} />
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0 px-2 pb-3">
        <h3 className="mt-2 text-sm sm:text-base font-semibold text-foreground truncate text-center leading-tight">{label}</h3>
        <div className="mt-2 flex items-center">
          <div
            className={cn(
              "h-1 w-full rounded-full transition-all duration-500",
              checked ? "bg-emerald-400/30" : "bg-white/20"
            )}
          >
            <div
              className={cn(
                "h-full rounded-full transition-all duration-700 ease-out",
                checked ? "bg-emerald-400 w-full" : "bg-transparent w-0"
              )}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
