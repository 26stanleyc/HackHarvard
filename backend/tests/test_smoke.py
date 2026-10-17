import importlib


def test_package_imports():
    for mod in ("clearinghouse", "clearinghouse.config", "clearinghouse.schemas"):
        importlib.import_module(mod)


def test_paths():
    from clearinghouse import config

    assert (config.REPO_ROOT / "docs" / "design.md").is_file()
    assert config.BACKEND_DIR == config.REPO_ROOT / "backend"
    assert config.DATA_RAW == config.BACKEND_DIR / "data" / "raw"
    assert config.DATA_PROCESSED == config.BACKEND_DIR / "data" / "processed"
    assert config.RESULTS_DIR == config.REPO_ROOT / "docs" / "results"
    assert config.FIXTURES_DIR == config.REPO_ROOT / "fixtures"
