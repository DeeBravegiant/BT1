### Title
Missing Zero-Address Validation in `withdrawFees` Permanently Burns Accumulated ETH Protocol Fees — (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`)

---

### Summary

The `withdrawFees(address receiver)` function in three pool contracts accepts a caller-supplied `receiver` address with no zero-address guard. Because a low-level ETH call to `address(0)` succeeds silently in the EVM, a `BRIDGER_ROLE` holder who passes `address(0)` — accidentally or otherwise — will permanently destroy all accumulated ETH fees in a single transaction, with no recovery path.

---

### Finding Description

All three pool contracts expose an ETH fee-withdrawal path:

**`RSETHPoolV3.sol`**
```solidity
function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
    uint256 amountToSendInETH = feeEarnedInETH;
    feeEarnedInETH = 0;
    (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
    if (!success) revert TransferFailed();
    emit FeesWithdrawn(amountToSendInETH);
}
``` [1](#0-0) 

**`RSETHPoolV3ExternalBridge.sol`** — identical pattern: [2](#0-1) 

**`RSETHPoolNoWrapper.sol`** — identical pattern: [3](#0-2) 

**Root cause**: None of these functions contain a `require(receiver != address(0))` guard before the low-level call. In the EVM, `payable(address(0)).call{value: X}("")` returns `(true, "")` — it does not revert. The ETH is irrecoverably destroyed. The accounting variable `feeEarnedInETH` is zeroed out before the call, so there is no way to retry or recover.

The ERC-20 overload `withdrawFees(address receiver, address token)` is **not** affected because OpenZeppelin's `safeTransfer` internally enforces `to != address(0)` and would revert. [4](#0-3) 

---

### Impact Explanation

`feeEarnedInETH` accumulates a fraction of every ETH deposit made by users across the pool's lifetime. These are protocol fees — unclaimed yield belonging to the protocol treasury. Sending them to `address(0)` is a one-way, irreversible destruction. The impact maps to **Medium — Permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

The `BRIDGER_ROLE` is a trusted operational role that regularly calls `withdrawFees` to route fees off-chain. The risk of accidentally supplying `address(0)` (e.g., from a misconfigured script, a deployment error, or a copy-paste mistake) is realistic in an operational context. No external attacker is required; the missing guard is the sole root cause.

---

### Recommendation

Add a zero-address check at the top of each ETH `withdrawFees` function:

```solidity
function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
    if (receiver == address(0)) revert InvalidReceiver();
    uint256 amountToSendInETH = feeEarnedInETH;
    feeEarnedInETH = 0;
    (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
    if (!success) revert TransferFailed();
    emit FeesWithdrawn(amountToSendInETH);
}
```

Apply the same fix to `RSETHPoolV3ExternalBridge.sol` and `RSETHPoolNoWrapper.sol`.

---

### Proof of Concept

```solidity
// Assume BRIDGER_ROLE holder calls with address(0) by mistake
pool.withdrawFees(address(0));

// Result:
// - feeEarnedInETH is set to 0 (state cleared before call)
// - payable(address(0)).call{value: X}("") returns (true, "")
// - ETH is permanently destroyed
// - No revert, no recovery
assert(address(pool).balance == 0);          // ETH gone
assert(pool.feeEarnedInETH() == 0);          // accounting zeroed
assert(address(0).balance == X);             // burned
```

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L453-461)
```text
    function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
        // withdraw fees in ETH
        uint256 amountToSendInETH = feeEarnedInETH;
        feeEarnedInETH = 0;
        (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
        if (!success) revert TransferFailed();

        emit FeesWithdrawn(amountToSendInETH);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L464-479)
```text
    function withdrawFees(
        address receiver,
        address token
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        // withdraw fees in token
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);

        emit FeesWithdrawn(amountToSendInToken, token);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L617-625)
```text
    function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
        // withdraw fees in ETH
        uint256 amountToSendInETH = feeEarnedInETH;
        feeEarnedInETH = 0;
        (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
        if (!success) revert TransferFailed();

        emit FeesWithdrawn(amountToSendInETH);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L382-390)
```text
    function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
        // withdraw fees in ETH
        uint256 amountToSendInETH = feeEarnedInETH;
        feeEarnedInETH = 0;
        (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
        if (!success) revert TransferFailed();

        emit FeesWithdrawn(amountToSendInETH);
    }
```
