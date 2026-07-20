from __future__ import annotations

import csv
import json

from monotone_calibrate.cli import main


def test_cli_run_verify_and_predict_form_one_complete_local_workflow(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    for name in tuple(__import__("os").environ):
        if name.startswith("MONOTONE_CALIBRATE_LLM_"):
            monkeypatch.delenv(name, raising=False)
    source = tmp_path / "curve.csv"
    source.write_text(
        "x,y,row_id\n"
        + "\n".join(f"{value},{1.0 + 0.4 * value},p-{value}" for value in range(12))
        + "\nmissing,3,bad-row\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "bundle"

    assert main(
        [
            "run",
            str(source),
            "--output",
            str(bundle),
            "--no-dotenv",
            "--validation-repetitions",
            "1",
            "--bootstrap-resamples",
            "0",
        ]
    ) == 0
    run_output = json.loads(capsys.readouterr().out)
    assert run_output["status"] == "ok"
    assert run_output["output_dir"] == str(bundle)
    assert run_output["recommendation"] == "P1"
    assert "INVALID_ROWS_SKIPPED" in run_output["warnings"]
    assert (bundle / "report.html").is_file()
    assert (bundle / "recommended-model.json").is_file()

    assert main(["verify", str(bundle)]) == 0
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["status"] == "VERIFIED"

    prediction_input = tmp_path / "prediction-input.csv"
    prediction_input.write_text("row_id,x\ninside,5\noutside,99\nbad,nope\n", encoding="utf-8")
    prediction_output = tmp_path / "predictions.csv"
    assert main(
        [
            "predict",
            str(bundle / "recommended-model.json"),
            str(prediction_input),
            "--output",
            str(prediction_output),
        ]
    ) == 0
    predict_output = json.loads(capsys.readouterr().out)
    assert predict_output["status"] == "WRITTEN"
    with prediction_output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["status"] for row in rows] == ["OK", "OUT_OF_DOMAIN", "INVALID_X"]


def test_cli_returns_typed_json_error_without_a_partial_output(tmp_path, capsys) -> None:
    source = tmp_path / "not-ready.csv"
    source.write_text("x,y\n0,1\n1,2\n", encoding="utf-8")
    output = tmp_path / "bundle"

    assert main(["run", str(source), "--output", str(output), "--no-dotenv"]) == 1

    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error["status"] == "error"
    assert error["code"] == "FIT_NOT_READY"
    assert not output.exists()
