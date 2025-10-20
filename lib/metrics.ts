import metricConfig from '@/config/metric-metadata.json'

export type MetricMeta = {
  id?: string
  label?: string
  unit?: string
  color?: string
}

const PALETTE = metricConfig.colorPalette

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
