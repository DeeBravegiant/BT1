### Title
Deposit at Stale rsETH Price Before `updateRSETHPrice()` Enables Yield Theft from Existing Holders - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositETH` and `depositAsset` mint rsETH using the stored, potentially stale `rsETHPrice` from `LRTOracle` without first calling `updateRSETHPrice()`. Because `updateRSETHPrice()` is a public, permissionless function, an attacker can deposit at the stale (lower) price to receive more rsETH than fair value, then immediately trigger a price update to capture accrued yield, and finally initiate a withdrawal at the new higher price — stealing yield that belongs to existing rsETH holders.

---

### Finding Description

`LRTDepositPool.getRsETHAmountToMint` computes the rsETH to mint as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

This reads `lrtOracle.rsETHPrice()`, which is the **last stored price** — not a freshly computed value. The price is only updated when `updateRSETHPrice()` is explicitly called.

`updateRSETHPrice()` is a public, permissionless function with no access control:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

`_updateRsETHPrice()` computes the new price from the current total ETH in the protocol (including accrued staking rewards from EigenLayer/LSTs) divided by the current rsETH supply:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [3](#0-2) 

When staking rewards accrue between price updates, `rsETHPrice` becomes stale and lower than the true value. A depositor who deposits before the price update receives **more rsETH than fair value** (because the denominator `rsETHPrice` is too low). After calling `updateRSETHPrice()`, the price rises to reflect the accrued rewards. The attacker then initiates a withdrawal at the new higher price, locking in a profit at the expense of existing holders.

`LRTWithdrawalManager.getExpectedAssetAmount` also reads the stored `rsETHPrice` at the time of withdrawal initiation:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

This locks in the inflated payout at the post-update price.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Let `P_stored` be the stale stored price and `P_true` be the true current price (`P_true > P_stored`). An attacker depositing `X` ETH receives `X / P_stored` rsETH instead of the fair `X / P_true`. After `updateRSETHPrice()` sets the price to `P_new` (where `P_stored < P_new ≤ P_true`), the attacker's rsETH is redeemable for `(X / P_stored) * P_new` ETH. Since `P_new > P_stored`, the attacker profits `X * (P_new / P_stored - 1)` ETH. This profit is extracted directly from the yield that should have been distributed to existing rsETH holders, whose share value is diluted.

The attack is repeatable at every price-update interval, allowing an attacker to systematically drain accrued staking yield.

---

### Likelihood Explanation

**Medium.** The attack is profitable whenever `rsETHPrice` is stale — i.e., any time staking rewards have accrued since the last `updateRSETHPrice()` call. The protocol does not enforce automatic price updates on deposit. The attacker needs only to:
1. Monitor the mempool or on-chain state for a stale price.
2. Execute deposit → `updateRSETHPrice()` → `initiateWithdrawal` in sequence (not necessarily atomically, since the price update step is separate from the deposit).
3. Wait for the `withdrawalDelayBlocks` (~8 days) to complete the withdrawal.

The capital lockup period reduces frequency but does not eliminate profitability, especially for large deposits or long accrual periods.

---

### Recommendation

Call `updateRSETHPrice()` (or its internal equivalent `_updateRsETHPrice()`) at the beginning of `depositETH` and `depositAsset` in `LRTDepositPool`, before computing `getRsETHAmountToMint`. This ensures the rsETH price is always fresh at the time of minting, analogous to the WildCredit recommendation to `accrue` before minting shares.

```solidity
function depositETH(...) external payable nonReentrant whenNotPaused ... {
    // Accrue price first
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    uint256 rsethAmountToMint = _beforeDeposit(...);
    _mintRsETH(rsethAmountToMint);
}
``` [5](#0-4) 

---

### Proof of Concept

1. Assume `rsETHPrice` stored = `1.00 ETH` (stale), true current price = `1.01 ETH` (0.01 ETH of staking rewards accrued since last update).
2. Attacker calls `depositETH{value: 100 ETH}(0, "")`.
   - `rsethAmountToMint = 100e18 * 1e18 / 1.00e18 = 100 rsETH` (should be ~99.01 rsETH at true price).
3. Attacker calls `LRTOracle.updateRSETHPrice()`.
   - New price reflects accrued rewards: `rsETHPrice` updates to ~`1.01 ETH`.
4. Attacker calls `LRTWithdrawalManager.initiateWithdrawal(ETH, 100 rsETH, "")`.
   - `expectedAssetAmount = 100 * 1.01e18 / 1e18 = 101 ETH`.
5. After `withdrawalDelayBlocks`, attacker calls `completeWithdrawal` and receives `101 ETH`.
6. **Net profit: 1 ETH** stolen from existing rsETH holders' accrued yield. [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

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

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
