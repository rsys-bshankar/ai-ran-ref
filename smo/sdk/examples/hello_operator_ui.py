#!/usr/bin/env python3
"""The smallest rApp package that declares an operator page (GUI-8.8): a CSAR with a manifest whose `operatorUi` is written with
`smo_sdk.operator_ui`, and no code of its own to run. It exists to show the authoring flow and is what the SDK test builds and validates;
it is not one of the sample rApps (`samples/`) and is not onboarded by the demo.

    PYTHONPATH=sdk:shared python sdk/examples/hello_operator_ui.py [output-dir]     # writes hello-operator-ui.csar

The rApp it describes would serve, under its operator API base, `GET /instances/{id}/status` (an object with `state`, `mode`, `processed`,
`lastRunAt`), `GET /instances/{id}/runs` (`{"items": [{"runId", "startedAt", "result", "changed"}]}`), `GET /instances/{id}/runs/{runId}/steps` (the drawer of a run) and `POST /instances/{id}/run`.
"""

import io
import sys
import zipfile
from pathlib import Path

from smo_sdk import operator_ui as ui

NAME = "hello-operator-ui"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)    # a rebuild of the same sources is byte-identical, as `samples/build_csar.py` does


def page() -> dict:
    """The declaration: a status block, the KPI the platform reports for the instance, a history table and one button."""
    return ui.declaration(
        ui.key_values("status", "Status", ui.source("/instances/{instanceId}/status", refresh_seconds=15), [
            ui.item("State", "state", "badge"), ui.item("Mode", "mode", "badge"),
            ui.item("Processed", "processed", "number"), ui.item("Last run", "lastRunAt", "datetime"),
        ]),
        ui.kpis("kpis", "Platform KPIs", [ui.tile("Config success", kpi="config_success_rate", format="percent")]),
        ui.table("runs", "Recent runs", ui.source("/instances/{instanceId}/runs", query={"limit": 20}), rows="items", row_key="runId", columns=[
            ui.column("startedAt", "Started", "datetime"), ui.column("result", "Result", "badge"), ui.column("changed", "Changed", "number"),
        ], empty="No runs yet.", row_detail=ui.row_detail(
            ui.json_block("Run", empty="No detail."),
            ui.table_block("Steps", [ui.column("name", "Step"), ui.column("result", "Result", "badge")],
                           src=ui.source("/instances/{instanceId}/runs/{row.runId}/steps"), rows="items", empty="No steps."),
            title="Run {row.runId}")),
        ui.actions("controls", "Controls", [
            ui.action("run", "Run now", "POST", "/instances/{instanceId}/run", success="Run started", tone="primary",
                      confirm="Start a run now?", inputs=[ui.input_field("dryRun", "Dry run", "boolean")]),
        ]),
    )


def package_files() -> dict[str, str]:
    """The files of the CSAR, by path."""
    manifest = ('rappManifest:\n  manifestVersion: "1.0"\n  aiRuntimeSdkVersion: "1.0"\n\nname: HelloOperatorUi_rApp\nversion: 1.0.0\n'
                'description: Declares an operator page and does nothing else.\n')
    return {
        "TOSCA-Metadata/TOSCA.meta": "TOSCA-Meta-File-Version: 1.0\nCSAR-Version: 1.1\nEntry-Definitions: Definitions/asd.yaml\n",
        "Definitions/asd.yaml": ("tosca_definitions_version: tosca_simple_yaml_1_3\napplicationServiceDescriptor:\n  properties:\n"
                                 "    application_name: HelloOperatorUi_rApp\n    application_version: \"1.0.0\"\n    provider: Example\n"
                                 "    descriptor_id: 6f0c6f3a-6a53-4a50-9d0e-0a1b2c3d4e5f\n    descriptor_invariant_id: 3b1d8c0e-7b2f-4a8e-9a53-5c1f0e2d9a77\n"
                                 "    descriptor_version: \"1.0.0\"\n    schema_version: \"2.0\"\n"),
        "manifest.yaml": manifest + "\n" + ui.to_yaml(page()),
    }


def build_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in sorted(package_files().items()):
            info = zipfile.ZipInfo(path, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".") / f"{NAME}.csar"
    out.write_bytes(build_bytes())
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
