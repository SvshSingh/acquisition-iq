"""serve from postgres: markets, filed facts, row level security

Revision ID: a41c7d9e2b10
Revises: e82be52836d2
Create Date: 2026-10-02 12:00:00.000000

The initial schema was designed while the API still served a JSON snapshot, and
it shows: the `companies` table was missing every column the collector had
learned to fill since (the facts filed with the licensing board, the provenance
of an inferred website), and the dataset's own metadata had nowhere to live.
This revision closes that gap so the table can hold everything the snapshot
does, which is the precondition for serving from it.

It also turns on row level security for every table, and that part is not
optional on Supabase. Supabase publishes the `public` schema through an
auto-generated REST API, reachable with the project's anon key. A table there
without RLS is readable and writable by anyone holding that key. Enabling RLS
with no policies denies the API roles everything, while the backend is
unaffected because it connects as the table owner, which bypasses RLS. On a
plain Postgres the statements are harmless for the same reason.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a41c7d9e2b10"
down_revision: Union[str, Sequence[str], None] = "e82be52836d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every table the application owns, plus Alembic's own bookkeeping table, which
# lives in `public` too and would otherwise be the one table left exposed.
_RLS_TABLES = (
    "markets",
    "companies",
    "contacts",
    "scores",
    "raw_payloads",
    "http_cache",
    "alembic_version",
)


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    op.add_column("companies", sa.Column("market_key", sa.String(length=64), nullable=True))
    op.add_column("companies", sa.Column("website_source", sa.String(length=64), nullable=True))
    op.add_column("companies", sa.Column("website_evidence", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("business_type", sa.String(length=64), nullable=True))
    op.add_column("companies", sa.Column("has_employees", sa.Boolean(), nullable=True))
    op.add_column("companies", sa.Column("licence_number", sa.String(length=32), nullable=True))
    op.add_column("companies", sa.Column("licence_issued", sa.Date(), nullable=True))
    op.add_column(
        "companies",
        sa.Column(
            "licence_classifications",
            postgresql.JSONB(astext_type=sa.Text()),
            # Existing rows need a value for the NOT NULL to hold; new rows get
            # theirs from the ORM default, so the server default is dropped again.
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("companies", "licence_classifications", server_default=None)
    op.add_column("companies", sa.Column("sibling_location_count", sa.Integer(), nullable=True))

    op.create_index(op.f("ix_companies_market_key"), "companies", ["market_key"], unique=False)
    op.create_index(op.f("ix_companies_business_type"), "companies", ["business_type"], unique=False)
    op.create_index(op.f("ix_companies_licence_number"), "companies", ["licence_number"], unique=False)
    op.create_index(op.f("ix_companies_founded_year"), "companies", ["founded_year"], unique=False)
    op.create_foreign_key(
        op.f("companies_market_key_fkey"),
        "companies",
        "markets",
        ["market_key"],
        ["key"],
        ondelete="SET NULL",
    )

    for table in _RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    op.drop_constraint(op.f("companies_market_key_fkey"), "companies", type_="foreignkey")
    op.drop_index(op.f("ix_companies_founded_year"), table_name="companies")
    op.drop_index(op.f("ix_companies_licence_number"), table_name="companies")
    op.drop_index(op.f("ix_companies_business_type"), table_name="companies")
    op.drop_index(op.f("ix_companies_market_key"), table_name="companies")

    op.drop_column("companies", "sibling_location_count")
    op.drop_column("companies", "licence_classifications")
    op.drop_column("companies", "licence_issued")
    op.drop_column("companies", "licence_number")
    op.drop_column("companies", "has_employees")
    op.drop_column("companies", "business_type")
    op.drop_column("companies", "website_evidence")
    op.drop_column("companies", "website_source")
    op.drop_column("companies", "market_key")

    op.drop_table("markets")
