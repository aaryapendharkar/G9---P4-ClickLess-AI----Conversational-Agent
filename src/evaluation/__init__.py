"""Benchmark, independent oracle, metrics, statistics, harness. Owner: Ian.

INDEPENDENCE RULE (proposal §V): evaluation.oracle must not import clickless.planning or
clickless.ranking. It may import clickless.contracts, clickless.common and clickless.data.
tests/test_architecture.py enforces this.
"""
