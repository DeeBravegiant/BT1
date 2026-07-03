### Title
`instantWithdrawal` Burns rsETH Without Checking Zero Output Amount - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.instantWithdrawal` burns the caller's rsETH before verifying that the computed asset output (`assetAmountUnlocked`) is non-zero. If the oracle-derived amount rounds to zero, the rsETH is permanently destroyed while the user receives nothing, and the function does not revert.

### Finding Description

`instantWithdrawal` computes the expected output via `getExpectedAssetAmount`, then immediately burns the caller's rsETH, and only afterwards checks whether the vault has enough liquidity:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked); // burn first
...
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
...
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
_transferAsset(asset, msg.sender, userAmount);
``` [1](#0-0) 

`getExpectedAssetAmount` computes:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [2](#0-1) 

When `assetAmountUnlocked == 0` (oracle returns 0 for `rsETHPrice`, or integer division truncates to zero), the guard `0 > getAssetsAvailableForInstantWithdrawal(asset)` evaluates to `false` and does **not** revert. The function proceeds to call `redeem(asset, 0)`, computes `fee = 0`, `userAmount = 0`, and transfers nothing to the user — while the rsETH burn at line 229 is already committed.

There is no `if (assetAmountUnlocked == 0) revert ...` guard anywhere in the function. The only input-side guard checks the rsETH amount, not the output:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [3](#0-2) 

### Impact Explanation

A user who calls `instantWithdrawal` when `assetAmountUnlocked` resolves to zero permanently loses their rsETH with no asset received in return. The rsETH total supply decreases (inflating the share price for remaining holders) while the caller's balance is zeroed. This is a direct, irreversible loss of user funds with no protocol-level revert to protect the caller.

**Impact: Critical** — direct theft/destruction of user funds (rsETH burned, zero assets returned).

### Likelihood Explanation

The zero-output condition is reachable via two paths:

1. **Oracle returns zero for `rsETHPrice`**: `LRTOracle.rsETHPrice()` is a computed value. If the oracle is stale, paused, or returns 0 due to a bug, `assetAmountUnlocked = amount * 0 / assetPrice = 0`.
2. **Integer truncation**: For an asset whose `assetPrice` is significantly larger than `rsETHPrice` (e.g., a high-value LST), small but non-zero `rsETHUnstaked` values (above `minRsEthAmountToWithdraw`) can produce `amount * rsETHPrice < assetPrice`, truncating to zero.

Both paths are reachable by any unprivileged user calling `instantWithdrawal` without requiring admin compromise.

### Recommendation

Add an explicit zero-output guard immediately after computing `assetAmountUnlocked`, **before** the burn:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
if (assetAmountUnlocked == 0) revert InvalidAmountToWithdraw();
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

This mirrors the fix recommended in the reference report: revert the transaction when the computed output is zero, rather than silently proceeding with a zero transfer.

### Proof of Concept

1. Deploy/use a mock `LRTOracle` that returns `rsETHPrice = 0` (or configure a real oracle in a state where it returns 0).
2. Call `instantWithdrawal(asset, minRsEthAmountToWithdraw[asset], "")` as any user holding rsETH.
3. Observe: `assetAmountUnlocked = minRsEthAmountToWithdraw[asset] * 0 / assetPrice = 0`.
4. The guard `0 > getAssetsAvailableForInstantWithdrawal(asset)` is `false` → no revert.
5. `burnFrom` executes, destroying the caller's rsETH.
6. `_transferAsset(asset, msg.sender, 0)` sends nothing.
7. User's rsETH balance is reduced; asset balance is unchanged. [4](#0-3) [2](#0-1)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L212-253)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(msg.sender, asset, rsETHUnstaked, userAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
