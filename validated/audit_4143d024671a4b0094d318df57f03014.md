### Title
`setFeeBps` Uses Incorrect Cap of `1000` Instead of `10_000` - (File: contracts/pools/RSETHPoolV2ExternalBridge.sol)

### Summary
`RSETHPoolV2ExternalBridge.setFeeBps` enforces a maximum of `1000` for `_feeBps`, while the fee calculation divides by `10_000`. This is inconsistent with every other pool contract in the ecosystem, which all cap at `10_000`.

### Finding Description
In `RSETHPoolV2ExternalBridge`, the fee setter enforces:

```solidity
function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_feeBps > 1000) revert InvalidFeeAmount();   // ← cap is 1000
    feeBps = _feeBps;
    emit FeeBpsSet(_feeBps);
}
``` [1](#0-0) 

But the fee is computed against a divisor of `10_000`:

```solidity
fee = amount * feeBps / 10_000;
``` [2](#0-1) 

Every other pool contract in the repository caps `feeBps` at `10_000`, consistent with the divisor:

- `RSETHPool.setFeeBps`: `if (_feeBps > 10_000) revert InvalidFeeAmount();` [3](#0-2) 
- `RSETHPoolNoWrapper.setFeeBps`: `if (_feeBps > 10_000) revert InvalidFeeAmount();` [4](#0-3) 
- `RSETHPoolV3ExternalBridge.setFeeBps`: `if (_feeBps > 10_000) revert InvalidFeeAmount();` [5](#0-4) 

### Impact Explanation
The contract fails to deliver its promised configuration range. The admin of `RSETHPoolV2ExternalBridge` is silently limited to a maximum fee of 10% (1000/10_000), while the same admin role on every other pool can set fees up to 100% (10_000/10_000). Any attempt to set a fee above 1000 bps reverts with `InvalidFeeAmount`, even though the arithmetic is designed to handle values up to 10_000. This is a **Low** impact — the contract fails to deliver promised configuration capability but does not directly cause loss of deposited funds.

### Likelihood Explanation
The bug is triggered any time the `DEFAULT_ADMIN_ROLE` holder calls `setFeeBps` with a value between 1001 and 10_000 (e.g., attempting to set a 15% fee = 1500 bps). The call reverts unexpectedly. This is a straightforward admin operation that will be exercised during normal protocol management.

### Recommendation
Change the cap in `RSETHPoolV2ExternalBridge.setFeeBps` to match the divisor and all other pool contracts:

```solidity
- if (_feeBps > 1000) revert InvalidFeeAmount();
+ if (_feeBps > 10_000) revert InvalidFeeAmount();
```

### Proof of Concept
1. Deploy `RSETHPoolV2ExternalBridge` and call `setFeeBps(1500)` (15% fee, valid in all other pools).
2. The call reverts with `InvalidFeeAmount` because `1500 > 1000`.
3. Call `setFeeBps(1000)` — succeeds. The resulting fee on a 1 ETH deposit is `1e18 * 1000 / 10_000 = 0.1 ETH` (10%).
4. On `RSETHPoolV3ExternalBridge`, `setFeeBps(1500)` succeeds and charges 15% as intended.
5. The inconsistency confirms the cap in `RSETHPoolV2ExternalBridge` is wrong by a factor of 10.

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L308-308)
```text
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L531-535)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPool.sol (L575-575)
```text
        if (_feeBps > 10_000) revert InvalidFeeAmount();
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L525-525)
```text
        if (_feeBps > 10_000) revert InvalidFeeAmount();
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L745-745)
```text
        if (_feeBps > 10_000) revert InvalidFeeAmount();
```
