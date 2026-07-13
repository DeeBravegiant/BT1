### Title
AND-Node Additive Share Overwrite Instead of Accumulate in `reconstruct_additive_share` / `reconstruct_pub_additive_shares` - (File: src/cbmpc/protocol/ec_dkg.cpp)

### Summary
In `key_share_mp_t::reconstruct_additive_share` and `key_share_mp_t::reconstruct_pub_additive_shares`, the AND-node branch of the recursive loop assigns (`=`) the child's contribution to the running total instead of accumulating (`+=`). This is the exact same bug class as the reported Kryptonite finding: a loop variable that must be a running sum is silently overwritten on each iteration. The result is that `to_additive_share` — a public API method — emits a structurally invalid additive key-share whenever the access structure contains an AND node whose children each carry a non-zero contribution for the calling party.

### Finding Description

`key_share_mp_t::reconstruct_additive_share` walks the access-structure tree recursively and is supposed to return the party's additive scalar share for the subtree rooted at `node`. For an AND node the secret is split additively across all children, so the correct reconstruction is:

```
additive_share = sum over all children of (child's additive share)
```

The actual code at lines 512–528 does:

```cpp
case node_e::AND:
  additive_share = 0;
  is_satisfied = true;
  for (int i = 0; i < n; i++) {
    bn_t additive_share_from_child;
    bool child_is_satisfied = false;
    rv = reconstruct_additive_share(q, node->children[i], quorum_names,
                                    additive_share_from_child, child_is_satisfied);
    is_satisfied = is_satisfied && child_is_satisfied;
    if (rv) return rv;
    if (additive_share_from_child != 0) {
      additive_share = additive_share_from_child;   // ← overwrites, not +=
    }
  }
  break;
```

The fix is `MODULO(q) additive_share += additive_share_from_child`.

The identical structural bug exists in `reconstruct_pub_additive_shares` at lines 621–638 for the AND-node branch:

```cpp
if (!additive_share_from_child.is_infinity()) {
  pub_additive_shares = additive_share_from_child;  // ← overwrites, not +=
}
```

Both functions are called exclusively from `to_additive_share` (lines 695–725), which is a **public** method on `key_share_mp_t`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

`to_additive_share` is the bridge between an access-structure key-share (`key_share_mp_t` produced by `dkg_ac` / `refresh_ac`) and the additive-share form required by the signing protocols. It writes the result into a fresh `key_share_mp_t` whose `x_share` and `Qis` fields are consumed directly by downstream ECDSA/EdDSA/Schnorr signing steps.

Because both the scalar share (`x_share`) and the per-party public shares (`Qis`) are computed with the same overwrite bug, the two outputs are internally consistent with each other but inconsistent with the true key. Specifically:

- The scalar `new_x_share` equals only the last non-zero child's Lagrange-weighted contribution, not the sum of all children's contributions.
- Each `new_Qi` equals only the last non-zero child's EC-point contribution, not the sum.

Any ZK proof that a party generates inside the signing protocol (proving knowledge of `x_share` against `Qi`) will verify correctly against the wrong `Qi`, so the protocol's per-round verification passes. The final aggregated signature, however, will not verify against the honest public key `Q` (which is copied unchanged from the original key-share). The protocol therefore emits a structurally accepted but cryptographically invalid signature output — matching the "accepted invalid cryptographic output" criterion.

