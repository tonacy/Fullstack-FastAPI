import pytest
import uuid
import asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import status
from backend.fastapi.main import app
from backend.fastapi.core.init_settings import global_settings

# Mock data for creating a message
valid_message_data = {
    "content": "Hello, world! async"
}

@pytest.fixture
async def async_client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as ac:
        yield ac

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
async def test_create_message_async(async_client):
    # Send a POST request to the async /messages/ endpoint
    response = await async_client.post("/api/v1/messages/async", json=valid_message_data)
    
    # Verify that the request was successful
    assert response.status_code == status.HTTP_201_CREATED
    
    # Verify that the response contains the expected data
    response_data = response.json()
    assert response_data["content"] == valid_message_data["content"]
    
    # Verify that the 'id' field is a valid UUID
    assert "id" in response_data
    try:
        uuid_obj = uuid.UUID(response_data["id"])
        assert isinstance(uuid_obj, uuid.UUID)
    except ValueError:
        pytest.fail("Invalid UUID format returned in the response.")
