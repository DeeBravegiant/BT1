Let me examine the relevant code to trace this potential vulnerability.

Now let me examine the VRF key hash structure and the POOL rule for uniqueness enforcement.

Now I have all the information needed to analyze this claim thoroughly.

I now have all the code needed for a complete analysis. Let me trace the exact execution path.

---

## Full Trace

**Before v11:** Pool A and Pool B both register with VRF_A. `psVRFKeyHashes = {}` (not populated pre-v11).

**v11 hard fork — `populateVRFKeyHashes`** (`HardFork.hs` lines 107–125):
Counts VRF keys from `psStakePools` for both pool_A and pool_B → `psVRFKeyHashes = {VRF_A: 2}`. [1](#0-0) 

**After v11 — pool_A re-registers with VRF_B** (`Pool.hs` lines 279–306):

Re-register uniqueness check:
```
sppVrf(VRF_B) == spsVrfL(VRF_A)?  No.
Map.notMember VRF_B psVRFKeyHashes? Yes (VRF_B is fresh). → passes.
``` [2](#0-1) 

`updateFutureVRFKeyHash`: no prior future registration for pool_A → `Map.insert VRF_B 1`.
Result: `psVRFKeyHashes = {VRF_A: 2, VRF_B: 1}`, `psFutureStakePoolParams[pool_A] = {vrf: VRF_B}`. [3](#0-2) 

**Epoch boundary — `poolReapTransition`:**

`danglingVRFKeyHashes` computation: pool_A has `spsVrfL = VRF_A` and `sppVrfL = VRF_B` → `VRF_A ≠ VRF_B` → `Just VRF_A`. So `danglingVRFKeyHashes = {VRF_A}`. [4](#0-3) 

Then the update:
```haskell
& certPStateL . psVRFKeyHashesL
  %~ ( removeVRFKeyHashOccurrences retiredVRFKeyHashes
         . (`Map.withoutKeys` danglingVRFKeyHashes)
     )
``` [5](#0-4) 

`Map.withoutKeys {VRF_A}` **removes VRF_A entirely** from `psVRFKeyHashes`, regardless of its reference count (which is 2). Result: `psVRFKeyHashes = {VRF_B: 1}`. Pool_B still uses VRF_A but it is now absent from `psVRFKeyHashes`.

**Pool C registers with VRF_A:**

New-pool uniqueness check: `Map.notMember VRF_A psVRFKeyHashes` → `True` → **passes**. Pool C is registered with VRF_A. Now pool_B and pool_C both actively use VRF_A — uniqueness invariant broken. [6](#0-5) 

---

## Root Cause

`psVRFKeyHashes` is a reference-counted map (`Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64)`). [7](#0-6) 

The `removeVRFKeyHashOccurrences` helper (lines 230–237) correctly decrements the count and removes the key only when it reaches zero. [8](#0-7) 

But `Map.withoutKeys danglingVRFKeyHashes` at line 226 **bypasses the reference count entirely** — it deletes the key unconditionally. When VRF_A has count 2 (shared by pool_A and pool_B), only pool_A is changing its VRF; the count should be decremented to 1, not removed. The correct call would be `removeVRFKeyHashOccurrences (Set.toList danglingVRFKeyHashes)`.

---

## Impact Assessment

The scenario is concrete and locally testable. However, the claimed impact — **"permanent ledger divergence requiring a hard fork"** — does not hold. All honest nodes apply the same (buggy) transition rules and arrive at the same post-epoch state. There is no disagreement between nodes; the ledger state is simply incorrect but universally agreed upon.

The actual impact fits **"Medium. Attacker-controlled transactions/certificates exceed intended validation limits"**: an unprivileged pool operator can submit a pool re-registration certificate (a normal, permissionless transaction) that, after one epoch boundary, causes the VRF uniqueness guard introduced in v11 to silently fail for a pre-existing pool's VRF key, allowing a third pool to register with that key. The v11 security invariant is violated, but no ADA is lost and no node diverges.

---

### Title
VRF Key Hash Reference Count Bypassed by `Map.withoutKeys` in `poolReapTransition`, Allowing Duplicate VRF Registration Post-v11 — (`eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs`)

### Summary
`danglingVRFKeyHashes` in `poolReapTransition` uses `Map.withoutKeys` to remove old VRF key hashes when a pool re-registers with a new VRF. This unconditionally deletes the key from the reference-counted `psVRFKeyHashes` map, ignoring that other pools may still hold the same VRF key (a state reachable from pre-v11 duplicate registrations). After the epoch boundary, the orphaned VRF key is absent from `psVRFKeyHashes`, so the v11 uniqueness guard accepts a new pool registration using that key, giving two active pools the same VRF key.

### Finding Description
`psVRFKeyHashes` is a `Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64)` reference-counted map. The count is incremented on registration and decremented (with removal at zero) by `removeVRFKeyHashOccurrence`. The `danglingVRFKeyHashes` set collects the *old* VRF key of any pool whose future re-registration uses a different VRF. At line 226, `Map.withoutKeys danglingVRFKeyHashes` removes those keys unconditionally, bypassing the reference count. If VRF_A has count ≥ 2 (because pool_B also uses it, registered before v11 when duplicates were allowed), removing it entirely leaves pool_B's VRF key untracked. The v11 guard (`Map.notMember sppVrf psVRFKeyHashes`) then passes for VRF_A, allowing pool_C to register with it.

### Impact Explanation
The VRF uniqueness invariant introduced at v11 is silently violated. Two active pools share the same VRF key. This undermines the security guarantee of the v11 hard fork (preventing duplicate VRF keys in block production). The impact is **Medium** per the allowed scope: an attacker-controlled pool re-registration certificate causes the intended validation limit (VRF uniqueness) to be bypassed. No ADA is lost and no ledger divergence occurs; the claimed "Critical / ledger divergence" impact does not apply.

### Likelihood Explanation
Requires: (1) two pools registered with the same VRF before v11 — explicitly allowed and tested; (2) one pool re-registers with a fresh VRF after v11 — a normal, permissionless operation; (3) one epoch passes; (4) a third pool registers with the original VRF. All four steps are unprivileged and locally reproducible. The existing test at `HardForkSpec.hs` line 54 ("Retiring a stake pool with a duplicate VRF Keyhash after v11 HardFork") covers retirement of duplicate-VRF pools but does **not** cover the re-registration path that triggers `danglingVRFKeyHashes`. [9](#0-8) 

### Recommendation
Replace `Map.withoutKeys danglingVRFKeyHashes` with a reference-count-aware decrement:

```haskell
& certPStateL . psVRFKeyHashesL
  %~ ( removeVRFKeyHashOccurrences retiredVRFKeyHashes
         . removeVRFKeyHashOccurrences (Set.toList danglingVRFKeyHashes)
     )
```

This mirrors the existing `removeVRFKeyHashOccurrences` logic used for retired pools, decrementing the count by 1 per dangling entry and removing the key only when the count reaches zero.

### Proof of Concept
```
-- Pre-v11
register pool_A with VRF_A
register pool_B with VRF_A   -- allowed pre-v11

-- Enact v11 hard fork
-- populateVRFKeyHashes: psVRFKeyHashes = {VRF_A: 2}

-- Post-v11
re-register pool_A with VRF_B
-- psVRFKeyHashes = {VRF_A: 2, VRF_B: 1}

passEpoch
-- danglingVRFKeyHashes = {VRF_A}
-- Map.withoutKeys removes VRF_A entirely
-- psVRFKeyHashes = {VRF_B: 1}   ← BUG: pool_B still uses VRF_A

assert: VRF_A absent from psVRFKeyHashes  -- passes (bug confirmed)

register pool_C with VRF_A
-- Map.notMember VRF_A psVRFKeyHashes → True → accepted

assert: pool_B and pool_C both active with VRF_A  -- uniqueness violated
```

### Citations

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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L265-267)
```haskell
          when (hardforkConwayDisallowDuplicatedVRFKeys pv) $ do
            Map.notMember sppVrf psVRFKeyHashes
              ?! injectFailure (VRFKeyHashAlreadyRegistered sppId sppVrf)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L279-282)
```haskell
          when (hardforkConwayDisallowDuplicatedVRFKeys pv) $ do
            sppVrf == stakePoolState ^. spsVrfL
              || Map.notMember sppVrf psVRFKeyHashes
                ?! injectFailure (VRFKeyHashAlreadyRegistered sppId sppVrf)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L283-295)
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
                | otherwise = id
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs (L138-148)
```haskell
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/CertState.hs (L233-233)
```haskell
  { psVRFKeyHashes :: !(Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64))
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/HardForkSpec.hs (L54-85)
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
      -- retire one of the pools after the hard fork
      retireStakePool kh1 (EpochInterval 1)
      retireStakePool kh2 (EpochInterval 1)
      passEpoch
      -- the vrf keyhash should still be present, since another pool is registered with it
      expectVRFs [(vrf, 1)]

      -- registration of the same vrf should be disallowed
      kh4 <- freshKeyHash
      registerStakePoolTx kh4 vrf >>= \tx ->
        submitFailingTx
          tx
          [injectFailure $ Shelley.VRFKeyHashAlreadyRegistered kh4 vrf]

      retireStakePool kh3 (EpochInterval 1)
      passEpoch
      expectVRFs []

      registerStakePool kh4 vrf
      expectVRFs [(vrf, 1)]
```
