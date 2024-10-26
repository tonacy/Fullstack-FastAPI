import pytest
import time
import uuid
from fastapi import status
from httpx import ASGITransport, AsyncClient
from backend.fastapi.main import app
from backend.fastapi.core.init_settings import global_settings

# Mock data for creating a message
valid_message_data = {
    "content": "Hello, world!"
}

# Define the number of times to run the bulk insert
Run_times = 10

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(app)

@pytest.fixture
async def async_client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as ac:
        yield ac

@pytest.fixture
def anyio_backend():
    return 'asyncio'

def test_create_message_bulk(client):
    start_time = time.time()
    for _ in range(Run_times):
        response = client.post("/api/v1/messages/", json=valid_message_data)
        assert response.status_code == status.HTTP_201_CREATED
        response_data = response.json()
        assert "id" in response_data
        try:
            uuid_obj = uuid.UUID(response_data["id"])
            assert isinstance(uuid_obj, uuid.UUID)
        except ValueError:
            pytest.fail("Invalid UUID format returned in the response.")
    
    end_time = time.time()
    print(f"Synchronous bulk insert of {Run_times} messages took {end_time - start_time} seconds.")

@pytest.mark.anyio
async def test_create_message_async_bulk(async_client):
    start_time = time.time()
    for _ in range(Run_times):
        response = await async_client.post("/api/v1/messages/async", json=valid_message_data)
        assert response.status_code == status.HTTP_201_CREATED
        response_data = response.json()
        assert "id" in response_data
        try:
            uuid_obj = uuid.UUID(response_data["id"])
            assert isinstance(uuid_obj, uuid.UUID)
        except ValueError:
            pytest.fail("Invalid UUID format returned in the response.")
    
    end_time = time.time()
    print(f"Asynchronous bulk insert of {Run_times} messages took {end_time - start_time} seconds.")
