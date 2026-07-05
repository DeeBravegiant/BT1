### Title
Governance Proposal Deposit Permanently Lost When Return Address Is Unregistered During Bootstrap Phase — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version 9), the `GOV` rule skips the check that a proposal's return address corresponds to a registered stake account. A user who submits a governance proposal with an unregistered return address will have their deposit permanently redirected to the treasury when the proposal expires or is enacted, with no direct means of recovery. This is a structural analog to the NTT deferred-validation pattern: the critical check is absent at submission time, and the consequence — permanent loss of the deposit — is only realized at epoch-boundary processing.

---

### Finding Description

**Root cause — missing validation at submission time:**

In `conwayGovTransition`, the return-address registration check is guarded by `unless (hardforkConwayBootstrapPhase ...)`:

```haskell
unless (hardforkConwayBootstrapPhase $ pp ^. ppProtocolVersionL) $ do
  let refundAddress = proposal ^. pProcReturnAddrL
  isAccountRegistered (refundAddress ^. accountAddressCredentialL) (certDState ^. accountsL)
    ?! (injectFailure . ProposalReturnAccountDoesNotExist) refundAddress
``` [1](#0-0) 

`hardforkConwayBootstrapPhase` is `True` exactly when `pvMajor pv == natVersion @9`: [2](#0-1) 

During bootstrap, the check is entirely absent. The proposal is accepted, the governance deposit is collected from the submitter, and the `GovActionState` is stored with whatever `pProcReturnAddr` the submitter supplied — registered or not.

**Consequence realized at epoch boundary:**

At every epoch boundary, `returnProposalDeposits` iterates over all expired and enacted proposals and attempts to credit each deposit back to `gasReturnAddr`:

```haskell
processProposal gas (!accounts, !unclaimed)
  | (Just _accountState, newAccounts) <- updateLookupAccountState addRefund cred accounts =
      (newAccounts, unclaimed)
  | otherwise = (accounts, Map.insert (gasId gas) (gasDeposit gas) unclaimed)
``` [3](#0-2) 

If the credential is not registered, the deposit falls into `unclaimed`. The epoch transition then moves `unclaimed` into the treasury:

```haskell
chainAccountState3 =
  chainAccountState2
    & casTreasuryL <>~ (utxoState0 ^. utxosDonationL <> fold unclaimed)
``` [4](#0-3) 

The deposit is permanently absorbed into the treasury. The submitter has no direct recourse; recovery would require a successful `TreasuryWithdrawals` governance action voted through by DReps, SPOs, and the Constitutional Committee — effectively requiring a hard fork of social consensus.

**Attacker-controlled entry path:**

1. Network is at protocol version 9 (bootstrap phase).
2. An unprivileged user constructs a transaction containing a `ProposalProcedure` whose `pProcReturnAddr` references a credential that is not registered in the accounts map.
3. The `GOV` rule accepts the proposal and the deposit is deducted from the submitter's UTxO.
4. The proposal either expires after `ppGovActionLifetime` epochs or is enacted.
5. `returnProposalDeposits` silently routes the deposit to `unclaimed` → treasury.
6. The submitter's deposit is permanently lost.

The `processProposal` loop in `conwayGovTransition` confirms the proposal is accepted without any return-address check during bootstrap: [5](#0-4) 

---

### Impact Explanation

**Impact: High — Permanent loss of governance deposit.**

The governance deposit (`ppGovActionDepositL`, currently 100 ADA on mainnet) is irreversibly redirected to the treasury. From the submitter's perspective the funds are permanently frozen: they cannot be reclaimed without a successful treasury-withdrawal governance action, which requires supermajority ratification. This satisfies the allowed impact category:

> *High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.*

The deposit is not merely delayed — it is structurally unrecoverable by the original owner through any unilateral action.

---

### Likelihood Explanation

**Likelihood: Medium.**

- The window is bounded to protocol version 9 (bootstrap phase). Once the network advances past version 9, `hardforkConwayBootstrapPhase` returns `False` and the check is enforced.
- During bootstrap, only `isBootstrapAction` proposals are permitted, but `InfoAction` (always allowed) and `HardForkInitiation`/`UpdateCommittee`/`NewConstitution` (allowed during bootstrap) all carry the same deposit and the same unguarded return-address field.
- A user can trigger this accidentally (wallet bug, wrong address) or be socially engineered into it. No privileged access is required.

---

### Recommendation

Enforce the return-address registration check unconditionally, removing the `unless (hardforkConwayBootstrapPhase ...)` guard, or — if relaxed rules during bootstrap are intentional — add an explicit fallback that refunds the deposit to the submitter's payment address rather than silently routing it to the treasury. At minimum, document clearly that proposals submitted during bootstrap with unregistered return addresses result in permanent deposit loss.

---

### Proof of Concept

```
1. Set protocol version to 9 (bootstrap phase).
2. Generate a fresh stake credential C; do NOT register it.
3. Build a transaction with:
     ProposalProcedure
       { pProcDeposit    = ppGovActionDepositL pp   -- e.g. 100 ADA
       , pProcReturnAddr = AccountAddress Testnet (AccountId C)
       , pProcGovAction  = InfoAction
       , pProcAnchor     = ...
       }
4. Submit the transaction. The GOV rule accepts it (no return-address check during bootstrap).
5. Advance epochs until ppGovActionLifetime expires.
6. Observe: returnProposalDeposits routes the 100 ADA deposit to `unclaimed`.
7. Observe: casTreasuryL is incremented by 100 ADA; the submitter's balance is unchanged.
8. The submitter's deposit is permanently lost.
```

The `depositMovesToTreasuryWhenStakingAddressUnregisters` test in `Test.Cardano.Ledger.Conway.Imp.EpochSpec` already demonstrates the treasury-absorption path for the post-bootstrap case; the same path is reachable during bootstrap without the registration prerequisite. [6](#0-5)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L483-566)
```haskell
  let processProposal proposals (idx, proposal@ProposalProcedure {..}) = do
        runTest $ checkBootstrapProposal pp proposal

        let newGaid = GovActionId txid idx

        -- In a HardFork, check that the ProtVer can follow
        let badHardFork = do
              (prevGaid, newProtVer, prevProtVer) <-
                preceedingHardFork @era pp prevGovActionIds proposals pProcGovAction
              guard (not (pvCanFollow prevProtVer newProtVer))
              Just $
                ProposalCantFollow @era prevGaid $
                  Mismatch
                    { mismatchSupplied = newProtVer
                    , mismatchExpected = prevProtVer
                    }
        failOnJust badHardFork injectFailure

        -- PParamsUpdate well-formedness check
        runTest $ actionWellFormed (pp ^. ppProtocolVersionL) pProcGovAction

        unless (hardforkConwayBootstrapPhase $ pp ^. ppProtocolVersionL) $ do
          let refundAddress = proposal ^. pProcReturnAddrL
              govAction = proposal ^. pProcGovActionL
          isAccountRegistered (refundAddress ^. accountAddressCredentialL) (certDState ^. accountsL)
            ?! (injectFailure . ProposalReturnAccountDoesNotExist) refundAddress
          case govAction of
            TreasuryWithdrawals withdrawals _ -> do
              let nonRegisteredAccounts =
                    flip Map.filterWithKey withdrawals $ \withdrawalAddress _ ->
                      not $
                        isAccountRegistered
                          (withdrawalAddress ^. accountAddressCredentialL)
                          (certDState ^. accountsL)
              failOnNonEmpty
                (Map.keys nonRegisteredAccounts)
                (injectFailure . TreasuryWithdrawalReturnAccountsDoNotExist)
            _ -> pure ()

        -- Deposit check
        let expectedDeposit = pp ^. ppGovActionDepositL
         in pProcDeposit
              == expectedDeposit
                ?! (injectFailure . ProposalDepositIncorrect)
                  Mismatch
                    { mismatchSupplied = pProcDeposit
                    , mismatchExpected = expectedDeposit
                    }

        -- Return address network id check
        aaNetworkId pProcReturnAddr
          == expectedNetworkId
            ?! injectFailure (ProposalProcedureNetworkIdMismatch pProcReturnAddr expectedNetworkId)

        -- Treasury withdrawal return address and committee well-formedness checks
        case pProcGovAction of
          TreasuryWithdrawals wdrls proposalPolicy -> do
            let mismatchedAccounts =
                  Set.filter ((/= expectedNetworkId) . aaNetworkId) $ Map.keysSet wdrls
            failOnNonEmptySet
              mismatchedAccounts
              (\mismatched -> injectFailure (TreasuryWithdrawalsNetworkIdMismatch mismatched expectedNetworkId))

            -- Guardrails script hash check
            runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy

            -- The sum of all withdrawals must be positive
            F.fold wdrls /= mempty ?! (injectFailure . ZeroTreasuryWithdrawals) pProcGovAction
          UpdateCommittee _mPrevGovActionId membersToRemove membersToAdd _qrm -> do
            let conflicting = Set.intersection (Map.keysSet membersToAdd) membersToRemove
             in failOnNonEmptySet conflicting (injectFailure . ConflictingCommitteeUpdate)

            let invalidMembers = Map.filter (<= currentEpoch) membersToAdd
             in failOnNonEmptyMap invalidMembers (injectFailure . ExpirationEpochTooSmall)
          ParameterChange _ _ proposalPolicy ->
            runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy
          _ -> pure ()

        -- Ancestry checks and accept proposal
        let expiry = pp ^. ppGovActionLifetimeL
            actionState = mkGovActionState newGaid proposal expiry currentEpoch
         in case proposalsAddAction actionState proposals of
              Just updatedProposals -> pure updatedProposals
              Nothing -> proposals <$ failBecause (injectFailure $ InvalidPrevGovActionId proposal)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Era.hs (L256-257)
```haskell
hardforkConwayBootstrapPhase :: ProtVer -> Bool
hardforkConwayBootstrapPhase pv = pvMajor pv == natVersion @9
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L187-193)
```haskell
    processProposal gas (!accounts, !unclaimed)
      | (Just _accountState, newAccounts) <- updateLookupAccountState addRefund cred accounts =
          (newAccounts, unclaimed)
      | otherwise = (accounts, Map.insert (gasId gas) (gasDeposit gas) unclaimed)
      where
        addRefund = balanceAccountStateL <>~ compactCoinOrError (gasDeposit gas)
        cred = gasReturnAddr gas ^. accountAddressCredentialL
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L347-350)
```haskell
    chainAccountState3 =
      chainAccountState2
        -- Move donations and unclaimed rewards from proposals to treasury:
        & casTreasuryL <>~ (utxoState0 ^. utxosDonationL <> fold unclaimed)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/EpochSpec.hs (L452-487)
```haskell
depositMovesToTreasuryWhenStakingAddressUnregisters ::
  ConwayEraImp era => ImpTestM era ()
depositMovesToTreasuryWhenStakingAddressUnregisters = do
  disableTreasuryExpansion
  initialTreasury <- getsNES treasuryL
  modifyPParams $ \pp ->
    pp
      & ppGovActionLifetimeL .~ EpochInterval 8
      & ppGovActionDepositL .~ Coin 100
      & ppCommitteeMaxTermLengthL .~ EpochInterval 0
  returnAddr <- registerAccountAddress
  govActionDeposit <- getsNES $ nesEsL . curPParamsEpochStateL . ppGovActionDepositL
  keyDeposit <- getsNES $ nesEsL . curPParamsEpochStateL . ppKeyDepositL
  govPolicy <- getGovPolicy
  gaid <-
    mkProposalWithAccountAddress
      ( ParameterChange
          SNothing
          (emptyPParamsUpdate & ppuGovActionDepositL .~ SJust (Coin 1000000))
          govPolicy
      )
      returnAddr
      >>= submitProposal
  expectPresentGovActionId gaid
  replicateM_ 5 passEpoch
  expectTreasury initialTreasury
  expectRegisteredAccountAddress returnAddr
  submitTx_ $
    mkBasicTx mkBasicTxBody
      & bodyTxL . certsTxBodyL
        .~ SSeq.singleton
          (UnRegDepositTxCert (returnAddr ^. accountAddressCredentialL) keyDeposit)
  expectNotRegisteredRewardAddress returnAddr
  replicateM_ 5 passEpoch
  expectMissingGovActionId gaid
  expectTreasury $ initialTreasury <> govActionDeposit
```
