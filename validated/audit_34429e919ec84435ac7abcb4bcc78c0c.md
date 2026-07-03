### Title
Admin Role Is a Single Point of Failure Enabling Direct Theft of Any User's rsETH via Freeze-and-Recover Mechanism - (File: contracts/RSETH.sol)

### Summary

The `DEFAULT_ADMIN_ROLE` held in `LRTConfig` is a single point of failure across the LRT-rsETH protocol. In `RSETH.sol`, the admin role controls both the `custodyAddress` destination and the ability to drain any non-exempt user's entire rsETH balance via `recoverFrozenFunds`. Because `DEFAULT_ADMIN_ROLE` is the OpenZeppelin role-admin for all roles (including `MANAGER`), a single compromised admin key can execute a complete theft of any user's rsETH in three on-chain steps with no timelock or multi-step delay protecting the victim.

### Finding Description

`RSETH.sol` introduces a freeze-and-recover system with three admin-controlled primitives:

1. **`setCustodyAddress(address)`** — callable by `onlyLRTAdmin`, sets the destination for recovered funds to any arbitrary address.
2. **`blockUserTransfers(address[])`** — callable by `onlyLRTManager`, blocks all transfers to/from the listed accounts for 24 hours.
3. **`recoverFrozenFunds(address from)`** — callable by `onlyLRTAdmin`, forcibly transfers the victim's entire rsETH balance to `custodyAddress` via `super._transfer`, bypassing the transfer-block enforcement. [1](#0-0) 

