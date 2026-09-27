"""Isolated Nginx contract smoke with test-only HTTP stubs; no real services/data."""
from __future__ import annotations

import http.client
import json
import subprocess
import time
import uuid
from urllib.request import urlopen


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def main() -> None:
    suffix = uuid.uuid4().hex[:10]
    network, stub, frontend = ("moodflow-proxy-test-" + suffix + s for s in ("", "-stub", "-frontend"))
    tag = "worldline-v1-fe24b04"
    owned_containers = []
    owned_network = None
    mock = """
const http = require('http');
for (const port of [8080,8001]) http.createServer((req,res)=>{
  if(port===8001 && req.url==='/api/ai/chat') {
    res.writeHead(200,{'content-type':'text/event-stream'});
    res.write('event: first\\ndata: ready\\n\\n');
    setTimeout(()=>{res.end('event: final\\ndata: done\\n\\n')},1200);
    return;
  }
  res.writeHead(200,{'content-type':'application/json'});
  res.end(JSON.stringify({port,path:req.url,method:req.method,origin:req.headers.origin,
    forwardedHost:req.headers['x-forwarded-host'],forwardedProto:req.headers['x-forwarded-proto'],
    cookie:req.headers.cookie}));
}).listen(port,'0.0.0.0');
"""
    try:
        owned_network = docker("network", "create", network)
        owned_containers.append(docker("run", "-d", "--name", stub, "--network", network, "--network-alias", "java",
               "--network-alias", "ai-service", "--read-only", "--entrypoint", "node",
               "moodflow/netease:" + tag, "-e", mock))
        owned_containers.append(docker("run", "-d", "--name", frontend, "--network", network,
               "-p", "127.0.0.1::80", "moodflow/frontend:" + tag))
        address = docker("port", frontend, "80/tcp")
        base = "http://" + address
        for attempt in range(40):
            try:
                with urlopen(base + "/healthz", timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                if attempt == 39:
                    raise
                time.sleep(0.1)
        connection = http.client.HTTPConnection(address, timeout=5)
        headers = {"Origin": base, "Cookie": "JSESSIONID=fixture-only", "Content-Type": "application/json"}
        connection.request("POST", "/api/auth/logout", body="{}", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        java = json.loads(response.read())
        assert java["port"] == 8080 and java["path"] == "/api/auth/logout" and java["method"] == "POST"
        assert java["cookie"] == "JSESSIONID=fixture-only"
        assert java["origin"] == base and java["forwardedHost"] == address and java["forwardedProto"] == "http"
        connection.request("GET", "/api/ai/worldline/session/fixture", headers={"X-Forwarded-Proto": "https"})
        ai = json.loads(connection.getresponse().read())
        assert ai["port"] == 8001 and ai["path"] == "/api/ai/worldline/session/fixture"
        assert ai["forwardedProto"] == "https"
        started = time.monotonic()
        connection.request("POST", "/api/ai/chat", body="{}", headers={"Content-Type": "application/json"})
        stream = connection.getresponse()
        assert stream.status == 200 and stream.getheader("Content-Type") == "text/event-stream"
        assert stream.readline() == b"event: first\n"
        assert time.monotonic() - started < 1, "SSE first event was buffered until completion"
        assert b"event: final" in stream.read()
        connection.request("GET", "/worldline")
        spa = connection.getresponse()
        assert spa.status == 200 and b'<div id="root">' in spa.read()
        connection.close()
        print(json.dumps({"passed": True, "test_only_stubs": True, "api_paths_preserved": True,
                          "cookie_forwarding": True, "origin_host_port_preserved": True,
                          "sse_first_event_unbuffered": True, "spa_deep_link": True}))
    finally:
        # Only the unique test-owned containers/network; no volumes or user services.
        for identity in reversed(owned_containers):
            subprocess.run(["docker", "rm", "-f", identity], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if owned_network:
            subprocess.run(["docker", "network", "rm", owned_network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
