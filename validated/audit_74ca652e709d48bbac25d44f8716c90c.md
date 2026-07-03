### Title
`completeUnstaking` Convenience Wrapper Is Permanently Broken for Non-Operators Due to Conflicting Access Control on Internal Callee - (File: contracts/NodeDelegator.sol)

### Summary

`NodeDelegator.completeUnstaking(withdrawal, assets)` is an `external` function with **no access-control modifier**, making it appear callable by anyone. It immediately delegates to the overloaded `completeUnstaking(withdrawal, assets, receiveAsTokens)`, which carries `onlyLRTOperator`. Because the call is an internal Solidity dispatch (not `this.completeUnstaking(...)`), `msg.sender` is the original external caller throughout. Any non-operator who calls the no-modifier wrapper will always revert at the `onlyLRTOperator` check inside the callee, making the wrapper permanently unusable for the public.

### Finding Description

`NodeDelegator` exposes two overloads of `completeUnstaking`:

```solidity
// contracts/NodeDelegator.sol  L336-338
function completeUnstaking(
    IDelegationManager.Withdrawal calldata withdrawal,
    IERC20[] calldata assets
) external {
    completeUnstaking(withdrawal, assets, true);   // internal dispatch
}
```

```solidity
// contracts/NodeDelegator.sol  L346-400
function completeUnstaking(
    IDelegationManager.Withdrawal calldata withdrawal,
    IERC20[] calldata assets,
    bool receiveAsTokens
)
    public
    nonReentrant
    whenNotPaused
    onlyLRTOperator          // ← role check on msg.sender
{
    ...
}
```

The two-argument wrapper has **no modifier**. Its sole action is to call the three-argument overload. Because this is a direct internal call (not `this.completeUnstaking(...)`), Solidity does **not** update `msg.sender`; the original external caller's address is still `msg.sender` when `onlyLRTOperator` is evaluated. Any address that is not granted `OPERATOR_ROLE` in `LRTConfig` will hit:

```solidity
// contracts/utils/LRTConfigRoleChecker.sol  L34-39
modifier onlyLRTOperator() {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.OPERATOR_ROLE, msg.sender)) {
        revert ILRTConfig.CallerNotLRTConfigOperator();
    }
    _;
}
```

and revert unconditionally. The two-argument entry point is therefore dead code for every non-operator address, despite advertising no restriction.

The same structural defect exists in `stake32EthValidated` (L186-200), which is `external` with no modifier and internally calls `stake32Eth` (L150-175, `onlyLRTOperator`).

### Impact Explanation

`completeUnstaking` is the only on-chain path to finalise a queued EigenLayer withdrawal and move assets from EigenLayer back into `LRTUnstakingVault`. If the two-argument wrapper were genuinely open to the public (as its signature implies), any keeper or user could advance the unstaking queue after the EigenLayer delay expires. Because it is silently operator-gated, assets queued for withdrawal remain locked in EigenLayer until an operator acts. If the operator set is unavailable (key rotation, incident response, etc.) the locked ETH/LST cannot be recovered by any other party, constituting a **temporary freezing of funds**.

### Likelihood Explanation

The two-argument `completeUnstaking` is a deployed, externally callable function with no NatSpec warning about operator-only access. Any integrator, keeper bot, or user who discovers the function signature and attempts to call it after the EigenLayer delay will receive an opaque revert. The likelihood of attempted use by non-operators is high given the function's apparent openness.

### Recommendation

Either:
1. Add `onlyLRTOperator` to the two-argument wrapper to make the restriction explicit and consistent, **or**
2. Remove the `onlyLRTOperator` modifier from the three-argument overload and perform the role check only where strictly necessary (e.g., when `receiveAsTokens == false`), allowing any caller to complete withdrawals with `receiveAsTokens = true`.

```solidity
// Option 1 – make restriction explicit
function completeUnstaking(
    IDelegationManager.Withdrawal calldata withdrawal,
    IERC20[] calldata assets
) external onlyLRTOperator {
    completeUnstaking(withdrawal, assets, true);
}
```

### Proof of Concept

1. Operator calls `initiateUnstaking(strategies, shares)` — withdrawal is queued in EigenLayer.
2. EigenLayer delay passes.
3. Non-operator (e.g., a keeper bot) calls `completeUnstaking(withdrawal, assets)` (the two-argument form).
4. Internally, `completeUnstaking(withdrawal, assets, true)` is invoked with the keeper's address as `msg.sender`.
5. `onlyLRTOperator` checks `hasRole(OPERATOR_ROLE, keeper)` → `false` → `CallerNotLRTConfigOperator` revert.
6. Assets remain locked in EigenLayer; the keeper cannot advance the queue regardless of how long it waits. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/NodeDelegator.sol (L336-338)
```text
    function completeUnstaking(IDelegationManager.Withdrawal calldata withdrawal, IERC20[] calldata assets) external {
        completeUnstaking(withdrawal, assets, true);
    }
```

**File:** contracts/NodeDelegator.sol (L346-355)
```text
    function completeUnstaking(
        IDelegationManager.Withdrawal calldata withdrawal,
        IERC20[] calldata assets,
        bool receiveAsTokens
    )
        public
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
```

**File:** contracts/utils/LRTConfigRoleChecker.sol (L34-39)
```text
    modifier onlyLRTOperator() {
        if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.OPERATOR_ROLE, msg.sender)) {
            revert ILRTConfig.CallerNotLRTConfigOperator();
        }
        _;
    }
```
