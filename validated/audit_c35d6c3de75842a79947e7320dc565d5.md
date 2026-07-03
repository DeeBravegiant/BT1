### Title
L2 Oracle Rate Divergence from L1 Execution Rate Causes wrsETH Over-Minting and Wrapper Undercollateralization - (File: contracts/pools/RSETHPoolV3.sol, contracts/L1Vault.sol)

### Summary
The L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`) mint wrsETH/rsETH to depositors immediately using the L2 oracle rate (`rsETHOracle.getRate()`). The deposited ETH/LST is only converted to rsETH on L1 in a separate, later transaction at the then-current L1 oracle rate. When the L2 oracle rate lags behind the actual L1 rsETH price, users receive more wrsETH than the rsETH that will be deposited to back it, causing the wrapper to become undercollateralized.

### Finding Description

The deposit lifecycle is a two-step, two-chain process:

**Step 1 (L2, immediate):** A user calls `RSETHPoolV3.deposit()`. The pool computes `rsETHAmount` using the L2 oracle rate and immediately mints wrsETH to the user.

```
// RSETHPoolV3.sol L299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // <-- L2 oracle rate
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The ETH stays in the pool contract. The user already holds wrsETH.

**Step 2 (L1, separate transaction):** A privileged bridger later calls `moveAssetsForBridging()` / `bridgeAssets()` to send the pooled ETH to L1. On L1, the manager calls `L1Vault.depositETHForL1VaultETH()`, which deposits into `LRTDepositPool` at the **current L1 oracle rate**:

```
// L1Vault.sol L152-158
uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);
lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
```

```
// LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

The rsETH minted on L1 is then bridged back to the L2 wrapper to back the wrsETH already in circulation.

**The mismatch:** The L2 oracle (`rsETHOracle`) is a cross-chain rate provider that is updated periodically and asynchronously relative to the L1 `LRTOracle.rsETHPrice`. As rsETH accrues staking rewards, `rsETHPrice` on L1 increases monotonically. If the L2 oracle has not yet been updated to reflect the latest L1 price:

- L2 oracle rate (stale, lower) → `rsETHAmount = ETH / staleRate` → **larger** wrsETH minted to user
- L1 oracle rate (current, higher) → `rsethAmountToMint = ETH / currentRate` → **smaller** rsETH minted on L1

The wrapper receives less rsETH than the wrsETH it has already issued, making it structurally undercollateralized.

### Impact Explanation

**Critical — Protocol insolvency / permanent undercollateralization of the wrsETH wrapper.**

Every deposit made while the L2 oracle lags behind the L1 price results in more wrsETH being minted than rsETH deposited to back it. Since wrsETH is redeemable 1:1 for rsETH through the wrapper, the shortfall is a direct loss borne by the protocol or by later redeemers who cannot withdraw their full rsETH entitlement. The deficit compounds with each deposit during the lag window and is not self-correcting.

### Likelihood Explanation

**Medium.** The L2 oracle rate is updated off-chain and pushed cross-chain. There is always a non-zero lag between L1 rsETH price appreciation (driven by EigenLayer staking rewards accruing continuously) and the L2 oracle reflecting that price. The bridging delay (ETH must travel from L2 to L1) and the additional delay before the manager calls `depositETHForL1VaultETH()` further widen the window. During periods of rapid reward accrual or oracle update delays, the divergence can be material. No special attacker capability is required — any depositor benefits from the stale rate.

### Recommendation

1. **Record the L2 oracle rate at deposit time** and pass it as a minimum rsETH expectation to the L1 deposit step, reverting if the L1 execution rate yields fewer rsETH than the wrsETH already minted.
2. Alternatively, **do not mint wrsETH at deposit time**. Instead, issue a receipt/claim ticket and only mint wrsETH after the L1 rsETH amount is confirmed and bridged back, ensuring the minted amount matches the actual rsETH received.
3. Enforce a **maximum acceptable divergence** between the L2 oracle rate and the L1 oracle rate before allowing deposits, similar to a slippage guard.

### Proof of Concept

Assume:
- L1 `rsETHPrice` = 1.05 ETH (recently updated, reflects latest staking rewards)
- L2 `rsETHOracle.getRate()` = 1.03 ETH (stale, not yet updated)

**Attacker deposits 100 ETH on L2 (Step 1):**

```
rsETHAmount = 100e18 * 1e18 / 1.03e18 ≈ 97.087 wrsETH minted to attacker
```

**Bridger moves 100 ETH to L1 and manager calls `depositETHForL1VaultETH()` (Step 2):**

```
rsethAmountToMint = 100e18 * 1e18 / 1.05e18 ≈ 95.238 rsETH minted on L1
```

**Result:**
- wrsETH in circulation backed by this deposit: **97.087**
- rsETH deposited into wrapper to back it: **95.238**
- **Shortfall: ~1.849 rsETH** (≈1.85% of deposit value) permanently missing from the wrapper

The attacker holds 97.087 wrsETH redeemable for 97.087 rsETH, but only 95.238 rsETH was deposited. The deficit is absorbed by the wrapper's existing rsETH reserves, diluting all other wrsETH holders. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L258-263)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/L1Vault.sol (L150-161)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
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
