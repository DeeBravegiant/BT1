### Title
Missing Active DRep Expiry Check in `validateWithdrawalsDelegated` Allows Reward Withdrawals Without Active Governance Participation — (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs`)

---

### Summary

The Conway LEDGER rule requires that key-hash stake credentials be delegated to a DRep before withdrawing rewards (post-bootstrap). The enforcement function `validateWithdrawalsDelegated` only checks whether a DRep delegation field is set in the account state, but never verifies that the delegated DRep is currently **active** (non-expired). Because expired DReps are not unregistered and their delegators' account states are not cleared, any key-hash stake credential holder can withdraw rewards while delegated to an expired DRep, bypassing the governance participation requirement. This is a direct analog to the external report's pattern: a user claims a benefit (reward withdrawal) without fulfilling the corresponding obligation (active governance participation via a live DRep).

---

### Finding Description

**Root cause — `validateWithdrawalsDelegated`:** [1](#0-0) 

```haskell
validateWithdrawalsDelegated accounts tx =
  let wdrls = unWithdrawals $ tx ^. bodyTxL . withdrawalsTxBodyL
      wdrlsKeyHashes =
        [ kh | (ra, _) <- Map.toList wdrls, Just kh <- [credKeyHash $ ra ^. accountAddressCredentialL]
        ]
      isNotDRepDelegated keyHash = isNothing $ do
        accountState <- lookupAccountState (KeyHashObj keyHash) accounts
        accountState ^. dRepDelegationAccountStateL   -- only checks presence, not liveness
      nonExistentDelegations =
        filter isNotDRepDelegated wdrlsKeyHashes
   in failureOnNonEmpty nonExistentDelegations ConwayWdrlNotDelegatedToDRep
```

The predicate `isNotDRepDelegated` returns `False` (i.e., "delegation exists, allow withdrawal") whenever `dRepDelegationAccountStateL` is `Just _`. It does **not** look up the DRep in `vsDRepsL` and does **not** compare `drepExpiry` against the current epoch.

**Why expired DReps leave the delegation field set:**

When a DRep is **unregistered** via `ConwayUnRegDRep`, the handler explicitly clears all delegators' `dRepDelegationAccountStateL` via `clearDRepDelegations`: [2](#0-1) 

When a DRep merely **expires** (its `drepExpiry` epoch passes without activity), no such cleanup occurs. The DRep remains in `vsDRepsL` with a stale expiry, and every delegator's `dRepDelegationAccountStateL` remains `Just (DRepCredential cred)`. The `validateWithdrawalsDelegated` check therefore passes for all of them.

**Confirmed by existing tests:**

The test `"Withdraw from a key delegated to an expired DRep"` (DRep expired *before* delegation) uses `submitTx_` — it succeeds: [3](#0-2) 

The test `disableInConformanceIt "Withdraw from a key delegated to a DRep that expired after delegation"` (DRep expired *after* delegation) is explicitly disabled in conformance testing with a reference to formal-ledger-specifications issue #635, confirming the implementation diverges from the formal spec: [4](#0-3) 

**Where the check is invoked in the LEDGER rule:** [5](#0-4) 

The check is applied to the pre-certificate `accounts` snapshot, which is correct for the unregistration-in-same-tx case, but it still does not consult DRep expiry.

**DRep expiry is tracked but not consulted here:**

The `drepExpiry` field is present in `DRepState` and is used in `dRepAcceptedRatio` (ratification) to exclude expired DReps from governance vote counting: [6](#0-5) 

The same liveness check is absent from `validateWithdrawalsDelegated`.

---

### Impact Explanation

The design intent of requiring DRep delegation for withdrawals (introduced post-bootstrap, version ≥ 10) is to ensure that reward-earning stake actively participates in on-chain governance. By delegating to an expired DRep — or by waiting for a previously active DRep to expire — a key-hash stake credential holder can withdraw accumulated rewards without contributing to any governance quorum or ratification threshold. This constitutes **withdrawals outside design parameters**: the governance participation obligation is bypassed while the withdrawal benefit is retained, matching the Medium impact category ("attacker-controlled transactions… modify… withdrawals outside design parameters").

---

### Likelihood Explanation

Any unprivileged key-hash stake credential holder can trigger this. DRep expiry is a normal, predictable protocol event: a DRep expires after `ppDRepActivityL` epochs of inactivity. A user can either (a) delegate to a DRep that is already expired, or (b) delegate to an active DRep and simply wait for it to expire without re-delegating. No privileged access, key compromise, or consensus majority is required. The attacker-controlled entry path is a standard withdrawal transaction.

---

### Recommendation

`validateWithdrawalsDelegated` should additionally verify that the delegated DRep is currently active. This requires passing the current epoch and the DRep state map into the check:

```haskell
isNotDRepDelegated keyHash = isNothing $ do
  accountState <- lookupAccountState (KeyHashObj keyHash) accounts
  dRep <- accountState ^. dRepDelegationAccountStateL
  case dRep of
    DRepAlwaysAbstain        -> Just ()
    DRepAlwaysNoConfidence   -> Just ()
    DRepCredential cred      -> do
      drepState <- Map.lookup cred vsDReps
      guard (currentEpoch <= drepExpiry drepState)
      Just ()
```

Alternatively, the epoch-boundary `updateNumDormantEpochs` / DRep expiry logic could clear `dRepDelegationAccountStateL` for delegators of expired DReps, mirroring the cleanup already done for unregistered DReps.

---

### Proof of Concept

1. Register stake credential `cred = KeyHashObj kh` and accumulate rewards (e.g., via `submitAndExpireProposalToMakeReward`).
2. Register a DRep and delegate `cred` to it via `DelegTxCert cred (DelegVote (DRepCredential drep))`.
3. Advance epochs past `ppDRepActivityL` without any DRep activity — `isDRepExpired drep` returns `True`.
4. Submit a withdrawal transaction: `withdrawalsTxBodyL .~ Withdrawals [(ra, balance)]`.
5. The transaction is accepted (`submitTx_` succeeds). The `validateWithdrawalsDelegated` check passes because `dRepDelegationAccountStateL` is still `Just (DRepCredential drep)`, even though `drep` is expired and excluded from all governance vote counting.

This is demonstrated directly by the existing (passing) test at: [3](#0-2) 

and the conformance-disabled test at: [4](#0-3)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L373-380)
```haskell
          -- Starting with version 10, we don't allow withdrawals into RewardAcounts that are
          -- KeyHashes and not delegated to Dreps.
          --
          -- We also need to make sure we are using the certState before certificates are applied,
          -- because otherwise it would not be possible to unregister an account address and withdraw
          -- all funds from it in the same transaction.
          unless (hardforkConwayBootstrapPhase (pp ^. ppProtocolVersionL)) $ do
            runTest $ validateWithdrawalsDelegated accounts tx
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L473-488)
```haskell
validateWithdrawalsDelegated ::
  ( EraTx era
  , ConwayEraCertState era
  ) =>
  Accounts era -> Tx l era -> Test (ConwayLedgerPredFailure era)
validateWithdrawalsDelegated accounts tx =
  let wdrls = unWithdrawals $ tx ^. bodyTxL . withdrawalsTxBodyL
      wdrlsKeyHashes =
        [ kh | (ra, _) <- Map.toList wdrls, Just kh <- [credKeyHash $ ra ^. accountAddressCredentialL]
        ]
      isNotDRepDelegated keyHash = isNothing $ do
        accountState <- lookupAccountState (KeyHashObj keyHash) accounts
        accountState ^. dRepDelegationAccountStateL
      nonExistentDelegations =
        filter isNotDRepDelegated wdrlsKeyHashes
   in failureOnNonEmpty nonExistentDelegations ConwayWdrlNotDelegatedToDRep
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L244-254)
```haskell
        certState' =
          certState & certVStateL . vsDRepsL %~ Map.delete cred
        clearDRepDelegations delegs accountsMap =
          foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
      pure $
        case mDRepState of
          Nothing -> certState'
          Just dRepState ->
            certState'
              & certDStateL . accountsL . accountsMapL
                %~ clearDRepDelegations (drepDelegs dRepState)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/LedgerSpec.hs (L148-173)
