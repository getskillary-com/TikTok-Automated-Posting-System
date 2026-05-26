from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from shared.database import init_database, session, utc_now

from .models import PRODUCT_STATUSES


def new_product_id() -> str:
    return "PRD-" + uuid.uuid4().hex[:12].upper()


class ProductRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path
        init_database(db_path)

    def add_product(
        self,
        *,
        title: str = "",
        search_title: str = "",
        publish_name: str = "",
        product_link: str = "",
        account_scope: str = "",
        note: str = "",
        status: str = "active",
    ) -> str:
        validate_product_status(status)
        clean_search_title = search_title.strip()
        clean_publish_name = publish_name.strip()
        clean_title = title.strip() or clean_search_title or clean_publish_name
        if not clean_title:
            raise ValueError("Product title or search title is required.")
        if not clean_search_title:
            clean_search_title = clean_title
        if not clean_publish_name:
            clean_publish_name = clean_title

        now = utc_now()
        product_id = new_product_id()
        with session(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO products(
                    product_id, title, search_title, publish_name, product_link,
                    account_scope, status, note, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    clean_title,
                    clean_search_title,
                    clean_publish_name,
                    product_link.strip(),
                    account_scope.strip(),
                    status,
                    note,
                    now,
                    now,
                ),
            )
        return product_id

    def get(self, product_id: str) -> dict[str, Any] | None:
        with session(self.db_path) as connection:
            row = connection.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()
        return row_to_dict(row)

    def list_products(
        self,
        *,
        status: str = "",
        account_scope: str = "",
        search: str = "",
        limit: int = 100,
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        elif not include_removed:
            clauses.append("status != 'removed'")
        if account_scope:
            clauses.append("account_scope = ?")
            params.append(account_scope)
        if search:
            clauses.append(
                "(product_id LIKE ? OR title LIKE ? OR search_title LIKE ? OR publish_name LIKE ? OR product_link LIKE ? OR note LIKE ?)"
            )
            params.extend([f"%{search}%"] * 6)

        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        with session(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT * FROM products{where_sql} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def update_product(
        self,
        product_id: str,
        *,
        title: str | None = None,
        search_title: str | None = None,
        publish_name: str | None = None,
        product_link: str | None = None,
        account_scope: str | None = None,
        note: str | None = None,
    ) -> None:
        fields: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("title", title),
            ("search_title", search_title),
            ("publish_name", publish_name),
            ("product_link", product_link),
            ("account_scope", account_scope),
            ("note", note),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value.strip())
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(utc_now())
        params.append(product_id)
        with session(self.db_path) as connection:
            result = connection.execute(f"UPDATE products SET {', '.join(fields)} WHERE product_id = ?", params)
            if result.rowcount == 0:
                raise KeyError(f"Product not found: {product_id}")

    def update_status(self, product_id: str, status: str) -> None:
        validate_product_status(status)
        with session(self.db_path) as connection:
            result = connection.execute(
                "UPDATE products SET status = ?, updated_at = ? WHERE product_id = ?",
                (status, utc_now(), product_id),
            )
            if result.rowcount == 0:
                raise KeyError(f"Product not found: {product_id}")

    def remove_product(self, product_id: str) -> None:
        self.update_status(product_id, "removed")


def validate_product_status(value: str) -> None:
    if value not in PRODUCT_STATUSES:
        raise ValueError(f"Unsupported product status: {value}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
