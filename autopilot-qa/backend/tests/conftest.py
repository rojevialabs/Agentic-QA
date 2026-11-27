import uuid

import pytest

from db.mongo_client import create_client
from settings import get_settings


@pytest.fixture
async def db():
    """A fresh, empty database per test on the configured MongoDB; dropped afterwards."""
    uri = get_settings().mongo_uri
    client = create_client(uri)
    try:
        await client.admin.command("ping")
    except Exception as exc:  # fail loudly: these tests are meaningless without a real server
        await client.close()
        pytest.fail(f"MongoDB not reachable at {uri}: {exc}")
    name = f"autopilot_qa_test_{uuid.uuid4().hex[:8]}"
    yield client[name]
    await client.drop_database(name)
    await client.close()
