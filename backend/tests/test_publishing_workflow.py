import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select


def _seed_shop(domain):
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop
    async def run():
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(Shop).where(Shop.shop_domain == domain))).scalar_one_or_none()
            if row is None:
                row = Shop(shop_domain=domain, access_token_encrypted="encrypted-test-token", is_active=True)
                db.add(row)
                await db.commit()
            return row.id
    return asyncio.run(run())


def _auth(client, domain):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    client.app.dependency_overrides[get_optional_shop] = lambda: CurrentUser(shop_domain=domain)


def _payload():
    return {
        "action_type": "shopify_product_channels",
        "idempotency_key": "product-channel-test-0001",
        "payload": {
            "product_id": "gid://shopify/Product/123",
            "publication_ids": ["gid://shopify/Publication/1"],
            "publish": True,
        },
    }


def test_publishing_request_requires_approval_and_executes_once(client, monkeypatch):
    from app.routers import publishing
    domain = "publishing-lifecycle.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    calls = []
    async def fake_perform(job, shop, current, db):
        calls.append(job.id)
        return {"provider": "shopify", "publication_count": 1}
    monkeypatch.setattr(publishing, "_perform_action", fake_perform)
    try:
        created = client.post("/api/publishing/requests", json=_payload())
        assert created.status_code == 201, created.text
        action = created.json()["action"]
        assert action["status"] == "pending_approval"
        blocked = client.post(f"/api/publishing/requests/{action['id']}/execute")
        assert blocked.status_code == 409
        approved = client.post(f"/api/publishing/requests/{action['id']}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["action"]["status"] == "approved"
        executed = client.post(f"/api/publishing/requests/{action['id']}/execute")
        assert executed.status_code == 200, executed.text
        assert executed.json()["action"]["status"] == "published"
        duplicate = client.post(f"/api/publishing/requests/{action['id']}/execute")
        assert duplicate.status_code == 409
        assert calls == [action["id"]]
    finally:
        client.app.dependency_overrides.clear()


def test_same_idempotency_key_returns_existing_but_conflicting_payload_is_rejected(client):
    domain = "publishing-idempotency.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    try:
        first = client.post("/api/publishing/requests", json=_payload())
        assert first.status_code == 201, first.text
        repeated = client.post("/api/publishing/requests", json=_payload())
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["created"] is False
        assert repeated.json()["action"]["id"] == first.json()["action"]["id"]
        different = _payload()
        different["payload"]["publish"] = False
        conflict = client.post("/api/publishing/requests", json=different)
        assert conflict.status_code == 409
    finally:
        client.app.dependency_overrides.clear()


def test_publishing_action_is_merchant_scoped(client):
    owner = "publishing-owner.myshopify.com"
    other = "publishing-other.myshopify.com"
    _seed_shop(owner)
    _seed_shop(other)
    _auth(client, owner)
    created = client.post("/api/publishing/requests", json=_payload())
    assert created.status_code == 201, created.text
    action_id = created.json()["action"]["id"]
    _auth(client, other)
    try:
        assert client.get(f"/api/publishing/requests/{action_id}").status_code == 404
        assert client.post(f"/api/publishing/requests/{action_id}/approve").status_code == 404
        assert client.post(f"/api/publishing/requests/{action_id}/execute").status_code == 404
        assert all(a["id"] != action_id for a in client.get("/api/publishing/requests").json()["actions"])
    finally:
        client.app.dependency_overrides.clear()


def test_ambiguous_provider_failure_moves_to_needs_review_and_cannot_retry(client, monkeypatch):
    from app.routers import publishing
    domain = "publishing-ambiguous.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    calls = []
    async def ambiguous(job, shop, current, db):
        calls.append(job.id)
        raise TimeoutError("simulated provider timeout")
    monkeypatch.setattr(publishing, "_perform_action", ambiguous)
    try:
        created = client.post("/api/publishing/requests", json=_payload())
        action_id = created.json()["action"]["id"]
        assert client.post(f"/api/publishing/requests/{action_id}/approve").status_code == 200
        response = client.post(f"/api/publishing/requests/{action_id}/execute")
        assert response.status_code == 502
        status_response = client.get(f"/api/publishing/requests/{action_id}")
        assert status_response.json()["action"]["status"] == "needs_review"
        assert "do not retry" in status_response.json()["action"]["error"].lower()
        assert client.post(f"/api/publishing/requests/{action_id}/execute").status_code == 409
        assert calls == [action_id]
    finally:
        client.app.dependency_overrides.clear()


def test_direct_mutation_routes_require_approval(client):
    domain = "publishing-direct-block.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    response = client.post("/api/social/meta/facebook/publish", json={
        "account_id": 7, "message": "must not publish directly", "link": None,
    })
    assert response.status_code == 409
    response = client.post("/api/social/meta/instagram/publish", json={
        "account_id": 7, "image_url": "https://example.test/image.jpg", "caption": "test",
    })
    assert response.status_code == 409
    response = client.post("/api/products/sales-channels/products/gid://shopify/Product/123", json={
        "publication_ids": ["gid://shopify/Publication/1"], "publish": True,
    })
    assert response.status_code == 409
    client.app.dependency_overrides.clear()


