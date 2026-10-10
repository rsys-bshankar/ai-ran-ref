"""INFERENCE mode (MLIF). One joint plan for the cluster from each cell's
latest PM window; the engine supplies the moves each cell may make."""

from .CoverageModel import CoverageModel, dominant_problem, excess


def infer(model: CoverageModel, state: dict, allowed: dict[str, list[str]]) -> dict:
    """Runs the joint optimiser on the cluster state with the moves the engine allowed and returns its plan plus each cell's shares, excess and
    dominant problem, and the model's confidence.
    """
    out = model.optimise(state, allowed)
    return {**out, "cells": {c: {"shares": s["shares"], "excess": round(excess(s["shares"]), 3),
                                 "problem": dominant_problem(s["shares"])} for c, s in state.items()},
            "confidence": model.confidence()}
