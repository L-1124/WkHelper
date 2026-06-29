import os
import tempfile

import pytest

from wkhelper.core.credential_store import CredentialStore
from wkhelper.core.models import UserInfo


@pytest.fixture
def temp_db_dir():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        yield temp_dir


@pytest.fixture
def credential_store(temp_db_dir):
    db_path = os.path.join(temp_db_dir, "test_credentials.db")
    store = CredentialStore(db_path)
    yield store
    if hasattr(store, "conn"):
        store.conn.close()


def test_credential_store_save_and_get(credential_store):
    user = UserInfo(id="123", name="test_user", school="test_school")
    cookies = {"sessionid": "abc"}

    credential_store.save("ykt", user, cookies)

    retrieved_cookies = credential_store.get_cookies("ykt", "123")
    assert retrieved_cookies == cookies


def test_credential_store_list_accounts(credential_store):
    user1 = UserInfo(id="123", name="user1", school="school1")
    user2 = UserInfo(id="456", name="user2", school="school2")

    credential_store.save("ykt", user1, {"c": "1"})
    credential_store.save("ykt", user2, {"c": "2"})
    credential_store.save("xtzx", user1, {"c": "3"})

    # List all
    accounts = credential_store.list_accounts()
    assert len(accounts) == 3

    # List by platform
    ykt_accounts = credential_store.list_accounts("ykt")
    assert len(ykt_accounts) == 2
    assert ykt_accounts[0]["user_id"] in ("123", "456")
    assert ykt_accounts[1]["user_id"] in ("123", "456")
    assert ykt_accounts[0]["user_id"] != ykt_accounts[1]["user_id"]

    xtzx_accounts = credential_store.list_accounts("xtzx")
    assert len(xtzx_accounts) == 1
    assert xtzx_accounts[0]["user_id"] == "123"


def test_credential_store_delete(credential_store):
    user = UserInfo(id="123", name="test_user")
    cookies = {"sessionid": "abc"}

    credential_store.save("ykt", user, cookies)
    assert credential_store.get_cookies("ykt", "123") == cookies

    credential_store.delete("ykt", "123")
    assert credential_store.get_cookies("ykt", "123") is None


def test_credential_store_upsert(credential_store):
    user = UserInfo(id="123", name="test_user")
    cookies1 = {"sessionid": "old"}
    cookies2 = {"sessionid": "new"}

    credential_store.save("ykt", user, cookies1)
    credential_store.save("ykt", user, cookies2)

    retrieved_cookies = credential_store.get_cookies("ykt", "123")
    assert retrieved_cookies == cookies2