def test_invalid_action_payloads_rejected(client):
    domain = "publishing-validation.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    try:
        bad_gid = _payload()
        bad_gid["payload"]["product_id"] = "not-a-product-gid"
        assert client.post("/api/publishing/requests", json=bad_gid).status_code == 422
        bad_key = _payload()
        bad_key["idempotency_key"] = "x"
        assert client.post("/api/publishing/requests", json=bad_key).status_code == 422
        bad_ig = {
            "action_type": "meta_instagram", "idempotency_key": "instagram-invalid-0001",
            "payload": {"account_id": 1, "image_url": "http://example.test/image.jpg", "caption": "x"},
        }
        assert client.post("/api/publishing/requests", json=bad_ig).status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_audit_records_do_not_contain_post_body_or_token(client, monkeypatch):
    from app.routers import publishing
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AuditLog
    domain = "publishing-audit.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    secret_message = "UNIQUE_PRIVATE_POST_BODY_FOR_TEST"
    payload = {
        "action_type": "meta_facebook", "idempotency_key": "audit-content-test-0001",
        "payload": {"account_id": 8, "message": secret_message, "link": None},
    }
    # Seed the Meta account expected by the request validation.
    from app.models.schemas import SocialAccount, Shop
    async def seed_page():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == domain))).scalar_one()
            page = SocialAccount(shop_id=shop.id, provider="meta", account_type="facebook_page",
                external_account_id="page-audit", account_name="Audit Page",
                access_token_encrypted="encrypted-placeholder-not-a-real-token", metadata_json='{"tasks":["CREATE_CONTENT"]}', is_active=True)
            db.add(page)
            await db.commit()
            await db.refresh(page)
            return page.id
    payload["payload"]["account_id"] = asyncio.run(seed_page())
    async def fake_perform(job, shop, current, db):
        return {"provider": "meta_facebook", "external_id": "post-123"}
    monkeypatch.setattr(publishing, "_perform_action", fake_perform)
    try:
        created = client.post("/api/publishing/requests", json=payload)
        assert created.status_code == 201, created.text
        action_id = created.json()["action"]["id"]
        assert client.post(f"/api/publishing/requests/{action_id}/approve").status_code == 200
        assert client.post(f"/api/publishing/requests/{action_id}/execute").status_code == 200
        async def audits():
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(AuditLog).where(AuditLog.action.like("publishing.%")))).scalars().all()
                return [row.details_json or "" for row in rows]
        details = "\n".join(asyncio.run(audits()))
        assert secret_message not in details
        assert "DO_NOT_LOG_PROVIDER_TOKEN" not in details
    finally:
        client.app.dependency_overrides.clear()


def test_concurrent_execute_requests_only_call_provider_once(client, monkeypatch):
    from app.routers import publishing
    domain = "publishing-concurrent.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    async def slow_perform(job, shop, current, db):
        calls.append(job.id)
        entered.set()
        # The synchronous TestClient runs this coroutine in its own worker thread.
        await asyncio.to_thread(release.wait, 2)
        return {"provider": "shopify", "publication_count": 1}
    monkeypatch.setattr(publishing, "_perform_action", slow_perform)
    try:
        created = client.post("/api/publishing/requests", json=_payload())
        action_id = created.json()["action"]["id"]
        assert client.post(f"/api/publishing/requests/{action_id}/approve").status_code == 200
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, f"/api/publishing/requests/{action_id}/execute")
            assert entered.wait(2), "first request did not reach provider stub"
            second = client.post(f"/api/publishing/requests/{action_id}/execute")
            release.set()
            first_response = first.result(timeout=3)
        assert first_response.status_code == 200, first_response.text
        assert second.status_code == 409, second.text
        assert calls == [action_id]
    finally:
        release.set()
        client.app.dependency_overrides.clear()


def test_known_meta_rejection_is_failed_and_not_retryable(client, monkeypatch):
    from app.routers import publishing
    from app.services.meta import MetaAPIError
    domain = "publishing-provider-reject.myshopify.com"
    _seed_shop(domain)
    _auth(client, domain)
    async def rejected(job, shop, current, db):
        raise MetaAPIError("provider rejected request", 400, {"error": {"message": "private provider detail"}})
    monkeypatch.setattr(publishing, "_perform_action", rejected)
    try:
        created = client.post("/api/publishing/requests", json=_payload())
        action_id = created.json()["action"]["id"]
        assert client.post(f"/api/publishing/requests/{action_id}/approve").status_code == 200
        response = client.post(f"/api/publishing/requests/{action_id}/execute")
        assert response.status_code == 400
        action = client.get(f"/api/publishing/requests/{action_id}").json()["action"]
        assert action["status"] == "failed"
        assert action["error"] == "Provider rejected the action"
        assert client.post(f"/api/publishing/requests/{action_id}/execute").status_code == 409
    finally:
        client.app.dependency_overrides.clear()
