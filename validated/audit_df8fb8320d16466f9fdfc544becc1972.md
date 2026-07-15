The code confirms the vulnerability claim. Let me trace through the exact evidence.

**`pending_solver_requests` is never cleared by the cache task.**

In `farmer.py`, `pending_solver_requests` is declared as `dict[bytes, dict[str, Any]]` — keyed by raw `bytes`, not `bytes32`: [1](#0-0) 

`cache_add_time` is `dict[bytes32, uint64]` and only ever receives `sp_hash` (`bytes32`) keys. The cache-clearing task iterates only over `cache_add_time` and clears `sps`, `proofs_of_space`, `quality_str_to_identifiers`, and `number_of_responses` — `pending_solver_requests` is absent: [2](#0-1) 

**`partial_proofs` inserts into `pending_solver_requests` with no eviction path when the solver is silent.**

Each `partial_proof` in the message becomes a key `bytes(partial_proof)` inserted unconditionally. The only removal paths are: (a) `solution_response` pops the key when a solver replies, and (b) the `except` block removes it only if the `send_to_all` call itself raises — not if the solver simply never responds: [3](#0-2) 

**There is no per-SP rate limit in `partial_proofs`.**

`new_proof_of_space` has a `max_pos_per_sp = 5` guard and increments `number_of_responses`. `partial_proofs` has neither — it only checks that `sp_hash` is in `self.farmer.sps` (a valid, currently-known signage point), then loops over every entry in `partial_proof_data.partial_proofs` without any cap: [4](#0-3) 

**Attack path is concrete.**

A malicious harvester (any node that can open a TCP connection to the farmer's harvester port — the farmer does not authenticate harvester identity beyond TLS) sends repeated `PartialProofsData` messages for a valid, live `sp_hash` (which the farmer broadcasts to all harvesters). Each message carries N unique `partial_proof` byte strings. With no solver connected or a solver that never replies, every entry accumulates in `pending_solver_requests` permanently. After the cache-clear interval passes, `sps` is evicted but `pending_solver_requests` retains all entries. Across signage points the dict grows without bound.

---

### Title
Unbounded `pending_solver_requests` growth via unauthenticated harvester flooding `partial_proofs` — (`chia/farmer/farmer_api.py`)

### Summary
A malicious harvester can exhaust farmer memory by flooding `partial_proofs` messages with unique partial proof bytes for valid signage points. Because `pending_solver_requests` is never tracked in `cache_add_time` and never cleared by `_periodically_clear_cache_and_refresh_task`, entries accumulate indefinitely when no solver response arrives, eventually crashing the farmer process and permanently preventing legitimate proof submission.

### Finding Description
`FarmerAPI.partial_proofs` (lines 518–545 of `farmer_api.py`) inserts one entry into `self.farmer.pending_solver_requests` per `partial_proof` element, keyed by `bytes(partial_proof)`. The only removal path is `solution_response` (which pops the key on solver reply) or an exception during `send_to_all`. If no solver is connected or the solver is unresponsive, entries are never removed.

`Farmer._periodically_clear_cache_and_refresh_task` (lines 854–875 of `farmer.py`) iterates `cache_add_time` and clears `sps`, `proofs_of_space`, `quality_str_to_identifiers`, and `number_of_responses`. `pending_solver_requests` is not in this list and is not tracked in `cache_add_time` (its keys are raw `bytes`, not `bytes32`).

There is no per-SP rate limit in `partial_proofs` (unlike `new_proof_of_space` which caps at 5 responses per SP).

### Impact Explanation
Memory exhaustion crashes the farmer process. A crashed farmer cannot submit proofs of space to the full node, causing a permanent inability to win blocks for the duration of the attack. This satisfies: *"High: Permanent or long-lived inability for honest… farmers… to process valid blocks… under normal network assumptions."*

### Likelihood Explanation
Any node that can open a TCP connection to the farmer's harvester port can send `PartialProofsData` messages. The only prerequisite is that a valid `sp_hash` is known — the farmer broadcasts signage points to all connected harvesters, so a connected attacker trivially knows current valid hashes. The attack is amplified by embedding many unique `partial_proof` entries per message.

### Recommendation
1. Track `pending_solver_requests` entries with a timestamp and evict them in `_periodically_clear_cache_and_refresh_task` (e.g., after `SUB_SLOT_TIME_TARGET * 3` seconds).
2. Add a per-SP cap on the number of pending solver requests (analogous to `max_pos_per_sp` in `new_proof_of_space`).
3. Enforce a per-connection rate limit on `partial_proofs` messages.

### Proof of Concept
```python
# Pseudocode test plan
# 1. Start farmer with no solver connected.
# 2. Connect a mock harvester.
# 3. Inject a valid sp_hash (received from farmer's broadcast).
# 4. Send N PartialProofsData messages, each with M unique partial_proof bytes,
#    all referencing the valid sp_hash.
# 5. Wait for cache-clear interval (SUB_SLOT_TIME_TARGET seconds).
# 6. Assert len(farmer.pending_solver_requests) == N * M  (not 0).
# 7. Repeat across multiple signage points; observe monotonic growth.
```

### Citations

**File:** chia/farmer/farmer.py (L146-154)
```python
        # Track pending solver requests, keyed by partial proof
        self.pending_solver_requests: dict[bytes, dict[str, Any]] = {}

        # number of responses to each signage point
        self.number_of_responses: dict[bytes32, int] = {}

        # A dictionary of keys to time added. These keys refer to keys in the above 4 dictionaries. This is used
        # to periodically clear the memory
        self.cache_add_time: dict[bytes32, uint64] = {}
```

**File:** chia/farmer/farmer.py (L854-875)
```python
    async def _periodically_clear_cache_and_refresh_task(self) -> None:
        time_slept = 0
        refresh_slept = 0
        while not self._shut_down:
            try:
                if time_slept > self.constants.SUB_SLOT_TIME_TARGET:
                    now = time.time()
                    removed_keys: list[bytes32] = []
                    for key, add_time in self.cache_add_time.items():
                        if now - float(add_time) > self.constants.SUB_SLOT_TIME_TARGET * 3:
                            self.sps.pop(key, None)
                            self.proofs_of_space.pop(key, None)
                            self.quality_str_to_identifiers.pop(key, None)
                            self.number_of_responses.pop(key, None)
                            removed_keys.append(key)
                    for key in removed_keys:
                        self.cache_add_time.pop(key, None)
                    time_slept = 0
                    log.debug(
                        f"Cleared farmer cache. Num sps: {len(self.sps)} {len(self.proofs_of_space)} "
                        f"{len(self.quality_str_to_identifiers)} {len(self.number_of_responses)}"
                    )
```

**File:** chia/farmer/farmer_api.py (L500-516)
```python
        if partial_proof_data.sp_hash not in self.farmer.number_of_responses:
            self.farmer.number_of_responses[partial_proof_data.sp_hash] = 0
            self.farmer.cache_add_time[partial_proof_data.sp_hash] = uint64(time.time())

        if partial_proof_data.sp_hash not in self.farmer.sps:
            self.farmer.log.warning(
                f"Received partial proofs for a signage point that we do not have {partial_proof_data.sp_hash}"
            )
            return None

        self.farmer.cache_add_time[partial_proof_data.sp_hash] = uint64(time.time())

        self.farmer.log.info(
            f"Received V2 partial proof collection with {len(partial_proof_data.partial_proofs)} partail proofs "
            f"for plot {partial_proof_data.plot_identifier[:10]}... from {peer.peer_node_id}"
        )

```

**File:** chia/farmer/farmer_api.py (L518-545)
```python
        for partial_proof in partial_proof_data.partial_proofs:
            solver_info = SolverInfo(
                partial_proof=partial_proof,
                plot_id=partial_proof_data.plot_id,
                strength=partial_proof_data.strength,
                size=partial_proof_data.plot_size,
            )

            key = bytes(partial_proof)
            try:
                # store pending request data for matching with response
                self.farmer.pending_solver_requests[key] = {
                    "proof_data": partial_proof_data,
                    "peer": peer,
                }

                # send solve request to all solver connections
                msg = make_msg(ProtocolMessageTypes.solve, solver_info)
                await self.farmer.server.send_to_all([msg], NodeType.SOLVER)
                self.farmer.log.debug(f"Sent solve request for partial proof {partial_proof.fragments[:5]}...")

            except Exception as e:
                self.farmer.log.error(
                    f"Failed to call solver service for partial proof {partial_proof.fragments[:5]}...: {e}"
                )
                # clean up pending request
                if key in self.farmer.pending_solver_requests:
                    del self.farmer.pending_solver_requests[key]
```
