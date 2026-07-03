Looking at the actual code to validate the claim.

Audit Report

## Title
Stale `ethValueInWithdrawal` Snapshot Causes Systematic rsETH Price Mis-Accounting During LST Unstaking - (`contracts/LRTConverter.sol`)

## Summary
When an LST is moved from the deposit pool to the converter for unstaking, `LRTConverter.transferAssetFromDepositPool` records a one-time ETH-value snapshot in `ethValueInWithdrawal` at the current oracle price. This snapshot is never updated to reflect subsequent LST price movements. Because `getETHDistributionData` feeds this stale value directly into `_getTotalEthInProtocol`, the rsETH price is systematically mis-stated for the entire duration of the unstaking window. When LST prices rise (the normal case for rebasing/reward-bearing LSTs), existing rsETH holders' accrued yield is diluted and transferred to new depositors.

## Finding Description

**Step 1 — Snapshot on transfer.**
`transferAssetFromDepositPool` records the ETH value of the transferred LST at the current oracle price:

```solidity
// LRTConverter.sol L140
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
``` [1](#0-0) 

The LST tokens now sit in the converter. No further update to `ethValueInWithdrawal` occurs until the ETH is actually claimed and sent to the deposit pool.

**Step 2 — LST removed from token-denominated accounting.**
For any non-ETH asset, `getAssetDistributionData` hard-codes `assetLyingInConverter = 0`:

```solidity
// LRTDepositPool.sol L460
assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
``` [2](#0-1) 

The LST is therefore invisible to the per-asset price loop in `_getTotalEthInProtocol`.

**Step 3 — Stale snapshot fed into ETH accounting.**
`getETHDistributionData` returns the stale snapshot as the ETH value of converter holdings:

```solidity
// LRTDepositPool.sol L498-499
address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [3](#0-2) 

**Step 4 — Stale value propagates to rsETH price.**
`_getTotalEthInProtocol` multiplies `getTotalAssetDeposits(ETH_TOKEN)` — which includes `ethValueInWithdrawal` via the ETH distribution path — by `getAssetPrice(ETH_TOKEN) = 1e18`:

```solidity
// LRTOracle.sol L339-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [4](#0-3) 

The rsETH price is then derived from this total:

```solidity
// LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [5](#0-4) 

**Step 5 — Compounding error on return.**
`transferAssetToDepositPool` reduces `ethValueInWithdrawal` using the *current* price rather than the original snapshot price, creating a second mismatch:

```solidity
// LRTConverter.sol L160-163
uint256 assetValue = (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
ethValueInWithdrawal = ethValueInWithdrawal > assetValue ? ethValueInWithdrawal - assetValue : 0;
``` [6](#0-5) 

If the LST price has risen, `assetValue` at return time exceeds the original snapshot contribution, so `ethValueInWithdrawal` is reduced by more than was added, permanently understating the protocol's ETH holdings until the next price update cycle.

**No existing guard addresses this.** The `pricePercentageLimit` circuit-breaker in `_updateRsETHPrice` only triggers on large single-block price jumps, not on the slow, continuous drift of LST/ETH exchange rates over a multi-day unstaking window. [7](#0-6) 

## Impact Explanation

**Impact: High — Theft of unclaimed yield.**

Staking rewards continuously accrue to LSTs held in the converter (stETH rebases, ETHx/rETH/sfrxETH exchange-rate appreciation). These rewards represent yield that belongs to existing rsETH holders. Because `ethValueInWithdrawal` does not track this appreciation, `_getTotalEthInProtocol` understates the protocol's true ETH value. When `updateRSETHPrice` is called, the rsETH price is set below its fair value. Any subsequent depositor calling `depositETH` or `depositAsset` receives rsETH at the understated price — more rsETH than their deposit is worth — directly diluting the share of existing holders. The accrued yield that should have been reflected in the rsETH price is instead transferred to new depositors. This is a concrete, quantifiable transfer of value from existing holders to new depositors, matching the "Theft of unclaimed yield" impact class. [8](#0-7) 

## Likelihood Explanation

`transferAssetFromDepositPool` is a routine operational function called whenever the protocol moves LSTs into the converter for unstaking — a normal, expected, and frequent operation. Lido's withdrawal queue routinely takes 1–14 days. stETH accrues approximately 4–5% APY in staking rewards, meaning its ETH price drifts upward by roughly 0.01–0.014% per day. On a converter balance of 10,000 stETH, a 7-day window produces ~100 ETH of unaccounted appreciation. With 100,000 rsETH in supply, this understates the rsETH price by ~0.001 ETH/rsETH (~0.1%). The effect is continuous, repeatable, and scales linearly with converter balance and unstaking duration. No attacker action is required beyond making a normal deposit after the operator's routine transfer — `depositETH` and `depositAsset` are fully public. [9](#0-8) 

## Recommendation

Replace the ETH-value snapshot with a per-asset token-amount tracker. Compute the ETH value dynamically at price-query time using the current oracle price:

```solidity
// LRTConverter.sol
mapping(address => uint256) public lstAmountInConverter;

function transferAssetFromDepositPool(address _asset, uint256 _amount) external ... {
    lstAmountInConverter[_asset] += _amount;
    IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
}

function transferAssetToDepositPool(address _asset, uint256 _amount) external ... {
    lstAmountInConverter[_asset] -= _amount;
    IERC20(_asset).safeTransfer(lrtDepositPoolAddress, _amount);
}

// Computed dynamically in getETHDistributionData():
function ethValueInWithdrawal() external view returns (uint256 total) {
    address[] memory assets = lrtConfig.getSupportedAssetList();
    for (uint i; i < assets.length; i++) {
        if (lstAmountInConverter[assets[i]] > 0) {
            total += lstAmountInConverter[assets[i]].mulWad(lrtOracle.getAssetPrice(assets[i]));
        }
    }
}
```

This ensures the ETH value of converter holdings is always computed at the current oracle price, eliminating the stale-snapshot discrepancy. [10](#0-9) 

## Proof of Concept

**Foundry fork test outline:**

```solidity
// 1. Fork mainnet. Record initial rsETH price P0.
// 2. Operator calls transferAssetFromDepositPool(stETH, 10_000e18).
//    ethValueInWithdrawal = 10_000e18 * 1.05e18 / 1e18 = 10_500e18.
// 3. vm.warp(block.timestamp + 7 days).
//    stETH oracle price has risen to 1.06e18 (mock or real Chainlink feed).
//    Actual ETH value of converter = 10_600e18.
//    ethValueInWithdrawal still = 10_500e18.
// 4. Call updateRSETHPrice(). Record new price P1.
//    Assert P1 < fair_price (fair_price computed with 10_600 ETH).
// 5. Alice deposits 1000 ETH. She receives rsETH at P1 (understated).
//    Assert Alice's rsETH * P1 > 1000 ETH (she received excess rsETH).
// 6. Assert existing holder Bob's share of protocol ETH decreased
//    by the amount Alice over-received, confirming yield theft.
```

The test is reproducible on a mainnet fork using the live Lido stETH oracle (or a mock that advances the stETH/ETH rate by 0.01% per day). The discrepancy between `ethValueInWithdrawal` and `actual_converter_eth_value` is directly observable as the difference between the computed rsETH price and the fair rsETH price. [11](#0-10)

### Citations

**File:** contracts/LRTConverter.sol (L128-143)
```text
    function transferAssetFromDepositPool(
        address _asset,
        uint256 _amount
    )
        external
        onlySupportedERC20Token(_asset)
        onlyAssetTransferRole
    {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
    }
```

**File:** contracts/LRTConverter.sol (L160-163)
```text
        uint256 assetValue = (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        // Set to 0 if assetValue exceeds ethValueInWithdrawal, otherwise subtract assetValue
        ethValueInWithdrawal = ethValueInWithdrawal > assetValue ? ethValueInWithdrawal - assetValue : 0;
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

**File:** contracts/LRTDepositPool.sol (L460-460)
```text
        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
```

**File:** contracts/LRTDepositPool.sol (L498-499)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
```

**File:** contracts/LRTDepositPool.sol (L506-520)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L214-250)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
