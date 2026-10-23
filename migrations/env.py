from alembic import context

from packages.adapters.storage import metadata

with context.config.attributes["engine"].connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
