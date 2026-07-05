### Title
`psVRFKeyHashes` Dangling-Entry Removal Incorrectly Deletes Shared VRF Key Entries, Bypassing Post-v11 Uniqueness Invariant — (`File: eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs`)

---

### Summary

The `POOLREAP` rule removes "dangling" VRF key hashes from `psVRFKeyHashes` using `Map.withoutKeys`, which unconditionally deletes the entire map entry regardless of its reference count. When two stake pools legitimately share a VRF key (permitted before protocol version 11), and one of them re-registers with a new VRF key after v11, the shared VRF key is fully erased from `psVRFKeyHashes` even though the other pool still holds it. A subsequent new-pool registration can then reuse that VRF key, violating the uniqueness invariant that v11 was specifically designed to enforce.

---

### Finding Description

`PState` tracks registered VRF key hashes with a reference-counted map:

```haskell
psVRFKeyHashes :: Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64)
``` [1](#0-0) 

At the v11 hard fork, `populateVRFKeyHashes` populates this map from all currently registered pools, correctly setting counts > 1 for pre-v11 pools that share a VRF key: [2](#0-1) 

When a pool **retires**, `POOLREAP` correctly decrements the count via `removeVRFKeyHashOccurrences`: [3](#0-2) 

However, when a pool **re-registers with a new VRF key**, `POOLREAP` computes `danglingVRFKeyHashes` (the old VRF keys that were overwritten) and removes them with `Map.withoutKeys`: [4](#0-3) [5](#0-4) 

`Map.withoutKeys` deletes the **entire map entry** for each dangling VRF key, ignoring the reference count. If `psVRFKeyHashes[VRF_A] = 2` (two pre-v11 pools share VRF_A) and one re-registers with VRF_B, `danglingVRFKeyHashes = {VRF_A}` and `Map.withoutKeys {VRF_A}` removes VRF_A entirely — even though the second pool still holds it.

After this incorrect removal, the new-pool registration check:

```haskell
Map.notMember sppVrf psVRFKeyHashes
  ?! injectFailure (VRFKeyHashAlreadyRegistered sppId sppVrf)
``` [6](#0-5) 

…passes for VRF_A, allowing a third pool to register with it. The v11 uniqueness invariant is silently violated.

---

### Impact Explanation

The v11 hard fork (`hardforkConwayDisallowDuplicatedVRFKeys`) was introduced specifically to prevent two active pools from sharing a VRF key, because duplicate VRF keys allow both pools to be elected as slot leaders for the same slots. After this bug is triggered, a new pool can register with a VRF key already held by an existing pool, re-creating the duplicate-VRF condition that v11 was designed to eliminate. This exceeds the intended validation limit of the `POOL` rule and places the ledger in a state that violates its own post-v11 invariant. The impact maps to: **Medium — attacker-controlled certificates exceed intended validation limits (VRF key uniqueness)**.

---

### Likelihood Explanation

The precondition — two pools sharing a VRF key before v11 — was explicitly permitted and is tested in the codebase: [7](#0-6) 

Any pool operator who co-registered with a shared VRF key before v11 can trigger the bug by simply re-registering with a fresh VRF key after v11 — a routine, permissible operation. No privileged access or governance majority is required.

---

### Recommendation

Replace `Map.withoutKeys danglingVRFKeyHashes` with the same decrement-and-prune logic used for retirements (`removeVRFKeyHashOccurrences`), so that a dangling VRF key is only fully removed from `psVRFKeyHashes` when its reference count drops to zero:

```haskell
-- Instead of:
. (`Map.withoutKeys` danglingVRFKeyHashes)

-- Use:
. removeVRFKeyHashOccurrences (Set.toList danglingVRFKeyHashes)
``` [5](#0-4) 

---

### Proof of Concept

1. **Before v11**: Register pool P1 (`kh1`, `VRF_A`) and pool P2 (`kh2`, `VRF_A`). Both share `VRF_A`.
2. **Enact v11 hard fork**: `populateVRFKeyHashes` sets `psVRFKeyHashes[VRF_A] = 2`.
3. **After v11**: Pool P1 re-registers with a fresh `VRF_B`. `psVRFKeyHashes[VRF_B] = 1` is added. `psVRFKeyHashes[VRF_A]` remains 2 (the active VRF of P1 is not yet changed).
4. **Epoch boundary (POOLREAP)**: `danglingVRFKeyHashes = {VRF_A}` (P1's current VRF ≠ P1's future VRF). `Map.withoutKeys {VRF_A}` removes `VRF_A` entirely from `psVRFKeyHashes`, even though P2 still holds it.
5. **New registration**: Register pool P3 (`kh3`, `VRF_A`). The check `Map.notMember VRF_A psVRFKeyHashes` passes (VRF_A was incorrectly erased). P3 is accepted.
6. **Result**: P2 and P3 both hold `VRF_A` post-v11, violating the uniqueness invariant. [4](#0-3) [8](#0-7)

### Citations

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/CertState.hs (L232-234)
```haskell
data PState era = PState
  { psVRFKeyHashes :: !(Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64))
  -- ^ VRF key hashes that have been registered via PoolParams
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/HardFork.hs (L107-125)
```haskell
populateVRFKeyHashes :: PState era -> PState era
populateVRFKeyHashes pState =
  pState
    & psVRFKeyHashesL
      %~ accumulateVRFKeyHashes (pState ^. psStakePoolsL) (^. spsVrfL)
        . accumulateVRFKeyHashes (pState ^. psFutureStakePoolParamsL) (^. sppVrfL)
  where
    accumulateVRFKeyHashes ::
      Map (KeyHash StakePool) a ->
      (a -> VRFVerKeyHash StakePoolVRF) ->
      Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64) ->
      Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64)
    accumulateVRFKeyHashes spMap getVrf acc =
      Map.foldr' (addVRFKeyHashOccurrence . getVrf) acc spMap
    addVRFKeyHashOccurrence vrfKeyHash =
      Map.insertWith combine vrfKeyHash (knownNonZeroBounded @1)
      where
        -- Saturates at maxBound: if (+1) would overflow to 0, keep existing value
        combine _ oldVal = fromMaybe oldVal $ mapNonZero (+ 1) oldVal
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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs (L222-228)
```haskell
          & certPStateL . psStakePoolsL %~ (`Map.withoutKeys` retired)
          & certPStateL . psRetiringL %~ (`Map.withoutKeys` retired)
          & certPStateL . psVRFKeyHashesL
            %~ ( removeVRFKeyHashOccurrences retiredVRFKeyHashes
                   . (`Map.withoutKeys` danglingVRFKeyHashes)
               )
      )
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs (L230-237)
```haskell
    removeVRFKeyHashOccurrences ::
      [VRFVerKeyHash StakePoolVRF] ->
      Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64) ->
      Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64)
    removeVRFKeyHashOccurrences vrfs vrfsMap = F.foldl' (flip removeVRFKeyHashOccurrence) vrfsMap vrfs
    removeVRFKeyHashOccurrence =
      -- Removes the key from the map if the value drops to 0
      Map.update (mapNonZero (\n -> n - 1))
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L265-267)
```haskell
          when (hardforkConwayDisallowDuplicatedVRFKeys pv) $ do
            Map.notMember sppVrf psVRFKeyHashes
              ?! injectFailure (VRFKeyHashAlreadyRegistered sppId sppVrf)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/HardForkSpec.hs (L54-65)
```haskell
  it "Retiring a stake pool with a duplicate VRF Keyhash after v11 HardFork" $ do
    whenMajorVersion @10 $ do
      -- register two pools with the same vrf keyhash before the hard fork
      (kh1, vrf) <- (,) <$> freshKeyHash <*> freshKeyHashVRF
      registerStakePool kh1 vrf
      kh2 <- freshKeyHash
      registerStakePool kh2 vrf
      kh3 <- freshKeyHash
      registerStakePool kh3 vrf

      enactHardForkV11
      expectVRFs [(vrf, 3)]
```
