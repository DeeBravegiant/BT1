### Title
AND-node additive-share accumulation uses last-write assignment instead of modular addition, producing wrong key shares in `to_additive_share` — (File: src/cbmpc/protocol/ec_dkg.cpp)

### Summary
`reconstruct_additive_share` and `reconstruct_pub_additive_shares` both handle the `AND` node by **overwriting** the running total with the last non-zero child contribution instead of **accumulating** all children's contributions. When a party appears in more than one child of an AND node, the function silently drops all but the last non-zero child's share. The same assignment bug exists in the EC-point variant `reconstruct_pub_additive_shares`. Because both scalar and point paths are wrong in the same way, the internal self-consistency check `x_share * G == Qis[name]` still passes, so the corrupted additive share propagates undetected into ECDSA/EdDSA/Schnorr signing.

### Finding Description

In `src/cbmpc/protocol/ec_dkg.cpp`, the `AND` branch of `reconstruct_additive_share` reads:

```cpp
case node_e::AND:
  additive_share = 0;
  is_satisfied = true;
  for (int i = 0; i < n; i++) {
    bn_t additive_share_from_child;
    ...
    if (additive_share_from_child != 0) {
      additive_share = additive_share_from_child;   // ← assignment, not +=
    }
  }
  break;
```

The correct semantics for an AND node is that the secret equals the **sum** of all children's sub-secrets (`x = x₁ + x₂ + … + xₙ`), so each party's additive contribution must be accumulated across all children. The reference implementation in `secret_sharing.cpp` does this correctly:

```cpp
case node_e::AND:
  x = 0;
  for (int i = 0; i < n; i++) {
    bn_t share;
    if (rv = reconstruct_recursive(q, node->children[i], shares, share)) return rv;
    MODULO(q) x += share;   // ← correct accumulation
  }
  break;
```

The identical assignment-instead-of-accumulation defect appears in `reconstruct_pub_additive_shares` for the AND branch:

```cpp
if (!additive_share_from_child.is_infinity()) {
  pub_additive_shares = additive_share_from_child;  // ← should be pub_additive_shares + additive_share_from_child
}
```

`to_additive_share` calls both functions and then stores the results directly into the output `key_share_mp_t`:

```cpp
if (rv = reconstruct_additive_share(q, ac.root, quorum_names, new_x_share, ...)) return rv;
...
if (rv = reconstruct_pub_additive_shares(ac.root, quorum_names, quorum_names_vec[j], new_Qi, ...)) return rv;
new_Qis[quorum_names_vec[j]] = new_Qi;
...
additive_share.x_share = new_x_share;
additive_share.Qis = new_Qis;
```

Because both the scalar and the point are wrong in the same direction, the check `x_share * G == Qis[name]` passes, masking the corruption.

`to_additive_share` is called from the public signing API in `src/cbmpc/api/ecdsa_mp.cpp`, `src/cbmpc/api/eddsa_mp.cpp`, and `src/cbmpc/api/schnorr_mp.cpp`, making the path fully reachable.

### Impact Explanation

Any access structure that places the same party in more than one child of an AND node (e.g., `AND(THRESHOLD[1](p0,p1), THRESHOLD[1](p0,p2))`) triggers the bug. The party's `x_share` is the correct sum of all sub-secret contributions from the DKG, but `reconstruct_additive_share` returns only the last non-zero child's Lagrange-weighted piece. The resulting `additive_share.x_share` is therefore a **strict sub-sum** of the correct value. When this corrupted share is used in ECDSA/EdDSA/Schnorr signing, the combined effective private key differs from the key established during DKG. The produced signature is cryptographically valid but for a **different public key** than the one the parties agreed on, constituting invalid cryptographic output accepted without error.

### Likelihood Explanation

The public API accepts caller-supplied access structures with no validation that each party name appears in at most one leaf. A caller (or a peer who influences the access-structure setup) can supply an AND node whose children both reference the same party. The bug is silent: no error is returned, the internal consistency check passes, and the wrong signature is handed back to the caller.

### Recommendation

Replace the assignment with modular accumulation in both functions:

- `reconstruct_additive_share`, AND branch: change `additive_share = additive_share_from_child` to `MODULO(q) additive_share += additive_share_from_child` (remove the `!= 0` guard; zero contributions are harmless to add).
- `reconstruct_pub_additive_shares`, AND branch: change `pub_additive_shares = additive_share_from_child` to `pub_additive_shares = pub_additive_shares + additive_share_from_child` (remove the `!is_infinity()` guard for the same reason).

Optionally, add a validation pass in `to_additive_share` or `dkg_ac` that rejects access structures where any party name appears in more than one leaf.

### Proof of Concept

Access structure: `AND(THRESHOLD[1](p0, p1), THRESHOLD[1](p0, p2))`.

After DKG, party `p0` holds `x_share = s₁ + s₂` where `s₁` is its share from the first THRESHOLD child and `s₂` from the second.

When `p0` calls `to_additive_share`:
1. Child 1 (`THRESHOLD[1](p0,p1)`) → `reconstruct_additive_share` returns `λ₁ · s₁` (Lagrange weight for p0 in child 1).
2. Child 2 (`THRESHOLD[1](p0,p2)`) → returns `λ₂ · s₂`.
3. AND branch: `additive_share` is first set to `λ₁ · s₁`, then **overwritten** with `λ₂ · s₂`.
4. Correct value should be `λ₁ · s₁ + λ₂ · s₂`; returned value is only `λ₂ · s₂`.

The combined effective key across all parties therefore sums to a value different from the DKG-established key `Q`. Any ECDSA/EdDSA/Schnorr signature produced with these additive shares verifies against a different public key, silently producing invalid cryptographic output through the public signing API. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** src/cbmpc/crypto/secret_sharing.cpp (L433-440)
```cpp
    case node_e::AND:
      x = 0;
      for (int i = 0; i < n; i++) {
        bn_t share;
        if (rv = reconstruct_recursive(q, node->children[i], shares, share)) return rv;
        MODULO(q) x += share;
      }
      break;
```
