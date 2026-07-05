### Title
VRF Key Uniqueness Invariant Bypass via Incorrect Dangling-Key Removal in POOLREAP — (File: `eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs`)

---

### Summary

`poolReapTransition` removes "dangling" VRF key hashes from `psVRFKeyHashes` using `Map.withoutKeys`, which deletes the entry entirely regardless of its reference count. When multiple pools share a VRF key (grandfathered from before protocol version 11) and one of them re-registers with a new VRF key, the shared VRF key is wiped from the map even though the other pools still hold it. A subsequent pool registration can then claim that VRF key, silently creating two simultaneously active pools with the same VRF key — the exact scenario the v11 uniqueness check was designed to prevent.

---

### Finding Description

Protocol version 11 introduced `hardforkConwayDisallowDuplicatedVRFKeys`, which gates a check in `poolTransition` that rejects any pool registration whose `sppVrf` is already present in `psVRFKeyHashes`. [1](#0-0) 

`psVRFKeyHashes` is a reference-counted map (`Map VRFVerKeyHash (NonZero Word64)`). The count is incremented on registration and decremented on retirement via `removeVRFKeyHashOccurrences`. [2](#0-1) 

At each epoch boundary, `poolReapTransition` also computes a set of "dangling" VRF keys — keys whose pool has re-registered with a *different* VRF key during the epoch, making the old key stale for that pool: [3](#0-2) 

These dangling keys are then removed from `psVRFKeyHashes` using `Map.withoutKeys`, which **deletes the entry entirely**, bypassing the reference count: [4](#0-3) 

Compare this with the correct treatment of retired pools, which uses `removeVRFKeyHashOccurrences` to decrement by one: [5](#0-4) 

**Vulnerable scenario (step-by-step):**

1. Before v11: Pool A and Pool B both register with VRF key `X`. No uniqueness restriction exists; `psVRFKeyHashes` is not yet populated.
2. Hard fork to v11: `populateVRFKeyHashes` sets `psVRFKeyHashes = {X: 2}`.
3. After v11: Pool A re-registers with a fresh VRF key `Y`. The check passes (`Y ∉ psVRFKeyHashes`). `psVRFKeyHashes` becomes `{X: 2, Y: 1}`.
4. Epoch boundary (POOLREAP): `danglingVRFKeyHashes = {X}` (Pool A's current VRF is `X`, future VRF is `Y`). `Map.withoutKeys {X}` removes `X` entirely → `psVRFKeyHashes = {Y: 1}`. Pool B still holds VRF key `X` in `psStakePools`, but `X` is no longer tracked.
5. Attacker registers new Pool C with VRF key `X`. The check `Map.notMember X psVRFKeyHashes` passes (since `X` was wiped). Pool B and Pool C now both hold VRF key `X`. [6](#0-5) 

---

### Impact Explanation

Two simultaneously active pools sharing a VRF key violates the uniqueness invariant introduced in v11. The VRF key is used for Ouroboros Praos slot-leader election: its output is deterministic given the key and the epoch nonce. An attacker who controls Pool C (sharing VRF key `X` with Pool B) can predict Pool B's slot-leader schedule for every epoch, enabling:

- Targeted block-withholding or eclipse attacks against Pool B's slots.
- Nonce-grinding: the attacker can selectively publish or withhold their own blocks to bias the epoch nonce, influencing future slot-leader distributions.

This matches **Medium** impact: an attacker-controlled pool registration certificate bypasses the intended VRF key uniqueness validation limit, modifying the effective slot-leader distribution outside design parameters.

---

### Likelihood Explanation

The precondition — multiple pools sharing a VRF key before v11 — was explicitly permitted and is observable on-chain. The `HardForkSpec` test confirms the ledger correctly handles pools with shared VRF keys after the hard fork. [7](#0-6) 

Any pool operator holding a grandfathered shared VRF key who subsequently re-registers with a new VRF key (a routine operational action) silently triggers the bug. An attacker monitoring the chain can detect the epoch boundary at which `X` disappears from `psVRFKeyHashes` and immediately submit a registration for Pool C. No privileged access is required.

---

### Recommendation

Replace the `Map.withoutKeys danglingVRFKeyHashes` call with the same reference-count decrement used for retired pools:

```haskell
-- Current (incorrect): removes the key entirely regardless of count
& certPStateL . psVRFKeyHashesL
  %~ ( removeVRFKeyHashOccurrences retiredVRFKeyHashes
         . (`Map.withoutKeys` danglingVRFKeyHashes)
     )

-- Correct: decrement by one for each dangling key, remove only when count reaches 0
& certPStateL . psVRFKeyHashesL
  %~ removeVRFKeyHashOccurrences
       (retiredVRFKeyHashes ++ Set.toList danglingVRFKeyHashes)
``` [8](#0-7) 

---

### Proof of Concept

```
Epoch 0 (pre-v11, pv=10):
  Pool A registers with VRF key X  → psVRFKeyHashes = {}
  Pool B registers with VRF key X  → psVRFKeyHashes = {}

Hard fork to v11:
  populateVRFKeyHashes              → psVRFKeyHashes = {X: 2}

Epoch 1 (pv=11):
  Pool A re-registers with VRF key Y
    check: Y ∉ psVRFKeyHashes → passes
    psVRFKeyHashes = {X: 2, Y: 1}

Epoch boundary (POOLREAP):
  danglingVRFKeyHashes = {X}        (Pool A: current=X, future=Y)
  Map.withoutKeys {X}               → psVRFKeyHashes = {Y: 1}
  Pool B still has X in psStakePools, but X is gone from psVRFKeyHashes

Epoch 2:
  Attacker registers Pool C with VRF key X
    check: X ∉ psVRFKeyHashes → passes  ← BUG
    psVRFKeyHashes = {X: 1, Y: 1}

Result: Pool B and Pool C both active with VRF key X.
        VRF uniqueness invariant violated.
``` [9](#0-8) [6](#0-5)

### Citations

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Era.hs (L259-262)
```haskell
hardforkConwayDisallowDuplicatedVRFKeys ::
  ProtVer ->
  Bool
hardforkConwayDisallowDuplicatedVRFKeys pv = pvMajor pv > natVersion @10
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/CertState.hs (L232-245)
```haskell
data PState era = PState
  { psVRFKeyHashes :: !(Map (VRFVerKeyHash StakePoolVRF) (NonZero Word64))
  -- ^ VRF key hashes that have been registered via PoolParams
  , psStakePools :: !(Map (KeyHash StakePool) StakePoolState)
  -- ^ The state of current stake pools.
  , psFutureStakePoolParams :: !(Map (KeyHash StakePool) StakePoolParams)
  -- ^ Future pool params
  -- Changes to existing stake pool parameters are staged in order
  -- to give delegators time to react to changes.
  -- See section 11.2, "Example Illustration of the Reward Cycle",
  -- of the Shelley Ledger Specification for a sequence diagram.
  , psRetiring :: !(Map (KeyHash StakePool) EpochNo)
  -- ^ A map of retiring stake pools to the epoch when they retire.
  }
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/PoolReap.hs (L131-228)
```haskell
poolReapTransition :: forall era. EraCertState era => TransitionRule (POOLREAP era)
poolReapTransition = do
  TRC (_, PoolreapState us a cs0, e) <- judgmentContext
  let
    ps0 = cs0 ^. certPStateL
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

    -- activate future stakePools
    ps =
      ps0
        { psStakePools =
            Map.merge
              Map.dropMissing
              Map.preserveMissing
              ( Map.zipWithMatched $ \_ futureParams currentState ->
                  mkStakePoolState
                    (currentState ^. spsDepositL)
                    (currentState ^. spsDelegatorsL)
                    futureParams
              )
              (ps0 ^. psFutureStakePoolParamsL)
              (ps0 ^. psStakePoolsL)
        , psFutureStakePoolParams = Map.empty
        }
    cs = cs0 & certPStateL .~ ps

    ds = cs ^. certDStateL
    -- The set of pools retiring this epoch
    retired :: Set (KeyHash StakePool)
    retired = Set.fromDistinctAscList [k | (k, v) <- Map.toAscList (psRetiring ps), v == e]
    -- The Map of pools retiring this epoch
    retiringPools :: Map.Map (KeyHash StakePool) StakePoolState
    retiringPools = Map.restrictKeys (psStakePools ps) retired
    -- collect all accounts for stake pools that will retire
    retiredVRFKeyHashes = spsVrf <$> Map.elems retiringPools

    -- collect all of the potential refunds
    accountRefunds :: Map.Map (Credential Staking) (CompactForm Coin)
    accountRefunds =
      Map.fromListWith
        (<>)
        [(unAccountId $ spsAccountId sps, spsDeposit sps) | sps <- Map.elems retiringPools]
    accounts = ds ^. accountsL
    -- Deposits that can be refunded and those that are unclaimed (to be deposited into the treasury).
    refunds, unclaimedDeposits :: Map.Map (Credential Staking) (CompactForm Coin)
    (refunds, unclaimedDeposits) =
      Map.partitionWithKey
        (\stakeCred _ -> isAccountRegistered stakeCred accounts) -- (k ∈ dom (rewards ds))
        accountRefunds

    refunded = fold refunds
    unclaimed = fold unclaimedDeposits

  tellEvent $
    let rewardAccountsWithPool =
          Map.foldrWithKey'
            ( \poolId sps ->
                let cred = unAccountId $ spsAccountId sps
                 in Map.insertWith (Map.unionWith (<>)) cred (Map.singleton poolId (spsDeposit sps))
            )
            Map.empty
            retiringPools
        (refundPools', unclaimedPools') =
          Map.partitionWithKey
            (\cred _ -> isAccountRegistered cred accounts)
            rewardAccountsWithPool
     in RetiredPools
          { refundPools = refundPools'
          , unclaimedPools = unclaimedPools'
          , epochNo = e
          }
  pure $
    PoolreapState
      us {utxosDeposited = utxosDeposited us <-> fromCompact (unclaimed <> refunded)}
      a {casTreasury = casTreasury a <+> fromCompact unclaimed}
      ( cs
          & certDStateL . accountsL
            %~ removeStakePoolDelegations (delegsToClear cs retired)
              . addToBalanceAccounts refunds
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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Pool.hs (L262-276)
```haskell
      case Map.lookup sppId psStakePools of
        -- register new, Pool-Reg
        Nothing -> do
          when (hardforkConwayDisallowDuplicatedVRFKeys pv) $ do
            Map.notMember sppVrf psVRFKeyHashes
              ?! injectFailure (VRFKeyHashAlreadyRegistered sppId sppVrf)
          let updateVRFKeyHash
                | hardforkConwayDisallowDuplicatedVRFKeys pv = Map.insert sppVrf (knownNonZeroBounded @1)
                | otherwise = id
          tellEvent $ injectEvent $ RegisterPool sppId
          pure $
            ps
              & psStakePoolsL
                %~ Map.insert sppId (mkStakePoolState (pp ^. ppPoolDepositCompactL) mempty stakePoolParams)
              & psVRFKeyHashesL %~ updateVRFKeyHash
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
