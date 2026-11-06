"""Canonical SQLAlchemy metadata for future relational models and migrations."""

from hashlib import sha256
import re
from typing import Any

from sqlalchemy import Constraint, Index, MetaData, Table
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase


POSTGRESQL_IDENTIFIER_MAX_BYTES = 63
_SHORT_HASH_LENGTH = 10
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def bounded_identifier(name: str) -> str:
    """Apply the Milestone 09A deterministic PostgreSQL shortening policy."""
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError("Relational identifiers must use lowercase snake_case")
    if len(name.encode("ascii")) <= POSTGRESQL_IDENTIFIER_MAX_BYTES:
        return name

    suffix = f"__{sha256(name.encode('ascii')).hexdigest()[:_SHORT_HASH_LENGTH]}"
    prefix_length = POSTGRESQL_IDENTIFIER_MAX_BYTES - len(suffix)
    prefix = name[:prefix_length].rstrip("_")
    return f"{prefix}{suffix}"


def _column_or_purpose(item: Constraint | Index) -> str:
    purpose = item.info.get("purpose")
    if purpose is not None:
        return _validated_token(str(purpose), "constraint/index purpose")

    column_names = [column.name for column in item.columns if column.name]
    if not column_names:
        raise ValueError(
            "A constraint or index without named columns requires info['purpose']"
        )
    return "__".join(_validated_token(name, "column name") for name in column_names)


def _validated_token(value: str, description: str) -> str:
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{description} must use lowercase snake_case")
    return value


def _name(prefix: str, table: Table, *tokens: str) -> str:
    table_name = _validated_token(table.name, "table name")
    return bounded_identifier("__".join((f"{prefix}_{table_name}", *tokens)))


def _primary_key_name(_constraint: Constraint, table: Table) -> str:
    return bounded_identifier(f"pk_{_validated_token(table.name, 'table name')}")


def _foreign_key_name(constraint: Constraint, table: Table) -> str:
    columns = _column_or_purpose(constraint)
    elements: Any = getattr(constraint, "elements", ())
    if not elements:
        raise ValueError("Foreign-key naming requires a referenced table")
    referenced_table = elements[0].target_fullname.rsplit(".", 1)[0].split(".")[-1]
    return _name(
        "fk",
        table,
        columns,
        _validated_token(referenced_table, "referenced table name"),
    )


def _unique_name(constraint: Constraint, table: Table) -> str:
    return _name("uq", table, _column_or_purpose(constraint))


def _check_name(constraint: Constraint, table: Table) -> str:
    purpose = constraint.info.get("purpose")
    if purpose is None:
        raise ValueError("Check constraints require info['purpose']")
    return _name(
        "ck", table, _validated_token(str(purpose), "check-constraint purpose")
    )


def _index_name(index: Index, table: Table) -> str:
    return _name("ix", table, _column_or_purpose(index))


def _exclusion_name(constraint: Constraint, table: Table) -> str:
    purpose = constraint.info.get("purpose")
    if purpose is None:
        raise ValueError("Exclusion constraints require info['purpose']")
    return _name(
        "ex", table, _validated_token(str(purpose), "exclusion-constraint purpose")
    )


RELATIONAL_NAMING_CONVENTION: dict[str | type[Constraint], Any] = {
    "pk": "%(_pos_pk)s",
    "fk": "%(_pos_fk)s",
    "uq": "%(_pos_uq)s",
    "ck": "%(_pos_ck)s",
    "ix": "%(_pos_ix)s",
    ExcludeConstraint: "%(_pos_ex)s",
    "_pos_pk": _primary_key_name,
    "_pos_fk": _foreign_key_name,
    "_pos_uq": _unique_name,
    "_pos_ck": _check_name,
    "_pos_ix": _index_name,
    "_pos_ex": _exclusion_name,
}

RELATIONAL_METADATA = MetaData(naming_convention=RELATIONAL_NAMING_CONVENTION)


class RelationalBase(DeclarativeBase):
    """Declarative base for future infrastructure-owned relational models."""

    metadata = RELATIONAL_METADATA
