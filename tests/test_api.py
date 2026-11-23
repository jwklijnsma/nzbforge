from fastapi.testclient import TestClient
from main import app

class TestAPI:
    def test_root(self):
        client = TestClient(app)
        response = client.get('/')
        assert response.status_code == 200
        assert response.json() == {'message': 'Welcome to nzbforge API'}