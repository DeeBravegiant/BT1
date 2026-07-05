### Title
Governance Proposal Deposit Permanently Redirected to Treasury When Return Address Is Unregistered During Bootstrap Phase — (File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs)

---

### Summary

During the Conway bootstrap phase, the `GOV` transition rule skips the check that a proposal's `pProcReturnAddr` corresponds to a registered stake credential. Any transaction sender can submit a governance proposal (e.g., `InfoAction`, which is permitted during bootstrap) with an unregistered return address. When the proposal later expires or is enacted, the deposit refund logic silently fails to credit the unregistered address and instead routes the entire deposit to the treasury, permanently depriving the proposer of their funds.

---

### Finding Description

In `conwayGovTransition`, the return-address registration check is wrapped in a bootstrap guard:

```haskell
unless (hardforkConwayBootstrapPhase $ pp ^. ppProtocolVersionL) $ do
  let refundAddress = proposal ^. pProcReturnAddrL
  isAccountRegistered (refundAddress ^. accountAddressCredentialL) (certDState ^. accountsL)
    ?! (injectFailure . ProposalReturnAccountDoesNotExist) refundAddress
```

During bootstrap the entire block is skipped, so a `ProposalProcedure` whose `pProcReturnAddr` credential is not in the accounts map is accepted without error. [1](#0-0) 

At epoch boundary, `returnProposalDeposits` attempts to credit the deposit to the stored return address:

```haskell
processProposal gas (!accounts, !unclaimed)
  | (Just _accountState, newAccounts) <- updateLookupAccountState addRefund cred accounts =
      (newAccounts, unclaimed)
  | otherwise = (accounts, Map.insert (gasId gas) (gasDeposit gas) unclaimed)
  where
    cred = gasReturnAddr gas ^. accountAddressCredentialL
``` [2](#0-1) 

Because the credential is not registered, the `otherwise` branch fires and the deposit is placed in `unclaimed`. The epoch transition then sweeps `unclaimed` into the treasury:

```haskell
& casTreasuryL <>~ (utxoState0 ^. utxosDonationL <> fold unclaimed)
``` [3](#0-2) 

The deposit is gone. The proposer has no on-chain recourse.

The existing test suite confirms the bootstrap bypass explicitly via `FailPostBootstrap`:

```haskell
proposal <- mkProposalWithAccountAddress InfoAction unregisteredAccountAddress
submitBootstrapAwareFailingProposal_ proposal $
  FailPostBootstrap
    [injectFailure $ ProposalReturnAccountDoesNotExist unregisteredAccountAddress]
``` [4](#0-3) 

`FailPostBootstrap` means the proposal **succeeds** during bootstrap and only fails post-bootstrap — confirming the unregistered address is accepted without error in the bootstrap phase.

---

### Impact Explanation

The governance action deposit (`ppGovActionDepositL`) is a substantial amount (100,000 ADA on mainnet). When a proposer supplies an unregistered return address during bootstrap, the deposit is irrecoverably transferred to the treasury at the next epoch boundary. This modifies deposit refunds outside design parameters: the protocol's stated intent is that deposits are returned to `pProcReturnAddr`; instead they silently enrich the treasury. The proposer cannot recover the funds without registering the return-address credential before the proposal expires — which is only possible if they control that credential.

**Matched impact**: *Medium — attacker-controlled proposals modify deposits/refunds outside design parameters.*

---

### Likelihood Explanation

The bootstrap phase is a real, time-bounded window (protocol version < 9 in Conway). During that window, `InfoAction` proposals are explicitly permitted. A negligent user who supplies a freshly generated key hash (not yet registered) as the return address — a natural mistake when constructing a proposal manually or via a buggy wallet — will silently lose their deposit. No special privilege is required; any transaction sender can trigger this path.

---

### Recommendation

Remove the `unless (hardforkConwayBootstrapPhase ...)` guard from the `ProposalReturnAccountDoesNotExist` check, or apply it only to the governance-action-type restrictions while keeping the return-address registration check unconditional. The return address must be validated at proposal submission time regardless of era phase, because the refund logic at epoch boundary has no fallback recovery mechanism. [1](#0-0) 

---

### Proof of Concept

1. Network is in bootstrap phase (`pvMajor < 9`).
2. Attacker/negligent user constructs a `ProposalProcedure`:
   - `pProcGovAction = InfoAction`
   - `pProcReturnAddr = AccountAddress Testnet (AccountId (KeyHashObj freshUnregisteredKeyHash))`
   - `pProcDeposit = ppGovActionDepositL` (e.g., 100,000 ADA)
3. Submit the transaction. The `GOV` rule accepts it — the `unless (hardforkConwayBootstrapPhase ...)` block is skipped, so `ProposalReturnAccountDoesNotExist` is never raised.
4. The proposal sits in the proposals map until `gasExpiresAfter < reCurrentEpoch`.
5. At the epoch boundary, `proposalsApplyEnactment` moves the expired proposal into `expiredActions`.
6. `returnProposalDeposits expiredActions accounts` calls `updateLookupAccountState` on the unregistered credential — returns `Nothing` — and inserts the deposit into `unclaimed`.
7. `epochTransition` executes `casTreasuryL <>~ fold unclaimed`, permanently crediting the deposit to the treasury.
8. The proposer's 100,000 ADA is gone with no on-chain recovery path. [2](#0-1) [5](#0-4)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L504-508)
```haskell
        unless (hardforkConwayBootstrapPhase $ pp ^. ppProtocolVersionL) $ do
          let refundAddress = proposal ^. pProcReturnAddrL
              govAction = proposal ^. pProcGovActionL
          isAccountRegistered (refundAddress ^. accountAddressCredentialL) (certDState ^. accountsL)
            ?! (injectFailure . ProposalReturnAccountDoesNotExist) refundAddress
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L184-193)
```haskell
returnProposalDeposits removedProposals oldAccounts =
  foldr' processProposal (oldAccounts, mempty) removedProposals
  where
    processProposal gas (!accounts, !unclaimed)
      | (Just _accountState, newAccounts) <- updateLookupAccountState addRefund cred accounts =
          (newAccounts, unclaimed)
      | otherwise = (accounts, Map.insert (gasId gas) (gasDeposit gas) unclaimed)
      where
        addRefund = balanceAccountStateL <>~ compactCoinOrError (gasDeposit gas)
        cred = gasReturnAddr gas ^. accountAddressCredentialL
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L327-350)
```haskell
    allRemovedGovActions = Map.unions [expiredActions, enactedActions, removedDueToEnactment]
    (newAccounts, unclaimed) =
      returnProposalDeposits allRemovedGovActions $ dState2 ^. accountsL
  tellEvent $
    GovInfoEvent
      (Set.fromList $ Map.elems enactedActions)
      (Set.fromList $ Map.elems removedDueToEnactment)
      (Set.fromList $ Map.elems expiredActions)
      unclaimed

  let
    certState2 =
      mkConwayCertState
        -- Increment the dormant epoch counter
        ( updateNumDormantEpochs eNo newProposals vState
            -- Remove cold credentials of committee members that were removed or were invalid
            & vsCommitteeStateL %~ updateCommitteeState (govState1 ^. cgsCommitteeL)
        )
        (certState1 ^. certPStateL)
        (dState2 & accountsL .~ newAccounts)
    chainAccountState3 =
      chainAccountState2
        -- Move donations and unclaimed rewards from proposals to treasury:
        & casTreasuryL <>~ (utxoState0 ^. utxosDonationL <> fold unclaimed)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/GovSpec.hs (L90-97)
```haskell
    it "ProposalReturnAccountDoesNotExist" $ do
      mkProposal InfoAction >>= submitProposal_
      unregisteredAccountAddress <- freshKeyHash >>= getAccountAddressFor . KeyHashObj

      proposal <- mkProposalWithAccountAddress InfoAction unregisteredAccountAddress
      submitBootstrapAwareFailingProposal_ proposal $
        FailPostBootstrap
          [injectFailure $ ProposalReturnAccountDoesNotExist unregisteredAccountAddress]
```
