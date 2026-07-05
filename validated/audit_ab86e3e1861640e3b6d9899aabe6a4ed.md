### Title
Single-Actor `InfoAction` Proposal Permanently Extends Inactive DRep Expiries, Inflating Governance Denominator - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`)

### Summary

In Conway governance, `updateDormantDRepExpiry` extends the stored expiry of **all** registered DReps — including those that have never voted — whenever any transaction containing a governance proposal is processed. A single unprivileged actor can submit a minimal `InfoAction` proposal (deposit is refunded on expiry) to trigger this global extension, preventing inactive DReps from expiring. Because `dRepAcceptedRatio` counts non-expired, non-voting DReps as implicit "no" votes in the denominator, the attacker can indefinitely inflate the effective "no" stake, making it harder for legitimate governance proposals to reach their ratification threshold.

### Finding Description

**Root cause — `updateDormantDRepExpiry` applies to all DReps unconditionally:** [1](#0-0) 

The function iterates over `vsDRepsL` with `Map.map updateExpiry`, touching every registered DRep regardless of whether it has ever voted. The guard `if actualExpiry < currentEpoch then currentExpiry else actualExpiry` only skips DReps whose *actual* expiry (stored + `numDormantEpochs`) has already passed; DReps that are "virtually alive" (stored expiry in the past but actual expiry still in the future) have their stored expiry bumped forward.

**Trigger — any proposal resets `numDormantEpochs` and fires the extension:** [2](#0-1) 

`hasProposals` is true for *any* non-empty `proposalProceduresTxBodyL`, including a bare `InfoAction`. The deposit for an `InfoAction` is fully refunded when the proposal expires, so the attacker's net cost is only transaction fees.

**`numDormantEpochs` accumulates during quiet periods:** [3](#0-2) 

Each epoch with zero active proposals increments the counter. The attacker waits for this counter to grow, then submits an `InfoAction` just before an inactive DRep's actual expiry would pass, locking in the extended stored expiry.

**Inactive DReps count as implicit "no" votes in ratification:** [4](#0-3) 

A non-expired DRep that has not voted for a proposal contributes its full stake to `tot` (the denominator) without contributing to `yes`. Keeping inactive DReps alive therefore suppresses the accepted ratio below the required threshold.

**Known spec divergence — the formal spec says inactive DReps should NOT receive the dormant-epoch extension:** [5](#0-4) 

The test `"expiry is not updated for inactive DReps"` is disabled with `disableInConformanceIt` and references open issue [#923](https://github.com/IntersectMBO/formal-ledger-specifications/issues/923), confirming the implementation diverges from the specification in exactly the way exploited here.

### Impact Explanation

An attacker who periodically submits `InfoAction` proposals during dormant governance periods can keep arbitrarily many inactive DReps alive indefinitely. Because those DReps count as implicit "no" votes, the effective ratification threshold for any proposal requiring DRep approval is silently raised. Treasury withdrawal proposals, parameter-change proposals, and committee-update proposals can all be blocked

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L257-267)
```haskell
updateDormantDRepExpiries ::
  ( EraTx era
  , ConwayEraTxBody era
  , ConwayEraCertState era
  ) =>
  Tx l era -> EpochNo -> CertState era -> CertState era
updateDormantDRepExpiries tx currentEpoch =
  let hasProposals = not . OSet.null $ tx ^. bodyTxL . proposalProceduresTxBodyL
   in if hasProposals
        then certVStateL %~ updateDormantDRepExpiry currentEpoch
        else id
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L308-328)
```haskell
updateDormantDRepExpiry ::
  -- | Current Epoch
  EpochNo ->
  VState era ->
  VState era
updateDormantDRepExpiry currentEpoch vState =
  if numDormantEpochs == EpochNo 0
    then vState
    else
      vState
        & vsNumDormantEpochsL .~ EpochNo 0
        & vsDRepsL %~ Map.map updateExpiry
  where
    numDormantEpochs = vState ^. vsNumDormantEpochsL
    updateExpiry =
      drepExpiryL
        %~ \currentExpiry ->
          let actualExpiry = binOpEpochNo (+) numDormantEpochs currentExpiry
           in if actualExpiry < currentEpoch
                then currentExpiry
                else actualExpiry
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L195-201)
```haskell
-- | When there have been zero governance proposals to vote on in the previous epoch
-- increase the dormant-epoch counter by one.
updateNumDormantEpochs :: EpochNo -> Proposals era -> VState era -> VState era
updateNumDormantEpochs currentEpoch ps vState =
  if null $ OMap.filter ((currentEpoch <=) . gasExpiresAfter) $ ps ^. pPropsL
    then vState & vsNumDormantEpochsL %~ succ
    else vState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L261-275)
```haskell
    accumStake (!yes, !tot) drep (CompactCoin stake) =
      case drep of
        DRepCredential cred ->
          case Map.lookup cred reDRepState of
            Nothing -> (yes, tot) -- drep is not registered, so we don't consider it
            Just drepState
              | reCurrentEpoch > drepExpiry drepState -> (yes, tot) -- drep is expired, so we don't consider it
              | otherwise ->
                  case Map.lookup cred gasDRepVotes of
                    -- drep hasn't voted for this action, so we don't count
                    -- the vote but we consider it in the denominator:
                    Nothing -> (yes, tot + stake)
                    Just VoteYes -> (yes + stake, tot + stake)
                    Just Abstain -> (yes, tot)
                    Just VoteNo -> (yes, tot + stake)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/EpochSpec.hs (L190-219)
```haskell
    disableInConformanceIt "expiry is not updated for inactive DReps" $ do
      let
        drepActivity = 2
      modifyPParams $ \pp ->
        pp
          & ppGovActionLifetimeL .~ EpochInterval 2
          & ppDRepActivityL .~ EpochInterval drepActivity
      (drep, _, _) <- setupSingleDRep 1_000_000
      startEpochNo <- getsNES nesELL
      let
        -- compute the epoch number that is an offset from starting epoch number plus
        -- the ppDRepActivity parameter
        offDRepActivity offset =
          addEpochInterval startEpochNo $ EpochInterval (drepActivity + offset)

      expectNumDormantEpochs 0

      -- epoch 0: we submit a proposal
      submitParamChangeProposal
      passNEpochsChecking 2 $ do
        expectNumDormantEpochs 0
        expectDRepExpiry drep $ offDRepActivity 0

      passEpoch -- entering epoch 3
      -- proposal has expired
      -- drep has expired
      expectNumDormantEpochs 1
      expectDRepExpiry drep $ offDRepActivity 0
      expectActualDRepExpiry drep $ offDRepActivity 1
      isDRepExpired drep `shouldReturn` False -- numDormantEpochs is added to the drep exiry calculation
```
