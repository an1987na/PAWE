# Database migrations

Run migrations only against an explicitly configured PAWE database:

```bash
uv run alembic upgrade head
```

The initial migration creates versioned weekly decisions and daily brief tables. Published decisions and briefs are immutable at the application layer; corrections create new versions.

