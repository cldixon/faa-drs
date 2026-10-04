from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from faa_drs import DocType, catalog
from faa_drs.catalog import FieldType

ROOT = Path(__file__).resolve().parent.parent


def test_every_doctype_is_in_the_enum() -> None:
    codes = {info.code for info in catalog.list_doctypes(include_internal=True)}
    assert codes == {member.value for member in DocType}
    assert len(codes) == 105


def test_enum_members_are_strings() -> None:
    assert DocType.AC == "AC"
    assert DocType.ORDER_8900_1 == "ORDER_8900.1"
    assert DocType.ORDER_8900_1_SUMMARY_OF_CHANGES == "8900.1_SUMMARY_OF_CHANGES"
    assert f"{DocType.AFS_1_MEMORANDUMS}" == "AFS-1_MEMORANDUMS"


def test_internal_types_are_hidden_by_default() -> None:
    public = catalog.list_doctypes()
    assert len(public) == 79
    assert not any(info.internal_only for info in public)
    assert catalog.get_doctype("ICAO_ANNEX").internal_only


def test_filter_by_service() -> None:
    air = catalog.list_doctypes(service="air")
    assert air
    assert {info.service for info in air} == {"AIR"}


def test_doctype_info() -> None:
    info = catalog.get_doctype(DocType.PMA)
    assert info.name == "Parts Manufacturer Approvals (PMA)"
    assert info.sort_field == "drs:pmaSupDate"
    assert info.fields["drs:pmaSupDate"].type is FieldType.DATE
    assert "drs:pmaSupDate" in info.date_fields
    assert not info.has_inline_content


def test_live_type_corrections_are_applied() -> None:
    assert catalog.get_doctype("TSOI").fields["drs:tsoiPartNumber"].type is FieldType.ARRAY
    assert catalog.get_doctype("FAR").fields["drs:effectiveDate"].type is FieldType.DATE
    assert "fsims:docLevel2" in catalog.get_doctype("ORDER_8300.10").fields


def test_content_fields_exist_in_fields() -> None:
    for info in catalog.list_doctypes(include_internal=True):
        assert set(info.content_fields) <= set(info.fields)
    assert catalog.get_doctype("FAR").content_fields == ("drs:farSectionRule",)


def test_unknown_doctype_suggests() -> None:
    assert catalog.find_doctype("nope") is None
    with pytest.raises(KeyError, match="Did you mean: SAIB"):
        catalog.get_doctype("saib")


def test_catalog_is_read_only() -> None:
    fields: Any = catalog.get_doctype("AC").fields
    with pytest.raises(TypeError):
        fields["x"] = fields["drs:title"]


def test_generated_files_are_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_catalog.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
