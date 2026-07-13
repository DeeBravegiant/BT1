The code trace is complete. Here is the full analysis.

---

### Title
Missing `Q == reconstruct_exponent(Qis)` Consistency Check in Job-Bound AC Deserializer Enables Persistent Public-Key Substitution Through Refresh - (`src/cbmpc/api/ecdsa_mp.cpp`)

### Summary

The job-bound `deserialize_ac_key_blob` (lines 189–246) omits the `Q == reconstruct_exponent(Qis)` consistency check that the additive deserializer enforces. Because `dkg_or_refresh_ac` uses `key.Q` as the authoritative reference at line 448 of `ec_dkg.cpp`, an attacker who controls their own key blob can craft it so that `Q_compressed` encodes an arbitrary `Q'` while arranging the stored `Qis` to satisfy `reconstruct_exponent(Qis) = Q'`. After a successful refresh the new blob's `Q` is still `Q'`, and the substitution survives every subsequent refresh cycle.

### Finding Description

**Additive deserializer (lines 172–174) — has the guard:**

```cpp
coinbase::crypto::ecc_point_t Q_sum = curve.infinity();
for (const auto& kv : Qis) Q_sum += kv.second;
if (Q != Q_sum) return coinbase::error(E_FORMAT, "invalid key blob");
``` [1](#0-0) 

**Job-bound AC deserializer (lines 233–238) — guard is absent:**

```cpp
// Access-structure key blobs are validated using the access structure at use sites.
// Here we only enforce the self-share binding.
const auto& G = curve.generator();
...
if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");
``` [2](#0-1) 

The comment promises that use-sites will validate the `Q`/`Qis` relationship, but the use-site (`dkg_or_refresh_ac`) never performs that check independently — it uses `key.Q` itself as the reference:

```cpp
if (reconstructed_Q != key.Q) return coinbase::error(E_CRYPTO, "key.Q mismatch");
``` [3](#0-2) 

**Refresh path (lines 437–448) — `new_key.Q` is never recomputed:**

```cpp
if (is_refresh) {
    new_key = key;                          // copies key.Q = Q'
    MODULO(q) new_key.x_share += x_i;
    for (int j = 0; j < n; j++) {
        new_key.Qis[job.get_name(j)] += Qis[job.get_name(j)];
    }
    ecc_point_t reconstructed_Q;
    if (rv = ac.reconstruct_exponent(new_key.Qis, reconstructed_Q)) ...
    if (reconstructed_Q != key.Q) return ...   // circular: key.Q is attacker-supplied Q'
    new_key.party_name = job.get_name(i);
}
``` [4](#0-3) 

`new_key.Q` is never rewritten in the refresh branch; it inherits `key.Q = Q'` from the `new_key = key` copy.

**Attack construction:**

1. Attacker picks arbitrary target public key `Q'`.
2. Crafts blob with:
   - `x_share` = any valid scalar `s`
   - `Qi_self = s·G` (satisfies the only enforced check)
   - Remaining `Qis` chosen so that `reconstruct_exponent(Qis) = Q'` (feasible since the attacker controls all other shares)
   - `Q_compressed` = encoding of `Q'`
3. `deserialize_ac_key_blob` passes (only `x_share·G == Qi_self` is checked).
4. In `dkg_or_refresh_ac`, the refresh protocol generates additive-zero refresh shares; `reconstruct_exponent(refresh_Qis) = ∞`.
5. Therefore `reconstruct_exponent(new_key.Qis) = reconstruct_exponent(old_Qis) + ∞ = Q'`.
6. Line 448 check: `Q' != Q'` → false → passes.
7. `new_key.Q = Q'` is serialized into the output blob.

The call chain is:

