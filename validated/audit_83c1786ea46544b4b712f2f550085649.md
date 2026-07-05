I need to systematically trace the vulnerability class from the external report — **authorization check uses attacker-controlled data instead of trusted ledger state** — and map it to the Cardano Ledger codebase.

**Vulnerability class analysis:**

The external report's root cause: a function checks `sender == address(this)`, but `sender` is a parameter the attacker freely supplies, making the check trivially bypassable.

**Mapping to Cardano Ledger:**

The analog would require: (1) an authorization check that uses attacker-controlled transaction-body data instead of trusted ledger state, and (2) the attacker being able to set that data to a value that passes the check.

**Candidate 1 — `voterWitnesses` / `getConwayWitsVKeyNeeded`** [1](#0-0) 

Witness requirements are derived from the attacker-controlled `Voter` credential. However, the GOV rule's `VotersDoNotExist` check cross-references the voter against trusted ledger state (`knownCommitteeMembers`, `knownDReps`, `knownStakePools`). The attacker must provide a valid signature for whatever credential they claim — they cannot impersonate another voter without their private key. No bypass exists here.

**Candidate 2 — `checkGuardrailsScriptHash`** [2](#0-1) 

`proposalPolicy` (attacker-controlled) is checked against `constitutionPolicy` (ledger state). The attacker cannot set `proposalPolicy = SNothing` when `constitutionPolicy = SJust hash` — the check rejects it. Setting `proposalPolicy = constitutionPolicy` merely complies with the check and still requires the guardrails script to execute and validate. No bypass exists.

**Candidate 3 — `unelectedCommitteeVoters` / MEMPOOL vs GOV rule split** [3](#0-2) [4](#0-3) 

At PV ≤ 10, the unelected-committee-voter check lives only in the MEMPOOL rule; the GOV rule does not apply it. A block producer can include such a transaction directly in a block, bypassing the mempool. The test at `LedgerSpec.hs:313–318` explicitly confirms the ledger accepts such transactions at PV ≤ 10 via `withNoFixup $ submitTx_ txFixed`.

<cite repo="Linkmegit/cardano-ledger--011" path="eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/LedgerSpec.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L199-211)
```haskell
voterWitnesses ::
  ConwayEraTxBody era =>
  TxBody l era ->
  Set.Set (KeyHash Witness)
voterWitnesses txb =
  Map.foldrWithKey' accum mempty (unVotingProcedures (txb ^. votingProceduresTxBodyL))
  where
    accum voter _ khs =
      maybe khs (`Set.insert` khs) $
        case voter of
          CommitteeVoter cred -> credKeyHashWitness cred
          DRepVoter cred -> credKeyHashWitness cred
          StakePoolVoter poolId -> Just $ asWitness poolId
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L420-426)
```haskell
checkGuardrailsScriptHash ::
  StrictMaybe ScriptHash ->
  StrictMaybe ScriptHash ->
  Test (ConwayGovPredFailure era)
checkGuardrailsScriptHash expectedHash actualHash =
  failureUnless (actualHash == expectedHash) $
    InvalidGuardrailsScriptHash actualHash expectedHash
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L478-481)
```haskell
  when (hardforkConwayDisallowUnelectedCommitteeFromVoting $ pp ^. ppProtocolVersionL) $
    failOnNonEmpty
      (unelectedCommitteeVoters committee committeeState gsVotingProcedures)
      (injectFailure . UnelectedCommitteeVoters)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Mempool.hs (L121-135)
```haskell
  whenFailureFreeDefault ledgerState $ do
    let protVer = ledgerEnv ^. Shelley.ledgerPpL . ppProtocolVersionL
    unless (hardforkConwayDisallowUnelectedCommitteeFromVoting protVer) $
      -- This check can completely be removed once mainnet switches to protocol
      -- version 11, since the same check has been implemented in the GOV rule.
      --
      -- Disallow votes by unelected committee members
      let addPrefix = ("Unelected committee members are not allowed to cast votes: " <>)
       in failOnNonEmpty
            ( unelectedCommitteeVoters
                (ledgerState ^. lsUTxOStateL . utxosGovStateL . committeeGovStateL)
                (ledgerState ^. lsCertStateL . certVStateL . vsCommitteeStateL)
                (tx ^. bodyTxL . votingProceduresTxBodyL)
            )
            (ConwayMempoolFailure . addPrefix . T.pack . show . NE.toList)
```
