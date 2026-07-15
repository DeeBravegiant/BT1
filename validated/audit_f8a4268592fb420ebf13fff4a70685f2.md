### Title
Unauthenticated, Rate-Limit-Free Data Layer HTTP Server Allows Resource Exhaustion, Disrupting Data Layer Sync for All Subscribers — (`File: chia/data_layer/data_layer_server.py`)

### Summary
The `DataLayerServer` exposes a plain-HTTP file-serving endpoint with no SSL, no authentication, and no rate limiting. Any unprivileged internet attacker can flood it with concurrent GET requests, exhausting file descriptors and blocking the async event loop via synchronous disk I/O, causing a long-lived inability for honest Data Layer subscribers to download delta/full-tree files and sync their stores.

### Finding Description
`DataLayerServer.start()` creates a `WebServer` without passing an `ssl_context`, so the server runs plain HTTP. The two route handlers — `file_handler` and `folder_handler` — perform no caller authentication and impose no per-IP or aggregate rate limit. Each handler performs synchronous blocking `open()` + `read()` inside an async coroutine, which blocks the entire aiohttp event loop for the duration of the disk read. The server also actively calls `self.upnp.remap(self.port)` to punch through NAT, making it reachable from the public internet by design.

```python
# chia/data_layer/data_layer_server.py  lines 64-71
self.webserver = await WebServer.create(
    hostname=self.host_ip,
    port=self.port,
    routes=[
        web.get("/{filename}", self.file_handler),
        web.get("/{tree_id}/{filename}", self.folder_handler),
    ],
    # ssl_context omitted → plain HTTP, no TLS, no client cert auth
)
```

```python
# lines 91-103
async def file_handler(self, request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    if not is_filename_valid(filename):          # only format check, no auth
        raise Exception("Invalid file format requested.")
    file_path = self.server_dir.joinpath(filename)
    with open(file_path, "rb") as reader:        # synchronous blocking I/O
        content = reader.read()                  # blocks event loop
    ...
```

Contrast with every other Chia server component: the RPC server passes `ssl_context` (mutual TLS with private CA), the daemon passes `ssl_context`, and the P2P server enforces per-connection rate limits via `RateLimiter`. The Data Layer HTTP server has none of these controls.

### Impact Explanation
An unprivileged attacker sends a high volume of concurrent GET requests with valid-format filenames (e.g., `<64-hex>-<64-hex>-delta-1-v1.0.dat`) that resolve to non-existent files. Each request causes a blocking `open()` syscall that raises `FileNotFoundError` only after the OS has attempted the lookup, stalling the event loop. For existing files, the full file content (up to hundreds of MB for full-tree files) is read synchronously into memory per request. Either path exhausts the server's resources (file descriptors, memory, event-loop time), making it unresponsive. All Data Layer subscribers that rely on this mirror to download delta files will fail to sync their stores, constituting a long-lived inability to process Data Layer updates.

### Likelihood Explanation
The server is internet-facing by design (UPnP remapping is automatic). The valid filename format is fully public (documented in the Data Layer specification and derivable from `is_filename_valid`). No credential, token, or prior relationship is required. Any attacker who knows the server's IP and port can exploit this immediately.

### Recommendation
1. **Rate limiting**: Add per-IP request rate limiting at the aiohttp middleware layer (e.g., using `aiohttp`'s middleware or a token-bucket per remote IP).
2. **Non-blocking I/O**: Replace synchronous `open()`/`read()` with `asyncio.to_thread` or `aiofiles` so disk I/O does not block the event loop.
3. **Connection limits**: Configure `aiohttp`'s `client_max_size` and limit the number of concurrent connections.
4. **Optional authentication**: For private mirrors, support an optional bearer-token or IP-allowlist check in the handlers.

### Proof of Concept
```python
import asyncio, aiohttp, itertools

# Valid-format filename (file need not exist to trigger blocking open() attempt)
FILENAME = "a" * 64 + "-" + "b" * 64 + "-delta-1-v1.0.dat"
TARGET = "http://<victim-data-layer-host>:8575/" + FILENAME

async def flood():
    async with aiohttp.ClientSession() as s:
        tasks = [s.get(TARGET) for _ in range(500)]
        await asyncio.gather(*tasks, return_exceptions=True)

asyncio.run(flood())
# Repeat in a loop; the server's event loop stalls on synchronous open() calls,
# making it unresponsive to legitimate subscriber download requests.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** chia/data_layer/data_layer_server.py (L55-57)
```python
        # Setup UPnP for the data_layer_service port
        self.upnp.setup()
        self.upnp.remap(self.port)
```

**File:** chia/data_layer/data_layer_server.py (L64-71)
```python
        self.webserver = await WebServer.create(
            hostname=self.host_ip,
            port=self.port,
            routes=[
                web.get("/{filename}", self.file_handler),
                web.get("/{tree_id}/{filename}", self.folder_handler),
            ],
        )
```

**File:** chia/data_layer/data_layer_server.py (L91-103)
```python
    async def file_handler(self, request: web.Request) -> web.Response:
        filename = request.match_info["filename"]
        if not is_filename_valid(filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response
```

**File:** chia/data_layer/data_layer_server.py (L105-118)
```python
    async def folder_handler(self, request: web.Request) -> web.Response:
        tree_id = request.match_info["tree_id"]
        filename = request.match_info["filename"]
        if not is_filename_valid(tree_id + "-" + filename):
            raise Exception("Invalid file format requested.")
        file_path = self.server_dir.joinpath(tree_id).joinpath(filename)
        with open(file_path, "rb") as reader:
            content = reader.read()
        response = web.Response(
            content_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment;filename={filename}"},
            body=content,
        )
        return response
```

**File:** chia/util/network.py (L44-69)
```python
    async def create(
        cls,
        hostname: str,
        port: uint16,
        routes: Iterable[web.RouteDef] = (),
        max_request_body_size: int = 1024**2,  # Default `client_max_size` from web.Application
        ssl_context: ssl.SSLContext | None = None,
        keepalive_timeout: int = 75,  # Default from aiohttp.web
        shutdown_timeout: int = 60,  # Default `shutdown_timeout` from aiohttp.web_runner.BaseRunner
        prefer_ipv6: bool = False,
        logger: logging.Logger = web_logger,
        start: bool = True,
    ) -> WebServer:
        app = web.Application(client_max_size=max_request_body_size, logger=logger)
        runner = web.AppRunner(
            app,
            access_log=None,
            keepalive_timeout=keepalive_timeout,
            shutdown_timeout=shutdown_timeout,
        )

        self = cls(
            runner=runner,
            hostname=hostname,
            listen_port=uint16(port),
            scheme="https" if ssl_context is not None else "http",
```

**File:** chia/rpc/rpc_server.py (L196-206)
```python
    async def start(self, self_hostname: str, rpc_port: uint16, max_request_body_size: int) -> None:
        if self.webserver is not None:
            raise RuntimeError("RpcServer already started")
        self.webserver = await WebServer.create(
            hostname=self_hostname,
            port=rpc_port,
            max_request_body_size=max_request_body_size,
            routes=[web.post(route, wrap_http_handler(func, route)) for (route, func) in self._get_routes().items()],
            ssl_context=self.ssl_context,
            prefer_ipv6=self.prefer_ipv6,
        )
```
