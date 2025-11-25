import { DeviceToggle } from "@/components/device-toggle"
import { cn } from "@/lib/utils"
import type { ActuatorState } from "@/hooks/use-actuator-queue"
import { AlertTriangle, Bot } from "lucide-react"

type ControlMode = "auto" | "manual"

export type ActuatorCard = {
  key: string
  label: string
  unit?: string
  color?: string
  currentState: ActuatorState
}

type DeviceActuatorGroup = {
  deviceId: string
  actuators: ActuatorCard[]
}

interface ActuatorDeviceGridProps {
  devices: DeviceActuatorGroup[]
  controlModes: Record<string, Record<string, ControlMode | undefined> | undefined>
  onToggle: (deviceId: string, actuator: ActuatorCard) => void
  /** When true, user can control actuators even in AUTO mode (emergency override) */
  forceOverride?: boolean
}

/**
 * Mode meanings:
 * - AUTO: Normal operation - AI and automation control this actuator
 * - MANUAL: Emergency override - User has taken manual control, AI/automation blocked
 */
export function ActuatorDeviceGrid({ devices, controlModes, onToggle, forceOverride = false }: ActuatorDeviceGridProps) {
  if (devices.length === 0) {
    return null
  }

  return (
    <section className="mb-12 space-y-6">
      {devices.map(({ deviceId, actuators }) => (
        <div key={deviceId} className="space-y-3">
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{deviceId}</span>
            <span>
              {actuators.length} actuator{actuators.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="grid gap-6 grid-cols-2 md:grid-cols-4 lg:grid-cols-6">
            {actuators.map((actuator) => {
              const busyKey = `${deviceId}:${actuator.key}`
              const isOn = actuator.currentState === "on"
              const label = actuator.label || actuator.key

              const mode = controlModes[deviceId]?.[actuator.key] || "manual"
              const isAutoMode = mode === "auto"
              // In AUTO mode, user is blocked unless forceOverride is enabled
              const isUserBlocked = isAutoMode && !forceOverride

              return (
                <div key={busyKey} className="relative group">
                  <div className={cn(isUserBlocked && "opacity-50 pointer-events-none")}>
                    <DeviceToggle
                      id={busyKey}
                      label={label}
                      checked={isOn}
                      onToggle={() => onToggle(deviceId, actuator)}
                    />
                  </div>

                  {/* Mode badge */}
                  {isAutoMode ? (
                    <div 
                      className="absolute top-2 left-2 px-2 py-1 bg-blue-500 rounded text-xs font-bold text-white"
                      title="AI + Automation control (normal operation)"
                    >
                      <span className="flex items-center gap-1">
                        <Bot className="h-3.5 w-3.5" aria-hidden />
                        AI
                      </span>
                    </div>
                  ) : (
                    <div 
                      className="absolute top-2 left-2 px-2 py-1 bg-orange-500 rounded text-xs font-bold text-white"
                      title="Manual override - AI/automation blocked"
                    >
                      <span className="flex items-center gap-1">
                        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                        MANUAL
                      </span>
                    </div>
                  )}
                  
                  {/* Blocked indicator for AUTO mode */}
                  {isUserBlocked && (
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                      <span className="bg-black/80 text-white text-xs px-2 py-1 rounded">
                        Switch to Manual to control
                      </span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </section>
  )
}
