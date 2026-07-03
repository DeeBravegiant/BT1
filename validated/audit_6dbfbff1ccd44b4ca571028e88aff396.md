### Title
Pause Bypass in `swapAssetToPremintedRsETH` Allows Whitelisted Users to Extract ETH When Contract Is Paused - (File: contracts/pools/RSETHPoolV2ExternalBridge.sol)

---

### Summary

`RSETHPoolV2ExternalBridge.swapAssetToPremintedRsETH` lacks the `whenNotPaused` modifier. Any address holding `WHITELISTED_USER_ROLE` — a role explicitly designed for external, non-operator users — can call this function and extract ETH from the pool even when the contract is paused. This is the direct Solidity analog of the iOS report: a protection mechanism (pause / Data Protection) is not applied to all paths, leaving assets accessible when they should be locked.

---

### Finding Description

`RSETHPoolV2ExternalBridge` implements a custom pause mechanism via the `paused` boolean and `whenNotPaused` modifier. [1](#0-0) 

The `deposit()` function correctly applies `whenNotPaused`: [2](#0-1) 

However, `swapAssetToPremintedRsETH` carries only `onlyOperatorOrWhitelisted(msg.sender)` and `nonReentrant` — no `whenNotPaused`: [3](#0-2) 

The `onlyOperatorOrWhitelisted` modifier grants access to any address holding `WHITELISTED_USER_ROLE`: [4](#0-3) 

`WHITELISTED_USER_ROLE` is a distinct role from `OPERATOR_ROLE`, explicitly intended for external, non-privileged users: [5](#0-4) 

The function itself transfers rsETH from the caller to the wrapper and sends ETH from the pool to the caller: [6](#0-5) 

---

### Impact Explanation

**Medium — Temporary freezing of funds (pause bypass).**

When the contract is paused — the expected response to an emergency such as oracle manipulation or an active exploit — the pause is supposed to halt all user-facing fund movements. Because `swapAssetToPremintedRsETH` is not guarded by `whenNotPaused`, a whitelisted user can continue to swap rsETH for ETH from the pool at whatever rate the oracle currently reports. If the pause was triggered precisely because the oracle is returning a manipulated rate, the whitelisted user can exploit that rate to extract more ETH than they are entitled to. The damage is bounded by `wrapper.maxAmountToDepositBridgerAsset(rsETH)`, but the protection the pause is meant to provide is entirely absent for this path. [7](#0-6) 

---

### Likelihood Explanation

**Low–Medium.** Two conditions must hold simultaneously: (1) the contract must be paused, and (2) the attacker must already hold `WHITELISTED_USER_ROLE`. Because `WHITELISTED_USER_ROLE` is designed to be granted to external users (not just internal operators), condition (2) is a normal operational state, not an exceptional one. The window of exploitation is the duration of the pause, which in an emergency can be extended.

---

### Recommendation

Add `whenNotPaused` to `swapAssetToPremintedRsETH`:

```solidity
function swapAssetToPremintedRsETH(
    address rsETH,
    uint256 rsETHAmount
)
    external
    nonReentrant
    whenNotPaused                        // ← add this
    onlyOperatorOrWhitelisted(msg.sender)
{
``` [3](#0-2) 

---

### Proof of Concept

1. Admin grants `WHITELISTED_USER_ROLE` to `attacker` (normal operational grant).
2. An emergency occurs (e.g., oracle returns a manipulated rate). The pauser calls `pause()`.
3. `deposit()` now reverts for all callers — the pause is working as intended for deposits.
4. `attacker` calls `swapAssetToPremintedRsETH(rsETH, amount)`:
   - `whenNotPaused` is absent → no revert.
   - `onlyOperatorOrWhitelisted` passes because `attacker` holds `WHITELISTED_USER_ROLE`.
   - rsETH is pulled from `attacker` and sent to the wrapper.
   - ETH is sent from the pool to `attacker` at the (potentially manipulated) oracle rate.
5. The attacker successfully extracts ETH from the pool while the contract is paused, bypassing the emergency protection entirely. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L90-91)
```text
    bytes32 public constant WHITELISTED_USER_ROLE = keccak256("WHITELISTED_USER_ROLE");

```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L92-95)
```text
    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L130-135)
```text
    modifier onlyOperatorOrWhitelisted(address account) {
        if (!hasRole(OPERATOR_ROLE, account) && !hasRole(WHITELISTED_USER_ROLE, account)) {
            revert NotOperatorOrWhitelisted();
        }
        _;
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-289)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L418-446)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlyOperatorOrWhitelisted(msg.sender)
    {
        UtilLib.checkNonZeroAddress(rsETH);

        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();

        // Get the amount of ETH to transfer to the user for the given amount of rsETH provided
        uint256 ethAmount = viewSwapAssetToPremintedRsETH(rsETHAmount);

        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the ETH from the pool to the sender
        if (getETHBalanceMinusFees() < ethAmount) revert InsufficientETHBalanceForReverseSwap();
        (bool success,) = payable(msg.sender).call{ value: ethAmount }("");
        if (!success) revert TransferFailed();

        emit ReverseSwapOccurred(msg.sender, rsETH, rsETHAmount, ethAmount);
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L592-596)
```text
    function pause() external onlyRole(PAUSER_ROLE) whenNotPaused {
        paused = true;
        emit Paused(msg.sender);
    }

```
