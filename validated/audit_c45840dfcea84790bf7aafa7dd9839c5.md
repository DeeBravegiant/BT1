### Title
Unguarded `new()` Initializer on NEAR OmniBridge Allows Front-Run Takeover - (File: near/omni-bridge/src/lib.rs)

### Summary
The NEAR `OmniBridge` contract's `new()` constructor has no caller restriction. Any unprivileged account can call it before the legitimate deployer, seizing the `DAO` and super-admin roles and substituting an attacker-controlled `mpc_signer`, which governs all cross-chain transfer signing authority.

### Finding Description
`OmniBridge.new()` is decorated only with `#[init]`, which prevents re-initialization once state exists, but imposes **no restriction on who may call it first**. [1](#0-0) 

```rust
#[init]
pub fn new(mpc_signer: AccountId, wnear_account_id: AccountId) -> Self {
    let mut contract = Self { ..., mpc_signer, ... };
    contract.acl_init_super_admin(near_sdk::env::predecessor_account_id()); // ← attacker
    contract.acl_grant_role(Role::DAO.into(), near_sdk::env::predecessor_account_id());
    contract
}
```

The predecessor of the first call becomes the permanent super-admin and DAO. The attacker also supplies an arbitrary `mpc_signer` account — the account the bridge calls for every cross-chain signature request. [2](#0-1) 

Contrast this with the three NEAR prover contracts, which correctly add `#[private]` to their `init()` functions, restricting callers to the contract account itself: [3](#0-2) [4](#0-3) [5](#0-4) 

`OmniToken.new()` is also protected — it explicitly checks that the caller is the parent deployer account: [6](#0-5) 

`OmniBridge.new()` has neither protection.

The same pattern applies to `TokenDeployer.new()`: [7](#0-6) 

### Impact Explanation
An attacker who wins the initialization race:

1. **Becomes DAO and super-admin** — can add/remove factories (the per-chain emitter addresses that gate `fin_transfer`), register or replace provers, bind tokens to wrong EVM addresses, and manipulate decimal mappings.
2. **Controls `mpc_signer`** — every `sign_transfer` and `log_metadata` call routes through the attacker's account. A malicious `mpc_signer` can return fabricated `SignatureResponse` values, causing the bridge to emit `SignTransferEvent` logs with attacker-chosen payloads that EVM relayers will attempt to settle.
3. **Can register a malicious prover** — `fin_transfer` calls `verify_proof` against a registered prover; an attacker-controlled prover can approve any proof, enabling unbacked minting of wrapped assets on NEAR.

This maps to: **Critical — unauthorized release of bridge assets through verification failure** and **High — forged prover outputs that bypass execution gates**.

### Likelihood Explanation
NEAR does not atomically couple `deploy_contract` and a subsequent `function_call` unless the deployer explicitly constructs a batch transaction or Promise chain. A deployer who issues two separate transactions (deploy, then init) leaves a window — even a single block — during which any observer can submit a competing `new()` call with attacker-controlled arguments. NEAR's transaction pool is observable. The window is narrow but real and requires no privileged access, leaked keys, or social engineering.

### Recommendation
Add `#[private]` to `OmniBridge::new()` and `TokenDeployer::new()`, matching the pattern already used by the three prover contracts. This restricts the caller to the contract account itself, which is only achievable via a batch transaction that deploys the contract and calls `new()` in the same atomic unit — eliminating the front-run window entirely.

```rust
#[init]
#[private]   // ← add this
pub fn new(mpc_signer: AccountId, wnear_account_id: AccountId) -> Self { ... }
```

### Proof of Concept

1. Deployer broadcasts `deploy_contract(omni-bridge.near, wasm_bytes)` — transaction T1.
2. Attacker observes T1 in the NEAR mempool and immediately broadcasts:
   ```
   omni-bridge.near::new(
     mpc_signer = "attacker-mpc.near",
     wnear_account_id = "wrap.near"
   )
   ```
   signed by the attacker's account — transaction T2.
3. If T2 is included before the deployer's init transaction, `acl_init_super_admin` and `acl_grant_role(DAO)` are called with the attacker's account ID.
4. Attacker now holds DAO role: calls `add_factory` to register a legitimate-looking EVM factory address, and `register_prover` to register `attacker-prover.near`.
5. Attacker's prover approves any `fin_transfer` proof, minting wrapped tokens on NEAR without a corresponding lock on the source chain — unbacked supply is created. [1](#0-0) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L289-318)
```rust
    #[init]
    pub fn new(mpc_signer: AccountId, wnear_account_id: AccountId) -> Self {
        let mut contract = Self {
            factories: LookupMap::new(StorageKey::Factories),
            pending_transfers: LookupMap::new(StorageKey::PendingTransfers),
            finalised_transfers: LookupSet::new(StorageKey::FinalisedTransfers),
            finalised_utxo_transfers: LookupSet::new(StorageKey::FinalisedUtxoTransfers),
            fast_transfers: LookupMap::new(StorageKey::FastTransfers),
            token_id_to_address: LookupMap::new(StorageKey::TokenIdToAddress),
            token_address_to_id: LookupMap::new(StorageKey::TokenAddressToId),
            token_decimals: LookupMap::new(StorageKey::TokenDecimals),
            deployed_tokens: LookupSet::new(StorageKey::DeployedTokens),
            deployed_tokens_v2: LookupMap::new(StorageKey::DeployedTokensV2),
            token_deployer_accounts: LookupMap::new(StorageKey::TokenDeployerAccounts),
            mpc_signer,
            current_origin_nonce: 0,
            destination_nonces: LookupMap::new(StorageKey::DestinationNonces),
            accounts_balances: LookupMap::new(StorageKey::AccountsBalances),
            wnear_account_id,
            provers: UnorderedMap::new(StorageKey::RegisteredProvers),
            init_transfer_promises: LookupMap::new(StorageKey::InitTransferPromises),
            utxo_chain_connectors: HashMap::new(),
            migrated_tokens: LookupMap::new(StorageKey::MigratedTokens),
            locked_tokens: LookupMap::new(StorageKey::LockedTokens),
        };

        contract.acl_init_super_admin(near_sdk::env::predecessor_account_id());
        contract.acl_grant_role(Role::DAO.into(), near_sdk::env::predecessor_account_id());
        contract
    }
```

**File:** near/omni-bridge/src/lib.rs (L512-524)
```rust
        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SIGN_TRANSFER_CALLBACK_GAS)
                    .sign_transfer_callback(transfer_payload, &transfer_message.fee),
            )
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L39-47)
```rust
    #[init]
    #[private]
    #[must_use]
    pub fn init(light_client: AccountId, chain_kind: ChainKind) -> Self {
        Self {
            light_client,
            chain_kind,
        }
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L54-73)
```rust
    #[init]
    #[private]
    #[must_use]
    pub fn init(mpc_contract_id: AccountId) -> Self {
        let mut finalities = HashMap::new();
        finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));
        finalities.insert(
            ChainKind::Strk,
            MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),
        );
        finalities.insert(
            ChainKind::Aptos,
            MpcFinality::Aptos(AptosFinality::Committed),
        );

        Self {
            mpc_contract_id,
            finalities,
        }
    }
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs (L28-33)
```rust
    #[init]
    #[private]
    #[must_use]
    pub const fn init(prover_account: AccountId) -> Self {
        Self { prover_account }
    }
```

**File:** near/omni-token/src/lib.rs (L48-59)
```rust
    #[init]
    pub fn new(controller: AccountId, metadata: BasicMetadata) -> Self {
        let current_account_id = env::current_account_id();
        let deployer_account = current_account_id
            .get_parent_account_id()
            .unwrap_or_else(|| {
                env::panic_str(TokenError::InvalidParentAccount.to_string().as_str())
            });

        require!(
            env::predecessor_account_id().as_str() == deployer_account,
            "Only the deployer account can init this contract"
```

**File:** near/token-deployer/src/lib.rs (L49-60)
```rust
    #[init]
    pub fn new(controller: AccountId, dao: AccountId, global_code_hash: Base58CryptoHash) -> Self {
        let mut contract = Self {
            global_code_hash: global_code_hash.into(),
        };

        contract.acl_init_super_admin(near_sdk::env::predecessor_account_id());
        contract.acl_grant_role(Role::DAO.into(), dao.clone());
        contract.acl_grant_role(Role::Controller.into(), controller);
        contract.acl_transfer_super_admin(dao);
        contract
    }
```
