### Title
Missing Access Control on `NodeDelegator.initialize2()` Allows Any Caller to Permanently Block Protocol Reinitializer - (File: contracts/NodeDelegator.sol)

### Summary
`NodeDelegator.initialize2()` is an `external` reinitializer function that carries no access-control modifier. Because OpenZeppelin's `reinitializer(2)` guard allows the function to execute exactly once (when the stored initializer version is less than 2), any external caller can invoke it before the protocol's upgrade transaction does, permanently consuming the one-time slot and preventing the protocol from ever running its own initialization logic.

### Finding Description
`NodeDelegator.initialize2()` is declared as follows:

```solidity
function initialize2() external reinitializer(2) {
    lastNonce = _getNonce();
}
``` [1](#0-0) 

The function is `external` with no role check, no `onlyLRTAdmin`, no `onlyLRTManager`, and no `onlyLRTOperator` guard. The `reinitializer(2)` modifier from OpenZeppelin's `Initializable` only enforces that the function runs at most once — it does not restrict the caller identity.

Every other reinitializer in the codebase carries an explicit role guard:

- `LRTWithdrawalManager.initialize2()` — `onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)` [2](#0-1) 
- `LRTWithdrawalManager.initialize3()` — `onlyLRTManager` [3](#0-2) 
- `LRTConverter.initialize2()` — `onlyLRTAdmin` [4](#0-3) 

`NodeDelegator.initialize2()` is the sole reinitializer that omits this pattern.

The function sets `lastNonce` to the current EigenLayer `cumulativeWithdrawalsQueued` value for this contract:

```solidity
function _getNonce() internal view returns (uint256) {
    return _getDelegationManager().cumulativeWithdrawalsQueued(address(this));
}
``` [5](#0-4) 

`lastNonce` is a `private` state variable declared at the contract level: [6](#0-5) 

### Impact Explanation
An attacker who calls `initialize2()` before the protocol's upgrade transaction:

1. Permanently consumes the `reinitializer(2)` slot — the protocol can never call `initialize2()` again on that proxy instance.
2. Sets `lastNonce` to whatever `cumulativeWithdrawalsQueued` returns at the moment of the attack, rather than the value the protocol intended.

If the protocol's upgrade script includes a call to `initialize2()` (the standard pattern for UUPS upgrades with reinitializers), that upgrade transaction will revert, forcing the team to redeploy or restructure the upgrade. This constitutes a contract failing to deliver its promised upgrade behavior.

**Impact: Low** — Contract fails to deliver promised returns (upgrade initialization is permanently disrupted; `lastNonce` is set to an attacker-controlled value).

### Likelihood Explanation
The attack requires only monitoring the public mempool for the upgrade transaction and front-running it with a direct call to `initialize2()`. No capital, no special role, and no prior state is required. Any external account can execute this at negligible cost. Likelihood is **High** given the trivial execution path.

### Recommendation
Add an access-control modifier consistent with the rest of the codebase:

```solidity
function initialize2() external reinitializer(2) onlyLRTAdmin {
    lastNonce = _getNonce();
}
```

This mirrors the pattern used in `LRTConverter.initialize2()` and ensures only the protocol admin can trigger the one-time reinitializer.

### Proof of Concept
1. Protocol deploys `NodeDelegator` behind a UUPS proxy and calls `initialize(lrtConfigAddr)` — initializer version is now `1`.
2. Protocol prepares an upgrade transaction that calls `initialize2()` to seed `lastNonce`.
3. Attacker observes the pending upgrade in the mempool and submits `NodeDelegator(proxy).initialize2()` with a higher gas price.
4. Attacker's transaction is mined first: `reinitializer(2)` succeeds, `lastNonce` is set to the current nonce, initializer version advances to `2`.
5. Protocol's upgrade transaction is mined: `reinitializer(2)` reverts with `InvalidInitialization` because version is already `≥ 2`.
6. The protocol can never call `initialize2()` on this proxy again; `lastNonce` is permanently set to the attacker-chosen value.

### Citations

**File:** contracts/NodeDelegator.sol (L56-56)
```text
    uint256 private lastNonce;
```

**File:** contracts/NodeDelegator.sol (L75-77)
```text
    function initialize2() external reinitializer(2) {
        lastNonce = _getNonce();
    }
```

**File:** contracts/NodeDelegator.sol (L584-586)
```text
    function _getNonce() internal view returns (uint256) {
        return _getDelegationManager().cumulativeWithdrawalsQueued(address(this));
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L109-116)
```text
    function initialize2(
        uint256 unlockedWithdrawalsCountETHx,
        uint256 unlockedWithdrawalsCountSTETH,
        uint256 unlockedWithdrawalsCountETH
    )
        external
        reinitializer(2)
        onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)
```

**File:** contracts/LRTWithdrawalManager.sol (L126-129)
```text
    function initialize3(uint256 unlockedWithdrawalsCountSFRXETH) external reinitializer(3) onlyLRTManager {
        address sfrxETHAddress = 0xac3E018457B222d93114458476f3E3416Abbe38F;
        unlockedWithdrawalsCount[sfrxETHAddress] = unlockedWithdrawalsCountSFRXETH;
    }
```

**File:** contracts/LRTConverter.sol (L98-111)
```text
    function initialize2(
        address _withdrawalQueueAddress,
        address _stETHAddress,
        address _swEXITAddress,
        address _swETHAddress
    )
        external
        reinitializer(2)
        onlyLRTAdmin
    {
        __ReentrancyGuard_init();
        __initializeSwETH(_swEXITAddress, _swETHAddress);
        __initializeStETH(_withdrawalQueueAddress, _stETHAddress);
    }
```
