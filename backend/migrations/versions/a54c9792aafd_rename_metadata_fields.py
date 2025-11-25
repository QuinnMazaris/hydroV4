"""rename_metadata_fields

Revision ID: a54c9792aafd
Revises: 20240920_normalized
Create Date: 2024-11-24 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a54c9792aafd'
down_revision = '20240920_normalized'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.alter_column('device_metadata', new_column_name='device_meta')

    with op.batch_alter_table('conversation_messages', schema=None) as batch_op:
        batch_op.alter_column('message_metadata', new_column_name='message_meta')


def downgrade() -> None:
    with op.batch_alter_table('conversation_messages', schema=None) as batch_op:
        batch_op.alter_column('message_meta', new_column_name='message_metadata')

    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.alter_column('device_meta', new_column_name='device_metadata')
