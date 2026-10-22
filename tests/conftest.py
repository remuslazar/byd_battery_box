"""Fixtures for the BYD Battery Box tests."""

import pytest

from .fake_bmu import FakeBmu, hvs_registers

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    """The client writes its log files below the working directory."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
async def bmu(socket_enabled):
    fake = await FakeBmu(hvs_registers()).start()
    yield fake
    await fake.stop()
