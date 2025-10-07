-- Migration: Add control mode to metrics table for automation support
-- Date: 2025-10-07
-- Description: Adds control_mode column to distinguish manual vs auto control

-- Add control_mode column to metrics table (per-actuator storage)
ALTER TABLE metrics
ADD COLUMN control_mode VARCHAR(20) DEFAULT 'manual'
CHECK (control_mode IN ('manual', 'auto'));

-- Add comment for documentation
COMMENT ON COLUMN metrics.control_mode IS 'Control mode for actuators: manual (user controls) or auto (automation controls). NULL for sensors.';

-- Create index for faster mode queries
CREATE INDEX idx_metrics_control_mode ON metrics(control_mode) WHERE metric_type = 'actuator';
