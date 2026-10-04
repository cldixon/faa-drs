"""Generate the document type catalog from the FAA metadata mapping CSV.

Outputs:
    src/faa_drs/catalog.json
    src/faa_drs/_doctype.py
    docs/reference/doctypes.md

Usage:
    uv run scripts/generate_catalog.py          # write
    uv run scripts/generate_catalog.py --check  # fail if outputs are stale
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "spec" / "doctypes.csv"
CATALOG = ROOT / "src" / "faa_drs" / "catalog.json"
ENUM = ROOT / "src" / "faa_drs" / "_doctype.py"
DOCS = ROOT / "docs" / "reference" / "doctypes.md"

SERVICES = {
    "AGC": "Office of the Chief Counsel",
    "AIR": "Aircraft Certification Service",
    "ANG": "NextGen",
    "AOV": "Air Traffic Safety Oversight Service",
    "ARM": "Office of Rulemaking",
    "AST": "Commercial Space Transportation",
    "FS": "Flight Standards Service",
    "OTHER": "Other",
}

# Probed 2026-10-04 with an external API key.
INTERNAL_ONLY = {
    "AFS-1_MEMORANDUMS", "AFS_FFS_UPDATES", "AFS_POLICY_DEV_MEMOS", "AIR_QMS_PROCESSES",
    "AOV_JOB_AIDS", "AOV_SAFETY_OVERSIGHT_CIRCULAR", "AOV_WORK_INSTRUCTIONS", "AT_JTA",
    "GA_JTA", "ICAO_ANNEX", "ICAO_DOCUMENT", "ICAO_OTHER_PUBLICATIONS", "ICAO_PANS",
    "JOB_TASKS", "OTHER_AWO", "OTHER_CPD_6.03", "OTHER_FLIGHT_STANDARDS_ORM_WORKSHEETS",
    "OTHER_LASER_INVESTIGATION_REFERENCES", "OTHER_PROCEDURES_MANUAL",
    "OTHER_PS_LEGAL_INTERPRETATIONS", "OTHER_PS_POLICY_MEMORANDA", "OTHER_PS_PREAMBLES",
    "OTHER_QMS_AND_BP", "OTHER_RIRTP", "OTHER_SPRS", "OTHER_VPM",
}  # fmt: skip

# Fields that carry full document text inline.
CONTENT_FIELDS = {
    "ADFRAWD": ["drs:adfrawdSummary", "drs:adfrawdSupplementaryInfo", "drs:adfrawdRegulatoryText"],
    "ADNPRM": ["drs:adnprmSummary", "drs:adnprmSupplementaryInfo", "drs:adnprmRegulatoryText"],
    "CFRFRSFAR": [
        "drs:cfrfrsfarSummary", "drs:cfrfrsfarSupplementaryInfo", "drs:cfrfrsfarRegulatoryText",
    ],
    "FAR": ["drs:farSectionRule"],
    "NPRM": ["drs:nprmSummary", "drs:nprmSupplementaryInfo", "drs:nprmRegulatoryText"],
    "SCFINAL": ["drs:scfinalSummary", "drs:scfinalSupplementaryInfo", "drs:scfinalSCInfoText"],
    "SCPROPOSED": [
        "drs:scproposedSummary", "drs:scproposedSupplementaryInfo", "drs:scproposedSCInfoText",
    ],
    "SFAR": ["drs:sfarRegulatoryText"],
}  # fmt: skip

# Live types that differ from the CSV, or are blank in it.
TYPE_OVERRIDES = {
    ("AIRWORTHINESS_CONCERN_SHEETS", "faagov:acsMake"): "ARRAY",
    ("CIVIL_PENALTY_APPEALS", "faagov:cpaPrimarySubject"): "ARRAY",
    ("CIVIL_PENALTY_APPEALS", "faagov:cpaSecondarySubject"): "ARRAY",
    ("CIVIL_PENALTY_APPEALS", "faagov:cpaTertiarySubject"): "ARRAY",
    ("NORSEE", "faagov:norseeEquipmentArticleName"): "ARRAY",
    ("NORSEE", "faagov:norseeModelPartNumber"): "ARRAY",
    ("NORSEE", "faagov:norseeDescription"): "ARRAY",
    ("PMA", "drs:documentOwner"): "ARRAY",
    ("TCDSMODEL", "drs:tcdsmodelModel"): "ARRAY",
    ("TCDSMODEL", "drs:tcdsmodelUnofficialName"): "ARRAY",
    ("TCDSMODEL", "drs:tcdsmodelModelComments"): "ARRAY",
    ("TSOI", "drs:tsoiPartName"): "ARRAY",
    ("TSOI", "drs:tsoiPartNumber"): "ARRAY",
    ("FAR", "drs:partNumberDescription"): "ARRAY",
    ("FAR", "drs:subPart"): "ARRAY",
    ("FAR", "drs:sectionNumber"): "ARRAY",
    ("FAR", "drs:farAmendmentNumber"): "ARRAY",
    ("FAR", "drs:documentOwner"): "ARRAY",
    ("FAR", "drs:primaryRespOffice"): "ARRAY",
    ("FAR", "drs:effectiveDate"): "DATE",
}

# Fields returned by the API but missing from the CSV.
EXTRA_FIELDS = {
    code: [("fsims:docLevel2", "Document Level 2", "TEXT")]
    for code in ("ORDER_8300.10", "ORDER_8400.10", "ORDER_8700.1", "ORDER_8740.1")
}

ENUM_PREFIX_FOR_DIGIT = "ORDER_"


def member_name(code: str) -> str:
    name = re.sub(r"[^A-Z0-9]+", "_", code.upper()).strip("_")
    return ENUM_PREFIX_FOR_DIGIT + name if name[0].isdigit() else name


def infer_type(code: str, key: str, raw: str) -> str:
    if (code, key) in TYPE_OVERRIDES:
        return TYPE_OVERRIDES[(code, key)]
    if raw:
        return raw.upper()
    return "DATE" if re.search(r"date$", key, re.IGNORECASE) else "TEXT"


def load() -> list[dict]:
    with SOURCE.open(encoding="utf-8-sig", newline="") as fh:
        rows = [
            {k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)
        ]
    grouped: dict[str, dict] = {}
    fields: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        code = row["Document Type Name in API request"]
        key = row["Metadata Name in API Response"]
        info = grouped.setdefault(
            code,
            {
                "code": code,
                "name": row["Document Type name in DRS"],
                "service": row["Service"],
                "sort_field": None,
                "internal_only": code in INTERNAL_ONLY,
            },
        )
        if row["Deafult  Sort By"].upper() == "TRUE":
            info["sort_field"] = key
        fields[code][key] = {
            "key": key,
            "label": row["Metadata Name in DRS"],
            "type": infer_type(code, key, row["Metadata Value Data Type"]),
        }
    for code, extras in EXTRA_FIELDS.items():
        for key, label, kind in extras:
            fields[code].setdefault(key, {"key": key, "label": label, "type": kind})
    for code, keys in CONTENT_FIELDS.items():
        missing = [k for k in keys if k not in fields[code]]
        if missing:
            raise SystemExit(f"Content fields missing from {code}: {missing}")
    result = []
    for code in sorted(grouped):
        info = grouped[code]
        info["content_fields"] = CONTENT_FIELDS.get(code, [])
        info["fields"] = list(fields[code].values())
        result.append(info)
    names = [member_name(d["code"]) for d in result]
    if len(set(names)) != len(names):
        raise SystemExit("DocType member names collide.")
    return result


def render_catalog(doctypes: list[dict]) -> str:
    return json.dumps({"doctypes": doctypes}, indent=1, ensure_ascii=False) + "\n"


def render_enum(doctypes: list[dict]) -> str:
    lines = [
        "# Generated by scripts/generate_catalog.py. Do not edit.",
        "from enum import StrEnum",
        "",
        "",
        "class DocType(StrEnum):",
        '    """DRS document type codes. Members are strings and work anywhere a code does."""',
        "",
    ]
    for d in doctypes:
        note = " Internal only." if d["internal_only"] else ""
        doc = (d["name"] + "." + note).replace("\\", "\\\\").replace('"', '\\"')
        lines += [f'    {member_name(d["code"])} = "{d["code"]}"', f'    """{doc}"""', ""]
    return "\n".join(lines).rstrip() + "\n"


def render_docs(doctypes: list[dict]) -> str:
    public = [d for d in doctypes if not d["internal_only"]]
    internal = [d for d in doctypes if d["internal_only"]]
    out = [
        "# Document types",
        "",
        "<!-- Generated by scripts/generate_catalog.py. Do not edit. -->",
        "",
        f"DRS has {len(doctypes)} document types. External API keys can read "
        f"{len(public)}. The other {len(internal)} are internal only.",
        "",
        "Use the `DocType` member or the code string. Get the fields of a type with "
        "`catalog.get_doctype(code).fields`.",
        "",
        "**Text** shows that the API returns the full document text in the metadata.",
        "",
    ]
    for service in sorted({d["service"] for d in doctypes}):
        rows = [d for d in public if d["service"] == service]
        if not rows:
            continue
        out += [
            f"## {service}: {SERVICES.get(service, service)}",
            "",
            "| Member | Code | Name | Text |",
            "| --- | --- | --- | --- |",
        ]
        out += [
            f"| `{member_name(d['code'])}` | `{d['code']}` | {d['name']} | "
            f"{'Yes' if d['content_fields'] else ''} |"
            for d in rows
        ]
        out.append("")
    out += ["## Internal only", "", "| Member | Code | Name |", "| --- | --- | --- |"]
    out += [f"| `{member_name(d['code'])}` | `{d['code']}` | {d['name']} |" for d in internal]
    return "\n".join(out) + "\n"


def format_python(text: str) -> str:
    result = subprocess.run(
        ["ruff", "format", "--stdin-filename", str(ENUM), "-"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return result.stdout


def main() -> int:
    check = "--check" in sys.argv
    doctypes = load()
    outputs = {
        CATALOG: render_catalog(doctypes),
        ENUM: format_python(render_enum(doctypes)),
        DOCS: render_docs(doctypes),
    }
    stale = [p for p, text in outputs.items() if not p.exists() or p.read_text() != text]
    if check:
        for path in stale:
            print(f"Stale: {path.relative_to(ROOT)}")
        return 1 if stale else 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text := outputs[path])
        print(f"Wrote {path.relative_to(ROOT)} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
