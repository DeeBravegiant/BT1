### Title
Unprivileged Caller Can Trigger Protocol-Wide Auto-Pause via `updateRSETHPrice()` — (`contracts/LRTOracle.sol`)

---

### Summary

`updateRSETHPrice()` in `LRTOracle.sol` carries no role restriction and is callable by any address. When the rsETH price has fallen more than `pricePercentageLimit` below `highestRsethPrice`, any caller can invoke this function to automatically pause `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` itself, freezing all user deposits and withdrawals without any privileged authorization.

---

### Finding Description

`updateRSETHPrice()` is declared `public whenNotPaused` with no role guard: [1](#0-0) 

It delegates to `_updateRsETHPrice()`, which contains the following downside-protection branch: [2](#0-1) 

When `newRsETHPrice < highestRsethPrice` and the difference exceeds `pricePercentageLimit × highestRsethPrice`, the function unconditionally calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` on the oracle. Because `updateRSETHPrice()` has no access control, any unprivileged address can trigger this three-contract pause the moment the price condition is satisfied.

The protocol also exposes a manager-gated variant for controlled updates: [3](#0-2) 

The existence of `updateRSETHPriceAsManager()` confirms the design intent that sensitive price updates should be role-restricted, yet the public entry point bypasses this entirely.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Once the auto-pause fires:

- `LRTDepositPool.depositETH()` and `depositAsset()` revert (`whenNotPaused`). [4](#0-3) 
- `LRTWithdrawalManager.initiateWithdrawal()`, `completeWithdrawal()`, and `instantWithdrawal()` revert (`whenNotPaused`). [5](#0-4) 
- `LRTOracle.updateRSETHPrice()` itself becomes uncallable (`whenNotPaused`). [6](#0-5) 

All user funds are frozen until an admin holding `DEFAULT_ADMIN_ROLE` manually unpauses each contract. The attacker pays only gas; no capital is required.

---

### Likelihood Explanation

**Medium.** The trigger condition — rsETH price dropping more than `pricePercentageLimit` below `highestRsethPrice` — is realistic:

- EigenLayer slashing events reduce the ETH backing rsETH.
- LST de-pegs (stETH, ETHx) lower `totalETHInProtocol`.
- If `pricePercentageLimit` is configured conservatively (e.g., 1 %), even routine reward-cycle fluctuations can satisfy the condition.

Once the condition is met, the attack requires a single permissionless transaction. The attacker can monitor on-chain state and race ahead of any operator response.

---

### Recommendation

Add an operator or manager role guard to `updateRSETHPrice()` so that the auto-pause path is only reachable by authorized callers:

```solidity
// Option A: restrict the public entry point
function updateRSETHPrice() public whenNotPaused onlyLRTOperator {
    _updateRsETHPrice();
}

// Option B: move the auto-pause logic exclusively into the manager variant
// and keep the public function read-only / revert-safe
```

Alternatively, decouple the auto-pause trigger from `_updateRsETHPrice()` entirely and expose it as a separate, role-gated function so that price reads and emergency pausing are independently controlled.

---

### Proof of Concept

1. `highestRsethPrice = 1.05 ETH`, `pricePercentageLimit = 1e16` (1 %).
2. A slashing event reduces `totalETHInProtocol`; `_getTotalEthInProtocol()` now returns a value that yields `newRsETHPrice = 1.03 ETH`.
3. `diff = 1.05 - 1.03 = 0.02 ETH > 0.01 × 1.05 = 0.0105 ETH` → `isPriceDecreaseOffLimit = true`.
4. Attacker calls `LRTOracle.updateRSETHPrice()` (no role required, oracle not yet paused).
5. `_updateRsETHPrice()` executes lines 277–281: `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`.
6. All deposits and withdrawals revert for every user until an admin manually unpauses all three contracts. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L47-50)
```text
    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }
```

**File:** contracts/LRTOracle.sol (L85-89)
```text
    /// @notice updates RSETH/ETH exchange rate
    /// @dev calculates rsETH price based on stakedAsset value received from EigenLayer
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L91-96)
```text
    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L269-282)
```text
        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

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

**File:** contracts/LRTWithdrawalManager.sol (L150-185)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }

    /// @notice Completes a user's withdrawal process by transferring the ETH/LST amount corresponding to the rsETH
    /// unstaked.
    /// @param asset The asset address the user wishes to withdraw.
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```
