### Title
No Deadline Protection on `deposit()` Allows Fee Increase to Silently Reduce User's wrsETH Payout - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

Every L2 deposit pool calculates the protocol fee at **execution time** using the live `feeBps` state variable. The admin can raise `feeBps` instantly via `setFeeBps()` (no timelock). A user who submits a `deposit()` transaction while `feeBps` is low can have that transaction execute after the admin raises the fee, receiving materially fewer `wrsETH` tokens than they anticipated with no on-chain protection.

---

### Finding Description

In all L2 pool variants the deposit flow is:

1. User calls `deposit()` (ETH or token variant).
2. Inside the function, `viewSwapRsETHAmountAndFee()` is called, which reads the **current** `feeBps` from storage to compute the fee deducted from the deposit.
3. The net `rsETHAmount` after fee is minted to the user.

`RSETHPoolV3.sol` — ETH deposit path:

```solidity
// line 258-262
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

`viewSwapRsETHAmountAndFee` reads `feeBps` live:

```solidity
// line 300-301
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
```

The admin can change `feeBps` at any moment without a timelock:

```solidity
// line 518-521
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_feeBps > 1000) revert InvalidFeeAmount();
    feeBps = _feeBps;
    emit FeeBpsSet(_feeBps);
}
```

Contrast this with other sensitive setters (e.g. `setRSETHOracle`, `setIsEthDepositEnabled`) which are gated behind `TIMELOCK_ROLE`, giving users advance notice. `setFeeBps` has **no timelock** and takes effect in the same block it is called.

The `deposit()` function accepts no `minAmountOut` or `deadline` parameter, so the user has no on-chain mechanism to reject execution under changed fee conditions.

The same pattern is present identically in:
- `RSETHPoolV3ExternalBridge.sol` (`setFeeBps` line 744, `viewSwapRsETHAmountAndFee` line 419)
- `RSETHPool.sol` (`setFeeBps` line 574, `viewSwapRsETHAmountAndFee` line 312)
- `RSETHPoolV3WithNativeChainBridge.sol`
- `RSETHPoolNoWrapper.sol`

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previewed `viewSwapRsETHAmountAndFee()` off-chain (or via a UI) before submitting their transaction will receive fewer `wrsETH` tokens than shown. The shortfall is captured as protocol fees. The user's ETH/LST is not lost outright, but the wrsETH they receive is worth less than what they agreed to accept. The maximum fee cap is 1000 bps (10%) in `RSETHPoolV3` and 10 000 bps (100%) in `RSETHPool` and `RSETHPoolV3ExternalBridge`, bounding the worst-case loss per deposit.

---

### Likelihood Explanation

**Low-Medium.** The admin must raise `feeBps` in the same window that a user's transaction is pending in the mempool. On L2s (where these pools are deployed) block times are very short (< 1 second on Arbitrum/Base/Optimism), so the window is narrow in practice. However, the absence of any timelock on `setFeeBps` means the change can be made atomically in the same block as a pending user transaction if the admin (or a compromised admin key) acts deliberately. The scenario is realistic enough to warrant a protective parameter.

---

### Recommendation

1. **Add a `minAmountOut` parameter** to both `deposit()` overloads. Revert if the computed `rsETHAmount` is below this threshold:
   ```solidity
   function deposit(string memory referralId, uint256 minAmountOut) external payable {
       ...
       (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFrole(amount);
       if (rsETHAmount < minAmountOut) revert SlippageExceeded();
       ...
   }
   ```
2. **Alternatively, gate `setFeeBps` behind `TIMELOCK_ROLE`** (consistent with other sensitive setters), giving users advance notice of fee changes and time to avoid submitting transactions under the old fee assumption.

---

### Proof of Concept

1. `feeBps` is currently 0 bps. Alice calls `deposit{value: 1 ether}("ref")` on `RSETHPoolV3`.
2. Her transaction sits in the mempool.
3. Admin calls `setFeeBps(1000)` (10%) — takes effect immediately, no timelock.
4. Alice's `deposit` executes: `viewSwapRsETHAmountAndFee(1 ether)` computes `fee = 0.1 ether`, `amountAfterFee = 0.9 ether`. Alice receives wrsETH worth only 0.9 ETH instead of the 1 ETH she expected.
5. Alice had no on-chain way to reject this outcome.

Relevant code references: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L258-263)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

**File:** contracts/pools/RSETHPoolV3.sol (L299-301)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3.sol (L518-521)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L377-383)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-420)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L744-748)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPool.sol (L271-277)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPool.sol (L311-313)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPool.sol (L574-578)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```