The `DEFAULT_ADMIN_ROLE` holder in `LRTConfig` is the OpenZeppelin role-admin for every role, including `LRTConstants.MANAGER`. This means the admin can self-grant the MANAGER role at any time. [2](#0-1) 

The `onlyLRTManager` and `onlyLRTAdmin` modifiers in `LRTConfigRoleChecker` both resolve roles through the same `lrtConfig` `AccessControlUpgradeable` instance, so a single address holding `DEFAULT_ADMIN_ROLE` can satisfy both guards. [3](#0-2) 

### Impact Explanation

**Critical — Direct theft of any user's rsETH balance.**

A malicious or compromised admin executes:
1. `LRTConfig.grantRole(MANAGER, attacker)` — self-grants MANAGER.
2. `RSETH.setCustodyAddress(attacker)` — redirects recovery destination to attacker.
3. `RSETH.blockUserTransfers([victim])` — freezes victim for 24 hours.
4. `RSETH.recoverFrozenFunds(victim)` — drains victim's entire rsETH balance to attacker. [4](#0-3) [5](#0-4) 

The `super._transfer` call in `recoverFrozenFunds` bypasses the `_enforceNotBlocked` hook, so the transfer succeeds even while the victim's account is frozen. The victim cannot move their tokens to safety during the 24-hour freeze window. [6](#0-5) 

### Likelihood Explanation

**Medium.** The `DEFAULT_ADMIN_ROLE` is a single key (or multisig) that controls the entire protocol. All critical operations — oracle updates, contract address changes, strategy updates, and the freeze/recover mechanism — are gated behind this one role. This concentration makes the admin key a high-value target. No timelock or secondary approval is required between `blockUserTransfers` and `recoverFrozenFunds`, so the theft completes within a single block after the freeze is applied. [7](#0-6) [8](#0-7) 

### Recommendation

- **Short term:** Separate the `recoverFrozenFunds` capability from the general admin role. Require a dedicated, independently-held role (e.g., a separate `RECOVERY_ROLE`) that is not self-grantable by the admin. Enforce a mandatory time delay (e.g., 48–72 hours) between `blockUserTransfers` and `recoverFrozenFunds` to give victims time to respond.
- **Short term:** Add a `isPermanentlyExempt` flag for all active user addresses by default, requiring an explicit opt-out, rather than the current opt-in exemption model.
- **Long term:** Distribute critical privileges across separate multisigs with different key holders. Introduce a timelock contract as the admin of `LRTConfig` so that any role grant or critical parameter change has a mandatory delay before taking effect.

### Proof of Concept

```
// Attacker holds DEFAULT_ADMIN_ROLE in LRTConfig

// Step 1: Self-grant MANAGER role
lrtConfig.grantRole(LRTConstants.MANAGER, attacker);

// Step 2: Redirect custody to attacker
rsETH.setCustodyAddress(attacker);

// Step 3: Freeze victim
address[] memory victims = new address[](1);
victims[0] = victim;
rsETH.blockUserTransfers(victims);
// victim cannot transfer rsETH for 24 hours

// Step 4: Drain victim's balance (same block or any time within 24h)
rsETH.recoverFrozenFunds(victim);
// attacker now holds victim's entire rsETH balance
``` [9](#0-8) [1](#0-0)

### Citations

**File:** contracts/RSETH.sol (L156-177)
```text
    /// @notice Block transfers TO and FROM given users for 24 hours
    /// @dev Re-applying the block before expiry refreshes the hold to `block.timestamp + 1 days`
    ///      (i.e. not cumulative; never more than 24h from the latest call). Exempt addresses cannot be blocked.
    ///      Emits {UserTransfersBlocked} only when the timestamp changes.
    /// @param accounts Addresses to block.
    function blockUserTransfers(address[] calldata accounts) external onlyLRTManager {
        uint256 blockedUntil = block.timestamp + 1 days;
        uint256 length = accounts.length;

        for (uint256 i = 0; i < length; ++i) {
            address account = accounts[i];

            if (isPermanentlyExempt[account] || account == address(0)) continue;

            uint256 prevBlockedUntil = transfersBlockedUntil[account];

            if (blockedUntil != prevBlockedUntil) {
                transfersBlockedUntil[account] = blockedUntil;
                emit UserTransfersBlocked(account, blockedUntil);
            }
        }
    }
```

**File:** contracts/RSETH.sol (L199-219)
```text
    function setCustodyAddress(address newCustodyAddress) external onlyLRTAdmin {
        _setCustodyAddress(newCustodyAddress);
    }

    /// @notice Recover the entire balance from a currently blocked, non-exempt address to a designated custody address
    /// @dev Only callable by LRT admin. Works only while the block is active.
    ///      Emits {FrozenFundsRecovered} even if the recovered amount is zero (for transparency and completeness).
    function recoverFrozenFunds(address from) external onlyLRTAdmin {
        UtilLib.checkNonZeroAddress(from);
        UtilLib.checkNonZeroAddress(custodyAddress);

        if (isPermanentlyExempt[from]) revert AddressPermanentlyExempt(from);

        uint256 blockedUntil = transfersBlockedUntil[from];
        if (blockedUntil == 0 || block.timestamp >= blockedUntil) revert NoActiveTransferBlock(from);

        uint256 accountBalance = balanceOf(from);

        // Bypass transfer block enforcement when transferring to custody address
        super._transfer(from, custodyAddress, accountBalance);
        emit FrozenFundsRecovered(from, custodyAddress, accountBalance);
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

**File:** contracts/utils/LRTConfigRoleChecker.sol (L27-63)
```text
    modifier onlyLRTManager() {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
            revert ILRTConfig.CallerNotLRTConfigManager();
        }
        _;
    }

    modifier onlyLRTOperator() {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.OPERATOR_ROLE, msg.sender)) {
            revert ILRTConfig.CallerNotLRTConfigOperator();
        }
        _;
    }

    modifier onlyAssetTransferRole() {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.ASSET_TRANSFER_ROLE, msg.sender)) {
            revert ILRTConfig.CallerNotLRTConfigAssetTransferRole();
        }
        _;
    }

    modifier onlyAssetTransferOrOperatorRole() {
        if (
            !IAccessControl(address(lrtConfig)).hasRole(LRTConstants.ASSET_TRANSFER_ROLE, msg.sender)
                && !IAccessControl(address(lrtConfig)).hasRole(LRTConstants.OPERATOR_ROLE, msg.sender)
        ) {
            revert ILRTConfig.CallerNotLRTConfigOperatorOrAssetTransferRole();
        }
        _;
    }

    modifier onlyLRTAdmin() {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.DEFAULT_ADMIN_ROLE, msg.sender)) {
            revert ILRTConfig.CallerNotLRTConfigAdmin();
        }
        _;
    }
```

**File:** contracts/LRTOracle.sol (L101-119)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
    }

    /// @dev add/update the price oracle of any asset
    /// @dev only onlyLRTAdmin is allowed
    /// @param asset asset address for which oracle price needs to be added/updated
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
    }
```
