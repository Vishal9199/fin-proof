"""End-to-end API test: upload → SSE stream drives the run → fetch the result.

Exercises the full HTTP surface in-process (no live server) including the SSE
trigger semantics in main.events.
"""
from pathlib import Path

import httpx
from httpx import ASGITransport

from app.main import app

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data"


async def test_full_reconciliation_run():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        files = [
            ("files", (p.name, p.read_bytes()))
            for p in sorted(SAMPLE.rglob("*"))
            if p.is_file() and "drifted" not in p.name
        ]
        run_id = (await c.post("/reconcile", files=files)).json()["run_id"]

        # Consuming the SSE stream is what launches processing; read to completion.
        saw_completed = False
        async with c.stream("GET", f"/events/{run_id}") as stream:
            async for line in stream.aiter_lines():
                if "run.completed" in line:
                    saw_completed = True
        assert saw_completed

        data = (await c.get(f"/runs/{run_id}")).json()
        assert len(data["posted"]) == 3          # METRO, STELLAR, SWIGGY
        assert len(data["quarantined"]) == 3     # faded + both sides of BREW anomaly
        assert any(l["kind"] == "anomaly" for l in data["links"])
        assert data["total_posted_amount"] in ("1720.0", "1720.00", "1720")


async def test_quarantine_suggestions_and_resolution():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # 1. Run reconciliation
        files = [
            ("files", (p.name, p.read_bytes()))
            for p in sorted(SAMPLE.rglob("*"))
            if p.is_file() and "drifted" not in p.name
        ]
        run_id = (await c.post("/reconcile", files=files)).json()["run_id"]

        async with c.stream("GET", f"/events/{run_id}") as stream:
            async for line in stream.aiter_lines():
                if "run.completed" in line:
                    break

        # 2. Get the quarantined transactions
        run_data = (await c.get(f"/runs/{run_id}")).json()
        quarantined = run_data["quarantined"]
        assert len(quarantined) > 0
        
        # Pick the Brew & Co transaction which has amount conflict
        brew_txn = next((t for t in quarantined if "brew" in t["merchant"].lower()), None)
        assert brew_txn is not None
        txn_id = brew_txn["id"]

        # 3. Fetch AI suggestion
        sugg_resp = await c.get(f"/runs/{run_id}/quarantine/{txn_id}/suggest")
        assert sugg_resp.status_code == 200
        sugg_data = sugg_resp.json()
        assert "explanation" in sugg_data
        assert "actions" in sugg_data
        assert len(sugg_data["actions"]) >= 2
        
        # 4. Resolve the transaction by overriding to statement amount (₹540.00)
        action = sugg_data["actions"][0]  # E.g. statement override
        resolve_resp = await c.post(
            f"/runs/{run_id}/quarantine/{txn_id}/resolve",
            json={
                "action": action["action"],
                "amount": action.get("amount"),
                "merchant": action.get("merchant"),
                "category": action.get("category")
            }
        )
        assert resolve_resp.status_code == 200
        res_data = resolve_resp.json()
        assert res_data["ok"] is True
        
        # Verify run result was updated in the backend
        updated_run = (await c.get(f"/runs/{run_id}")).json()
        # Brew transaction should be removed from quarantined and added to posted
        assert not any(t["id"] == txn_id for t in updated_run["quarantined"])
        posted_brew = next((t for t in updated_run["posted"] if t["id"] == txn_id), None)
        assert posted_brew is not None
        assert float(posted_brew["amount"]) == 540.00
        
        # 5. Query the resolved ledger
        query_resp = await c.post(
            "/query",
            json={"question": "Show spending by category", "run_id": run_id}
        )
        assert query_resp.status_code == 200
        query_data = query_resp.json()
        assert "answer" in query_data
        assert "chart" in query_data
