### Title
`BURNER_ROLE` Can Burn Any rsETH Holder's Tokens Without Allowance — (`contracts/RSETH.sol`)

### Summary
`RSETH.burnFrom` is gated only by `BURNER_ROLE` and calls `_burn` directly, bypassing any ERC-20 allowance check. Any address holding `BURNER_ROLE` can destroy an arbitrary user's rsETH balance without that user's consent, causing direct, permanent loss of their staked-ETH position.

### Finding Description
The standard OpenZeppelin `ERC20Burnable.burnFrom` enforces `_spendAllowance(account, _msgSender(), amount)` before burning. `RSETH` overrides this pattern with a custom function that skips the allowance step entirely:

```solidity
// contracts/RSETH.sol L245-248
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _burn(account, amount);          // no _spendAllowance call
}
``` [1](#0-0) 

The `BURNER_ROLE` is intended to be held by protocol contracts such as `LRTWithdrawalManager`, which legitimately burns rsETH during withdrawals. However, the function signature accepts any `account` address, so any current or future `BURNER_ROLE` holder can target any rsETH holder's balance with no approval required from the victim.

Legitimate callers in `LRTWithdrawalManager` burn either from `msg.sender` (the withdrawing user, who consented by calling the function) or from `address(this)` (the contract's own balance). Neither use case requires the ability to burn from an arbitrary third-party address without allowance. [2](#0-1) [3](#0-2) 

### Impact Explanation
rsETH is a yield-bearing receipt token backed 1:1 by staked ETH assets. Burning a user's rsETH without redeeming the underlying assets is equivalent to confiscating their staked ETH position. A `BURNER_ROLE` holder can call `burnFrom(victim, victimBalance)` in a single transaction, reducing the victim's rsETH balance to zero while the underlying collateral remains in the protocol — permanently destroying the victim's claim on those assets.

**Impact: Critical — direct theft/permanent freezing of user funds.**

### Likelihood Explanation
Exploitation requires control of an address holding `BURNER_ROLE`. This role is granted by the LRT admin and is expected to be held by a small set of protocol contracts. The likelihood is low in a non-compromised deployment, but the absence of any allowance guard means the attack surface exists by design and would be immediately exploitable if the role were ever misused, misconfigured, or compromised.

**Likelihood: Low** (mirrors the external report's 2/10 rating for the same class of issue).

### Recommendation
Replace the direct `_burn` call with the standard allowance-checked path, or restrict `burnFrom` so it can only burn from `msg.sender` (self-burn) or from addresses that have explicitly approved the caller:

```solidity
function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
    _enforceNotBlocked(account);
    _spendAllowance(account, msg.sender, amount); // enforce ERC-20 allowance
    _burn(account, amount);
}
```

Alternatively, split the function into a self-burn (no allowance needed) and a third-party burn (allowance required), matching the semantics of `ERC20Burnable`.

### Proof of Concept

1. Alice deposits ETH into `LRTDepositPool` and receives 10 rsETH.
2. The `BURNER_ROLE` holder (e.g., a compromised or malicious `LRTWithdrawalManager` upgrade, or any address to which the admin grants the role) calls:
   ```solidity
   RSETH(rsETH).burnFrom(alice, 10e18);
   ```
3. Alice's rsETH balance drops to 0. No allowance was checked. No assets were redeemed to Alice.
4. Alice's staked ETH remains locked in the protocol with no receipt token to claim it. [4](#0-3)

### Citations

**File:** contracts/RSETH.sol (L242-248)
```text
    /// @notice Burns rsETH when called by an authorized caller
    /// @param account the account to burn from
    /// @param amount the amount of rsETH to burn
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L227-229)
```text
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L301-305)
```text
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```
