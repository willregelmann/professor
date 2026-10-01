import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from professor.hosts import hermes
from professor.hosts.hermes import HermesHost


@pytest.fixture
async def gateway():
    """A stand-in for the Hermes gateway: checks the key, streams a reply, hands back a session."""
    seen = []

    async def completions(request):
        body = await request.json()
        seen.append({"auth": request.headers.get("Authorization"),
                     "session": request.headers.get("X-Hermes-Session-Id"),
                     "prompt": body["messages"][0]["content"], "stream": body["stream"]})
        if request.headers.get("Authorization") != "Bearer sesame":
            return web.json_response({"error": "bad key sesame-not"}, status=401)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "X-Hermes-Session-Id": "s-42"})
        await resp.prepare(request)
        await resp.write(b": keep-alive\n\n")
        for piece in ("Hello ", "from ", "Wren."):
            await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
        await resp.write(b"data: {\"choices\": [{\"delta\": {}}]}\n\ndata: not json\n\ndata: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completions)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")), seen
    await server.close()


async def test_text_arrives_as_written_and_the_session_carries_over(gateway):
    url, seen = gateway
    host = HermesHost(url, "sesame")
    assert [d async for d in host.turn("first")] == ["Hello ", "from ", "Wren."]
    assert host.session == "s-42"
    assert "".join([d async for d in host.turn("second")]) == "Hello from Wren."
    await host.close()
    assert [s["session"] for s in seen] == [None, "s-42"]
    assert seen[0]["stream"] is True and seen[1]["prompt"] == "second"


async def test_a_gateway_that_refuses_is_a_failure_that_gives_nothing_away(gateway):
    url, _ = gateway
    host = HermesHost(url, "wrong")
    with pytest.raises(RuntimeError) as failure:
        [d async for d in host.turn("hello")]
    await host.close()
    assert str(failure.value) == "the Hermes gateway answered 401"


async def test_a_gateway_that_is_not_there_is_a_failure():
    host = HermesHost("http://127.0.0.1:9", "sesame")
    with pytest.raises(Exception) as failure:
        [d async for d in host.turn("hello")]
    await host.close()
    assert "sesame" not in str(failure.value)


def test_the_key_is_read_from_the_gateways_own_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nAPI_SERVER_KEY="sesame"\n')
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    assert hermes.find_key(env) == "sesame"
    monkeypatch.setenv("HERMES_API_KEY", "from-the-environment")
    assert hermes.find_key(env) == "from-the-environment"
    env.write_text("OTHER=1\n")
    with pytest.raises(ValueError, match="API_SERVER_KEY not found"):
        hermes.key_from_env_file(env)
