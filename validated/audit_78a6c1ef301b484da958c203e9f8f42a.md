### Title
`setFeeBps()` Missing Timelock Protection Allows Instant Fee Changes Without User Notice - (File: contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolV2NBA.sol, contracts/agETH/AGETHPoolV3.sol)

---

### Summary

Multiple L2 pool contracts define a `TIMELOCK_ROLE` and correctly apply it to other critical setter functions, but `setFeeBps()` (and `setTokenFeeBps()` in `RSETHPool.sol`) is guarded only by `DEFAULT_ADMIN_ROLE`. This allows the admin to instantly raise deposit fees to the maximum cap with no advance notice to users. The protocol's own sibling contracts (`RSETHPoolNoWrapper.sol` and `RSETHPoolV2.sol`) correctly gate `setFeeBps()` behind `TIMELOCK_ROLE`, confirming the intended design was to require a timelock for fee changes.

---

### Finding Description

The following contracts expose `setFeeBps()` (and in one case `setTokenFeeBps()`) behind `DEFAULT_ADMIN_ROLE` with no timelock delay:

| Contract | Function | Guard | Max Fee |
|---|---|---|---|
| `RSETHPool.sol` | `setFeeBps()` | `DEFAULT_ADMIN_ROLE` | 10,000 bps (100%) |
| `RSETHPool.sol` | `setTokenFeeBps()` | `DEFAULT_ADMIN_ROLE` | 10,000 bps (100%) |
| `RSETHPoolV2NBA.sol` | `setFeeBps()` | `DEFAULT_ADMIN_ROLE` | 10,000 bps (100%) |
| `RSETHPoolV3.sol` | `setFeeBps()` | `DEFAULT_ADMIN_ROLE` | 1,000 bps (10%) |
| `RSETHPoolV3WithNativeChainBridge.sol` | `setFeeBps()` | `DEFAULT_ADMIN_ROLE` | 1,000 bps (10%) |
| `AGETHPoolV3.sol` | `setFeeBps()` | `DEFAULT_ADMIN_ROLE` | 10,000 bps (100%) |

In contrast, the sibling contracts correctly use `TIMELOCK_ROLE`:

`RSETHPoolNoWrapper.sol` line 524 and `RSETHPoolV2.sol` line 303 both gate `setFeeBps()` behind `TIMELOCK_ROLE`.

The fee is applied at deposit time in all affected contracts:

```
fee = amount * feeBps / 10_000;
rsETHAmount = (amount - fee) * 1e18 / rsETHToETHrate;
```

A user calling `deposit()` receives `rsETHAmount` tokens computed from the current `feeBps` at the moment of the transaction. Because there is no timelock, the admin can raise `feeBps` to the maximum in the same block as a user's deposit, and the user has no on-chain mechanism to react.

In `RSETHPool.sol`, the same issue applies to per-token fees via `setTokenFeeBps()`, which uses `tokenFeeBps[token]` in `viewSwapRsETHAmountAndFee(amount, token)`.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns.**

Users depositing ETH or supported tokens into these pools receive fewer rsETH/agETH tokens than they observed when constructing their transaction. In the extreme case (RSETHPool, RSETHPoolV2NBA, AGETHPoolV3), the fee can be raised to 10,000 bps (100%), meaning the user's entire deposit is taken as a fee and they receive 0 rsETH/agETH in return. Even at lower caps (RSETHPoolV3, RSETHPoolV3WithNativeChainBridge), an instant jump to 1,000 bps (10%) represents a significant unannounced cost increase. The protocol's own design intent — evidenced by `RSETHPoolNoWrapper.sol` and `RSETHPoolV2.sol` using `TIMELOCK_ROLE` — is that fee changes should be delayed to give users time to react.

---

### Likelihood Explanation

**Low-Medium.** The admin is assumed to be non-malicious under normal operation, but the absence of a timelock means there is no on-chain enforcement of advance notice. Any key compromise, governance error, or unilateral decision results in an instant fee change with no recourse for users. The inconsistency across the contract family (some contracts correctly timelocked, others not) increases the probability that this gap is exploited or triggered accidentally.

---

### Recommendation

Apply `onlyRole(TIMELOCK_ROLE)` to `setFeeBps()` and `setTokenFeeBps()` in all affected contracts, consistent with the pattern already used in `RSETHPoolNoWrapper.sol` and `RSETHPoolV2.sol`:

```solidity
// Correct pattern (RSETHPoolNoWrapper.sol, RSETHPoolV2.sol)
function setFeeBps(uint256 _feeBps) external onlyRole(TIMELOCK_ROLE) { ... }

// Incorrect pattern (RSETHPool.sol, RSETHPoolV3.sol, etc.)
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) { ... }
```

---

### Proof of Concept

**Step 1 — Admin raises fee to maximum with no delay.**

In `RSETHPool.sol`, the admin calls:
```solidity
setFeeBps(10_000); // 100% fee, guarded only by DEFAULT_ADMIN_ROLE
``` [1](#0-0) 

**Step 2 — User's deposit in the same block receives 0 rsETH.**

The user calls `deposit(referralId)` with `msg.value = 1 ETH`:
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
// fee = 1e18 * 10_000 / 10_000 = 1e18 (100%)
// amountAfterFee = 1e18 - 1e18 = 0
// rsETHAmount = 0 * 1e18 / rate = 0
``` [2](#0-1) 

The user's 1 ETH is retained in the contract as `feeEarnedInETH` and they receive 0 rsETH.

**Contrast with correct implementation in `RSETHPoolNoWrapper.sol`:** [3](#0-2) 

**Same issue in `RSETHPoolV3.sol`** — `setFeeBps()` uses `DEFAULT_ADMIN_ROLE` while `setRSETHOracle()` and `setIsEthDepositEnabled()` in the same contract correctly use `TIMELOCK_ROLE`: [4](#0-3) [5](#0-4) 

**Same issue in `RSETHPoolV3WithNativeChainBridge.sol`:** [6](#0-5) 

**Same issue in `RSETHPoolV2NBA.sol`:** [7](#0-6) 

**Same issue in `AGETHPoolV3.sol`:** [8](#0-7) 

**Additional issue — `setTokenFeeBps()` in `RSETHPool.sol` also lacks timelock:** [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPool.sol (L311-320)
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

**File:** contracts/pools/RSETHPool.sol (L574-578)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPool.sol (L583-594)
```text
    function setTokenFeeBps(
        address token,
        uint256 _feeBps
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
        onlySupportedToken(token)
    {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        tokenFeeBps[token] = _feeBps;
        emit TokenFeeBpsSet(token, _feeBps);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L524-530)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(TIMELOCK_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();

        feeBps = _feeBps;

        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L518-522)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L526-528)
```text
    function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(TIMELOCK_ROLE) {
        isEthDepositEnabled = _isEthDepositEnabled;
        emit IsEthDepositEnabled(_isEthDepositEnabled);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L581-585)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L163-167)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L245-251)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();

        feeBps = _feeBps;

        emit FeeBpsSet(_feeBps);
    }
```
