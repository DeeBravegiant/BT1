### Title
Unprotected `initialize()` Allows Frontrunning to Seize `DEFAULT_ADMIN_ROLE` of the Central Protocol Configuration - (File: contracts/LRTConfig.sol)

### Summary
`LRTConfig.initialize()` carries no access control modifier. Any attacker who monitors the mempool can frontrun the deployer's initialization transaction, supply their own `admin` address, and permanently seize `DEFAULT_ADMIN_ROLE` over the root configuration contract that governs every other protocol component.

### Finding Description
`LRTConfig` is the single source of truth for all contract addresses, supported assets, strategies, and roles in the protocol. Its `initialize()` function is declared `external initializer` with no caller restriction:

```solidity
function initialize(address admin, address stETH, address ethX, address rsETH_) external initializer {
    ...
    _grantRole(DEFAULT_ADMIN_ROLE, admin);
    rsETH = rsETH_;
}
``` [1](#0-0) 

The `initializer` modifier from OpenZeppelin only prevents the function from being called a second time; it imposes no restriction on *who* may call it first. Between proxy deployment and the deployer's initialization transaction, any external actor can call `initialize()` with an attacker-controlled `admin` address and arbitrary token addresses. The constructor correctly calls `_disableInitializers()` on the implementation, but the proxy itself remains open until initialized. [2](#0-1) 

Once the attacker holds `DEFAULT_ADMIN_ROLE` on `LRTConfig`, every privileged setter is available to them:

- `setContract()` — redirects `LRT_ORACLE`, `LRT_DEPOSIT_POOL`, `LRT_WITHDRAW_MANAGER`, `LRT_UNSTAKING_VAULT`, `PROTOCOL_TREASURY`, etc. to attacker-controlled contracts.
- `setRSETH()` — replaces the rsETH token address.
- `updateAssetStrategy()` — points EigenLayer strategies to malicious contracts.
- `addNewSupportedAsset()` / `removeSupportedAsset()` — manipulates the supported asset list.
- `pauseAll()` — freezes the entire protocol. [3](#0-2) 

All downstream contracts (`LRTDepositPool`, `LRTOracle`, `NodeDelegator`, `LRTWithdrawalManager`, `LRTUnstakingVault`, `LRTConverter`, `RSETH`, `ChainlinkPriceOracle`) resolve their critical addresses exclusively through `LRTConfig`, so compromising `LRTConfig` at initialization time compromises the entire protocol. [4](#0-3) [5](#0-4) 

### Impact Explanation
An attacker who wins the initialization race becomes the unchecked administrator of the protocol's root configuration contract. They can immediately redirect all deposit, withdrawal, and oracle logic to malicious contracts, enabling direct theft of all user funds deposited thereafter, or freeze the protocol permanently. This satisfies the **Critical** impact tier: direct theft of user funds at-rest or in-motion, and permanent freezing of funds.

### Likelihood Explanation
The attack requires only standard mempool monitoring and a higher-gas-price transaction submitted before the deployer's initialization call. This is a well-documented, low-skill attack pattern for unprotected proxy initializers. The window is narrow (deployment to initialization), but the technique is automated by existing MEV infrastructure. Likelihood is **Medium** — the attack is realistic on any public network where deployment transactions are visible in the mempool.

### Recommendation
Protect `initialize()` with a deployment-time access control check. The simplest approach is to deploy and initialize the proxy atomically in a single factory transaction so no window exists. Alternatively, mirror the pattern used by `LRTConverter.initialize2()` and `RSETH.reinitialize()`, which gate re-initialization behind `onlyLRTAdmin` / `onlyLRTManager`, and apply an equivalent guard to the first initializer (e.g., using a deployer address stored in the implementation's immutable storage, or an `onlyOwner` pattern set in the constructor before `_disableInitializers()`). [6](#0-5) 

### Proof of Concept
1. Deployer broadcasts a transaction to deploy the `LRTConfig` proxy.
2. Deployer broadcasts a second transaction: `LRTConfig.initialize(legitAdmin, stETH, ethX, rsETH)`.
3. Attacker observes step 2 in the mempool and submits `LRTConfig.initialize(attackerEOA, maliciousStETH, maliciousEthX, maliciousRsETH)` with higher gas.
4. Attacker's transaction is mined first; `attackerEOA` is granted `DEFAULT_ADMIN_ROLE`.
5. Deployer's transaction reverts (`AlreadyInitialized`).
6. Attacker calls `setContract(LRT_ORACLE, maliciousOracle)` — all subsequent `rsETHPrice` reads return attacker-controlled values.
7. Attacker calls `setContract(LRT_DEPOSIT_POOL, maliciousPool)` — user deposits flow to the attacker.
8. All user funds deposited into the protocol after step 6 are stolen; the protocol is permanently under attacker control. [1](#0-0) [3](#0-2)

### Citations

**File:** contracts/LRTConfig.sol (L39-42)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** contracts/LRTConfig.sol (L49-62)
```text
    function initialize(address admin, address stETH, address ethX, address rsETH_) external initializer {
        UtilLib.checkNonZeroAddress(admin);
        UtilLib.checkNonZeroAddress(rsETH_);

        __AccessControl_init();
        _setToken(LRTConstants.ST_ETH_TOKEN, stETH);
        _setToken(LRTConstants.ETHX_TOKEN, ethX);
        _addNewSupportedAsset(stETH, 100_000 ether);
        _addNewSupportedAsset(ethX, 100_000 ether);

        _grantRole(DEFAULT_ADMIN_ROLE, admin);

        rsETH = rsETH_;
    }
```

**File:** contracts/LRTConfig.sol (L237-251)
```text
    function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setContract(contractKey, contractAddress);
    }

    /// @dev private function to set a contract
    /// @param key Contract key
    /// @param val Contract address
    function _setContract(bytes32 key, address val) private {
        UtilLib.checkNonZeroAddress(val);
        if (contractMap[key] == val) {
            revert ValueAlreadyInUse();
        }
        contractMap[key] = val;
        emit SetContract(key, val);
    }
```

**File:** contracts/LRTDepositPool.sol (L45-52)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTConverter.sol (L98-111)
```text
    function initialize2(
        address _withdrawalQueueAddress,
        address _stETHAddress,
        address _swEXITAddress,
        address _swETHAddress
    )
        external
        reinitializer(2)
        onlyLRTAdmin
    {
        __ReentrancyGuard_init();
        __initializeSwETH(_swEXITAddress, _swETHAddress);
        __initializeStETH(_withdrawalQueueAddress, _stETHAddress);
    }
```
