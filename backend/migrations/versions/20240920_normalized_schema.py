"""Normalize sensor storage into metric and reading tables."""

from alembic import op
import sqlalchemy as sa

revision = '20240920_normalized'
down_revision = None
branch_labels = None
depends_on = None


def _table_names(bind):
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _column_names(bind, table_name):
    inspector = sa.inspect(bind)
    return {col['name'] for col in inspector.get_columns(table_name)}


def _index_names(bind, table_name):
    inspector = sa.inspect(bind)
    return {idx['name'] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    tables = _table_names(bind)

    if 'sensor_readings' in tables:
        op.drop_table('sensor_readings')
    if 'actuator_states' in tables:
        op.drop_table('actuator_states')

    if 'devices' in tables:
        columns = _column_names(bind, 'devices')
        if 'device_id' in columns and 'device_key' not in columns:
            op.alter_column('devices', 'device_id', new_column_name='device_key')

        indexes = _index_names(bind, 'devices')
        if 'ix_devices_device_id' in indexes:
            op.drop_index('ix_devices_device_id', table_name='devices')
        if 'devices_device_id_key' in indexes:
            # Some SQLite variants create this implicit name
            op.drop_index('devices_device_id_key', table_name='devices')
        if 'ix_devices_device_key' not in indexes:
            op.create_index('ix_devices_device_key', 'devices', ['device_key'], unique=True)
    else:
        op.create_table(
            'devices',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('device_key', sa.String(length=100), nullable=False, unique=True, index=True),
            sa.Column('name', sa.String(length=200), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('last_seen', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('device_metadata', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        )

    tables = _table_names(bind)

    if 'metrics' not in tables:
        op.create_table(
            'metrics',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('device_id', sa.Integer(), nullable=False),
            sa.Column('metric_key', sa.String(length=100), nullable=False),
            sa.Column('display_name', sa.String(length=200), nullable=True),
            sa.Column('unit', sa.String(length=50), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('device_id', 'metric_key', name='uq_metric_device_key'),
        )
        op.create_index('ix_metrics_device_id', 'metrics', ['device_id'])

    if 'readings' not in tables:
        op.create_table(
            'readings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('metric_id', sa.Integer(), nullable=False),
            sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('value', sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(['metric_id'], ['metrics.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_readings_metric_id', 'readings', ['metric_id'])
        op.create_index('ix_readings_metric_ts', 'readings', ['metric_id', 'timestamp'])


def downgrade():
    bind = op.get_bind()
    tables = _table_names(bind)

    if 'readings' in tables:
        op.drop_index('ix_readings_metric_ts', table_name='readings')
        op.drop_index('ix_readings_metric_id', table_name='readings')
        op.drop_table('readings')

    tables = _table_names(bind)
    if 'metrics' in tables:
        op.drop_index('ix_metrics_device_id', table_name='metrics')
        op.drop_table('metrics')

    tables = _table_names(bind)
    if 'devices' in tables:
        indexes = _index_names(bind, 'devices')
        if 'ix_devices_device_key' in indexes:
            op.drop_index('ix_devices_device_key', table_name='devices')
        columns = _column_names(bind, 'devices')
        if 'device_key' in columns and 'device_id' not in columns:
            op.alter_column('devices', 'device_key', new_column_name='device_id')
        if 'ix_devices_device_id' not in indexes:
            op.create_index('ix_devices_device_id', 'devices', ['device_id'], unique=True)

    op.create_table(
        'sensor_readings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.String(length=100), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('pressure', sa.Float(), nullable=True),
        sa.Column('humidity', sa.Float(), nullable=True),
        sa.Column('gas_kohms', sa.Float(), nullable=True),
        sa.Column('lux', sa.Float(), nullable=True),
        sa.Column('water_temp_c', sa.Float(), nullable=True),
        sa.Column('tds_ppm', sa.Float(), nullable=True),
        sa.Column('ph', sa.Float(), nullable=True),
        sa.Column('distance_mm', sa.Float(), nullable=True),
        sa.Column('vpd_kpa', sa.Float(), nullable=True),
        sa.Column('raw_data', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id']),
    )

    op.create_index('ix_sensor_readings_timestamp', 'sensor_readings', ['timestamp'])
    op.create_index('ix_sensor_readings_device_id', 'sensor_readings', ['device_id'])

    op.create_table(
        'actuator_states',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.String(length=100), nullable=True),
        sa.Column('actuator_type', sa.String(length=50), nullable=True),
        sa.Column('actuator_number', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(length=10), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('command_source', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id']),
    )

    op.create_index('ix_actuator_states_timestamp', 'actuator_states', ['timestamp'])
