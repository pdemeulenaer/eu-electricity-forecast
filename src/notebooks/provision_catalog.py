# Databricks notebook source
"""Create an environment catalog and grant explicitly scoped catalog usage."""

import json
import re

for name, default in (("catalog", ""), ("writer", ""), ("readers_json", "[]")):
    dbutils.widgets.text(name, default)  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821
writer = dbutils.widgets.get("writer")  # noqa: F821
readers = json.loads(dbutils.widgets.get("readers_json"))  # noqa: F821
if not re.fullmatch(r"[a-z][a-z0-9_]*", catalog):
    raise ValueError("Catalog must be a lowercase SQL identifier")
if not isinstance(readers, list) or any(not isinstance(reader, str) for reader in readers):
    raise ValueError("readers_json must be a list of principal names")
principals = [writer, *readers]
if any(not re.fullmatch(r"[A-Za-z0-9@._-]+", principal) for principal in principals):
    raise ValueError("Principal names must be non-empty and contain only letters, numbers, @, dots, dashes or underscores")
if len(set(principals)) != len(principals):
    raise ValueError("Writer and reader principals must be distinct")

current_principal = spark.sql("SELECT current_user()").first()[0]  # noqa: F821
if current_principal.lower() != writer.lower():
    raise ValueError("Deploy this environment as its configured service principal; catalog ownership must stay isolated")

spark.sql(f"CREATE CATALOG IF NOT EXISTS `{catalog}`")  # noqa: F821
for principal in principals:
    spark.sql(f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{principal}`")  # noqa: F821