```
refresh_ac (ecdsa_mp.cpp:359)
  → deserialize_ac_key_blob(job, key_blob, key)   // no Q==reconstruct check
  → mpc::ecdsampc::refresh_ac (ec_dkg.cpp:470)
    → dkg_or_refresh_ac                           // uses key.Q as reference at line 448
  → serialize_ac_key_blob(job, new_key, new_key_blob)  // writes Q' into new blob
``` [5](#0-4) 

### Impact Explanation

After the first tampered refresh, the attacker's key blob permanently records `Q = Q'`. Every subsequent call to `refresh_ac` with that blob repeats the same cycle: `deserialize_ac_key_blob` accepts it, `dkg_or_refresh_ac` validates the new Qis against `Q'`, and the output blob again carries `Q'`. Any downstream consumer that trusts `Q` from the blob (e.g., signature verification, key export, audit) operates against the attacker-chosen public key rather than the true threshold public key. This is a persistent, self-reinforcing public-key substitution reachable through the public `refresh_ac` API.

### Likelihood Explanation

The attacker only needs to control their own key blob — a normal precondition for any party calling `refresh_ac`. No threshold collusion, no compromise of other parties, and no special network position is required. The arithmetic to construct a valid crafted blob (choose `s`, set `Qi_self = s·G`, solve for remaining Qis) is straightforward ECC arithmetic.

### Recommendation

Add the `reconstruct_exponent(Qis) == Q` check to `deserialize_ac_key_blob` (job-bound), mirroring the additive deserializer's `Q == Q_sum` guard at lines 172–174. The comment at line 233 ("validated at use sites") is incorrect as written — the use-site check at line 448 is circular and cannot substitute for an upfront consistency check.

### Proof of Concept

```
1. Run dkg_ac honestly → obtain valid blob B with true_Q.
2. Deserialize B; extract x_share = s, Qi_self = s·G.
3. Choose Q' ≠ true_Q (e.g., a random curve point).
4. For a 2-of-3 Shamir AC: solve for Qi_other such that
       reconstruct_exponent({Qi_self, Qi_other, Qi_third}) = Q'
   (one degree of freedom; trivially solvable).
5. Re-serialize blob B' with Q_compressed = Q', Qis as crafted.
6. Call refresh_ac(job, sid, B', ac, quorum, new_blob).
7. Deserialize new_blob → assert new_blob.Q_compressed == encoding(Q').
8. Repeat step 6 with new_blob as input → Q' persists.
```

### Citations

**File:** src/cbmpc/api/ecdsa_mp.cpp (L172-174)
```cpp
  coinbase::crypto::ecc_point_t Q_sum = curve.infinity();
  for (const auto& kv : Qis) Q_sum += kv.second;
  if (Q != Q_sum) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L233-238)
```cpp
  // Access-structure key blobs are validated using the access structure at use sites.
  // Here we only enforce the self-share binding.
  const auto& G = curve.generator();
  const auto it_self = Qis.find(blob.party_name);
  if (it_self == Qis.end()) return coinbase::error(E_FORMAT, "invalid key blob");
  if (blob.x_share * G != it_self->second) return coinbase::error(E_FORMAT, "invalid key blob");
```

**File:** src/cbmpc/api/ecdsa_mp.cpp (L359-387)
```cpp
error_t refresh_ac(const job_mp_t& job, buf_t& sid, mem_t key_blob, const access_structure_t& access_structure,
                   const std::vector<std::string_view>& quorum_party_names, buf_t& new_key_blob) {
  error_t rv = validate_job_mp(job);
  if (rv) return rv;
  if (rv = coinbase::api::detail::validate_mem_arg_max_size(key_blob, "key_blob",
                                                            coinbase::api::detail::MAX_OPAQUE_BLOB_SIZE))
    return rv;

  coinbase::mpc::ecdsampc::key_t key;
  rv = deserialize_ac_key_blob(job, key_blob, key);
  if (rv) return rv;

  coinbase::crypto::ss::ac_owned_t ac;
  rv = coinbase::api::detail::to_internal_access_structure(access_structure, job.party_names, key.curve, ac);
  if (rv) return rv;

  coinbase::mpc::party_set_t quorum_party_set;
  rv = coinbase::api::detail::to_internal_party_set(job.party_names, quorum_party_names, quorum_party_set);
  if (rv) return rv;

  coinbase::mpc::job_mp_t mpc_job = to_internal_job(job);

  coinbase::mpc::ecdsampc::key_t new_key;
  new_key_blob.free();
  rv = coinbase::mpc::ecdsampc::refresh_ac(mpc_job, key.curve, sid, ac, quorum_party_set, key, new_key);
  if (rv) return rv;

  return serialize_ac_key_blob(job, new_key, new_key_blob);
}
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L437-449)
```cpp
  if (is_refresh) {
    new_key = key;

    MODULO(q) new_key.x_share += x_i;
    for (int j = 0; j < n; j++) {
      crypto::vartime_scope_t vartime_scope;
      new_key.Qis[job.get_name(j)] += Qis[job.get_name(j)];
    }
    ecc_point_t reconstructed_Q;
    if (rv = ac.reconstruct_exponent(new_key.Qis, reconstructed_Q))
      return coinbase::error(rv, "Failed to reconstruct exponent for new_key");
    if (reconstructed_Q != key.Q) return coinbase::error(E_CRYPTO, "key.Q mismatch");
    new_key.party_name = job.get_name(i);
```
