### Title
Stale `rsETHPrice` Cache Enables Deposit-Then-Withdraw Arbitrage at Existing Holders' Expense - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.getRsETHAmountToMint()` computes the rsETH mint amount using a **live** Chainlink asset price (`lrtOracle.getAssetPrice(asset)`) divided by a **stale cached** rsETH price (`lrtOracle.rsETHPrice()`). Because `rsETHPrice` is only updated when the permissionless `LRTOracle.updateRSETHPrice()` is called, a window exists in every block where the two prices diverge. An unprivileged depositor can exploit this divergence to receive more rsETH than fair value, then call `updateRSETHPrice()` and initiate a withdrawal at the now-higher rsETH price, extracting value from existing holders.

### Finding Description
`LRTOracle` stores `rsETHPrice` as a cached state variable updated only on explicit calls to `updateRSETHPrice()` (public, no access control). [1](#0-0) [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` mixes a live oracle call with this stale cache:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`getAssetPrice(asset)` always reads the current Chainlink round: [4](#0-3) 

`rsETHPrice()` returns the last written value, which can be arbitrarily old.

The inverse formula in `LRTWithdrawalManager.getExpectedAssetAmount()` uses the same two values in the opposite direction:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [5](#0-4) 

This means the attacker can:
1. Deposit when `rsETHPrice` is stale-low → receive inflated rsETH.
2. Call `updateRSETHPrice()` → `rsETHPrice` rises to reflect the appreciated LST.
3. Initiate withdrawal → `expectedAssetAmount` is locked in at the now-higher `rsETHPrice`, returning more LST than was deposited.

The `expectedAssetAmount` is stored at `initiateWithdrawal` time and paid out at `completeWithdrawal` time, so the profit is crystallised even though there is a withdrawal delay. [6](#0-5) 

If `isInstantWithdrawalEnabled` is set for the asset, the entire cycle collapses into a single block. [7](#0-6) 

### Impact Explanation
Every time a supported LST appreciates on Chainlink before `updateRSETHPrice()` is called, an attacker can deposit at the stale (under-valued) rsETH price and withdraw at the updated (fair) price. The excess rsETH minted dilutes all existing holders; the attacker's gain is a direct transfer of yield from current rsETH holders. This maps to **High – theft of unclaimed yield**.

### Likelihood Explanation
`updateRSETHPrice()` is not called atomically with every deposit; it is called by off-chain keepers or bots. LST exchange rates (stETH, ETHx, sfrxETH) change continuously. Any block in which a Chainlink LST/ETH price update has been published but `updateRSETHPrice()` has not yet been called creates the exploitable window. This is a routine, recurring condition — **Medium likelihood**.

### Recommendation
Compute the rsETH mint amount using a freshly derived price rather than the cached `rsETHPrice`. Either:
- Call `_updateRsETHPrice()` (or an equivalent read-only computation of `totalETHInProtocol / rsethSupply`) inline inside `getRsETHAmountToMint()` before computing the mint ratio, or
- Require that `updateRSETHPrice()` has been called in the same block before any deposit is accepted (e.g., store `lastPriceUpdateBlock` and revert if `block.number != lastPriceUpdateBlock`).

### Proof of Concept

Assume:
- Current `rsETHPrice` (stale) = 1.050 ETH/rsETH
- stETH/ETH Chainlink price just updated to 1.010 ETH/stETH (was 1.000)
- Fair rsETH price (not yet written) ≈ 1.060 ETH/rsETH

**Step 1 – Deposit with stale price (same block, before `updateRSETHPrice`):**
```
rsethAmountToMint = 1000 stETH × 1.010e18 / 1.050e18 ≈ 961.9 rsETH
Fair amount                                             ≈ 952.8 rsETH
Excess rsETH received                                  ≈   9.1 rsETH
```
Entry point: `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` [8](#0-7) 

**Step 2 – Update price:**
```
LRTOracle.updateRSETHPrice()  →  rsETHPrice ≈ 1.060 ETH/rsETH
``` [2](#0-1) 

**Step 3 – Initiate withdrawal:**
```
expectedAssetAmount = 961.9 rsETH × 1.060e18 / 1.010e18 ≈ 1009.6 stETH
```
Entry point: `LRTWithdrawalManager.initiateWithdrawal(stETH, 961.9e18, "")` [6](#0-5) 

**Result:** Attacker deposited 1000 stETH and will receive ≈ 1009.6 stETH — a risk-free profit of ≈ 9.6 stETH extracted from existing rsETH holders. The profit scales linearly with deposit size and the magnitude of the price lag. With instant withdrawal enabled, the entire cycle executes atomically.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
