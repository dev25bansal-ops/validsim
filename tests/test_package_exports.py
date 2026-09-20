"""Guards for the ``validsim.engine`` and ``validsim.notify`` package facades.

These tests lock in the P1 audit fix: every name advertised in a package's
``__all__`` must actually be importable from that package (no drift between the
declared public surface and the real attributes), and the ``__all__`` lists must
stay alphabetically sorted so the facades remain easy to scan.
"""

from __future__ import annotations

import importlib

import pytest

#: Packages whose public facade is exercised here.
_PACKAGES = ("validsim.engine", "validsim.notify")

#: Symbols the audit found missing from each facade; regression guard so a
#: future refactor cannot silently drop them again.
_REQUIRED_EXPORTS = {
    "validsim.engine": {"MetricComparison", "render_scorecard_pdf", "scorecard_pdf_bytes"},
    "validsim.notify": {"SmtpSettings", "severity_from_scorecard"},
}


def _load(package: str):
    """Import and return ``package`` (fresh from the module cache)."""
    return importlib.import_module(package)


@pytest.mark.parametrize("package", _PACKAGES)
def test_all_is_present_and_non_empty(package: str) -> None:
    """Each facade declares a non-empty ``__all__``."""
    module = _load(package)
    assert isinstance(module.__all__, list)
    assert module.__all__, f"{package}.__all__ is empty"


@pytest.mark.parametrize("package", _PACKAGES)
def test_every_all_entry_is_importable(package: str) -> None:
    """Every name in ``__all__`` resolves to a real attribute on the package."""
    module = _load(package)
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert not missing, f"{package}.__all__ advertises missing attributes: {missing}"


@pytest.mark.parametrize("package", _PACKAGES)
def test_all_has_no_duplicates(package: str) -> None:
    """``__all__`` must not repeat a name."""
    names = _load(package).__all__
    dupes = {name for name in names if names.count(name) > 1}
    assert not dupes, f"{package}.__all__ has duplicate entries: {sorted(dupes)}"


@pytest.mark.parametrize("package", _PACKAGES)
def test_all_is_alphabetized(package: str) -> None:
    """``__all__`` is kept in sorted (ASCII) order: classes before functions."""
    names = _load(package).__all__
    assert names == sorted(names), f"{package}.__all__ is not alphabetized"


@pytest.mark.parametrize("package", sorted(_REQUIRED_EXPORTS))
def test_required_exports_present(package: str) -> None:
    """The audit-missing symbols are advertised and importable."""
    module = _load(package)
    required = _REQUIRED_EXPORTS[package]
    assert required.issubset(set(module.__all__)), (
        f"{package}.__all__ is missing required exports: "
        f"{sorted(required - set(module.__all__))}"
    )
    for name in required:
        assert hasattr(module, name), f"{package}.{name} is not importable"


def test_facade_attributes_are_the_real_objects() -> None:
    """The re-exported symbols are the very objects defined in their modules."""
    engine = _load("validsim.engine")
    benchmark = importlib.import_module("validsim.engine.benchmark")
    pdf = importlib.import_module("validsim.engine.pdf")
    assert engine.MetricComparison is benchmark.MetricComparison
    assert engine.scorecard_pdf_bytes is pdf.scorecard_pdf_bytes
    assert engine.render_scorecard_pdf is pdf.render_scorecard_pdf

    notify = _load("validsim.notify")
    email = importlib.import_module("validsim.notify.email")
    dispatcher = importlib.import_module("validsim.notify.dispatcher")
    assert notify.SmtpSettings is email.SmtpSettings
    assert notify.severity_from_scorecard is dispatcher.severity_from_scorecard