In a scenario where the AND node has two or more children that each carry a non-zero contribution for the same party (e.g., a party appears as a leaf in multiple branches of an AND node, or a nested AND/threshold structure routes non-zero weight through multiple children), the discarded contributions are silently lost. A single malicious peer who can influence the ordering of children in the AND node can ensure its own contribution is always "last" and therefore the only one retained, effectively substituting its chosen scalar for the honest sum. [4](#0-3) [5](#0-4) 

### Likelihood Explanation

The bug is triggered whenever:
1. The caller uses `dkg_ac` or `refresh_ac` to produce a key-share under an access structure that contains at least one AND node, **and**
2. The party whose share is being converted appears with non-zero weight in two or more children of that AND node.

Both `dkg_ac` and `refresh_ac` are public static methods. `to_additive_share` is a public instance method. No threshold collusion or key leakage is required; a single honest party calling the public API with a valid AND-structured access structure is sufficient to trigger the incorrect output. [6](#0-5) 

### Recommendation

In `reconstruct_additive_share`, replace the assignment in the AND-node branch:

```cpp
// Before (buggy)
if (additive_share_from_child != 0) {
  additive_share = additive_share_from_child;
}

// After (correct)
MODULO(q) additive_share += additive_share_from_child;
```

In `reconstruct_pub_additive_shares`, replace the assignment in the AND-node branch:

```cpp
// Before (buggy)
if (!additive_share_from_child.is_infinity()) {
  pub_additive_shares = additive_share_from_child;
}

// After (correct)
pub_additive_shares = pub_additive_shares + additive_share_from_child;
```

Add a test with an AND access structure where the same party leaf appears under two children and verify that `to_additive_share` produces shares that sum to the original private key.

### Proof of Concept

Consider a 3-party setup with access structure `AND(leaf_A, leaf_B)` where party A holds shares in both children (e.g., a nested structure where A is a leaf in both branches):

1. Run `dkg_ac` to produce key-shares for all parties.
2. Party A calls `to_additive_share` with a quorum that satisfies the AND node.
3. Inside `reconstruct_additive_share`, the AND-node loop iterates over both children. Child 0 returns `share_0 ≠ 0` for party A; child 1 returns `share_1 ≠ 0`. After the loop, `additive_share = share_1` (only the last non-zero), not `share_0 + share_1`.
4. The returned `key_share_mp_t` has `x_share = share_1` and `Qi_A = share_1 * G`.
5. Party A's ZK proof in the signing protocol verifies against `Qi_A` (consistent), but the aggregate `sum(x_shares)` ≠ private key `x`, so the produced signature fails external verification.
6. A malicious party controlling child ordering can ensure its own contribution is always "last," substituting an arbitrary scalar for the honest sum. [1](#0-0) [2](#0-1)

### Citations

**File:** src/cbmpc/protocol/ec_dkg.cpp (L512-528)
```cpp
    case node_e::AND:
      additive_share = 0;
      is_satisfied = true;
      for (int i = 0; i < n; i++) {
        bn_t additive_share_from_child;
        bool child_is_satisfied = false;
        rv = reconstruct_additive_share(q, node->children[i], quorum_names, additive_share_from_child,
                                        child_is_satisfied);
        is_satisfied = is_satisfied && child_is_satisfied;
        if (rv) {
          return rv;
        }
        if (additive_share_from_child != 0) {
          additive_share = additive_share_from_child;
        }
      }
      break;
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L621-638)
```cpp
    case node_e::AND:
      pub_additive_shares = curve.infinity();
      is_satisfied = true;
      for (int i = 0; i < n; i++) {
        ecc_point_t additive_share_from_child = curve.infinity();
        bool child_is_satisfied = false;
        rv = reconstruct_pub_additive_shares(node->children[i], quorum_names, target, additive_share_from_child,
                                             child_is_satisfied);
        is_satisfied = is_satisfied && child_is_satisfied;
        if (rv) {
          return rv;
        }

        if (!additive_share_from_child.is_infinity()) {
          pub_additive_shares = additive_share_from_child;
        }
      }
      break;
```

**File:** src/cbmpc/protocol/ec_dkg.cpp (L695-725)
```cpp
error_t key_share_mp_t::to_additive_share(const crypto::ss::ac_t ac, const std::set<crypto::pname_t>& quorum_names,
                                          key_share_mp_t& additive_share) {
  if (!ac.enough_for_quorum(quorum_names)) {
    return coinbase::error(E_INSUFFICIENT);
  }
  error_t rv = UNINITIALIZED_ERROR;
  const mod_t& q = curve.order();
  bn_t new_x_share;
  bool _ignore_is_satisfied = false;
  if (rv = reconstruct_additive_share(q, ac.root, quorum_names, new_x_share, _ignore_is_satisfied)) return rv;

  party_map_t<ecc_point_t> new_Qis;
  std::vector<crypto::pname_t> quorum_names_vec(quorum_names.begin(), quorum_names.end());

  for (size_t j = 0; j < quorum_names_vec.size(); j++) {
    crypto::vartime_scope_t vartime_scope;
    bool _ignore_is_satisfied = false;
    ecc_point_t new_Qi;
    if (rv = reconstruct_pub_additive_shares(ac.root, quorum_names, quorum_names_vec[j], new_Qi, _ignore_is_satisfied))
      return rv;
    new_Qis[quorum_names_vec[j]] = new_Qi;
  }

  additive_share.x_share = new_x_share;
  additive_share.Q = Q;
  additive_share.Qis = new_Qis;
  additive_share.curve = curve;
  additive_share.party_name = party_name;

  return SUCCESS;
}
```

**File:** include-internal/cbmpc/internal/protocol/ec_dkg.h (L74-76)
```text
  error_t to_additive_share(const crypto::ss::ac_t ac, const std::set<crypto::pname_t>& quorum_names,
                            key_share_mp_t& additive_share);

```

**File:** include-internal/cbmpc/internal/protocol/ec_dkg.h (L98-107)
```text
  static error_t dkg_ac(job_mp_t& job, const ecurve_t& curve, buf_t& sid, const crypto::ss::ac_t,
                        const party_set_t& quorum_party_set, key_share_mp_t& key);
  /**
   * @specs:
   * - ec-dkg-spec | EC-Refresh-Threshold-MP
   * @notes:
   * - See `dkg` for notes.
   */
  static error_t refresh_ac(job_mp_t& job, const ecurve_t& curve, buf_t& sid, const crypto::ss::ac_t,
                            const party_set_t& quorum_party_set, key_share_mp_t& key, key_share_mp_t& new_key);
```
