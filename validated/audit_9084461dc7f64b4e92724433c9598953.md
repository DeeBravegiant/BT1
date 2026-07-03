### Title
Fee Calculation Rounds Down to Zero, Allowing Depositors to Bypass Protocol Fees Entirely - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
Every L2 deposit pool in the LRT-rsETH protocol computes the protocol fee using integer division that rounds down toward zero. When a depositor's `amount * feeBps` is less than `10_000`, the fee truncates to exactly zero. This is the direct analog of the reported `mulDivDown` rounding vulnerability: the rounding direction favors the depositor instead of the protocol, allowing fee-free deposits and draining the protocol's expected fee revenue.

### Finding Description
In `viewSwapRsETHAmountAndFee` across every pool variant, the fee is computed as:

```solidity
fee = amount * feeBps / 10_000;
```

Solidity integer division truncates toward zero. Whenever `amount * feeBps < 10_000`, the result is `0` and the depositor pays no fee at all. The full `amount` is then used as `amountAfterFee`, and the depositor receives the maximum possible rsETH/wrsETH/agETH output with zero cost to the protocol.

This pattern is present identically in all production pool contracts:

- `RSETHPoolV3.sol` — `viewSwapRsETHAmountAndFee(uint256)` and `viewSwapRsETHAmountAndFee(uint256, address)`
- `RSETHPoolV3ExternalBridge.sol` — same two overloads
- `RSETHPoolNoWrapper.sol` — same two overloads
- `RSETHPool.sol` — same two overloads (ETH uses `feeBps`, tokens use `tokenFeeBps`)
- `RSETHPoolV2.sol` — `viewSwapRsETHAmountAndFee(uint256)`
- `AGETHPoolV3.sol` — `viewSwapAgETHAmountAndFee(uint256)` and `viewSwapAgETHAmountAndFee(uint256, address)` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10) 

### Impact Explanation
The fee is the protocol's sole revenue from L2 deposits. When it rounds to zero, the protocol earns nothing on those deposits. An attacker (or any ordinary depositor) can split a large deposit into many sub-threshold transactions and receive the full rsETH/wrsETH/agETH output while paying zero fees. Over time, across all deployed L2 pools, this continuously drains the protocol's expected fee income.

**Impact: High — Theft of unclaimed yield (protocol fee revenue).**

### Likelihood Explanation
The zero-fee threshold is `floor(10_000 / feeBps)` units of the deposit token. For example:

- `feeBps = 5` (0.05%): any single deposit of `< 2000 wei` of ETH pays zero fee.
- `feeBps = 1` (0.01%): any single deposit of `< 10_000 wei` of ETH pays zero fee.
- For tokens with low decimals (e.g., WBTC at 8 decimals, `feeBps = 1`): a deposit of `9_999` satoshis (~$6 at $60k/BTC) pays zero fee.

Any depositor can exploit this without any special privilege, capital, or front-running. The attack is simply splitting deposits into amounts below the threshold. On L2s where gas is cheap, this is economically rational for any depositor who wants to avoid fees. **Likelihood: High.**

### Recommendation
Round the fee up in favor of the protocol using ceiling division:

```solidity
// Replace:
fee = amount * feeBps / 10_000;

// With (ceiling division):
fee = (amount * feeBps + 9_999) / 10_000;
// or equivalently using OZ Math:
fee = Math.mulDiv(amount, feeBps, 10_000, Math.Rounding.Up);
```

This ensures that any non-zero `feeBps` on any non-zero `amount` always results in at least 1 unit of fee, matching the invariant that the protocol always collects its configured fee rate.

### Proof of Concept
Consider `RSETHPoolV3` deployed on an L2 with `feeBps = 5` (0.05%):

```
amount = 1999 wei ETH
fee = 1999 * 5 / 10_000 = 9995 / 10_000 = 0   ← rounds to zero
amountAfterFee = 1999 - 0 = 1999               ← full amount used
rsETHAmount = 1999 * 1e18 / rsETHToETHrate      ← depositor gets full rsETH
feeEarnedInETH += 0                             ← protocol earns nothing
```

An attacker deposits 1 ETH as 501 separate transactions of 1999 wei each:
- Total deposited: 501 × 1999 = ~1 ETH
- Total fee paid: 501 × 0 = **0 wei**
- Expected fee at 0.05%: ~500,000 wei (~$0.001 per tx, but scales with deposit size)

On L2s with sub-cent gas costs, this is profitable for any depositor with a non-trivial deposit amount. The same attack applies to all token deposits across `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV2`, and `AGETHPoolV3`. [12](#0-11) [13](#0-12)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPoolV3.sol (L323-325)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-279)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L300-302)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-162)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L183-185)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-420)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L441-443)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPool.sol (L311-313)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPool.sol (L335-337)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-227)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```
