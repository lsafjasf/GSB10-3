#!/usr/bin/env python3
"""Run the four required scenarios and export window traces to data/.

Usage: python3 run_scenarios.py [--out DIR]
"""

import argparse
import os

from cwnd_sim import (
    ConstantRtt,
    ScriptLoss,
    StepRtt,
    simulate,
    samples_to_csv,
    samples_to_table,
    write_csv,
)

FLIGHTS = 24


def scenarios():
    return {
        "no_loss": dict(
            loss_model=None,
            rtt_profile=ConstantRtt(100.0),
        ),
        "single_loss": dict(
            loss_model=ScriptLoss({8: [0]}),
            rtt_profile=ConstantRtt(100.0),
        ),
        "consecutive_loss": dict(
            loss_model=ScriptLoss({8: [0], 9: [0], 10: [0, 1]}),
            rtt_profile=ConstantRtt(100.0),
        ),
        "rtt_spike": dict(
            loss_model=None,
            rtt_profile=StepRtt([(0, 100.0), (8, 400.0), (16, 100.0)]),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    for name, kwargs in scenarios().items():
        samples = simulate(FLIGHTS, **kwargs)
        path = os.path.join(args.out, f"{name}.csv")
        write_csv(samples, path)
        print(f"=== {name} -> {path} ===")
        print(samples_to_table(samples))


if __name__ == "__main__":
    main()
