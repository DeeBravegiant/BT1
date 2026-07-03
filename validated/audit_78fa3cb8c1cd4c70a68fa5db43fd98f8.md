Audit Report

## Title
`LRTOracle._updateRsETHPrice()` Uses Spot Balances Manipulable via Flash Loan, Enabling Excess Protocol Fee Minting at Existing Holders' Expense — (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is a permissionless public function that computes protocol TVL using live spot balances of `LRTDepositPool`. An attacker can flash-loan LST tokens, transfer them directly to `LRTDepositPool` (bypassing `depositAsset`, so no rsETH is minted), and call `updateRSETHPrice()` in the same transaction. This inflates `totalETHInProtocol` without a corresponding increase in `rsethSupply`, manufacturing a fake reward delta against which the protocol mints excess rsETH as a fee to the treasury, permanently diluting existing rsETH holders.

## Finding Description

`updateRSETHPrice()` carries only a `whenNotPaused` modifier and is callable by any EOA or contract: [1](#0-0) 

`_updateRsETHPrice()` computes `previousTVL` from the stored `rsETHPrice` state variable and the current `rsethSupply`, then compares it against a freshly-read live TVL: [2](#0-1) 

`_getTotalEthInProtocol()` calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for each supported asset: [3](#0-2) 

`getAssetDistributionData` reads the raw ERC-20 balance of the deposit pool for LST assets: [4](#0-3) 

And the raw ETH balance for the ETH asset: [5](#0-4) 

`LRTDepositPool` exposes a bare `receive()` that accepts arbitrary ETH: [6](#0-5) 

Because `depositAsset` / `depositETH` are the only paths that mint rsETH, a direct ERC-20 transfer or ETH send to the pool inflates `balanceOf(address(this))` / `address(this).balance` without increasing `rsethSupply`. The fee computation then treats the entire inflated delta as a real reward: [7](#0-6) 

The resulting fee rsETH is minted to the treasury: [8](#0-7) 

And `highestRsethPrice` is updated to the inflated value: [9](#0-8) 

**Existing guards are insufficient:**

- `pricePercentageLimit`: Only blocks if `pricePercentageLimit > 0` AND the price increase exceeds the limit. The attacker calibrates flash-loan size to stay within the limit. If `pricePercentageLimit == 0` (unset), there is no per-call cap at all. [10](#0-9) 

- `maxFeeMintAmountPerDay` / `_checkAndUpdateDailyFeeMintLimit`: Caps total daily fee minting. If `maxFeeMintAmountPerDay == 0`, fee minting reverts (blocking the attack). However, for the protocol to function normally and collect legitimate fees, this value must be set to a non-zero amount, at which point the attacker can drain up to the full daily limit per day. [11](#0-10) 

**Secondary impact — protocol pause:** After the flash loan is repaid, the next honest `updateRSETHPrice()` call observes `newRsETHPrice < highestRsethPrice` (the inflated value was stored). If the drop exceeds `pricePercentageLimit`, the downside-protection branch executes, pausing `LRTDepositPool` and `LRTWithdrawalManager`: [12](#0-11) 

## Impact Explanation

**High — Theft of unclaimed yield.**

The treasury receives rsETH minted against no real new yield. Because the minted rsETH represents a claim on the underlying ETH pool, every pre-existing rsETH holder's proportional share of that pool is permanently reduced. The value extracted equals `protocolFeeInBPS / 10_000` of the flash-loaned amount converted to ETH, bounded only by `maxFeeMintAmountPerDay` per day. This is a concrete, on-chain, irreversible dilution of existing holders' unclaimed yield, matching the "High — Theft of unclaimed yield" impact class. The SECURITY.md explicitly states flash-loan attacks are not excluded from scope.

## Likelihood Explanation

**Medium.**

- `updateRSETHPrice()` requires no role, key, or governance compromise — any EOA or contract can call it.
- Flash loans for stETH, rETH, and cbETH are available on Ethereum mainnet via Aave and Balancer.
- The attacker must calibrate flash-loan size to stay within `pricePercentageLimit` (if set) to avoid a revert; multiple calls across blocks can drain `maxFeeMintAmountPerDay`.
- If `pricePercentageLimit == 0`, a single large flash loan suffices with no per-call cap.
- The attacker bears only the flash-loan fee; the dilution is permanent and repeatable each day.
- The attack is economically rational as a griefing/dilution vector or if the attacker holds a short position on rsETH.

## Recommendation

1. **Snapshot-guard `updateRSETHPrice()`**: Store the TVL at the end of each successful price update and use that snapshot as `previousTVL` in the next call, rather than recomputing it from live balances. This eliminates the ability to manufacture a fake reward delta within a single transaction.

2. **Restrict callers**: Gate `updateRSETHPrice()` to `onlyLRTManager` or a keeper role, removing the permissionless entry point entirely.

3. **Separate accounting from raw balances**: Track deposited LST amounts in an internal mapping updated only through `depositAsset` / `depositETH`, rather than reading `IERC20(asset).balanceOf(address(this))` and `address(this).balance` directly. This prevents direct-transfer inflation regardless of who calls the oracle update.

## Proof of Concept

```
Block N (single transaction):

1. Attacker flash-loans 10,000 stETH from Aave/Balancer.

2. Attacker calls stETH.transfer(LRTDepositPool, 10_000e18).
   - LRTDepositPool.getAssetDistributionData(stETH).assetLyingInDepositPool
     increases by 10_000e18.
   - rsETH.totalSupply() is UNCHANGED (no depositAsset call).

3. Attacker calls LRTOracle.updateRSETHPrice().
   - _getTotalEthInProtocol() returns real_TVL + 10_000 * stETH_price_in_ETH.
   - previousTVL = rsethSupply * rsETHPrice  (unchanged supply, old stored price).
   - rewardAmount = 10_000 * stETH_price_in_ETH  (entirely fake).
   - protocolFeeInETH = rewardAmount * feeBPS / 10_000.
   - rsethAmountToMintAsProtocolFee is minted to treasury.
   - rsETHPrice and highestRsethPrice are updated to the inflated value.

4. Attacker repays 10,000 stETH flash loan.

Result:
- Treasury holds excess rsETH minted against no real yield.
- All pre-existing rsETH holders are diluted by the minted fee amount.
- highestRsethPrice is now set to the inflated value; the next honest
  updateRSETHPrice() call will see a price drop and may trigger a protocol pause.
```

**Foundry fork test plan:**

```solidity
function testFlashLoanFeeManipulation() public {
    // Fork mainnet, deploy/use existing LRTDepositPool + LRTOracle
    uint256 supplyBefore = rsETH.totalSupply();
    uint256 treasuryBalanceBefore = rsETH.balanceOf(treasury);
    uint256 priceBefore = lrtOracle.rsETHPrice();

    // Simulate flash loan: transfer stETH directly to deposit pool
    deal(address(stETH), address(lrtDepositPool), 10_000e18);

    // Call permissionless price update
    lrtOracle.updateRSETHPrice();

    // Assert treasury received excess rsETH
    assertGt(rsETH.balanceOf(treasury), treasuryBalanceBefore);
    // Assert rsETH supply increased (fee minted) without any user deposit
    assertGt(rsETH.totalSupply(), supplyBefore);
    // Assert price was inflated
    assertGt(lrtOracle.rsETHPrice(), priceBefore);
    // Assert highestRsethPrice was set to inflated value
    assertEq(lrtOracle.highestRsethPrice(), lrtOracle.rsETHPrice());
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L204-207)
```text
        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }
```

**File:** contracts/LRTOracle.sol (L231-247)
```text
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

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L294-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L299-308)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```
