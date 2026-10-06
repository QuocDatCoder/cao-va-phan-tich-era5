import inspect

import frontend.dashboard as dashboard


def test_dashboard_imports_without_backend_internals() -> None:
    source = inspect.getsource(dashboard)

    forbidden = [
        "backend.app.services",
        "backend.app.providers",
        "backend.app.storage",
        "cdsapi",
        "xarray",
    ]
    assert all(item not in source for item in forbidden)
