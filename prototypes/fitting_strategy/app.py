"""TUI and batch runner for the throwaway fitting-strategy prototype."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from time import perf_counter

import numpy as np
import scipy

from experiment import run_experiment
from profiled_logic import admissible_cells, cubic_grid_trap, fit_problem, make_scenarios


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
STRATEGIES = ("profile_cells", "joint_cells", "global_profiled_reference")


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "platform": platform.platform(),
        "blas_threads": os.environ.get("OPENBLAS_NUM_THREADS", "not-pinned"),
        "omp_threads": os.environ.get("OMP_NUM_THREADS", "not-pinned"),
    }


def render(state: dict) -> None:
    os.system("clear" if os.name != "nt" else "cls")
    print(f"{BOLD}PROTOTYPE — fitting strategy v2.2{RESET}")
    print(f"{DIM}Question: profile linear coefficients inside each tie-safe boundary cell?{RESET}\n")
    print(f"scenario: {state['scenario']}  ({state['index'] + 1}/{state['count']})")
    print(f"families: {' | '.join(state['families'])}  direction: {state['direction']:+d}")
    print(f"expected: {state['expected']}  eligible cells: {state['eligible_cells']}")
    if "last" in state:
        last = state["last"]
        print("\nstrategy                    status                         SSE(scaled)       c(t)  outer evals")
        print("-" * 95)
        for row in last:
            print(
                f"{row['strategy']:<27} {row['status']:<30} "
                f"{_number(row['sse']):>13} {_number(row['c']):>10} {row['outer_evaluations']:>12}"
            )
    if "gates" in state:
        print("\nrequired gates")
        for name, gate in state["gates"].items():
            if gate["required"]:
                print(f"  {'PASS' if gate['pass'] else 'FAIL':<4}  {name}")
        print(f"\nverdict: {state['decision']}")
    if "certificate" in state:
        print("\ncertificate trap: " + json.dumps(state["certificate"], ensure_ascii=False))
    print(
        "\n"
        f"{BOLD}[n/p]{RESET} scenario  {BOLD}[1/2/3]{RESET} strategy  {BOLD}[a]{RESET} compare  "
        f"{BOLD}[g]{RESET} all gates  {BOLD}[c]{RESET} certificate  {BOLD}[q]{RESET} quit"
    )


def _number(value) -> str:
    return "—" if value is None else f"{value:.6g}"


def _run_rows(problem, strategies) -> list[dict]:
    rows = []
    for strategy in strategies:
        started = perf_counter()
        trace = fit_problem(
            problem,
            strategy,
            budget="normal" if strategy == "global_profiled_reference" else "small",
        )
        outer_evaluations = sum(attempt.outer_evaluations for attempt in trace.attempts)
        rows.append({
            "strategy": strategy,
            "status": trace.status,
            "sse": None if trace.best is None else trace.best.sse_scaled,
            "c": None if trace.best is None else trace.best.c_t,
            "outer_evaluations": outer_evaluations,
            "wall_ms_diagnostic": (perf_counter() - started) * 1000.0,
            "trace": trace.public(),
        })
    return rows


def interactive() -> None:
    scenarios = make_scenarios()
    index = 0
    state: dict = {}

    def select(new_index: int) -> None:
        nonlocal index, state
        index = new_index % len(scenarios)
        problem = scenarios[index]
        cells = admissible_cells(problem)
        state = {
            "scenario": problem.name,
            "index": index,
            "count": len(scenarios),
            "families": problem.families,
            "direction": problem.direction,
            "expected": problem.expected_state,
            "eligible_cells": len(cells),
        }

    select(0)
    while True:
        render(state)
        choice = input("> ").strip().lower()
        if choice == "q":
            return
        if choice == "n":
            select(index + 1)
        elif choice == "p":
            select(index - 1)
        elif choice in {"1", "2", "3"}:
            state["last"] = _run_rows(scenarios[index], [STRATEGIES[int(choice) - 1]])
        elif choice == "a":
            state["last"] = _run_rows(scenarios[index], STRATEGIES)
        elif choice == "g":
            result = run_experiment()
            state["gates"] = result["gates"]
            state["decision"] = result["decision"]
        elif choice == "c":
            state["certificate"] = cubic_grid_trap()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch",
        action="store_true",
        help="run machine gates; wall-clock is reported as diagnostic only",
    )
    args = parser.parse_args()
    if args.batch:
        result = run_experiment()
        result["environment"] = environment()
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=_json_default)
        print()
    else:
        interactive()


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


if __name__ == "__main__":
    main()
