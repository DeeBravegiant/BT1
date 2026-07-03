### Title
Stale Cross-Chain Oracle Rate Allows Users to Mint Excess rsETH and Drain Pool Yield - (File: contracts/pools/RSETHPoolV3.sol)

### Summary

All L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`) compute the rsETH amount to mint using a cross-chain oracle rate that is inherently stale between LayerZero update messages. When the on-chain oracle rate is lower than the true rsETH/ETH rate, depositors receive more rsETH than their deposit is worth. For token deposits, a second Chainlink oracle with its own deviation threshold compounds the discrepancy. The excess rsETH represents a claim on more ETH than was deposited, and the deficit is borne by existing rsETH holders.

### Finding Description

Every L2 pool computes the rsETH amount to mint via `viewSwapRsETHAmountAndFee`. For ETH deposits:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

For token deposits (two-oracle path):

```solidity
uint256 rsETHToETHrate = getRate();
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`getRate()` reads from `rsETHOracle`, which is a cross-chain rate propagated from L1 via LayerZero. This rate is only updated when someone explicitly triggers a cross-chain message from the `RSETHRateProvider` or `RSETHMultiChainRateProvider`. Between updates the L2 oracle is stale. Because rsETH is a yield-bearing token whose value monotonically increases, the L2 oracle rate is always at most equal to, and typically below, the true L1 rate.

When `rsETHToETHrate` (oracle) < true rsETH/ETH rate, the division `amountAfterFee * 1e18 / rsETHToETHrate` yields a larger rsETH amount than the deposited ETH is actually worth at the true rate. For the token path, if the Chainlink `tokenToETHRate` is simultaneously at the top of its deviation band (e.g. 0.5% above true price for wstETH/ETH), the two errors compound: the numerator is inflated and the denominator is deflated.

The minted wrsETH/rsETH is a claim on the L1 backing pool. When the user bridges it to L1 and redeems via `LRTWithdrawalManager`, they receive assets computed at the true (higher) rsETH price. The shortfall is absorbed by all existing rsETH holders through dilution of the backing ratio.

There is no mechanism that prevents this: `feeBps` can be set to 0, and even when non-zero, a combined oracle discrepancy of 0.5–1% easily exceeds a typical 10–30 bps deposit fee.

### Impact Explanation

Every time the L2 oracle lags the true L1 rsETH price (which is the normal state between cross-chain updates), any depositor can extract value from the pool. The extracted value comes directly from the yield accrued by existing rsETH holders, because the excess rsETH minted dilutes the backing ratio. This is a continuous, permissionless drain of accrued yield from all rsETH holders. At scale (multiple L2 chains, multiple pools, daily mint limits reset each day), the cumulative loss is material.

**Impact class**: High — Theft of unclaimed yield from existing rsETH holders.

### Likelihood Explanation

The L2 oracle is structurally stale between cross-chain rate updates. rsETH accrues staking yield continuously, so the true rate always exceeds the last propagated rate. No special market conditions are required; the discrepancy exists at all times. Any depositor can observe the stale rate on-chain and time their deposit accordingly. The Chainlink deviation threshold for collateral tokens (e.g. wstETH/ETH at 0.5%) is a well-known, publicly observable parameter. The attack requires no privileged access, no flash loans, and no front-running.

### Recommendation

1. **Enforce a minimum deposit fee** (`feeBps`) that exceeds the maximum expected oracle lag plus the Chainlink deviation threshold for all supported tokens. This makes the attack unprofitable.
2. **Add a withdrawal fee** on the L1 side for rsETH redeemed shortly after an L2 deposit, analogous to the Olympus fix.
3. **Bound oracle staleness**: revert deposits if the L2 oracle rate has not been updated within a configurable heartbeat window.
4. **Use a TWAP or rate-limiting mechanism** on the L2 oracle to smooth out sudden discrepancies.

### Proof of Concept

**Setup**: `RSETHPoolV3` on Arbitrum. True rsETH/ETH rate on L1 = 1.060 ETH/rsETH. Last propagated L2 oracle rate = 1.050 ETH/rsETH (stale by ~1%). Chainlink wstETH/ETH oracle at top of 0.5% deviation band: reports 1.005 ETH/wstETH, true price = 1.000 ETH/wstETH. `feeBps = 0`.

**Step 1**: Attacker calls `deposit(wstETH, 1000e18, "")` on `RSETHPoolV3`.

`viewSwapRsETHAmountAndFee(1000e18, wstETH)` computes:
- `rsETHToETHrate = 1.050e18` (stale oracle)
- `tokenToETHRate = 1.005e18` (Chainlink at top of band)
- `rsETHAmount = 1000e18 * 1.005e18 / 1.050e18 = 957.14e18` rsETH

True fair rsETH amount = `1000 * 1.000 / 1.060 = 943.40` rsETH.

Attacker receives **957.14 rsETH** instead of **943.40 rsETH** — an excess of **13.74 rsETH**.

**Step 2**: Attacker unwraps wrsETH → rsETH, bridges to L1 via LayerZero OFT.

**Step 3**: Attacker calls `LRTWithdrawalManager.initiateWithdrawal` then `completeWithdrawal` (or `instantWithdrawal`). At the true rate of 1.060 ETH/rsETH, 957.14 rsETH redeems for **1014.57 ETH**.

**Profit**: 1014.57 − 1000 = **14.57 ETH** extracted from existing rsETH holders per 1000 wstETH deposited. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L234-237)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-307)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-334)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-452)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-311)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/cross-chain/RSETHRateProvider.sol (L26-28)
```text
    /// @notice Returns the latest rate from the rsETH contract
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
```
