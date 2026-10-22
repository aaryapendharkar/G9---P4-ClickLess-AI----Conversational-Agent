"""Shared fixtures. Everything runs on the synthetic data/fixtures world -- no network, no keys."""

import pytest

from clickless.config import load_config
from clickless.data import DataStore
from clickless.pipeline import build_pipeline

DEMO_TEXT = (
    "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want to arrive with at "
    "least 20%, lunch stop and a wheelchair accessible restroom please"
)


@pytest.fixture(scope="session")
def cfg_a():
    return load_config("A")


@pytest.fixture(scope="session")
def store(cfg_a):
    return DataStore.from_config(cfg_a)


@pytest.fixture
def pipe_a(cfg_a, store):
    return build_pipeline(cfg_a, store)


@pytest.fixture
def demo_text():
    return DEMO_TEXT
