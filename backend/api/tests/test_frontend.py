def test_index_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="app"' in r.text


def test_script_files_are_served(client):
    for name in ("strings.js", "api.js", "app.js", "styles.css"):
        assert client.get(f"/{name}").status_code == 200


def test_api_routes_still_win_over_static_files(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_api_path_is_404(client):
    assert client.get("/api/does-not-exist").status_code == 404