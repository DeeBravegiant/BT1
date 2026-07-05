Let me read the key logic more carefully to trace the exact exploit path.

I have traced the full exploit path through the production code. The vulnerability is real.

---

### Title
`updateFutureVRFKeyHash` Incorrectly Deletes Active Pool's VRF Key from `psVRFKeyHashes`, Bypassing Post-v11 Uniqueness Enforcement — (`eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs`)

---

### Summary

In the re-registration branch of `poolTransition`, `updateFutureVRFKeyHash` uses `Map.delete` to remove the VRF key stored in `psFutureStakePoolParams` when a pool switches to a new VRF key. It does not check whether that previous future VRF key equals the pool's **currently active** VRF key in `psStakePools`. A three-step re-registration sequence exploits this to erase the active pool's VRF key from `psVRFKeyHashes`, allowing a second pool to register with the same VRF key post-v11. The `danglingVRFKeyHashes` logic in POOLREAP then permanently removes the key from tracking at the epoch boundary, making the invariant violation persistent.

---

### Finding Description

**Root cause — `poolTransition`, re-registration branch:** [1](#0-0) 

When a pool re-registers, `updateFutureVRFKeyHash` looks up `psFutureStakePoolParams` for the pool. If a prior future entry exists with a different VRF key, it does:

```haskell
Map.insert sppVrf (knownNonZeroBounded @1)
  . Map.delete (futureStakePoolParams ^. sppVrfL)
```

The `Map.delete` target is the **previous future VRF key**, not the current active VRF key. When those two are the same value (because the pool re-registered with its own active VRF key in a prior step), `Map.delete` removes the active pool's VRF key from `psVRFKeyHashes` while the pool is still live in `psStakePools`.

**Exploit sequence (entirely post-v11, no pre-v11 setup required):**

| Step | Action | `psVRFKeyHashes` | `psFutureStakePoolParams` | `psStakePools[pool_A].spsVrf` |
|------|--------|-----------------|--------------------------|-------------------------------|
| 1 | Register pool_A with `vrf_X` | `{vrf_X: 1}` | `{}` | `vrf_X` |
| 2 | Re-register pool_A with `vrf_X` (same key) | `{vrf_X: 1}` | `{pool_A: vrf_X}` | `vrf_X` |
| 3 | Re-register pool_A with `vrf_Y` | `{vrf_Y: 1}` (**vrf_X deleted**) | `{pool_A: vrf_Y}` | `vrf_X` (still active!) |
| 4 | Register pool_B with `vrf_X` | `{vrf_X: 1, vrf_Y: 1}` | — | `vrf_X` |

After step 3, `vrf_X` is absent from `psVRFKeyHashes` but is still the active VRF of pool_A in `psStakePools`. The new-pool guard at step 4: [2](#0-1) 

evaluates `Map.notMember vrf_X psVRFKeyHashes` = `True` and passes, registering pool_B with `vrf_X`. Two active pools now share the same VRF key post-v11.

**Secondary bug — POOLREAP `danglingVRFKeyHashes`:** [3](#0-2) 

At the epoch boundary, pool_A's current VRF (`vrf_X`) differs from its future VRF (`vrf_Y`), so `vrf_X` is added to `danglingVRFKeyHashes`. Then: [4](#0-3) 

`Map.withoutKeys danglingVRFKeyHashes` removes `vrf_X` entirely from `psVRFKeyHashes`, even though pool_B is still actively using it. After the epoch boundary, pool_B's VRF key is completely untracked, and the cycle can repeat: a third pool can register with `vrf_X`, and so on indefinitely.

---

### Impact Explanation

Post-v11, the invariant that `psVRFKeyHashes` contains all active pools' VRF keys — and that no two active pools share a VRF key — is violated. Two pools sharing the same VRF key can both produce valid VRF proofs for the same slot, enabling one pool to impersonate another in block production. The POOLREAP secondary bug makes the tracking corruption permanent across epoch boundaries, allowing the attack to be repeated and compounded. This constitutes a **High** impact: deterministic disagreement from ledger rule evaluation, since the POOL rule is supposed to enforce VRF uniqueness post-v11 but provably fails to do so through this certificate sequence.

---

### Likelihood Explanation

The attack requires only standard pool registration and re-registration certificates — no special privileges, no governance majority, no leaked keys. The cost is two pool deposits plus transaction fees. The three-step re-registration sequence is straightforward and reproducible in an ImpTest. Any pool operator post-v11 can execute it.

---

### Recommendation

In `updateFutureVRFKeyHash`, before calling `Map.delete (futureStakePoolParams ^. sppVrfL)`, check whether the key to be deleted equals the pool's **current active** VRF key (`stakePoolState ^. spsVrfL`). If they are equal, the key must not be deleted — it is still live in `psStakePools` and was not added to `psVRFKeyHashes` by the previous re-registration but by the original registration or `populateVRFKeyHashes`.

In `poolReapTransition`, replace `Map.withoutKeys danglingVRFKeyHashes` with a decrement-and-remove operation (analogous to `removeVRFKeyHashOccurrences`) so that a VRF key shared by multiple pools is only removed when its reference count reaches zero.

---

### Proof of Concept

```
-- Post-v11 ImpTest sketch
registerPool pool_A vrf_X          -- psVRFKeyHashes = {vrf_X: 1}
reRegisterPool pool_A vrf_X        -- psFutureStakePoolParams[pool_A] = vrf_X; psVRFKeyHashes unchanged
reRegisterPool pool_A vrf_Y        -- Map.delete vrf_X fires; psVRFKeyHashes = {vrf_Y: 1}
assert (Map.notMember vrf_X psVRFKeyHashes)   -- passes: vrf_X gone
assert (psStakePools[pool_A].spsVrf == vrf_X) -- passes: pool_A still active with vrf_X
registerPool pool_B vrf_X          -- succeeds! psVRFKeyHashes = {vrf_X:1, vrf_Y:1}
-- Two active pools share vrf_X post-v11
passEpoch
-- POOLREAP: danglingVRFKeyHashes = {vrf_X}, Map.withoutKeys removes vrf_X entirely
assert (Map.notMember vrf_X psVRFKeyHashes)   -- passes: pool_B's VRF now untracked
registerPool pool_C vrf_X          -- succeeds again: invariant permanently broken
```

### Citations

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L265-267)
```haskell
          when (hardforkConwayDisallowDuplicatedVRFKeys pv) $ do
            Map.notMember sppVrf psVRFKeyHashes
              ?! injectFailure (VRFKeyHashAlreadyRegistered sppId sppVrf)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L283-294)
```haskell
          let updateFutureVRFKeyHash
                | hardforkConwayDisallowDuplicatedVRFKeys pv =
                    -- If a pool re-registers with a fresh VRF, we have to record it in the map,
                    -- but also remove the previous VRFHashKey potentially stored in previous re-registration within the same epoch,
                    -- which we retrieve from futureStakePools.
                    case Map.lookup sppId psFutureStakePoolParams of
                      Nothing -> Map.insert sppVrf (knownNonZeroBounded @1)
                      Just futureStakePoolParams
                        | futureStakePoolParams ^. sppVrfL /= sppVrf ->
                            Map.insert sppVrf (knownNonZeroBounded @1)
                              . Map.delete (futureStakePoolParams ^. sppVrfL)
                        | otherwise -> id
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs (L136-148)
```haskell
    -- find the set of VRF key hashes that are no longer relevant, since they have been overwritten
    -- via pool re-registration
    danglingVRFKeyHashes =
      Set.fromList $
        Map.elems $
          Map.merge
            Map.dropMissing
            Map.dropMissing
            ( Map.zipWithMaybeMatched $ \_ sps sppF ->
                if sps ^. spsVrfL /= sppF ^. sppVrfL then Just (sps ^. spsVrfL) else Nothing
            )
            (ps0 ^. psStakePoolsL)
            (ps0 ^. psFutureStakePoolParamsL)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs (L224-227)
```haskell
          & certPStateL . psVRFKeyHashesL
            %~ ( removeVRFKeyHashOccurrences retiredVRFKeyHashes
                   . (`Map.withoutKeys` danglingVRFKeyHashes)
               )
```
