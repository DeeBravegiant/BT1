Audit Report

## Title
Stale Off-Chain Cap Read Allows Temporary Under-Collateralization of Minted agETH — (`contracts/agETH/AGETHTokenWrapper.sol`)

## Summary
`depositBridgerAssets` accepts a caller-supplied `_amount` and only checks that it does not exceed the live cap at call time. Because `mint` (callable by the bridge contract in normal operation) can increase `totalSupply` between the bridger's off-chain read of `maxAmountToDepositBridgerAsset` and the on-chain execution of `depositBridgerAssets`, the bridger deposits a stale (lower) amount, leaving the delta of newly minted agETH temporarily unbacked. Holders of those agETH tokens cannot redeem them until the bridger makes a subsequent deposit covering the remaining gap.

## Finding Description
`maxAmountToDepositBridgerAsset` computes the uncollateralized gap as `totalSupply() - balanceOf(address(this))`. [1](#0-0) 

`depositBridgerAssets` re-evaluates this at call time but only reverts if the supplied amount *exceeds* the cap — it does not require the full cap to be consumed: [2](#0-1) 

`mint` is callable by any `MINTER_ROLE` holder (the bridge contract in normal operation) and increases `totalSupply` without adding any backing: [3](#0-2) 

**Race sequence (no malicious intent required):**

| Step | Action | `totalSupply` | `balance` | `cap` |
|------|--------|--------------|-----------|-------|
| 0 | Initial state | S | B | C = S−B |
| 1 | Bridger reads cap off-chain | S | B | C |
| 2 | Bridge mints D agETH (normal bridge op) | S+D | B | C+D |
| 3 | Bridger calls `depositBridgerAssets(asset, C)` | S+D | B | C+D |
| 4 | Check: `C+D >= C` → passes; deposits C | S+D | B+C | D |

After step 4, D agETH exist with zero backing. Any holder of those D agETH who calls `withdraw` triggers `_withdraw`, which burns their agETH and then calls `safeTransfer`: [4](#0-3) 

This reverts because the contract holds only `B+C` altAgETH against `S+D` agETH supply. The D agETH holders cannot withdraw until the bridger makes a second `depositBridgerAssets` call covering the remaining D.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The contract advertises a 1:1 redemption guarantee between agETH and altAgETH. During the window between the bridger's stale deposit and its next deposit cycle, holders of the D freshly-bridged agETH cannot redeem their tokens. No value is permanently destroyed — the bridger's next deposit cycle restores full collateralization — but the temporary non-redeemability is a concrete, observable failure of the contract's core promise.

## Likelihood Explanation
No malicious actor is required. The MINTER_ROLE is the bridge contract performing its normal function (minting agETH for users bridging from L1). The BRIDGER_ROLE is performing its normal function (depositing backing assets). The race is an inherent consequence of the two-step off-chain-read / on-chain-deposit design. On any active L2 deployment with regular bridge traffic, concurrent mints between the bridger's read and deposit are routine and expected.

## Recommendation
Replace the caller-supplied `_amount` with the live cap computed atomically inside `depositBridgerAssets`, so the bridger always deposits exactly the current uncollateralized gap in a single atomic operation:

```solidity
function depositBridgerAssets(address _asset) external onlyRole(BRIDGER_ROLE) {
    uint256 amount = maxAmountToDepositBridgerAsset(_asset);
    if (amount == 0) revert CannotDeposit();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), amount);
    emit BridgerDeposited(_asset, amount);
}
```

This eliminates the stale-read window entirely: the cap is read and consumed atomically within the same transaction.

## Proof of Concept
The submitted Foundry test directly demonstrates the issue:

1. Minter mints 100e18 agETH to `user` (no backing yet); bridger reads cap = 100e18 off-chain.
2. Minter mints 50e18 agETH to `bridgee` (simulating a concurrent bridge operation); live cap is now 150e18.
3. Bridger calls `depositBridgerAssets(altAgETH, 100e18)` — check `150e18 >= 100e18` passes; 100e18 altAgETH deposited.
4. `maxAmountToDepositBridgerAsset` now returns 50e18 — `bridgee`'s 50e18 agETH are unbacked.
5. `bridgee` calls `withdraw(altAgETH, 50e18)` — reverts because the contract holds only 100e18 altAgETH against 150e18 agETH supply.

The test confirms the revert at step 5 with `vm.expectRevert()`, proving the temporary freeze of `bridgee`'s funds.

### Citations

**File:** contracts/agETH/AGETHTokenWrapper.sol (L90-101)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrapped agETH minted
        uint256 agETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > agETHSupply) return 0;

        return agETHSupply - balanceOfAssetInWrapper;
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L114-116)
```text
        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L143-151)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L165-167)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
