### Title
HTTPS Enforcement Bypass via 301/308 Redirect in Pool URL Update — (`chia/farmer/farmer.py`)

---

### Summary

The `update_pool_state` method enforces that pool URLs must use HTTPS on mainnet, but this check is only applied to the **original** URL read from the config file. When `_pool_get_pool_info` follows a 301/308 redirect to an HTTP URL, the redirected URL is written back to the config **without any HTTPS re-validation**. On the subsequent update cycle, the in-memory `pool_config` is updated to the HTTP URL before the HTTPS check fires, causing all subsequent partial submissions to be sent over plaintext HTTP to the attacker-controlled endpoint.

---

### Finding Description

**Step 1 — HTTPS check on original URL only.**

In `update_pool_state`, the HTTPS enforcement check reads the URL from the config file and validates it: [1](#0-0) 

This check passes when `pool_config.pool_url` is `https://pool.example.com`. The code then calls `_pool_get_pool_info(pool_config)`.

**Step 2 — Redirect detection in `_pool_get_pool_info` with no scheme validation.**

`_pool_get_pool_info` makes a GET request to `https://pool.example.com/pool_info`. aiohttp follows 301/308 redirects by default (including cross-scheme HTTPS→HTTP downgrades). The code detects the redirect and extracts the new URL: [2](#0-1) 

If the pool server redirects to `http://evil.com/pool_info`, then `new_pool_url = "http://evil.com"` — no scheme check is performed here.

**Step 3 — HTTP URL written to config without validation.**

Back in `update_pool_state`, the redirected URL is unconditionally written to the persistent config: [3](#0-2) 

There is no `startswith("https://")` guard on `pool_info_result.new_pool_url` before it is persisted.

**Step 4 — In-memory pool_config updated to HTTP URL on next cycle.**

On the next `update_pool_state` invocation, the config is re-read. The in-memory pool state is updated at line 592 **before** the HTTPS check at line 601: [4](#0-3) 

So `self.pool_state[p2_singleton_puzzle_hash]["pool_config"]` now holds the HTTP URL. The HTTPS check then fires and `continue`s, but the in-memory state is already poisoned.

**Step 5 — Partial submissions use the poisoned in-memory pool_config.**

In `farmer_api.py`, partial submissions use `pool_state_dict["pool_config"]` directly: [5](#0-4) 

The `pool_url` variable is derived from `pool_state_dict["pool_config"].pool_url`, which is now `http://evil.com`. All subsequent `POST /partial` requests — carrying authentication tokens and proof-of-space data — are sent over plaintext HTTP to the attacker's endpoint.

---

### Impact Explanation

- **Pool membership state corruption**: The persistent config file is overwritten with an HTTP URL, violating the mainnet HTTPS invariant.
- **Authentication token leakage**: `PostPartialPayload` includes a time-limited authentication token signed with the farmer's authentication key. Sending this over HTTP exposes it to any network observer, enabling replay attacks within the token's validity window.
- **Proof-of-space data exposure**: All partial submission data is sent in plaintext to the attacker-controlled endpoint.
- **Payout instruction leakage**: `POST /farmer` and `PUT /farmer` requests (which include `payout_instructions`) are also sent to the HTTP endpoint if the farmer re-registers.

This maps to: **High — Corruption of pool membership state with direct security impact** and **High — Bypass of pool authorization enabling payout redirection**.

---

### Likelihood Explanation

The attacker is a pool operator (or someone who has compromised the pool server). The farmer explicitly connects to the pool, and the HTTPS requirement exists precisely to protect against this threat. The attack requires no special privileges beyond operating or compromising the pool server. The redirect is served over the legitimate HTTPS connection (so TLS validation of the initial request is not bypassed), making this straightforward to execute.

---

### Recommendation

Add an HTTPS scheme check on `new_pool_url` before persisting it, mirroring the existing check:

```python
if pool_info_result is not None and pool_info_result.new_pool_url is not None:
    new_url = pool_info_result.new_pool_url
    if enforce_https and not new_url.startswith("https://"):
        self.log.error(f"Redirected pool URL must be HTTPS on mainnet: {new_url}")
    else:
        with PoolingShareState.acquire(...) as editable_pool_config:
            editable_pool_config.pool_url = new_url
```

Additionally, `_pool_get_pool_info` should validate the scheme of the final response URL before returning it as `new_pool_url`.

---

### Proof of Concept

```python
# Mock aiohttp to simulate a 308 redirect from HTTPS to HTTP
import aiohttp
from yarl import URL

class FakeRedirectHistory:
    status = 308

class FakeResponse:
    ok = True
    url = URL("http://evil.com/pool_info")
    history = [FakeRedirectHistory()]
    async def text(self):
        return json.dumps(make_valid_pool_info())

# Patch aiohttp.ClientSession.get to return FakeResponse
# Set pool_url = "https://pool.example.com" in config (passes HTTPS check)
# Call update_pool_state() once → HTTP URL written to config
# Call update_pool_state() again → in-memory pool_config.pool_url == "http://evil.com"
# Assert pool_state_dict["pool_config"].pool_url == "http://evil.com"
# Assert next POST /partial goes to "http://evil.com/partial"
```

The existing test infrastructure in `chia/_tests/farmer_harvester/test_farmer.py` (using `mocker.patch("aiohttp.ClientSession.get", ...)`) already demonstrates the exact mock pattern needed to reproduce this. [6](#0-5)

### Citations

**File:** chia/farmer/farmer.py (L372-381)
```python
                        new_pool_url: str | None = None
                        response_url_str = f"{resp.url}"
                        if (
                            response_url_str != url
                            and len(resp.history) > 0
                            and all(r.status in {301, 308} for r in resp.history)
                        ):
                            new_pool_url = response_url_str.replace("/pool_info", "")

                        return GetPoolInfoResult(pool_info=pool_info, new_pool_url=new_pool_url)
```

**File:** chia/farmer/farmer.py (L591-603)
```python
                else:
                    self.pool_state[p2_singleton_puzzle_hash]["pool_config"] = pool_config

                pool_state = self.pool_state[p2_singleton_puzzle_hash]

                # Skip state update when self pooling
                if pool_config.pool_url == "":
                    continue

                enforce_https = config["full_node"]["selected_network"] == "mainnet"
                if enforce_https and not pool_config.pool_url.startswith("https://"):
                    self.log.error(f"Pool URLs must be HTTPS on mainnet {pool_config.pool_url}")
                    continue
```

**File:** chia/farmer/farmer.py (L619-623)
```python
                    if pool_info_result is not None and pool_info_result.new_pool_url is not None:
                        with PoolingShareState.acquire(
                            root_path=self._root_path, p2_singleton_puzzle_hash=p2_singleton_puzzle_hash
                        ) as editable_pool_config:
                            editable_pool_config.pool_url = pool_info_result.new_pool_url
```

**File:** chia/farmer/farmer_api.py (L365-370)
```python
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"{pool_url}/partial",
                            json=post_partial_request.to_json_dict(),
                            ssl=ssl_context_for_root(get_mozilla_ca_crt(), log=self.farmer.log),
                            headers={
```

**File:** chia/_tests/farmer_harvester/test_farmer.py (L1262-1270)
```python
    mock_http_get = mocker.patch("aiohttp.ClientSession.get", return_value=case.pool_response)

    await farmer_service._node.update_pool_state()

    mock_http_get.assert_called_once()
    with PoolingShareState.acquire(
        root_path=farmer_service.root_path, p2_singleton_puzzle_hash=p2_singleton_puzzle_hash
    ) as pool_config:
        assert pool_config.pool_url == case.expected_pool_url_in_config
```
