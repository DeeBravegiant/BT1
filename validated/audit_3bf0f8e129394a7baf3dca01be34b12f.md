### Title
Missing Cross-Party Public Key Consistency Check in 2-Party Schnorr Refresh — (`src/cbmpc/protocol/ec_dkg.cpp`)

### Summary

`key_share_2p_t::refresh` copies `Q` from each party's local blob without any cross-party verification that both parties hold the same `Q`. A Byzantine P2 supplying a blob from a different DKG session causes the protocol to complete successfully, leaving P1 and P2 with refreshed blobs encoding different public keys.

### Finding Description

The call chain is:

```
cbmpc_schnorr_2p_refresh          (src/cbmpc/c_api/schnorr2pc.cpp:46-72)
  → coinbase::api::schnorr_2p::refresh  (src/cbmpc/api/schnorr2pc.cpp:79-99)
    → key_share_2p_t::refresh           (src/cbmpc/protocol/ec_dkg.cpp:91-111)
```

Inside `key_share_2p_t::refresh`, each party independently copies `Q` from its own local blob:

```cpp
new_key.Q = key.Q;   // line 95 — local blob only, no peer check
``` [1](#0-0) 

The only interactive step is `agree_random`, which generates a shared random scalar `r` from fresh entropy:

```cpp
if (rv = agree_random(job, q.get_bits_count() + SEC_P_STAT, rand_bits)) return rv;
``` [2](#0-1) 

`agree_random` takes only `job` and `bitlen`; it commits to P1's random contribution but does **not** bind to `Q`, the curve, or any session identifier:

```cpp
error_t agree_random(job_2p_t& job, int bitlen, buf_t& out) {
  ...
  coinbase::crypto::commitment_t com(sender_pid);   // no Q in domain
  ...
  out = mem_t(r1) ^ mem_t(r2);
``` [3](#0-2) 

There is no subsequent step that broadcasts or compares `Q` values between the two parties. The protocol returns `SUCCESS` regardless of whether P1 and P2 started from blobs belonging to the same DKG session.

The API layer (`coinbase::api::schnorr_2p::refresh`) validates each blob individually — role, curve, scalar range, and point decompression — but performs no cross-party `Q` equality check: [4](#0-3) 

### Impact Explanation

**Attack setup:**
- Session A: P1 holds `(x1_A, Q_A)`, P2 holds `(x2_A, Q_A)` where `Q_A = (x1_A + x2_A)·G`
- Session B: P2 holds `(x2_B, Q_B)` where `Q_B = (x1_B + x2_B)·G`

**During refresh:**
- `agree_random` produces a shared `r` (no Q binding)
- P1 outputs: `new_x_share = x1_A + r`, `new_Q = Q_A`
- P2 outputs: `new_x_share = x2_B − r`, `new_Q = Q_B`

Both new blobs pass `deserialize_key_blob` validation (valid curve point, scalar in range). The protocol returns `SUCCESS` to both parties.

**Post-refresh state:**
- P1 believes the refreshed public key is `Q_A`
- P2 believes the refreshed public key is `Q_B`
- The actual combined scalar is `x1_A + x2_B`, whose public key `(x1_A + x2_B)·G` is neither `Q_A` nor `Q_B`

This is a concrete instance of the High impact category: attacker-controlled key material from the wrong session is accepted by the refresh protocol, producing refreshed blobs that encode different public keys — accepted invalid cryptographic output.

### Likelihood Explanation

P2 is a required participant in the 2-party refresh protocol but cannot sign alone (threshold = 2), so this is a Byzantine-below-threshold attacker. The attack requires only that P2 possess any valid key blob from any prior DKG session on the same curve, which is a realistic precondition. No cryptographic break is needed; the attacker simply supplies a structurally valid blob with a different `Q`.

### Recommendation

Before executing `agree_random`, both parties should commit to and verify each other's `Q`. A minimal fix is to include `Q` in the `agree_random` transcript or to add an explicit cross-party consistency round:

1. Each party broadcasts `H(Q)` (or `Q` itself) at the start of refresh.
2. Each party verifies the received value equals its own `H(Q)` before proceeding.
3. Alternatively, include `Q_compressed` as a domain-separation label in the `agree_random` commitment, so a mismatch causes the commitment verification to fail on P2's side.

The multi-party `key_share_mp_t::refresh` already demonstrates the correct pattern — it hashes `sid`, `current_key.Q`, and `current_key.Qis` into `h_consistency` and broadcasts it for cross-party verification before any randomness is generated:

```cpp
h_consistency._i = crypto::sha256_t::hash(sid, current_key.Q, current_key.Qis);
``` [5](#0-4) 

The 2-party refresh should adopt the same pattern.

### Proof of Concept

```
1. Run DKG session A → P1 gets blob_A1 (x1_A, Q_A), P2 gets blob_A2 (x2_A, Q_A)
2. Run DKG session B → P2 gets blob_B2 (x2_B, Q_B) where Q_B ≠ Q_A
3. Call cbmpc_schnorr_2p_refresh:
     P1 supplies blob_A1  (honest, from session A)
     P2 supplies blob_B2  (Byzantine, from session B)
4. agree_random produces shared r (no Q binding → succeeds)
5. P1 receives new_blob with Q = Q_A, x_share = x1_A + r  → SUCCESS
6. P2 receives new_blob with Q = Q_B, x_share = x2_B − r  → SUCCESS
7. Assert: get_public_key(new_blob_P1) ≠ get_public_key(new_blob_P2)
   → Both assertions pass; both parties hold "valid" refreshed blobs
     encoding different public keys.
```

### Citations

**File:** src/cbmpc/protocol/ec_dkg.cpp (L91-111)
```cpp
error_t key_share_2p_t::refresh(job_2p_t& job, const key_share_2p_t& key, key_share_2p_t& new_key) {
  error_t rv = UNINITIALIZED_ERROR;
  new_key.role = key.role;
  new_key.curve = key.curve;
  new_key.Q = key.Q;

  const mod_t& q = key.curve.order();
  buf_t rand_bits;
  if (rv = agree_random(job, q.get_bits_count() + SEC_P_STAT, rand_bits)) return rv;
  bn_t r = bn_t::from_bin(rand_bits) % q;

  if (job.is_p1()) {
    MODULO(q) { new_key.x_share = key.x_share + r; }
  }

  if (job.is_p2()) {
    MODULO(q) { new_key.x_share = key.x_share - r; }
  }

  return SUCCESS;
}
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L188-213)
```cpp
  auto h_consistency = job.uniform_msg<buf256_t>();
  h_consistency._i = crypto::sha256_t::hash(sid, current_key.Q, current_key.Qis);

  new_key = current_key;

  auto r = job.nonuniform_msg<bn_t>();
  auto R = job.uniform_msg<std::vector<ecc_point_t>>(std::vector<ecc_point_t>(n));
  auto pi_r = job.uniform_msg<std::vector<zk::uc_dl_t>>(std::vector<zk::uc_dl_t>(n));
  for (int j = 0; j < n; j++) {
    r._ij = bn_t::rand(q);
    R._i[j] = r._ij * G;
    pi_r._i[j].prove(R._i[j], r._ij, sid, i * n + j);
  }

  crypto::commitment_t com_R(sid, pid);
  auto c = job.uniform_msg<buf256_t>();
  auto rho = job.uniform_msg<buf256_t>();
  com_R.gen(R.msg, pi_r.msg);
  c._i = com_R.msg;     // c_i
  rho._i = com_R.rand;  // rho_i
  if (rv = job.plain_broadcast(c, h_consistency)) return rv;

  for (int j = 0; j < n; j++) {
    if (j == i) continue;
    if (h_consistency._j != h_consistency) return coinbase::error(E_CRYPTO);
  }
```

**File:** src/cbmpc/protocol/agree_random.cpp (L7-35)
```cpp
error_t agree_random(job_2p_t& job, int bitlen, buf_t& out) {
  error_t rv = UNINITIALIZED_ERROR;
  buf_t r1, r2;
  const crypto::mpc_pid_t& sender_pid = job.get_pid(party_t::p1);
  coinbase::crypto::commitment_t com(sender_pid);

  if (job.is_p1()) {
    r1 = crypto::gen_random_bitlen(bitlen);
    com.gen(r1);
  }

  if (rv = job.p1_to_p2(com.msg)) return rv;

  if (job.is_p2()) {
    r2 = crypto::gen_random_bitlen(bitlen);
  }

  if (rv = job.p2_to_p1(r2)) return rv;
  if (rv = job.p1_to_p2(r1, com.rand)) return rv;

  if (job.is_p2()) {
    if (rv = com.open(r1)) return rv;
  }

  if (r1.size() != coinbase::bits_to_bytes(bitlen)) return coinbase::error(E_CRYPTO);
  if (r2.size() != coinbase::bits_to_bytes(bitlen)) return coinbase::error(E_CRYPTO);

  out = mem_t(r1) ^ mem_t(r2);
  return SUCCESS;
```

**File:** src/cbmpc/api/schnorr2pc.cpp (L79-99)
```cpp
error_t refresh(const coinbase::api::job_2p_t& job, mem_t key_blob, buf_t& new_key_blob) {
  if (const error_t rv = validate_job_2p(job)) return rv;
  if (const error_t rv = coinbase::api::detail::validate_mem_arg_max_size(key_blob, "key_blob",
                                                                          coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;
  coinbase::mpc::schnorr2p::key_t key;
  error_t rv = deserialize_key_blob(key_blob, key);
  if (rv) return rv;

  const auto self = to_internal_party(job.self);
  if (key.role != self) return coinbase::error(E_BADARG, "job.self mismatch key blob role");

  coinbase::mpc::job_2p_t mpc_job = to_internal_job(job);

  coinbase::mpc::schnorr2p::key_t new_key;
  new_key_blob.free();
  rv = coinbase::mpc::eckey::key_share_2p_t::refresh(mpc_job, key, new_key);
  if (rv) return rv;

  return serialize_key_blob(new_key, new_key_blob);
}
```
