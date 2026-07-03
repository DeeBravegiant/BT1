### Title
Publicly Callable `updateRSETHPrice()` Enables Sandwich Attack to Extract Yield from rsETH Holders - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` is unrestricted (`public`, no role check) and updates `rsETHPrice`, the single variable used to price both deposits in `LRTDepositPool` and redemptions in `LRTWithdrawalManager`. An unprivileged attacker can atomically: (1) deposit at the stale low price, (2) trigger the price update themselves, and (3) instantly redeem at the new high price — extracting accrued yield that belongs to all rsETH holders.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is callable by anyone:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

`_updateRsETHPrice()` computes a new `rsETHPrice` from the current on-chain TVL divided by total rsETH supply and writes it to storage:

```solidity
rsETHPrice = newRsETHPrice;
``` [2](#0-1) 

This price is consumed directly by `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

And by `LRTWithdrawalManager.getExpectedAssetAmount()`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

`LRTWithdrawalManager.instantWithdrawal()` is a same-block redemption path (no delay) that uses the live `rsETHPrice` at execution time:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
``` [5](#0-4) 

Because `updateRSETHPrice()` is public and the deposit and instant-withdrawal paths both read `rsETHPrice` at execution time with no cooldown or snapshot isolation, the three operations can be composed in a single block.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

When staking rewards accrue (EigenLayer rewards, ETH staking yield, etc.), `totalETHInProtocol` grows above `rsethSupply × rsETHPrice`. Until `updateRSETHPrice()` is called, `rsETHPrice` is stale. An attacker who deposits at the stale price receives more rsETH than the fair post-reward price would grant. After triggering the price update themselves, they redeem at the inflated price, capturing yield that should have been distributed pro-rata to all existing rsETH holders.

Profit per attack ≈ `depositAmount × (P_new − P_old) / P_old`, minus fees. This is a direct transfer of accrued yield from honest holders to the attacker.

---

### Likelihood Explanation

**Likelihood: Medium.**

Prerequisites:
- `isInstantWithdrawalEnabled[asset]` must be `true` for at least one asset (a manager-controlled flag, but a normal operational state when instant withdrawals are live).
- Sufficient assets must be available in `LRTUnstakingVault` for the instant redemption.
- A meaningful gap must exist between the last `updateRSETHPrice()` call and the current TVL (i.e., rewards have accrued).

All three conditions are routinely satisfied during normal protocol operation. The attacker needs no special role — only capital and the ability to submit three transactions in sequence (or a single multicall). The `pricePercentageLimit` guard limits the per-update price jump for non-managers but does not prevent the attack; it only caps per-iteration profit, and the attack is repeatable every reward accrual cycle.

---

### Recommendation

1. **Snapshot price at deposit/withdrawal initiation** rather than reading live `rsETHPrice` at execution time, or enforce a cooldown between `updateRSETHPrice()` and any deposit/withdrawal in the same block.
2. **Restrict `updateRSETHPrice()` to a privileged role** (e.g., `OPERATOR_ROLE`) so the attacker cannot self-trigger the price update.
3. **Alternatively**, require the protocol to be paused during price updates and unpause only after verifying state consistency, analogous to the recommendation in the reference report.

---

### Proof of Concept

Assume:
- `rsETHPrice` = 1.00 ETH (stale; rewards have accrued, true price = 1.01 ETH)
- `isInstantWithdrawalEnabled[ETH_TOKEN]` = `true`
- Unstaking vault holds sufficient ETH

**Block N:**

1. Attacker calls `LRTDepositPool.depositETH{value: 100 ETH}(0, "")`.
   - `rsethAmountToMint = 100e18 * 1e18 / 1.00e18 = 100e18` rsETH minted.

2. Attacker calls `LRTOracle.updateRSETHPrice()`.
   - `rsETHPrice` updates to 1.01e18.

3. Attacker calls `LRTWithdrawalManager.instantWithdrawal(ETH_TOKEN, 100e18, "")`.
   - `assetAmountUnlocked = 100e18 * 1.01e18 / 1e18 = 101 ETH`.
   - Attacker receives 101 ETH (minus instant withdrawal fee), having deposited 100 ETH.

**Result:** Attacker extracts ~1 ETH of yield that belonged to all existing rsETH holders, with no risk and no privileged access. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

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

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
