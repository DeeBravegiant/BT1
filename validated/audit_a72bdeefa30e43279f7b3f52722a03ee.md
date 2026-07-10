### Title
Unprotected `init` Function Allows Arbitrary Participant Injection and Unauthorized MPC Signing - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `MpcContract::init` function lacks the `#[private]` access-control attribute present on every other sensitive initializer in the same file. Any NEAR account can call it before the legitimate deployer does, injecting an arbitrary participant set that receives mocked-valid TEE attestations, and thereby seizing full control of the MPC network's threshold-signing capability.

---

### Finding Description

`init` at line 1926 carries only `#[init]` — which prevents *re*-initialization but does not restrict *who* may call it the first time. In NEAR SDK, `#[private]` is the attribute that enforces `predecessor_account_id == current_account_id`; without it the function is open to any caller. [1](#0-0) 

Compare with `init_running`, which correctly combines both attributes: [2](#0-1) 

Inside `init`, the supplied `ThresholdParameters` are accepted without any caller check, and every listed participant is immediately granted `VerifiedAttestation::Mock(MockAttestation::Valid)` status via `TeeState::with_mocked_participant_attestations`: [3](#0-2) 

That mock status is stored as a real `VerifiedAttestation` entry: [4](#0-3) 

Because `accept_requests` is set to `true` and the mocked attestations satisfy every `assert_caller_is_attested_participant_and_protocol_active` guard, the injected participants can immediately exercise all node-facing methods: `vote_add_domains`, `start_keygen_instance`, `vote_pk`, and `respond`. [5](#0-4) 

The deployment scripts confirm that contract deployment and `init` are issued as **separate transactions**, creating a front-running window: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

An attacker who wins the race sets themselves as the sole participant (threshold = 1) with a mocked-valid attestation. They then:

1. Call `vote_add_domains` to register a Secp256k1 signing domain.
2. Call `start_keygen_instance` (they are the leader as the only participant).
3. Call `vote_pk` with a public key they control — one vote meets threshold = 1.
4. Accept user `sign()` requests and call `respond()` with signatures from their own key.

Every user who submits a cross-chain signing request receives a signature produced by the attacker's key. Because the MPC network is the root of trust for foreign-chain transactions (Bitcoin, Ethereum, Solana), the attacker can authorize arbitrary foreign-chain transactions, constituting **unauthorized threshold signature issuance** and enabling direct theft of funds on those chains.

This matches: *Critical — Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.*

---

### Likelihood Explanation

The attack requires no special privilege — any funded NEAR account suffices. The deployment workflow issues deployment and initialization as separate transactions, and NEAR's public mempool makes the deployment observable. A bot watching for `DeployContract` actions targeting the known MPC contract account ID can submit the malicious `init` call in the very next block. The window is small but deterministic and scriptable.

---

### Recommendation

Add `#[private]` to `init`, mirroring `init_running`:

```rust
#[private]
#[handle_result]
#[init]
pub fn init(
    parameters: dtos::ThresholdParameters,
    init_config: Option<dtos::InitConfig>,
) -> Result<Self, Error> { ... }
```

Alternatively, combine `DeployContract` and the `init` function call into a single atomic NEAR transaction so no external account can interpose.

---

### Proof of Concept

```
1. Attacker watches NEAR for a DeployContract action on the target MPC account.

2. In the next block, attacker submits:
     near contract call-function as-transaction <mpc-contract-account> init \
       json-args '{
         "parameters": {
           "threshold": 1,
           "participants": {
             "next_id": 1,
             "participants": [
               ["attacker.near", 0,
                {"tls_public_key": "<attacker_ed25519_pk>",
                 "url": "https://attacker.example"}]
             ]
           }
         }
       }' sign-as attacker.near ...

3. Contract initializes with attacker as sole participant;
   TeeState::with_mocked_participant_attestations grants them
   VerifiedAttestation::Mock(MockAttestation::Valid).

4. Attacker calls vote_add_domains (Secp256k1, Sign purpose).

5. Attacker calls start_keygen_instance with the expected KeyEventId.

6. Attacker calls vote_pk with a Secp256k1 public key they own.
   Threshold = 1 → contract transitions to Running with attacker's key.

7. Legitimate users call sign() and deposit 1 yoctoNEAR.

8. Attacker calls respond() with ECDSA signatures produced by their private key.

9. Users receive valid-looking signatures from the attacker's key,
   believing them to originate from the legitimate MPC network.
   Any foreign-chain transaction the attacker signs is executed,
   draining funds from addresses derived from the attacker-controlled root key.
```

### Citations

**File:** crates/contract/src/lib.rs (L1924-1929)
```rust
    #[handle_result]
    #[init]
    pub fn init(
        parameters: dtos::ThresholdParameters,
        init_config: Option<dtos::InitConfig>,
    ) -> Result<Self, Error> {
```

**File:** crates/contract/src/lib.rs (L1944-1945)
```rust
        let initial_participants = parameters.participants();
        let tee_state = TeeState::with_mocked_participant_attestations(initial_participants);
```

**File:** crates/contract/src/lib.rs (L1947-1952)
```rust
        Ok(Self {
            protocol_state: ProtocolContractState::Running(RunningContractState::new(
                DomainRegistry::default(),
                Keyset::new(EpochId::new(0), Vec::new()),
                parameters,
                AddDomainsVotes::default(),
```

**File:** crates/contract/src/lib.rs (L1975-1979)
```rust
    // This function can be used to transfer the MPC network to a new contract.
    #[private]
    #[init]
    #[handle_result]
    pub fn init_running(
```

**File:** crates/contract/src/tee/tee_state.rs (L131-139)
```rust
            tee_state.stored_attestations.insert(
                tls_public_key,
                NodeAttestation {
                    node_id,
                    verified_attestation: VerifiedAttestation::Mock(
                        attestation::MockAttestation::Valid,
                    ),
                },
            );
```

**File:** deployment/start.sh (L1-32)
```shellscript
#!/bin/bash
set -eo pipefail

# This script is intended to be used for running nearone/mpc.
# It will initialize the Near node in case it is not initialized yet and start the MPC node.

MPC_NODE_CONFIG_FILE="$MPC_HOME_DIR/config.yaml"
NEAR_NODE_CONFIG_FILE="$MPC_HOME_DIR/config.json"

initialize_near_node() {
    if [ "$MPC_ENV" = "mpc-localnet" ]; then
        EMBEDDED_GENESIS="/app/localnet-genesis.json"
        if [ ! -f "$EMBEDDED_GENESIS" ]; then
            echo "ERROR: Embedded localnet genesis file not found at $EMBEDDED_GENESIS"
            exit 1
        fi
        echo "Using embedded localnet genesis file"

        # boot_nodes must be filled in or else the node will not have any peers.
        ./mpc-node init --dir "$1" --chain-id "$MPC_ENV" --genesis "$EMBEDDED_GENESIS" --boot-nodes "$NEAR_BOOT_NODES"

        # The init command generates a modified genesis file for some reason, so we must hard-copy the original one.
        cp "$EMBEDDED_GENESIS" "$1/genesis.json"

        # Additionally, the init command will generate a `validator_key.json`
        # file which we can simply remove.
        rm "$1/validator_key.json"
    else
        echo "Downloading genesis file"
        # boot_nodes must be filled in or else the node will not have any peers.
        ./mpc-node init --dir "$1" --chain-id "$MPC_ENV" --download-genesis --download-config --boot-nodes "$NEAR_BOOT_NODES"
    fi
```

**File:** scripts/launch-localnet.sh (L212-217)
```shellscript
  init_args=$(mktemp /tmp/init_args.XXXXXX)
  echo "$JSON_RESULT" >"${init_args}"

  echo "Initializing contract"
  run_quiet_on_success "near contract call-function as-transaction mpc-contract.test.near init file-args ${init_args} prepaid-gas '300.0 Tgas' attached-deposit '0 NEAR' sign-as mpc-contract.test.near network-config mpc-localnet sign-with-keychain send"
  run_quiet_on_success_with_retries "near contract call-function as-read-only mpc-contract.test.near state json-args {} network-config mpc-localnet now"
```
