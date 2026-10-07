"""Temp: prove the bench regression gate changes the process exit code."""
import pytest, sys
def pytest_addoption(parser):
    g = parser.getgroup("x"); g.addoption("--validsim-benchmark-compare", action="store_true", default=False)
    g.addoption("--validsim-benchmark-save", action="store_true", default=False)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not config.getoption("validsim_benchmark_compare"):
        return
    terminalreporter.write_sep("=", "benchmark regression gate")
    terminalreporter.write_line("SIMULATED REGRESSION", red=True)
    terminalreporter._session.testsfailed += 1
def test_ok():
    assert True
