from ghost_agent.dashboard import render_dashboard


def test_dashboard_uses_local_control_plane_snapshot() -> None:
    page = render_dashboard()
    assert "/v1/snapshot" in page
    assert "Ghost Control Plane" in page
