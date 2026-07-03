### Title
wrsETH Minted at L2 Oracle Rate Before L1 Deposit Dilutes Existing Holders During Bridge Delay - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
When a user deposits ETH on L2, `wrsETH` is minted immediately at the current oracle rate. The deposited ETH is only bridged to L1 and deposited into `LRTDepositPool` after a delay (bridge latency + operator action). During this delay, the rsETH exchange rate increases due to staking rewards. When the ETH is finally deposited on L1, it mints fewer rsETH than the wrsETH already issued, creating an undercollateralization shortfall borne by existing wrsETH holders — directly analogous to the original report's "interest accrues during delay, borne by existing vault users."

### Finding Description
In `RSETHPoolV3ExternalBridge.deposit()`, wrsETH is minted immediately at the current oracle rate:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
``` [1](#0-0) 

The rsETH amount is calculated as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [2](#0-1) 

The ETH then sits in the pool until a privileged bridger calls `bridgeAssets()` or `bridgeAssetsViaNativeBridge()` to send it to L1. On L1, `L1Vault.depositETHForL1VaultETH()` deposits the ETH to `LRTDepositPool` to mint rsETH:

```solidity
uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);
lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
``` [3](#0-2) 

The rsETH rate (`getRate()`) continuously increases due to staking rewards. Between the L2 mint and L1 deposit, the rate increases from `rate_t0` to `rate_t1 > rate_t0`. The ETH deposited to L1 mints `ETH / rate_t1` rsETH, but `ETH * (1 - feeBps/10000) / rate_t0` wrsETH was already minted. Since `rate_t1 > rate_t0`, the wrsETH minted exceeds the rsETH received, creating a shortfall borne by existing wrsETH holders.

The same pattern exists in `RSETHPoolV3.deposit()`: [4](#0-3) 

The fee (`feeBps`) is collected into `feeEarnedInETH` and sent to the protocol treasury — it does not compensate existing wrsETH holders for the dilution. The fee is also set by the admin and can be 0. [5](#0-4) 

### Impact Explanation
Existing wrsETH holders receive less rsETH than they should when redeeming, because the wrsETH is undercollateralized relative to the rsETH backing it. This is a continuous, ongoing loss that accumulates with every deposit and every bridge delay. The magnitude is proportional to the rate appreciation during the delay (~4–5% APY on rsETH, so ~0.01–0.05% per day of delay). The protocol's fee partially mitigates this but does not route compensation to existing holders, and the fee can be set to zero.

**Impact class**: Low — contract fails to deliver promised returns to existing wrsETH holders (their wrsETH redeems for less rsETH than it should).

### Likelihood Explanation
This occurs with every deposit, as there is always a non-zero delay between L2 mint and L1 deposit. The delay ranges from hours to days depending on bridge congestion and operator cadence. No special conditions are required — any unprivileged depositor calling `deposit()` triggers the condition.

### Recommendation
1. Ensure `feeBps` is set to at least cover the expected rate appreciation during the bridge delay, and route the fee to existing wrsETH holders rather than the protocol treasury.
2. Alternatively, use a conservative oracle rate that discounts for the expected delay period.
3. Document the maximum acceptable bridge delay and enforce it operationally to bound the dilution.

### Proof of Concept
1. Alice holds 100 wrsETH (backed by 100 rsETH on L1). rsETH rate = 1.050 ETH/rsETH.
2. Bob deposits 1 ETH on L2 (fee = 0 for simplicity). Oracle rate = 1.050 → `rsETHAmount = 1e18 / 1.050e18 = 0.9524 wrsETH` minted immediately.
3. ETH sits in pool for 1 day. Rate increases to 1.0514 ETH/rsETH (~5% APY).
4. Bridger bridges ETH to L1. `L1Vault.depositETHForL1VaultETH()` deposits 1 ETH → mints `1 / 1.0514 = 0.9511 rsETH`.
5. wrsETH minted (0.9524) > rsETH received (0.9511) → shortfall of 0.0013 rsETH per deposit.
6. Alice's 100 wrsETH is now backed by slightly less rsETH than before — her yield is permanently diluted by each such deposit.

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L377-383)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/L1Vault.sol (L152-158)
```text
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
