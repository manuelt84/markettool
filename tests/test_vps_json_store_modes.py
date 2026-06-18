import pytest

from markettool.infra.storage import vps_json_store


@pytest.mark.parametrize(
    (
        "backend",
        "gcp_primary",
        "vps_primary",
        "gcp_to_vps",
        "vps_to_gcp",
        "hybrid",
    ),
    [
        ("gcp", True, False, False, False, False),
        ("vps", False, True, False, False, False),
        ("gcp_vps", True, False, True, False, True),
        ("vps_gcp", False, True, False, True, True),
    ],
)
def test_cloud_backend_modes_select_one_primary_and_explicit_hybrid(
    monkeypatch,
    backend,
    gcp_primary,
    vps_primary,
    gcp_to_vps,
    vps_to_gcp,
    hybrid,
):
    monkeypatch.setenv("MARKETTOOL_CLOUD_BACKEND", backend)
    monkeypatch.delenv("CLOUD_BACKEND", raising=False)

    assert vps_json_store.gcp_primary_enabled() is gcp_primary
    assert vps_json_store.vps_primary_enabled() is vps_primary
    assert vps_json_store.gcp_to_vps_enabled() is gcp_to_vps
    assert vps_json_store.vps_to_gcp_enabled() is vps_to_gcp
    assert vps_json_store.hybrid_mode_enabled() is hybrid
