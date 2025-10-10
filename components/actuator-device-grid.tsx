import { DeviceToggle } from "@/components/device-toggle"
import { cn } from "@/lib/utils"
import type { ActuatorState } from "@/hooks/use-actuator-queue"

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
}

export function ActuatorDeviceGrid({ devices, controlModes, onToggle }: ActuatorDeviceGridProps) {
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

              return (
                <div key={busyKey} className="relative">
                  <div className={cn(isAutoMode && "opacity-60")}>
                    <DeviceToggle
                      id={busyKey}
                      label={label}
                      checked={isOn}
                      onToggle={() => onToggle(deviceId, actuator)}
                    />
                  </div>

                  {isAutoMode && (
                    <div className="absolute top-2 left-2 px-2 py-1 bg-blue-500 rounded text-xs font-bold text-white">
                      AUTO
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
