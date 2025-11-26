"use client"

import { cn } from "@/lib/utils"
import { Card } from "@/components/ui/card"

interface DeviceToggleProps {
  id: string
  label: string
  checked: boolean
  disabled?: boolean
  onToggle?: () => void
}

export function DeviceToggle({ id, label, checked, disabled = false, onToggle }: DeviceToggleProps) {
  const handleClick = (e: React.MouseEvent) => {
    if (disabled) return
    e.preventDefault()
    e.stopPropagation()
    onToggle?.()
  }

  const PlantAnimation = ({ isActive, id }: { isActive: boolean, id: string }) => (
    <div className="relative w-full h-full flex items-center justify-center pointer-events-none">
      {isActive ? (
        <svg viewBox="0 0 24 24" className="w-full h-full pointer-events-none" xmlns="http://www.w3.org/2000/svg">
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
        <svg viewBox="0 0 24 24" className="w-full h-full text-emerald-300/70 pointer-events-none" fill="currentColor">
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
        "w-full aspect-square p-0 overflow-hidden cursor-pointer backdrop-blur-xl shadow-xl select-none active:scale-95 transition-transform duration-100",
        disabled && "cursor-not-allowed opacity-50",
        checked
          ? "bg-emerald-900/40 border-emerald-500/20"
          : "bg-black/50 border-white/5"
      )}
      onClick={handleClick}
      style={{ touchAction: "manipulation" }}
    >
      <div className="flex h-full flex-col">
        <div className="flex-1 min-h-0 p-2 flex items-center justify-center">
          <div className="w-full h-full flex items-center justify-center">
            <PlantAnimation isActive={checked} id={id} />
          </div>
        </div>
        <div className="px-2 pb-2">
          <h3 className="text-xs font-semibold text-foreground truncate text-center leading-tight">
            {label}
          </h3>
        </div>
      </div>
    </Card>
  )
}
