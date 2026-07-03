I have the full contract. Let me trace the exact exploit path through the code.

### Title
Cross-Token Withdrawal Drains Bridger Collateral and Permanently Freezes Depositor's Assets - (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`_withdraw` only validates that the requested asset is in `allowedTokens`. It does not enforce that a user redeems the same token they deposited. When two tokens are both allowed, a user who deposited tokenA can burn their wrsETH to withdraw tokenB, draining the bridger's collateral and leaving tokenA permanently irrecoverable.

---

### Finding Description

`_withdraw` performs exactly one asset-level check:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  line 121
if (!allowedTokens[_asset]) revert TokenNotAllowed();
``` [1](#0-0) 

There is no record of which token each wrsETH unit was minted against, and no check that the asset being withdrawn matches the asset that was deposited. wrsETH is a single fungible token whose `totalSupply` aggregates deposits of all allowed tokens. Once a second token is added via `addAllowedToken` (TIMELOCK_ROLE) or `reinitialize` (DEFAULT_ADMIN_ROLE), the invariant `sum(tokenX.balanceOf(wrapper)) == totalSupply()` can be broken by any holder. [2](#0-1) [3](#0-2) 

`depositBridgerAssets` is the mechanism by which the bridger posts collateral for wrsETH that was already minted on L2 (e.g. via the `mint` path). Its cap is computed per-asset:

```solidity
return wrsETHSupply - balanceOfAssetInWrapper;   // line 109
``` [4](#0-3) 

This means the bridger can legitimately deposit N tokenB as long as `totalSupply() - tokenB.balanceOf(wrapper) >= N`, regardless of how much tokenA is already held.

---

### Impact Explanation

- **tokenA is permanently frozen**: after the attack `totalSupply() == 0`, so no wrsETH exists to redeem tokenA. There is no admin rescue path in the contract.
- **tokenB (bridger collateral) is stolen**: the bridger deposited tokenB to back already-circulating wrsETH; the attacker drains it.

Both impacts qualify as **Critical: Permanent freezing of funds** and **Critical: Direct theft of user funds**.

---

### Likelihood Explanation

The precondition — two tokens in `allowedTokens` — is the explicit purpose of `addAllowedToken` / `reinitialize`. The bridger depositing collateral for a second token is the normal operational flow. No privileged role, leaked key, or external protocol failure is required; any wrsETH holder can execute step 3 unilaterally.

---

### Recommendation

Track per-token accounting. The simplest fix is to record a `depositedToken` per wrsETH mint and enforce it on burn, or to maintain per-token share balances. Alternatively, restrict `withdraw` so that a user can only redeem the token they deposited by storing a `mapping(address user => mapping(address token => uint256 shares))`.

A lighter-weight mitigation is to prohibit cross-token withdrawal by requiring that `_asset` matches the token the caller originally deposited, but this requires per-user deposit tracking.

---

### Proof of Concept

```
State: tokenA and tokenB both in allowedTokens; totalSupply = 0.

Step 1 — user calls deposit(tokenA, 100):
  tokenA.balanceOf(wrapper) = 100
  totalSupply()             = 100

Step 2 — bridger calls depositBridgerAssets(tokenB, 100):
  maxAmountToDepositBridgerAsset(tokenB)
    = totalSupply() - tokenB.balanceOf(wrapper)
    = 100 - 0 = 100  ✓ (no revert)
  tokenB.balanceOf(wrapper) = 100
  totalSupply()             = 100   (unchanged)

Step 3 — user calls withdraw(tokenB, 100):
  allowedTokens[tokenB] == true  ✓ (only guard)
  burns 100 wrsETH from user
  transfers 100 tokenB to user

Final state:
  tokenA.balanceOf(wrapper) = 100   ← permanently frozen
  tokenB.balanceOf(wrapper) = 0     ← drained
  totalSupply()             = 0     ← no wrsETH left to redeem tokenA
```

The invariant `sum(tokenX.balanceOf(wrapper)) <= totalSupply()` is violated: 100 ≤ 0 is false. tokenA can never be recovered. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L99-110)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L162-170)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, msg.sender, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-176)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
