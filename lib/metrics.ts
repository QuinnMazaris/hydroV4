export type MetricMeta = {
  id?: string
  label?: string
  unit?: string
  color?: string
}

const PALETTE = [
  'oklch(0.7 0.15 142)',
  'oklch(0.65 0.18 220)',
  'oklch(0.75 0.12 60)',
  'oklch(0.68 0.16 300)',
  'oklch(0.72 0.14 180)',
]

const colorForId = (id: string) => {
  const sum = Array.from(id).reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  return PALETTE[sum % PALETTE.length]
}

const titleCase = (id: string) =>
  id
    .split(/[_\s]+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')

export function resolveMetricMeta(id: string, meta?: MetricMeta): Required<Pick<MetricMeta, 'label' | 'color'>> & Pick<MetricMeta, 'unit' | 'id'> {
  return {
    id,
    label: meta?.label ?? titleCase(id),
    color: meta?.color ?? colorForId(id),
    unit: meta?.unit,
  }
}

export function describeValue(value: number, unit?: string) {
  const rounded = Number.isInteger(value) ? value : Number(value.toFixed(2))
  return unit ? `${rounded} ${unit}` : `${rounded}`
}
