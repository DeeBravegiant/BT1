### Title
AND-node additive share reconstruction uses last-write assignment instead of accumulation, producing incorrect key shares for parties appearing in multiple AND children — (File: `src/cbmpc/protocol/ec_dkg.cpp`)

### Summary
`reconstruct_additive_share` and `reconstruct_pub_additive_shares` both use **assignment** (`=`) instead of **accumulation** (`+=`) when aggregating a party's contribution across children of an AND node. For any access structure where a single party leaf appears under more than one child of an AND node, the party's reconstructed additive share silently drops all but the last non-zero child contribution. Because the public-share function carries the identical defect, the internal consistency check `x_share * G == Qi` still passes, so the corrupted share is accepted without error and propagates into the signing path, producing an invalid signature returned as `SUCCESS`.

### Finding Description

In `reconstruct_additive_share` the AND-node branch iterates over children and writes:

```cpp
// src/cbmpc/protocol/ec_dkg.cpp  lines 512-528
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
      additive_share = additive_share_from_child;   // ← assignment, not +=
    }
  }
  break;
``` [1](#0-0) 

An AND node splits the secret additively: `x = x₁ + x₂ + … + xₙ`. A party whose leaf appears under *k* children of the AND node holds a non-zero share from each of those children; its correct additive contribution to the AND node is their **sum**. The code instead overwrites `additive_share` with each successive non-zero child value, so only the **last** child's contribution survives.

The mirror defect exists in `reconstruct_pub_additive_shares`:

```cpp
// src/cbmpc/protocol/ec_dkg.cpp  lines 621-638
case node_e::AND:
  pub_additive_shares = curve.infinity();
  is_satisfied = true;
  for (int i = 0; i < n; i++) {
    ecc_point_t additive_share_from_child = curve.infinity();
    ...
    if (!additive_share_from_child.is_infinity()) {
      pub_additive_shares = additive_share_from_child;  // ← assignment, not +=
    }
  }
  break;
``` [2](#0-1) 

Because both the scalar and the EC-point reconstruction drop contributions identically, the per-party check `x_share * G == Qi` still holds for the corrupted values, masking the error.

`to_additive_share` (the public entry point) calls both functions and stores the results without any further cross-check:

```cpp
// src/cbmpc/protocol/ec_dkg.cpp  lines 695-724
if (rv = reconstruct_additive_share(q, ac.root, quorum_names, new_x_share, ...)) return rv;
...
if (rv = reconstruct_pub_additive_shares(ac.root, quorum_names, ..., new_Qi, ...)) return rv;
additive_share.x_share = new_x_share;
additive_share.Qis[...] = new_Qi;
``` [3](#0-2) 

The corrupted `additive_share` is then consumed by the AC-based signing protocols (`sign_ac` for ECDSA-MP, EdDSA-MP, Schnorr-MP) reachable through the public C++ and C APIs.

### Impact Explanation

For any access structure of the form `AND(child_A_containing_pᵢ, child_B_containing_pᵢ)` — e.g. `AND(THRESHOLD[1](p0,p1), THRESHOLD[1](p0,p2))` — party `p0` holds non-zero shares from both children. The reconstructed additive share equals only the second child's contribution; the first is silently discarded. The signing protocol proceeds with this wrong share, computes a partial signature over the wrong effective private scalar, and the combined output is an invalid ECDSA/EdDSA/Schnorr signature. No error is returned; the caller receives `SUCCESS` with cryptographically invalid output. This satisfies the **High** impact criterion: *public API reachable access-structure reconstruction creates accepted invalid cryptographic output*.

### Likelihood Explanation

The trigger condition — a party leaf appearing under more than one child of an AND node — is a natural and documented feature of general access structures (e.g. `AND(OR(p0,p1), OR(p0,p2))`). Any caller who constructs such a structure via `dkg_ac` and later calls `sign_ac` will silently receive a broken signature. No adversarial intent is required; a legitimate multi-party deployment with overlapping quorum sets is sufficient.

### Recommendation

Replace the last-write assignment with modular accumulation in both functions:

**`reconstruct_additive_share` AND branch:**
```cpp
// Replace:
if (additive_share_from_child != 0) {
  additive_share = additive_share_from_child;
}
// With:
MODULO(q) additive_share += additive_share_from_child;
```

**`reconstruct_pub_additive_shares` AND branch:**
```cpp
// Replace:
if (!additive_share_from_child.is_infinity()) {
  pub_additive_shares = additive_share_from_child;
}
// With:
pub_additive_shares = pub_additive_shares + additive_share_from_child;
```

After the fix, add a cross-check in `to_additive_share` that `SUM(new_Qis) == Q` to catch any future regression.

### Proof of Concept

1. Construct access structure `AND(THRESHOLD[1](p0,p1), THRESHOLD[1](p0,p2))` with parties `{p0, p1, p2}`.
2. Run `dkg_ac` — the DKG correctly splits the secret `x = x₁ + x₂`; party `p0` receives share `s₁` from child 1 and `s₂` from child 2, so `p0`'s correct additive share is `s₁ + s₂`.
3. Call `sign_ac` with quorum `{p0, p1, p2}`. Internally `to_additive_share` → `reconstruct_additive_share` iterates AND children: after child 1, `additive_share = s₁`; after child 2, `additive_share = s₂` (overwrites). Party `p0` signs with scalar `s₂` instead of `s₁ + s₂`.
4. `reconstruct_pub_additive_shares` makes the same error, so `s₂ * G == Qi` passes; no error is raised.
5. The returned signature fails external ECDSA/EdDSA/Schnorr verification, yet the API returned `SUCCESS`.

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

**File:** src/cbmpc/protocol/ec_dkg.cpp (L695-724)
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
```
