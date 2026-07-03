### Title
Stale `rsETHPrice` Read Without Prior Update in Deposit Minting Leads to Excess rsETH Issued — (File: `contracts/LRTDepositPool.sol`)

### Summary
`depositETH` and `depositAsset` in `LRTDepositPool` compute the rsETH mint amount using `lrtOracle.rsETHPrice()`, a cached state variable that is only updated when `updateRSETHPrice()` is explicitly called. Neither deposit function triggers a price update before reading it. When staking rewards have accrued since the last update, the stored price is lower than the true current rate, causing depositors to receive more rsETH than they are entitled to. This dilutes existing holders and constitutes theft of their accrued yield.

### Finding Description
`getRsETHAmountToMint` computes the mint amount as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.rsETHPrice()` is a stored state variable in `LRTOracle` that is only written when `_updateRsETHPrice()` is called:

```solidity
rsETHPrice = newRsETHPrice;
``` [2](#0-1) 

Neither `depositETH` nor `depositAsset` calls `updateRSETHPrice()` before invoking `_beforeDeposit` → `getRsETHAmountToMint`: [3](#0-2) [4](#0-3) 

As staking rewards accrue, the true rsETH/ETH rate rises, but `rsETHPrice` remains at its last-updated (lower) value. A depositor who deposits while the price is stale receives:

```
rsethAmountToMint = amount * assetPrice / stalePrice   (stalePrice < truePrice)
```

which is strictly greater than the correct amount `amount * assetPrice / truePrice`. The excess rsETH represents a claim on yield that belongs to existing holders.

This is the direct analog of the reported issue: just as `calcLiquidationAmounts` reads `violatorLoanBorrow.balance` without first calling `updateLoanBorrowInterests`, `depositETH`/`depositAsset` reads `rsETHPrice` without first calling `updateRSETHPrice()`.

### Impact Explanation
**High — Theft of unclaimed yield.** Existing rsETH holders' accrued staking rewards are diluted by the excess rsETH minted to the depositor. After `updateRSETHPrice()` is eventually called (reflecting the true higher price), the attacker's excess rsETH redeems for more underlying assets than were deposited, extracting value from existing holders. The magnitude scales with deposit size and the duration of price staleness.

### Likelihood Explanation
`updateRSETHPrice()` is a public function with no on-chain enforcement of call frequency: [5](#0-4) 

The protocol relies entirely on off-chain keepers to call it. Any gap between keeper calls creates an exploitable window. An attacker can monitor the chain, identify when the stored `rsETHPrice` lags behind the true TVL-derived rate, and deposit a large amount immediately before the next keeper update. The `pricePercentageLimit` guard can also cause `updateRSETHPrice()` to revert for non-managers when the price increase is large, potentially extending the staleness window: [6](#0-5) 

### Recommendation
Call `updateRSETHPrice()` (or an internal equivalent) at the start of `depositETH` and `depositAsset` before computing the mint amount, ensuring the price reflects the current TVL before any rsETH is issued.

### Proof of Concept
1. At block T₀, `updateRSETHPrice()` is called; `rsETHPrice` is set to P₀ = 1.01 ETH/rsETH.
2. Staking rewards accrue. At block T₁ (e.g., 24 hours later), the true rate is P₁ = 1.012 ETH/rsETH, but `rsETHPrice` still stores P₀.
3. Attacker calls `depositETH{value: 1000 ETH}(minRSETH, "")`.
4. `getRsETHAmountToMint` returns `1000e18 * 1e18 / P₀ ≈ 990.099 rsETH` instead of the correct `1000e18 * 1e18 / P₁ ≈ 988.142 rsETH`.
5. Attacker receives ≈ 1.957 excess rsETH at no cost.
6. Keeper calls `updateRSETHPrice()`, setting `rsETHPrice = P₁`.
7. Attacker initiates withdrawal; their excess rsETH redeems for ≈ 1.957 × 1.012 ≈ 1.98 ETH of value extracted from existing holders.
8. The attack is repeatable every keeper cycle and scales linearly with deposit size. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L110-116)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

```

**File:** contracts/LRTDepositPool.sol (L506-521)
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
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
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

```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