```haskell
  it "Withdraw from a key delegated to an expired DRep" $ do
    modifyPParams $ \pp ->
      pp
        & ppGovActionLifetimeL .~ EpochInterval 4
        & ppDRepActivityL .~ EpochInterval 1
    kh <- freshKeyHash
    let cred = KeyHashObj kh
    ra <- registerStakeCredential cred
    submitAndExpireProposalToMakeReward cred
    balance <- getBalance cred

    (drep, _, _) <- setupSingleDRep 1_000_000

    -- expire the drep before delegation
    mkMinFeeUpdateGovAction SNothing >>= submitGovAction_
    passNEpochs 4
    isDRepExpired drep `shouldReturn` True

    _ <- delegateToDRep cred (Coin 1_000_000) (DRepCredential drep)

    submitTx_ $
      mkBasicTx $
        mkBasicTxBody
          & withdrawalsTxBodyL
            .~ Withdrawals
              [(ra, balance)]
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/LedgerSpec.hs (L175-198)
```haskell
  -- https://github.com/IntersectMBO/formal-ledger-specifications/issues/635
  -- TODO: Re-enable after issue is resolved, by removing this override
  disableInConformanceIt "Withdraw from a key delegated to a DRep that expired after delegation" $ do
    modifyPParams $ \pp ->
      pp
        & ppGovActionLifetimeL .~ EpochInterval 4
        & ppDRepActivityL .~ EpochInterval 1
    (drep, cred, _) <- setupSingleDRep 1_000_000
    ra <- getAccountAddressFor cred
    submitAndExpireProposalToMakeReward cred
    balance <- getBalance cred

    -- expire the drep after delegation
    mkMinFeeUpdateGovAction SNothing >>= submitGovAction_

    passNEpochs 4
    isDRepExpired drep `shouldReturn` True

    submitTx_ $
      mkBasicTx $
        mkBasicTxBody
          & withdrawalsTxBodyL
            .~ Withdrawals
              [(ra, balance)]
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L263-268)
```haskell
        DRepCredential cred ->
          case Map.lookup cred reDRepState of
            Nothing -> (yes, tot) -- drep is not registered, so we don't consider it
            Just drepState
              | reCurrentEpoch > drepExpiry drepState -> (yes, tot) -- drep is expired, so we don't consider it
              | otherwise ->
```
